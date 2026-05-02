// SPDX-License-Identifier: Apache-2.0
//
// WelcomeViewModel — drives the first-run wizard that asks the user
// where their Nexus server lives.
//
// Wizard flow
// ===========
//   1. App starts; SettingsStore.IsConfigured() == false → MainViewModel
//      flips ShowWelcome = true and we land here.
//   2. User types a URL (default placeholder hints "https://1-2-3-4.nip.io").
//   3. User clicks "Test connection" → we hit `<url>/healthz` (or fall
//      back to `/docs` since FastAPI exposes that unconditionally).
//      TestStatus updates to "ok" / "error" with a friendly message.
//   4. "Continue" enables only after a successful test (or the user
//      explicitly chooses "Use anyway" when offline). On click:
//        * SettingsStore.SetServerUrl(...)
//        * fire SetupComplete event → MainViewModel rebuilds ApiClient
//          + flips to LoginView.
//   5. Logout / settings gear can re-trigger this VM later — same code
//      path, just with the field pre-populated from current settings.

using System;
using System.Net.Http;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using RuneDesktop.UI.Helpers;

namespace RuneDesktop.UI.ViewModels;

public partial class WelcomeViewModel : ObservableObject
{
    /// <summary>Connection test result enum, modelled as a string so
    /// XAML can match on it via DataTrigger / IsVisible converter
    /// without us having to ship a custom enum-to-bool converter.</summary>
    public const string StatusIdle    = "idle";
    public const string StatusTesting = "testing";
    public const string StatusOk      = "ok";
    public const string StatusError   = "error";

    /// <summary>The server URL the user is configuring. Two-way bound
    /// from the input box. Auto-trimmed on save; we don't trim on
    /// every keystroke because that fights the user's typing UX.</summary>
    [ObservableProperty] private string _serverUrl = "";

    /// <summary>One of <see cref="StatusIdle"/>/<see cref="StatusTesting"/>/
    /// <see cref="StatusOk"/>/<see cref="StatusError"/>. XAML uses this
    /// to render the status pill (green/red/spinner).</summary>
    [ObservableProperty] private string _testStatus = StatusIdle;

    /// <summary>Friendly status message — drives the line beneath the
    /// input. Examples: "Connected — Nexus server v0.1.0", "Could not
    /// reach 1-2-3-4.nip.io: timeout".</summary>
    [ObservableProperty] private string _testMessage = "";

    /// <summary>True only after a successful connection test, OR the
    /// user has clicked "Use anyway". XAML's Continue button binds to
    /// this so they can't proceed with garbage URLs without consciously
    /// overriding the safety check.</summary>
    [ObservableProperty] private bool _canContinue;

    /// <summary>User opted into trusting a self-signed TLS cert.
    /// Required when the server was set up via
    /// <c>scripts/generate_self_signed_cert.sh</c> rather than getting
    /// a real Let's Encrypt cert. Pre-loaded from saved settings so
    /// it survives app restarts.</summary>
    [ObservableProperty] private bool _acceptSelfSignedCert;

    /// <summary>Fires after the user successfully completes the
    /// wizard (clicks Continue). MainViewModel listens and switches
    /// to LoginView. Carries the new ServerUrl + cert-trust flag.</summary>
    public event EventHandler<WelcomeResult>? SetupComplete;

    /// <summary>Wizard output passed to MainViewModel.</summary>
    public sealed record WelcomeResult(string ServerUrl, bool AcceptSelfSignedCert);

    public WelcomeViewModel()
    {
        // Pre-populate from current settings so users opening the
        // wizard from the gear icon see what's currently configured.
        var existing = SettingsStore.Load();
        if (!string.IsNullOrWhiteSpace(existing.ServerUrl))
        {
            ServerUrl = existing.ServerUrl;
            AcceptSelfSignedCert = existing.AcceptSelfSignedCert;
            // Don't auto-test — we don't want a network call before
            // the user opens the wizard. They can re-test if they
            // want by clicking the button.
        }
    }

    /// <summary>Validate the URL syntactically. Called on every
    /// keystroke via the partial OnServerUrlChanged hook.</summary>
    private static bool IsValidUrl(string url)
    {
        if (string.IsNullOrWhiteSpace(url)) return false;
        if (!Uri.TryCreate(url, UriKind.Absolute, out var u)) return false;
        return u.Scheme == "http" || u.Scheme == "https";
    }

    partial void OnServerUrlChanged(string value)
    {
        // Any edit invalidates a previous successful test result.
        TestStatus = StatusIdle;
        TestMessage = "";
        CanContinue = false;
    }

    [RelayCommand]
    private async Task TestConnectionAsync()
    {
        var url = (ServerUrl ?? "").Trim().TrimEnd('/');
        if (!IsValidUrl(url))
        {
            TestStatus = StatusError;
            TestMessage = "Enter a full URL — e.g. https://1-2-3-4.nip.io or http://localhost:8001";
            CanContinue = false;
            return;
        }

        TestStatus = StatusTesting;
        TestMessage = "Reaching the server…";
        CanContinue = false;

        // Build a one-shot HttpClient with a short timeout. We try
        // `/healthz` first (cheap purpose-built endpoint) and fall back
        // to `/docs` (FastAPI's auto-generated UI; always 200 if the
        // app is up). Both verify TCP + TLS + the FastAPI app is
        // serving — that's all we need to confirm "this URL is alive".
        //
        // If the user opted into self-signed cert trust we attach a
        // permissive cert validator so the test doesn't fail on the
        // exact cert this checkbox exists to accept.
        try
        {
            HttpClient http;
            if (AcceptSelfSignedCert)
            {
                var handler = new HttpClientHandler
                {
                    ServerCertificateCustomValidationCallback =
                        (_, _, _, _) => true,
                };
                http = new HttpClient(handler)
                {
                    Timeout = TimeSpan.FromSeconds(8),
                };
            }
            else
            {
                http = new HttpClient { Timeout = TimeSpan.FromSeconds(8) };
            }
            using var _http = http;
            HttpResponseMessage? resp = null;
            foreach (var path in new[] { "/healthz", "/docs" })
            {
                try
                {
                    resp = await http.GetAsync($"{url}{path}");
                    if (resp.IsSuccessStatusCode) break;
                }
                catch { /* try next path */ }
            }

            if (resp is { IsSuccessStatusCode: true })
            {
                // Connection is healthy — but warn if the URL is plain
                // HTTP to a non-localhost host. WebAuthn passkeys only
                // work over HTTPS or localhost; users hitting an HTTP
                // remote IP would hit the silent button-does-nothing
                // failure. Better to flag it now in the wizard than
                // surprise them at the passkey ceremony.
                var u = new Uri(url);
                var isLocalhost = u.Host == "localhost"
                    || u.Host == "127.0.0.1"
                    || u.Host == "[::1]";
                if (u.Scheme == "http" && !isLocalhost)
                {
                    TestStatus = StatusOk;
                    TestMessage =
                        "Reachable — but this is plain HTTP to a remote " +
                        "host. Passkey login won't work (browsers require " +
                        "HTTPS for WebAuthn). Use the Docker + Caddy + " +
                        "nip.io setup for free Let's Encrypt HTTPS.";
                    CanContinue = true;
                }
                else
                {
                    TestStatus = StatusOk;
                    TestMessage = "Connected — server is reachable.";
                    CanContinue = true;
                }
            }
            else if (resp is not null)
            {
                TestStatus = StatusError;
                TestMessage = $"Server responded with HTTP {(int)resp.StatusCode}. " +
                              "Check the URL or that the server is running.";
                CanContinue = false;
            }
            else
            {
                TestStatus = StatusError;
                TestMessage = "Could not connect — check the URL or that the server is running.";
                CanContinue = false;
            }
        }
        catch (TaskCanceledException)
        {
            TestStatus = StatusError;
            TestMessage = "Connection timed out (8 s). Check the URL or that the server is up.";
            CanContinue = false;
        }
        catch (HttpRequestException e)
        {
            TestStatus = StatusError;
            TestMessage = $"Could not reach the server: {e.Message}";
            CanContinue = false;
        }
        catch (Exception e)
        {
            TestStatus = StatusError;
            TestMessage = $"Unexpected error: {e.Message}";
            CanContinue = false;
        }
    }

    /// <summary>Bypass for the "I know my URL is right but I'm
    /// offline / behind a VPN" case. Lets the user save the config
    /// without a green test result. We log a hint so the user knows
    /// they're skipping the safety check.</summary>
    [RelayCommand]
    private void UseAnyway()
    {
        var url = (ServerUrl ?? "").Trim();
        if (string.IsNullOrEmpty(url) || !IsValidUrl(url))
        {
            TestStatus = StatusError;
            TestMessage = "Enter a valid URL first.";
            return;
        }
        TestStatus = StatusIdle;
        TestMessage = "Saving without verification — you can change this later from the gear icon.";
        CanContinue = true;
    }

    [RelayCommand]
    private void Continue()
    {
        if (!CanContinue) return;
        var url = (ServerUrl ?? "").Trim().TrimEnd('/');
        // Persist BOTH fields. The cert flag stays on disk so users
        // don't have to re-toggle it across app restarts.
        var s = SettingsStore.Load();
        s.ServerUrl = url;
        s.AcceptSelfSignedCert = AcceptSelfSignedCert;
        SettingsStore.Save(s);
        SetupComplete?.Invoke(
            this, new WelcomeResult(url, AcceptSelfSignedCert));
    }
}
