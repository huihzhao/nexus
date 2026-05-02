using System.Text.Json;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using RuneDesktop.Core.Models;
using RuneDesktop.Core.Services;

namespace RuneDesktop.UI.ViewModels;

/// <summary>
/// Top-level view model — owns the shared <see cref="ApiClient"/> and the
/// child VMs for login + chat.
///
/// Round 2-C: the desktop is now a thin client. Pre-refactor this VM
/// also managed a per-user data directory tree
/// (<c>%AppData%/RuneProtocol/users/{user_id}/events.db</c>) and built
/// a fresh <see cref="RuneEngine"/> on every login to scope local
/// SQLite to that user. After the refactor:
///
///   * Server is the single source of truth for chat history, memories,
///     anchors, and identity. The desktop holds nothing on disk besides
///     the JWT in <see cref="SecureTokenStore"/>.
///   * Login → set bearer token → call <see cref="ChatViewModel.ResetForUserAsync"/>
///     to clear in-memory state and pull the new user's history from
///     <c>GET /api/v1/agent/messages</c>.
///   * Logout → clear bearer token → reset chat VM. No SQLite to delete,
///     no per-user dir to GC, no JWT-decode to derive a folder name.
/// </summary>
public partial class MainViewModel : ObservableObject
{
    [ObservableProperty] private bool _isLoggedIn;
    [ObservableProperty] private string _statusText = "Not connected";
    [ObservableProperty] private string _userName = "";
    [ObservableProperty] private string _userId = "";

    /// <summary>First grapheme of the display name, used for the avatar
    /// pill in the top-right corner. Returns "?" when no profile.</summary>
    public string UserInitial => string.IsNullOrEmpty(UserName)
        ? "?" : UserName.Substring(0, 1).ToUpperInvariant();

    /// <summary>Short identity hint for the top bar — the prefix of the
    /// server-side user_id so the user can confirm "yes, I'm signed in
    /// as the right account" at a glance. Falls back to ``""`` when
    /// the profile hasn't loaded yet.</summary>
    public string UserShortId => string.IsNullOrEmpty(UserId)
        ? "" : (UserId.Length > 8 ? UserId[..8] : UserId);

    partial void OnUserNameChanged(string value)
        => OnPropertyChanged(nameof(UserInitial));

    partial void OnUserIdChanged(string value)
        => OnPropertyChanged(nameof(UserShortId));

    public LoginViewModel LoginVm { get; }
    public ChatViewModel ChatVm { get; }
    /// <summary>Left-rail multi-session list. Owns CurrentSessionId
    /// state and notifies <see cref="ChatVm"/> when the user picks a
    /// different thread.</summary>
    public SessionListViewModel SessionsVm { get; }
    public ApiClient Api { get; }

    public MainViewModel()
    {
        var serverUrl = LoadServerUrl();
        Api = new ApiClient(serverUrl);

        LoginVm = new LoginViewModel(Api);
        ChatVm = new ChatViewModel(Api);
        SessionsVm = new SessionListViewModel(Api);

        LoginVm.LoginSuccess += OnLoginSuccess;

        // When the rail picks a session (or creates a new one), tell
        // the chat surface to refresh history filtered by that id.
        // Best-effort — a slow load doesn't block the UI thread.
        SessionsVm.SessionSelected += (_, sessionId) =>
        {
            _ = ChatVm.SwitchSessionAsync(sessionId);
        };
    }

    private async void OnLoginSuccess(object? sender, LoginViewModel.LoginSuccessArgs e)
    {
        Api.SetBearerToken(e.Token);
        UserName = e.Profile.Name;
        UserId = e.Profile.AgentId;
        StatusText = "Connected";
        IsLoggedIn = true;

        // Reset in-memory state and pull this user's history from the
        // server. No per-user data directory needed — server scopes
        // everything by JWT user_id, and the chat VM holds nothing
        // durable across users.
        await ChatVm.ResetForUserAsync();

        // Pick the user's initial session (most recent, or default,
        // or a fresh one if they're brand-new). This fires
        // SessionSelected → ChatVm.SwitchSessionAsync via the wiring
        // we set up in the ctor, so the chat surface lands populated.
        try { await SessionsVm.SelectInitialAsync(); }
        catch (Exception ex)
        {
            System.Diagnostics.Debug.WriteLine(
                $"SessionsVm.SelectInitialAsync: {ex}");
        }

        // Best-effort chain registration in the background.
        _ = EnsureChainRegistrationAsync(e.Profile.Name);
    }

    private async Task EnsureChainRegistrationAsync(string agentName)
    {
        // StatusText is the top-bar's "transitional state" line — only
        // surface things that are NOT already shown by the ERC-8004 pill
        // or the user pill. So:
        //   * happy path (registered) → empty (the pill says it all)
        //   * mid-bootstrap            → "Registering on chain…"
        //   * chain disabled / failed  → keep the warning visible
        //
        // Old text "Connected · ERC-8004 #953" duplicated info already
        // shown by the green pill on the left and the user pill on the
        // right ("Connected" was redundant once the user is even seeing
        // the chat surface).
        try
        {
            StatusText = "Checking on-chain status…";
            var info = await Api.GetMyChainAgentInfoAsync();

            if (info is not null && info.IsOnChain)
            {
                StatusText = "";   // pill shows the token id
                await ChatVm.RefreshChainStatusAsync();
                return;
            }

            StatusText = "Registering on chain…";
            var result = await Api.RegisterAgentOnChainAsync(agentName);
            switch (result.Status)
            {
                case "registered":
                    StatusText = "";  // pill takes over now
                    break;
                case "pending":
                    StatusText = "chain disabled — local-only mode";
                    break;
                case "failed":
                    StatusText = "chain register failed: "
                                 + (result.ErrorMessage ?? "(no detail)");
                    break;
                default:
                    StatusText = result.Status;
                    break;
            }
            await ChatVm.RefreshChainStatusAsync();
        }
        catch (Exception ex)
        {
            StatusText = $"chain check error: {ex.Message}";
            System.Diagnostics.Debug.WriteLine(
                $"EnsureChainRegistrationAsync: {ex}");
        }
    }

    [RelayCommand]
    private void Logout()
    {
        ChatVm.StopChainStatusPolling();
        Api.ClearBearerToken();
        IsLoggedIn = false;
        UserName = "";
        UserId = "";
        StatusText = "Not connected";

        // Clear in-memory chat state so flicker of the prior user's
        // messages doesn't leak onto the login screen. No engine swap
        // needed — there's nothing local to retain.
        _ = ChatVm.ResetForUserAsync();

        // Clear the session rail so user A's threads don't briefly
        // flash to user B on next login.
        SessionsVm.Sessions.Clear();
        SessionsVm.CurrentSessionId = "";
    }

    private static string LoadServerUrl()
    {
        try
        {
            var configPath = Path.Combine(AppContext.BaseDirectory, "appsettings.json");
            if (File.Exists(configPath))
            {
                var json = File.ReadAllText(configPath);
                var doc = JsonDocument.Parse(json);
                if (doc.RootElement.TryGetProperty("ServerUrl", out var urlProp))
                    return urlProp.GetString() ?? "http://localhost:8001";
            }
        }
        catch { }
        return "http://localhost:8001";
    }
}
