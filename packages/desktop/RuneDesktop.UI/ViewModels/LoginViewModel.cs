using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json.Serialization;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using RuneDesktop.Core.Models;
using RuneDesktop.Core.Services;
using RuneDesktop.UI.Services;

namespace RuneDesktop.UI.ViewModels;

// Match server's actual response format
public record ServerRegisterResponse
{
    [JsonPropertyName("user_id")] public string UserId { get; init; } = "";
    [JsonPropertyName("jwt_token")] public string JwtToken { get; init; } = "";
    [JsonPropertyName("created_at")] public string CreatedAt { get; init; } = "";
}

public record ServerLoginResponse
{
    [JsonPropertyName("jwt_token")] public string JwtToken { get; init; } = "";
    [JsonPropertyName("expires_in_seconds")] public int ExpiresInSeconds { get; init; }
}

public partial class LoginViewModel : ObservableObject
{
    private readonly ApiClient _api;

    [ObservableProperty] private string _displayName = "";
    [ObservableProperty] private bool _isLoading;
    [ObservableProperty] private string _errorMessage = "";

    public record LoginSuccessArgs(string Token, AgentProfile Profile);
    public event EventHandler<LoginSuccessArgs>? LoginSuccess;

    public LoginViewModel(ApiClient api)
    {
        _api = api;
    }

    [RelayCommand]
    private async Task RegisterAsync()
    {
        if (string.IsNullOrWhiteSpace(DisplayName))
        {
            ErrorMessage = "Please enter your name";
            return;
        }

        IsLoading = true;
        ErrorMessage = "";

        try
        {
            // Call server's actual /api/v1/auth/register endpoint
            using var http = new HttpClient { BaseAddress = new Uri(_api.ServerUrl) };
            var resp = await http.PostAsJsonAsync("/api/v1/auth/register", new { display_name = DisplayName });

            if (!resp.IsSuccessStatusCode)
            {
                var err = await resp.Content.ReadAsStringAsync();
                ErrorMessage = $"Registration failed: {err}";
                return;
            }

            var result = await resp.Content.ReadFromJsonAsync<ServerRegisterResponse>();
            if (result == null)
            {
                ErrorMessage = "Empty response from server";
                return;
            }

            var profile = new AgentProfile
            {
                AgentId = result.UserId,
                Name = DisplayName,
                Erc8004TokenId = "pending",
                Network = "local",
                WalletAddress = "N/A",
            };

            LoginSuccess?.Invoke(this, new LoginSuccessArgs(result.JwtToken, profile));
        }
        catch (HttpRequestException)
        {
            ErrorMessage = "Cannot connect to server. Is it running?";
        }
        catch (Exception ex)
        {
            ErrorMessage = ex.Message;
        }
        finally
        {
            IsLoading = false;
        }
    }

    [RelayCommand]
    private async Task LoginAsync()
    {
        if (string.IsNullOrWhiteSpace(DisplayName))
        {
            ErrorMessage = "Please enter your name";
            return;
        }

        IsLoading = true;
        ErrorMessage = "";

        try
        {
            // For MVP: register = login (server creates user if not exists)
            await RegisterAsync();
        }
        finally
        {
            IsLoading = false;
        }
    }

    [RelayCommand]
    private async Task LoginWithPasskeyAsync()
    {
        IsLoading = true;
        ErrorMessage = "";

        try
        {
            // Pre-flight: ping the server before opening a browser tab.
            // Without this, a stopped / unreachable server dumps the
            // user at the browser's ERR_CONNECTION_REFUSED page with no
            // clue what to do. Better to fail in-app with a hint to
            // (a) start the server, or (b) click ⚙ to change the URL.
            //
            // We try /healthz first (purpose-built) then /docs (FastAPI
            // auto-renders this whenever the app is up). 5 s timeout —
            // if the server is up it'll respond instantly; if it's down
            // we want to fail fast, not stall the UI.
            var alive = await IsServerReachableAsync(_api.ServerUrl);
            if (!alive)
            {
                ErrorMessage =
                    $"Cannot reach {_api.ServerUrl}. Start the server, or " +
                    "click the ⚙ icon to change the URL.";
                return;
            }

            // Launch passkey authentication via browser
            string? token = await PasskeyAuthService.AuthenticateAsync(_api.ServerUrl);

            if (token == null)
            {
                ErrorMessage = "Authentication was cancelled";
                return;
            }

            // Set token, then fetch the real profile from the server.
            // The JWT alone doesn't carry display_name; we have to GET
            // /api/v1/user/profile to populate the top-bar pill with
            // the user's actual handle.
            _api.SetBearerToken(token);

            var serverProfile = await _api.GetUserProfileAsync();
            string nameForProfile;
            string userId;
            if (serverProfile is not null
                && !string.IsNullOrWhiteSpace(serverProfile.DisplayName))
            {
                nameForProfile = serverProfile.DisplayName;
                userId = serverProfile.UserId;
            }
            else if (!string.IsNullOrWhiteSpace(DisplayName))
            {
                // The user typed a name into the optional input — use it
                // and accept the empty user_id (the chip will hide the
                // short-id line gracefully).
                nameForProfile = DisplayName!;
                userId = "";
            }
            else
            {
                // Last-resort fallback. The Cognition panel's workdir
                // call still fetches the real user_id from /agent/state
                // so this only affects the top-bar display.
                nameForProfile = "Nexus User";
                userId = "";
            }

            var profile = new AgentProfile
            {
                AgentId = userId,
                Name = nameForProfile,
                Erc8004TokenId = "pending",
                Network = "local",
                WalletAddress = "N/A",
            };
            LoginSuccess?.Invoke(this, new LoginSuccessArgs(token, profile));
        }
        catch (OperationCanceledException)
        {
            ErrorMessage = "Authentication timed out (5 minutes). Please try again.";
        }
        catch (InvalidOperationException ex)
        {
            ErrorMessage = $"Error: {ex.Message}";
        }
        catch (Exception ex)
        {
            ErrorMessage = ex.Message;
        }
        finally
        {
            IsLoading = false;
        }
    }

    /// <summary>
    /// Cancel an in-flight passkey browser flow. The pending
    /// <see cref="LoginWithPasskeyAsync"/> resolves with a null token,
    /// which we surface as "Authentication was cancelled".
    /// </summary>
    [RelayCommand]
    private void CancelPasskey()
    {
        PasskeyAuthService.Cancel();
    }

    /// <summary>Fires when the user clicks the ⚙ gear icon on the
    /// Login screen. MainViewModel listens and flips ShowWelcome=true
    /// to re-open the wizard. We route through an event (not via
    /// XAML ancestor binding to MainViewModel.OpenWelcomeCommand)
    /// because the binding {$parent[Window].DataContext.X} doesn't
    /// always resolve cleanly when LoginView is rendered inside a
    /// Panel — easier to plumb explicitly.</summary>
    public event EventHandler? OpenSettingsRequested;

    [RelayCommand]
    private void OpenSettings()
    {
        OpenSettingsRequested?.Invoke(this, EventArgs.Empty);
    }

    /// <summary>Pre-flight server reachability check.
    ///
    /// We hit /healthz first (purpose-built endpoint) and fall back to
    /// /docs (FastAPI auto-renders this whenever the app is up). Both
    /// confirm "TCP + TLS + FastAPI is alive". 5 s timeout — if up,
    /// we get an instant response; if down, fail fast.
    ///
    /// Used by LoginWithPasskeyAsync as a guard before opening the
    /// browser-based passkey ceremony, so an unreachable server
    /// surfaces as an inline error in the desktop instead of a blank
    /// "ERR_CONNECTION_REFUSED" tab.</summary>
    private static async Task<bool> IsServerReachableAsync(string serverUrl)
    {
        var url = (serverUrl ?? "").TrimEnd('/');
        if (string.IsNullOrEmpty(url)) return false;
        try
        {
            using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };
            foreach (var path in new[] { "/healthz", "/docs" })
            {
                try
                {
                    var resp = await http.GetAsync($"{url}{path}");
                    if (resp.IsSuccessStatusCode) return true;
                }
                catch { /* try next path */ }
            }
        }
        catch { /* outer catch — anything unexpected = unreachable */ }
        return false;
    }
}
