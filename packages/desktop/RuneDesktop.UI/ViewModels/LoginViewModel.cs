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
            // Launch passkey authentication via browser
            string? token = await PasskeyAuthService.AuthenticateAsync(_api.ServerUrl);

            if (token == null)
            {
                ErrorMessage = "Authentication was cancelled";
                return;
            }

            // Set token and create profile from JWT claims
            _api.SetBearerToken(token);
            // The passkey-page login flow doesn't surface a name input
            // anymore, so DisplayName is typically empty here. `??` ONLY
            // catches null — empty string slips through. Treat both
            // null and whitespace as "no name provided" and fall back
            // to a sensible default.
            var nameForProfile = string.IsNullOrWhiteSpace(DisplayName)
                ? "Passkey User"
                : DisplayName!;
            // Round 2-C: we used to decode the JWT here to derive
            // ``userId`` and scope the desktop's LocalEventLog to a
            // per-user SQLite path. After the thin-client refactor
            // the desktop holds no on-disk per-user state — server
            // verifies the JWT properly and scopes everything itself.
            // ``AgentId`` is left empty and is no longer consumed by
            // any view model.
            var profile = new AgentProfile
            {
                AgentId = "",
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
}
