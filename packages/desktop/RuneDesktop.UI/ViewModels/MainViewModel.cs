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

    public LoginViewModel LoginVm { get; }
    public ChatViewModel ChatVm { get; }
    public ApiClient Api { get; }

    public MainViewModel()
    {
        var serverUrl = LoadServerUrl();
        Api = new ApiClient(serverUrl);

        LoginVm = new LoginViewModel(Api);
        ChatVm = new ChatViewModel(Api);

        LoginVm.LoginSuccess += OnLoginSuccess;
    }

    private void OnLoginSuccess(object? sender, LoginViewModel.LoginSuccessArgs e)
    {
        Api.SetBearerToken(e.Token);
        UserName = e.Profile.Name;
        StatusText = "Connected";
        IsLoggedIn = true;

        // Reset in-memory state and pull this user's history from the
        // server. No per-user data directory needed — server scopes
        // everything by JWT user_id, and the chat VM holds nothing
        // durable across users.
        _ = ChatVm.ResetForUserAsync();

        // Best-effort chain registration in the background.
        _ = EnsureChainRegistrationAsync(e.Profile.Name);
    }

    private async Task EnsureChainRegistrationAsync(string agentName)
    {
        // Surface every state transition to StatusText so it's visible in
        // the top bar — chain failures used to hide silently in Debug
        // output. Every branch ends with a *visible* status.
        try
        {
            StatusText = "Checking on-chain status…";
            var info = await Api.GetMyChainAgentInfoAsync();

            // Already-registered short-circuit. If the call failed (info==null)
            // we *still* try to register — better than wedging on a transient
            // /chain/me blip when the server is otherwise reachable. The
            // server-side register endpoint itself is idempotent against the
            // user's chain_agent_id row.
            //
            // S6: the legacy /chain/register-agent endpoint just delegates
            // to twin_manager.bootstrap_chain_identity now, but we still
            // call it during onboarding so the user sees on-chain status
            // populated before their first chat (otherwise twin's
            // background bootstrap fires on first /llm/chat and the UI
            // shows "—" for a few seconds).
            if (info is not null && info.IsOnChain)
            {
                StatusText = $"Connected · ERC-8004 #{info.AgentId}";
                await ChatVm.RefreshChainStatusAsync();
                return;
            }

            StatusText = "Registering on chain…";
            var result = await Api.RegisterAgentOnChainAsync(agentName);
            switch (result.Status)
            {
                case "registered":
                    StatusText = $"Connected · ERC-8004 #{result.AgentId}";
                    break;
                case "pending":
                    StatusText = "Connected · chain disabled (server has no key)";
                    break;
                case "failed":
                    StatusText = $"Connected · chain register failed: " +
                                 (result.ErrorMessage ?? "(no detail)");
                    break;
                default:
                    StatusText = $"Connected · {result.Status}";
                    break;
            }
            await ChatVm.RefreshChainStatusAsync();
        }
        catch (Exception ex)
        {
            StatusText = $"Connected · chain check error: {ex.Message}";
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
        StatusText = "Not connected";

        // Clear in-memory chat state so flicker of the prior user's
        // messages doesn't leak onto the login screen. No engine swap
        // needed — there's nothing local to retain.
        _ = ChatVm.ResetForUserAsync();
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
