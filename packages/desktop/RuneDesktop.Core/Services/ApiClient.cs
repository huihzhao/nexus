using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using RuneDesktop.Core.Models;

namespace RuneDesktop.Core.Services;

/// <summary>
/// Request model for authentication with passkey credentials.
/// </summary>
public record AuthRequest
{
    /// <summary>
    /// Passkey credential (typically a base64-encoded signed challenge).
    /// </summary>
    [JsonPropertyName("credential")]
    public required string Credential { get; init; }
}

/// <summary>
/// Response model from authentication endpoint.
/// </summary>
public record AuthResult
{
    /// <summary>
    /// JWT bearer token for subsequent API requests.
    /// </summary>
    [JsonPropertyName("token")]
    public required string Token { get; init; }

    /// <summary>
    /// Agent profile information after successful authentication.
    /// </summary>
    [JsonPropertyName("profile")]
    public required AgentProfile AgentProfile { get; init; }
}

/// <summary>
/// A file attached to a chat turn.
///
/// Round 2-B (thin client): the modern path is for the desktop to upload
/// each file separately via <c>POST /api/v1/files/upload</c>, get back a
/// <see cref="FileId"/>, and reference it here. The server resolves the
/// id, reads bytes from disk, and runs distill — without forcing a
/// 100 MB base64-in-JSON payload through the chat endpoint.
///
/// The legacy fields <see cref="ContentText"/> / <see cref="ContentBase64"/>
/// remain on the wire for back-compat (server still accepts them) but
/// new desktop builds always go through <see cref="FileId"/>.
/// </summary>
public record ChatAttachment
{
    [JsonPropertyName("name")]
    public required string Name { get; init; }

    [JsonPropertyName("mime")]
    public string Mime { get; init; } = "application/octet-stream";

    [JsonPropertyName("size_bytes")]
    public required long SizeBytes { get; init; }

    /// <summary>
    /// Server-assigned id from <c>/api/v1/files/upload</c>. Preferred
    /// over inline <see cref="ContentText"/> / <see cref="ContentBase64"/>
    /// — those still work but lift the entire file into the chat
    /// request body.
    /// </summary>
    [JsonPropertyName("file_id")]
    public string? FileId { get; init; }

    [JsonPropertyName("content_text")]
    public string? ContentText { get; init; }

    [JsonPropertyName("content_base64")]
    public string? ContentBase64 { get; init; }
}

/// <summary>
/// Response model from <c>POST /api/v1/files/upload</c>. The desktop
/// keeps the <see cref="FileId"/> alongside its in-memory pending
/// attachment chip and references it from the next <see cref="ChatRequest"/>
/// so the server doesn't have to receive bytes twice.
/// </summary>
public record FileUploadResponse
{
    [JsonPropertyName("file_id")]
    public required string FileId { get; init; }

    [JsonPropertyName("name")]
    public string Name { get; init; } = "";

    [JsonPropertyName("mime")]
    public string Mime { get; init; } = "application/octet-stream";

    [JsonPropertyName("size_bytes")]
    public long SizeBytes { get; init; }
}

/// <summary>
/// One chat history row from <c>GET /api/v1/agent/messages</c>. The
/// desktop binds these directly into its message list on every login —
/// no local SQLite event log needed.
/// </summary>
public record ChatMessageView
{
    [JsonPropertyName("role")]
    public required string Role { get; init; }   // "user" | "assistant"

    [JsonPropertyName("content")]
    public required string Content { get; init; }

    [JsonPropertyName("timestamp")]
    public string Timestamp { get; init; } = "";

    [JsonPropertyName("sync_id")]
    public long SyncId { get; init; }
}

/// <summary>
/// Request model for chat endpoint.
/// </summary>
public record ChatRequest
{
    /// <summary>
    /// Conversation messages to send to the LLM.
    /// </summary>
    [JsonPropertyName("messages")]
    public required List<ChatMessage> Messages { get; init; }

    /// <summary>
    /// System prompt that guides the agent behavior.
    ///
    /// Round 2-C: the desktop no longer builds a system prompt — the
    /// server-side twin owns persona / capabilities / identity context
    /// construction (twin.chat builds its own from CuratedMemory +
    /// ContractEngine + skills). The field is kept nullable so any
    /// non-thin-client caller (e.g. raw API consumers, tests) can still
    /// pass one through.
    /// </summary>
    [JsonPropertyName("system_prompt")]
    public string? SystemPrompt { get; init; }

    /// <summary>
    /// Optional list of tool definitions the assistant can invoke.
    /// </summary>
    [JsonPropertyName("tool_definitions")]
    public List<ToolDefinition> ToolDefinitions { get; init; } = [];

    /// <summary>
    /// Optional file attachments to include with this turn. Folded into
    /// the last user message server-side.
    /// </summary>
    [JsonPropertyName("attachments")]
    public List<ChatAttachment> Attachments { get; init; } = [];
}

/// <summary>
/// Definition of a tool/function the assistant can call.
/// </summary>
public record ToolDefinition
{
    /// <summary>
    /// Name of the tool.
    /// </summary>
    [JsonPropertyName("name")]
    public required string Name { get; init; }

    /// <summary>
    /// Description of what the tool does.
    /// </summary>
    [JsonPropertyName("description")]
    public required string Description { get; init; }

    /// <summary>
    /// JSON schema for the tool's input parameters.
    /// </summary>
    [JsonPropertyName("parameters")]
    public required object Parameters { get; init; }
}

/// <summary>
/// One distilled summary returned alongside a chat response, one per
/// attachment. The desktop persists these as <c>attachment_distilled</c>
/// events so future turns naturally include the summary in context.
/// </summary>
public record AttachmentSummary
{
    [JsonPropertyName("name")]
    public required string Name { get; init; }

    [JsonPropertyName("mime")]
    public string Mime { get; init; } = "application/octet-stream";

    [JsonPropertyName("size_bytes")]
    public long SizeBytes { get; init; }

    [JsonPropertyName("summary")]
    public required string Summary { get; init; }

    [JsonPropertyName("source")]
    public string Source { get; init; } = "";

    /// <summary>Server-assigned sync_id for the attachment_distilled row.</summary>
    [JsonPropertyName("sync_id")]
    public long? SyncId { get; init; }
}

/// <summary>
/// Response model from chat endpoint.
/// </summary>
public record ChatResponse
{
    /// <summary>
    /// The assistant's reply message.
    /// </summary>
    [JsonPropertyName("reply")]
    public required string Reply { get; init; }

    /// <summary>
    /// Tool calls made by the assistant during this response (if any).
    /// </summary>
    [JsonPropertyName("tool_calls")]
    public List<ToolCall> ToolCalls { get; init; } = [];

    /// <summary>
    /// Token usage statistics for this request.
    /// </summary>
    [JsonPropertyName("usage")]
    public TokenUsage? Usage { get; init; }

    /// <summary>
    /// LLM-distilled summaries of any attachments sent with this turn —
    /// one per attachment. Empty when no files were attached.
    /// </summary>
    public List<AttachmentSummary> AttachmentSummaries { get; init; } = [];
}

// [REMOVED — Round 2-A] PushEventsRequest / SyncResponse records were
// the wire shape for /sync/push. Both endpoints retired client-side;
// chat history now flows from GET /api/v1/agent/messages via
// ChatMessageView and MessagesListResponse.

/// <summary>
/// HTTP client for communication with the Rune Protocol server.
/// Handles authentication, chat requests, event sync, and profile operations.
/// Includes retry logic and automatic Bearer token injection.
/// </summary>
public class ApiClient
{
    private readonly HttpClient _httpClient;
    private readonly string _serverUrl;
    public string ServerUrl => _serverUrl;
    private string? _bearerToken;

    private const int MaxRetries = 3;
    private const int TimeoutSeconds = 30;
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = false,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
    };

    /// <summary>
    /// Initializes a new API client for a given server URL.
    /// </summary>
    /// <param name="serverUrl">Base URL of the Rune Protocol server (e.g., "https://api.runeprotocol.io").</param>
    public ApiClient(string serverUrl)
    {
        _serverUrl = serverUrl.TrimEnd('/');
        _httpClient = new HttpClient
        {
            Timeout = TimeSpan.FromSeconds(TimeoutSeconds)
        };
    }

    /// <summary>
    /// Sets the bearer token for authenticated requests.
    /// </summary>
    /// <param name="token">JWT bearer token.</param>
    public void SetBearerToken(string token)
    {
        _bearerToken = token;
        _httpClient.DefaultRequestHeaders.Authorization =
            new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", token);
    }

    /// <summary>
    /// Clears the bearer token and removes authorization header.
    /// </summary>
    public void ClearBearerToken()
    {
        _bearerToken = null;
        _httpClient.DefaultRequestHeaders.Authorization = null;
    }

    /// <summary>
    /// Authenticates with the server using a passkey credential.
    /// </summary>
    /// <param name="credential">Passkey credential (base64-encoded signed challenge).</param>
    /// <returns>Authentication result containing JWT token and agent profile.</returns>
    /// <exception cref="HttpRequestException">Thrown if the request fails after retries.</exception>
    public async Task<AuthResult> LoginWithPasskeyAsync(string credential)
    {
        var request = new AuthRequest { Credential = credential };
        var url = $"{_serverUrl}/api/v1/auth/login";

        return await PostWithRetryAsync<AuthResult>(url, request);
    }

    /// <summary>
    /// Sends a chat message to the LLM endpoint.
    /// </summary>
    /// <param name="chatRequest">Chat request containing messages and system prompt.</param>
    /// <returns>Chat response with assistant reply and optional tool calls.</returns>
    /// <exception cref="HttpRequestException">Thrown if the request fails after retries.</exception>
    /// <exception cref="InvalidOperationException">Thrown if bearer token is not set.</exception>
    public async Task<ChatResponse> SendChatAsync(ChatRequest chatRequest)
    {
        EnsureAuthenticated();

        // Convert to server's expected format: messages as [{role: "user", content: "..."}]
        var serverPayload = new
        {
            messages = chatRequest.Messages.Select(m => new
            {
                role = m.Role switch
                {
                    ChatMessageRole.User => "user",
                    ChatMessageRole.Assistant => "assistant",
                    ChatMessageRole.System => "system",
                    _ => "user"
                },
                content = m.Content
            }).ToList(),
            system_prompt = chatRequest.SystemPrompt,
            enable_tools = true,
            attachments = chatRequest.Attachments.Select(a => new
            {
                name = a.Name,
                mime = a.Mime,
                size_bytes = a.SizeBytes,
                content_text = a.ContentText,
                content_base64 = a.ContentBase64,
            }).ToList(),
        };

        var url = $"{_serverUrl}/api/v1/llm/chat";
        var serverResp = await PostWithRetryAsync<ServerChatResponse>(url, serverPayload);

        return new ChatResponse
        {
            Reply = serverResp.Content ?? "",
            ToolCalls = [],
            Usage = null,
            AttachmentSummaries = serverResp.AttachmentSummaries ?? [],
        };
    }

    // Matches server's actual LLMChatResponse
    private record ServerChatResponse
    {
        [JsonPropertyName("role")] public string Role { get; init; } = "";
        [JsonPropertyName("content")] public string Content { get; init; } = "";
        [JsonPropertyName("model")] public string Model { get; init; } = "";
        [JsonPropertyName("stop_reason")] public string? StopReason { get; init; }
        [JsonPropertyName("tool_calls_executed")] public List<string> ToolCallsExecuted { get; init; } = [];
        [JsonPropertyName("attachment_summaries")] public List<AttachmentSummary> AttachmentSummaries { get; init; } = [];
    }

    // [REMOVED — Round 2-A] PushEventsAsync / PullEventsAsync used to
    // ship LocalEventLog rows up to the server's /sync/push and pull
    // unsynced rows back via /sync/pull. After the thin-client
    // refactor the desktop has no LocalEventLog to sync, and the chat
    // history pull goes through GET /api/v1/agent/messages instead
    // (see GetMessagesAsync below). The /sync/* endpoints still exist
    // server-side but only as a transitional surface — they'll be
    // retired alongside sync_anchor.py in Round 2-C / S6 cleanup.

    /// <summary>
    /// Retrieves the current user's agent profile.
    /// </summary>
    /// <returns>Agent profile information.</returns>
    /// <exception cref="HttpRequestException">Thrown if the request fails after retries.</exception>
    /// <exception cref="InvalidOperationException">Thrown if bearer token is not set.</exception>
    public async Task<AgentProfile> GetProfileAsync()
    {
        EnsureAuthenticated();

        var url = $"{_serverUrl}/api/v1/user/profile";
        var profile = await GetWithRetryAsync<AgentProfile>(url);

        if (profile == null)
            throw new InvalidOperationException("Server returned empty profile.");

        return profile;
    }

    // ── Chain / Anchor APIs ───────────────────────────────────────────

    /// <summary>
    /// Asks the server to register an ERC-8004 agent on chain on behalf
    /// of the authenticated user. Returns even when the server is in
    /// "no chain configured" mode (status="pending") or when the call
    /// itself failed (status="failed") so the UI can show the right state.
    /// </summary>
    public async Task<ChainAgentResult> RegisterAgentOnChainAsync(string agentName)
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/chain/register-agent";
        try
        {
            var resp = await PostWithRetryAsync<ChainAgentResult>(
                url, new { agent_name = agentName });
            return resp ?? new ChainAgentResult { AgentId = "", Status = "failed" };
        }
        catch (Exception ex)
        {
            return new ChainAgentResult
            {
                AgentId = "",
                Status = "failed",
                ErrorMessage = ex.Message,
            };
        }
    }

    /// <summary>Fetch the current user's on-chain agent info (token id + tx hash).</summary>
    public async Task<ChainAgentInfo?> GetMyChainAgentInfoAsync()
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/chain/me";
        try
        {
            return await GetWithRetryAsync<ChainAgentInfo>(url);
        }
        catch
        {
            // UI calls this on a polling timer — never let it surface
            // as an unhandled exception that breaks the dispatcher.
            return null;
        }
    }

    /// <summary>List the user's recent sync anchors (newest first).</summary>
    public async Task<List<SyncAnchorEntry>> GetSyncAnchorsAsync(int limit = 20)
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/sync/anchors?limit={limit}";
        try
        {
            var resp = await GetWithRetryAsync<SyncAnchorListResponse>(url);
            return resp?.Anchors ?? [];
        }
        catch
        {
            return [];
        }
    }

    private record SyncAnchorListResponse
    {
        [JsonPropertyName("anchors")]
        public List<SyncAnchorEntry> Anchors { get; init; } = [];
    }

    // ── Agent state / timeline / memories ─────────────────────────────

    /// <summary>One-shot sidebar snapshot: chain id, counts, last anchor.</summary>
    public async Task<AgentStateSnapshot?> GetAgentStateAsync()
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/agent/state";
        try { return await GetWithRetryAsync<AgentStateSnapshot>(url); }
        catch { return null; }
    }

    /// <summary>Newest-first activity stream (sync_events ∪ sync_anchors).</summary>
    public async Task<List<ActivityItem>> GetTimelineAsync(int limit = 60)
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/agent/timeline?limit={limit}";
        try
        {
            var resp = await GetWithRetryAsync<TimelineResponse>(url);
            return resp?.Items ?? [];
        }
        catch { return []; }
    }

    /// <summary>Memory snapshots (memory_compact events) newest first.</summary>
    public async Task<List<MemoryEntry>> GetMemoriesAsync(int limit = 50)
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/agent/memories?limit={limit}";
        try
        {
            var resp = await GetWithRetryAsync<MemoriesListResponse>(url);
            return resp?.Memories ?? [];
        }
        catch { return []; }
    }

    /// <summary>
    /// Round 2-A: server-authoritative chat history. Replaces the
    /// desktop's old LocalEventLog — every login pulls history from here
    /// and renders messages from this stream alone.
    ///
    /// Returns oldest-first within the requested window.
    /// <paramref name="beforeSyncId"/> is the pagination cursor for
    /// loading older history (server's EventLog ``idx``).
    /// </summary>
    public async Task<List<ChatMessageView>> GetMessagesAsync(
        int limit = 200, long? beforeSyncId = null)
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/agent/messages?limit={limit}";
        if (beforeSyncId is { } cursor)
            url += $"&before_sync_id={cursor}";
        try
        {
            var resp = await GetWithRetryAsync<MessagesListResponse>(url);
            return resp?.Messages ?? [];
        }
        catch { return []; }
    }

    /// <summary>
    /// Round 2-B: upload one file via multipart/form-data. The server
    /// stores it under the user's data dir and returns a
    /// <see cref="FileUploadResponse.FileId"/> the desktop then
    /// references in <see cref="ChatRequest.Attachments"/>.
    ///
    /// Streams the bytes — no base64 encode, no JSON wrap — so a 100 MB
    /// upload doesn't multiply by 1.33 over the wire.
    /// </summary>
    public async Task<FileUploadResponse> UploadFileAsync(
        Stream content, string filename, string mime)
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/files/upload";

        using var form = new MultipartFormDataContent();
        var fileContent = new StreamContent(content);
        fileContent.Headers.ContentType =
            new System.Net.Http.Headers.MediaTypeHeaderValue(mime);
        form.Add(fileContent, "file", filename);

        // Bypass PostWithRetryAsync — that helper PostsAsJson; multipart
        // needs raw HttpClient. We still want one retry on 5xx but keep
        // it simple here: retry once on transient.
        for (int attempt = 0; attempt < 2; attempt++)
        {
            HttpResponseMessage resp;
            try
            {
                resp = await _httpClient.PostAsync(url, form);
            }
            catch (HttpRequestException) when (attempt == 0)
            {
                await Task.Delay(TimeSpan.FromSeconds(1));
                continue;
            }
            if (resp.IsSuccessStatusCode)
            {
                var body = await resp.Content
                    .ReadFromJsonAsync<FileUploadResponse>(JsonOptions);
                return body ?? throw new InvalidOperationException(
                    "Empty response from /files/upload");
            }
            if ((int)resp.StatusCode >= 500 && attempt == 0)
            {
                await Task.Delay(TimeSpan.FromSeconds(1));
                continue;
            }
            resp.EnsureSuccessStatusCode();
        }
        throw new HttpRequestException(
            $"Upload of {filename} to {url} failed after retries.");
    }

    private record TimelineResponse
    {
        [JsonPropertyName("items")]
        public List<ActivityItem> Items { get; init; } = [];
    }

    private record MemoriesListResponse
    {
        [JsonPropertyName("memories")]
        public List<MemoryEntry> Memories { get; init; } = [];

        [JsonPropertyName("total")]
        public int Total { get; init; }
    }

    private record MessagesListResponse
    {
        [JsonPropertyName("messages")]
        public List<ChatMessageView> Messages { get; init; } = [];

        [JsonPropertyName("total")]
        public int Total { get; init; }
    }

    /// <summary>
    /// Checks connectivity to the server by making a health check request.
    /// </summary>
    /// <returns>True if server is reachable, false otherwise.</returns>
    public async Task<bool> HealthCheckAsync()
    {
        try
        {
            var url = $"{_serverUrl}/api/v1/health";
            var response = await _httpClient.GetAsync(url);
            return response.IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }

    /// <summary>
    /// Performs a POST request with automatic retry logic.
    /// </summary>
    private async Task<T> PostWithRetryAsync<T>(string url, object? requestBody = null)
    {
        for (int attempt = 0; attempt < MaxRetries; attempt++)
        {
            try
            {
                var response = await _httpClient.PostAsJsonAsync(url, requestBody, JsonOptions);

                if (response.IsSuccessStatusCode)
                {
                    var result = await response.Content.ReadFromJsonAsync<T>(JsonOptions);
                    return result ?? throw new InvalidOperationException($"Empty response from {url}");
                }

                if ((int)response.StatusCode >= 500 && attempt < MaxRetries - 1)
                {
                    await Task.Delay(TimeSpan.FromSeconds(Math.Pow(2, attempt)));
                    continue;
                }

                response.EnsureSuccessStatusCode();
            }
            catch (HttpRequestException) when (attempt < MaxRetries - 1)
            {
                await Task.Delay(TimeSpan.FromSeconds(Math.Pow(2, attempt)));
                continue;
            }
        }

        throw new HttpRequestException($"Request to {url} failed after {MaxRetries} attempts.");
    }

    /// <summary>
    /// Performs a GET request with automatic retry logic.
    /// </summary>
    private async Task<T?> GetWithRetryAsync<T>(string url)
    {
        for (int attempt = 0; attempt < MaxRetries; attempt++)
        {
            try
            {
                var response = await _httpClient.GetAsync(url);

                if (response.IsSuccessStatusCode)
                {
                    return await response.Content.ReadFromJsonAsync<T>(JsonOptions);
                }

                if ((int)response.StatusCode >= 500 && attempt < MaxRetries - 1)
                {
                    await Task.Delay(TimeSpan.FromSeconds(Math.Pow(2, attempt)));
                    continue;
                }

                response.EnsureSuccessStatusCode();
            }
            catch (HttpRequestException) when (attempt < MaxRetries - 1)
            {
                await Task.Delay(TimeSpan.FromSeconds(Math.Pow(2, attempt)));
                continue;
            }
        }

        throw new HttpRequestException($"Request to {url} failed after {MaxRetries} attempts.");
    }

    /// <summary>
    /// Ensures bearer token is set before making authenticated requests.
    /// </summary>
    /// <exception cref="InvalidOperationException">Thrown if token is not set.</exception>
    private void EnsureAuthenticated()
    {
        if (string.IsNullOrEmpty(_bearerToken))
            throw new InvalidOperationException("Not authenticated. Call LoginWithPasskeyAsync first.");
    }

    /// <summary>
    /// Disposes the HTTP client and resources.
    /// </summary>
    public void Dispose()
    {
        _httpClient?.Dispose();
        GC.SuppressFinalize(this);
    }
}
