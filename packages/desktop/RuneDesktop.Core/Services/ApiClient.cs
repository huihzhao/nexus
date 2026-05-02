using System.Collections.Generic;
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

    /// <summary>Phase Q: attachments persisted as structured metadata
    /// on user_message events. Server returns the original file
    /// names/mime/size so the desktop can render real chips on
    /// reload instead of falling back to "📎 paper.pdf" plain text.</summary>
    [JsonPropertyName("attachments")]
    public List<HistoryAttachmentInfo> Attachments { get; init; } = [];
}

/// <summary>Mirror of the server's AttachmentInfo. Lives here as a
/// minimal record because it's only consumed by ChatMessageView in
/// history reload — full ChatAttachment carries upload bytes which
/// we don't ship over the wire on history reads.</summary>
public record HistoryAttachmentInfo
{
    [JsonPropertyName("name")]
    public required string Name { get; init; }

    [JsonPropertyName("mime")]
    public string Mime { get; init; } = "application/octet-stream";

    [JsonPropertyName("size_bytes")]
    public long SizeBytes { get; init; }
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

    /// <summary>
    /// Multi-session: route this chat turn to a specific server-side
    /// thread. Null/empty means "twin's current default thread"
    /// (legacy behaviour, used for the synthetic Default chat that
    /// holds pre-multi-session messages). When set, the server's
    /// chat handler tells twin to switch its in-memory thread before
    /// running the turn so the LLM sees only that thread's history.
    /// </summary>
    [JsonPropertyName("session_id")]
    public string? SessionId { get; init; }
}

/// <summary>
/// One row of <c>GET /api/v1/sessions</c>. Models the server's
/// SessionInfo Pydantic shape from <c>nexus_server/sessions.py</c>.
/// </summary>
public record SessionInfo
{
    [JsonPropertyName("id")]
    public required string Id { get; init; }

    [JsonPropertyName("title")]
    public required string Title { get; init; }

    [JsonPropertyName("created_at")]
    public string CreatedAt { get; init; } = "";

    [JsonPropertyName("last_message_at")]
    public string? LastMessageAt { get; init; }

    [JsonPropertyName("message_count")]
    public int MessageCount { get; init; }

    [JsonPropertyName("archived")]
    public bool Archived { get; init; }

    /// <summary>True for the synthetic legacy / pre-multi-session
    /// thread (id == ""). The desktop hides rename/archive controls
    /// for these.</summary>
    [JsonPropertyName("is_default")]
    public bool IsDefault { get; init; }
}

/// <summary>Wire shape of <c>GET /api/v1/sessions</c>.</summary>
public record SessionListResponse
{
    [JsonPropertyName("sessions")]
    public List<SessionInfo> Sessions { get; init; } = [];
}

/// <summary>Wire shape of <c>DELETE /api/v1/sessions/{id}?hard=true</c>.
/// Lets the desktop surface "deleted N messages, K Greenfield orphans
/// remain (BSC anchors immutable)" in the confirmation toast.</summary>
public record DeleteSessionResult
{
    [JsonPropertyName("session_id")]
    public string SessionId { get; init; } = "";

    [JsonPropertyName("hard_deleted")]
    public bool HardDeleted { get; init; }

    [JsonPropertyName("deleted_event_count")]
    public int DeletedEventCount { get; init; }

    [JsonPropertyName("bsc_note")]
    public string BscNote { get; init; } = "";
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
    // Default per-request timeout. Chat is interactive but the
    // server-side path is genuinely slow on cold starts:
    //   * twin._initialize loads persona / skills / knowledge / memory
    //     from Greenfield (3-10s each on cold cache)
    //   * the LLM completion itself takes 5-30s on busy days
    //   * RLM-mode chat projection can issue several sub-LLM calls
    //   * first-turn chain bootstrap (ERC-8004 mint + bucket create)
    //     adds another 10-30s before the first response can return.
    // 30s was too tight and produced visible "request canceled" errors
    // in normal use. 180s is a roomy upper bound for interactive chat;
    // read endpoints (timeline / memories / namespaces) typically
    // settle in <5s so the bigger ceiling doesn't slow them down.
    private const int TimeoutSeconds = 180;
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
                // BUG FIX: file_id was being dropped on the way out, so
                // the server's resolve_files() returned [] and the
                // chat handler fell into the inline-content path with
                // content_text/content_base64 both null. Result: the
                // distiller saw an empty payload and the LLM replied
                // "your PDF is empty" no matter how big the file was.
                // file_id is the canonical reference now (Round 2-B);
                // the inline fields are only used for legacy callers
                // that haven't moved to /files/upload yet.
                file_id = a.FileId,
                content_text = a.ContentText,
                content_base64 = a.ContentBase64,
            }).ToList(),
            // Multi-session: thread the active session id through to
            // the server so twin routes this turn to the right thread.
            // Null/empty here = twin's default thread (legacy users).
            session_id = string.IsNullOrEmpty(chatRequest.SessionId)
                ? null : chatRequest.SessionId,
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

    /// <summary>Read the current user's server-side profile —
    /// {user_id, display_name, created_at}. Used by the passkey login
    /// path to populate the top-bar pill with the real handle (the
    /// JWT alone doesn't carry it; the server table does). Returns
    /// null on transient failure so the caller can fall back.</summary>
    public async Task<UserProfileResponse?> GetUserProfileAsync()
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/user/profile";
        try { return await GetWithRetryAsync<UserProfileResponse>(url); }
        catch { return null; }
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

    /// <summary>Per-path sync state — which Greenfield writes are
    /// still pending (in the chain backend's WAL). The Workdir tree
    /// uses this to badge each file as ✅ synced or ⏳ pending.</summary>
    public async Task<SyncStatusResponse?> GetSyncStatusAsync()
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/agent/sync_status";
        try { return await GetWithRetryAsync<SyncStatusResponse>(url); }
        catch { return null; }
    }

    /// <summary>Phase J.9: typed memory namespaces (episodes / facts /
    /// skills / persona / knowledge) for the desktop's Memory panel.</summary>
    public async Task<NamespacesResponse?> GetMemoryNamespacesAsync(
        bool includeItems = true, int itemsLimit = 50)
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/agent/memory/namespaces"
                  + $"?include_items={includeItems.ToString().ToLowerInvariant()}"
                  + $"&items_limit={itemsLimit}";
        try { return await GetWithRetryAsync<NamespacesResponse>(url); }
        catch { return null; }
    }

    /// <summary>Agent's inner-monologue / thinking trace — feeds the
    /// desktop's 🧠 Thinking panel. Pass ``sinceSyncId`` to get only
    /// new steps since the last poll.</summary>
    public async Task<ThinkingResponse?> GetThinkingAsync(int limit = 60, long? sinceSyncId = null)
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/agent/thinking?limit={limit}";
        if (sinceSyncId is { } cursor)
            url += $"&since_sync_id={cursor}";
        try { return await GetWithRetryAsync<ThinkingResponse>(url); }
        catch { return null; }
    }

    /// <summary>Phase O.5: falsifiable-evolution timeline (proposal +
    /// verdict + revert events) for the desktop's Evolution panel.</summary>
    public async Task<EvolutionTimelineResponse?> GetEvolutionTimelineAsync(int limit = 100)
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/agent/evolution/verdicts?limit={limit}";
        try { return await GetWithRetryAsync<EvolutionTimelineResponse>(url); }
        catch { return null; }
    }

    /// <summary>Phase O.6: user-driven manual revert for one edit.</summary>
    public async Task<EvolutionDecisionResult?> RevertEvolutionAsync(string editId)
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/agent/evolution/{Uri.EscapeDataString(editId)}/revert";
        try { return await PostWithRetryAsync<EvolutionDecisionResult>(url); }
        catch { return null; }
    }

    /// <summary>Phase O.6: user-driven manual approve for one edit.</summary>
    public async Task<EvolutionDecisionResult?> ApproveEvolutionAsync(string editId)
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/agent/evolution/{Uri.EscapeDataString(editId)}/approve";
        try { return await PostWithRetryAsync<EvolutionDecisionResult>(url); }
        catch { return null; }
    }

    /// <summary>Phase C: Pressure Dashboard data source.
    ///
    /// Fetches every evolver's current accumulator + 24h histogram so
    /// the desktop can render the gauges + lineage + frequency
    /// pyramid views. Polled every 5s by ``CognitionPanelViewModel``
    /// — slower cadence than the cognition stream because pressure
    /// changes slowly.</summary>
    public async Task<EvolutionPressureResponse?> GetEvolutionPressureAsync()
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/agent/evolution/pressure";
        try { return await GetWithRetryAsync<EvolutionPressureResponse>(url); }
        catch { return null; }
    }

    /// <summary>Brain panel: per-namespace mirror+anchor state +
    /// Chain Health card (Phase D 续 / #159). Polled every ~10s.</summary>
    public async Task<ChainStatusResponse?> GetChainStatusAsync()
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/agent/chain_status";
        try { return await GetWithRetryAsync<ChainStatusResponse>(url); }
        catch { return null; }
    }

    /// <summary>Brain panel: 7-day timeline + just-learned feed +
    /// data-flow snapshot. Polled every ~10s (Phase D 续 / #159).</summary>
    public async Task<LearningSummaryResponse?> GetLearningSummaryAsync(string window = "7d")
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/agent/learning_summary?window={Uri.EscapeDataString(window)}";
        try { return await GetWithRetryAsync<LearningSummaryResponse>(url); }
        catch { return null; }
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
        int limit = 200, long? beforeSyncId = null, string? sessionId = null)
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/agent/messages?limit={limit}";
        if (beforeSyncId is { } cursor)
            url += $"&before_sync_id={cursor}";
        if (sessionId is not null)
            // Empty string is a meaningful filter (the synthetic
            // default session — events with empty session_id) so we
            // append it even when it's "".
            url += $"&session_id={Uri.EscapeDataString(sessionId)}";
        try
        {
            var resp = await GetWithRetryAsync<MessagesListResponse>(url);
            return resp?.Messages ?? [];
        }
        catch { return []; }
    }

    // ── Multi-session: list / create / rename / archive ──────────────

    /// <summary>List the current user's chat sessions, newest activity
    /// first. The synthetic Default chat is appended automatically by
    /// the server when the user has any pre-multi-session history.</summary>
    public async Task<List<SessionInfo>> ListSessionsAsync(
        bool includeArchived = false)
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/sessions?include_archived={(includeArchived ? "true" : "false")}";
        try
        {
            var resp = await GetWithRetryAsync<SessionListResponse>(url);
            return resp?.Sessions ?? [];
        }
        catch { return []; }
    }

    /// <summary>Create a new session. ``title`` is optional — leave it
    /// null and the server seeds a "New chat" placeholder which the
    /// auto-title heuristic replaces after the first user message.</summary>
    public async Task<SessionInfo?> CreateSessionAsync(string? title = null)
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/sessions";
        var body = new { title };
        try
        {
            return await PostWithRetryAsync<SessionInfo>(url, body);
        }
        catch { return null; }
    }

    /// <summary>Rename a session. Returns the updated row, or null if
    /// the session doesn't exist (or belongs to another user).</summary>
    public async Task<SessionInfo?> RenameSessionAsync(string sessionId, string title)
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/sessions/{Uri.EscapeDataString(sessionId)}";
        var body = new { title };
        try
        {
            using var req = new HttpRequestMessage(HttpMethod.Patch, url)
            {
                Content = JsonContent.Create(body),
            };
            using var resp = await _httpClient.SendAsync(req);
            if (!resp.IsSuccessStatusCode) return null;
            return await resp.Content.ReadFromJsonAsync<SessionInfo>(JsonOptions);
        }
        catch { return null; }
    }

    /// <summary>Archive (soft-delete) a session. Twin's event_log
    /// retains every message — archive only hides the row from the
    /// sidebar's default list. Returns true when a row was archived.</summary>
    public async Task<bool> ArchiveSessionAsync(string sessionId)
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/sessions/{Uri.EscapeDataString(sessionId)}";
        try
        {
            using var resp = await _httpClient.DeleteAsync(url);
            return resp.IsSuccessStatusCode;
        }
        catch { return false; }
    }

    /// <summary>Hard-delete a session. Wipes message rows from twin's
    /// EventLog, drops pending Greenfield writes, removes the
    /// metadata row. BSC state-root anchors are immutable and stay.
    /// Returns the server's summary dict on success, or null on
    /// failure. Reading the result lets the caller surface counts /
    /// the BSC immutability note in a confirmation toast.</summary>
    public async Task<DeleteSessionResult?> DeleteSessionHardAsync(string sessionId)
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/sessions/{Uri.EscapeDataString(sessionId)}?hard=true";
        try
        {
            using var resp = await _httpClient.DeleteAsync(url);
            if (!resp.IsSuccessStatusCode) return null;
            return await resp.Content.ReadFromJsonAsync<DeleteSessionResult>(JsonOptions);
        }
        catch { return null; }
    }

    // ── Live thinking SSE ────────────────────────────────────────────

    /// <summary>One frame off the live thinking stream. Mirrors the
    /// shape emitted by the SDK's ThinkingEmitter (one row of
    /// reasoning telemetry).</summary>
    public record ThinkingStreamFrame
    {
        [JsonPropertyName("turn_id")] public long TurnId { get; init; }
        [JsonPropertyName("seq")] public long Seq { get; init; }
        [JsonPropertyName("kind")] public string Kind { get; init; } = "";
        [JsonPropertyName("label")] public string Label { get; init; } = "";
        [JsonPropertyName("content")] public string Content { get; init; } = "";
        [JsonPropertyName("metadata")] public Dictionary<string, object>? Metadata { get; init; }
        [JsonPropertyName("timestamp")] public double Timestamp { get; init; }
        [JsonPropertyName("duration_ms")] public long? DurationMs { get; init; }
        // Phase A1: per-session ids so cognition panel can filter
        // and render "Turn N of THIS chat" rather than the global
        // turn counter that keeps climbing across session switches.
        [JsonPropertyName("session_id")] public string SessionId { get; init; } = "";
        [JsonPropertyName("session_turn_id")] public long SessionTurnId { get; init; }
    }

    /// <summary>Open the live thinking SSE stream and yield frames as
    /// they arrive. Caller passes a <paramref name="ct"/> to stop —
    /// closing the cancellation token tears the HTTP connection down,
    /// the server's handler unsubscribes its emitter queue.
    ///
    /// Reconnect on transient failure is the caller's responsibility
    /// (the cognition VM owns the retry loop). Implementation:
    ///   * raw HttpClient request with HttpCompletionOption.ResponseHeadersRead
    ///     so we don't buffer the whole stream
    ///   * line-oriented parse (split on '\n'); a blank line flushes
    ///     the accumulated ``data:`` lines as one frame
    ///   * comment frames (lines starting with ':') are silently dropped
    ///   * ``hello`` / ``error`` kinds pass through to the consumer
    ///     so it can render a status badge.</summary>
    public async IAsyncEnumerable<ThinkingStreamFrame> StreamThinkingAsync(
        [System.Runtime.CompilerServices.EnumeratorCancellation]
        System.Threading.CancellationToken ct)
    {
        EnsureAuthenticated();
        var url = $"{_serverUrl}/api/v1/agent/thinking/stream";
        using var req = new HttpRequestMessage(HttpMethod.Get, url);
        req.Headers.Accept.Add(new System.Net.Http.Headers.MediaTypeWithQualityHeaderValue("text/event-stream"));

        HttpResponseMessage resp;
        try
        {
            resp = await _httpClient.SendAsync(
                req, HttpCompletionOption.ResponseHeadersRead, ct);
        }
        catch (Exception)
        {
            yield break;
        }
        using var _ = resp;
        if (!resp.IsSuccessStatusCode) yield break;

        using var stream = await resp.Content.ReadAsStreamAsync(ct);
        using var reader = new System.IO.StreamReader(stream);

        var dataBuffer = new System.Text.StringBuilder();
        while (!ct.IsCancellationRequested)
        {
            string? line;
            try { line = await reader.ReadLineAsync(ct); }
            catch (OperationCanceledException) { yield break; }
            catch (System.IO.IOException) { yield break; }

            if (line is null) yield break;

            if (string.IsNullOrEmpty(line))
            {
                // Blank line = frame boundary. Try to deserialise the
                // accumulated data lines as one ThinkingStreamFrame.
                if (dataBuffer.Length == 0) continue;
                ThinkingStreamFrame? frame = null;
                try
                {
                    frame = JsonSerializer.Deserialize<ThinkingStreamFrame>(
                        dataBuffer.ToString(), JsonOptions);
                }
                catch (JsonException) { /* malformed — skip */ }
                dataBuffer.Clear();
                if (frame is not null) yield return frame;
                continue;
            }

            if (line.StartsWith(":"))
            {
                // Comment / keepalive — ignore.
                continue;
            }

            if (line.StartsWith("data:"))
            {
                // SSE allows multi-line ``data:`` blocks; we only emit
                // one-line JSON server-side, but be defensive and
                // concatenate just in case.
                var payload = line.Length > 5 && line[5] == ' '
                    ? line.Substring(6) : line.Substring(5);
                if (dataBuffer.Length > 0) dataBuffer.Append('\n');
                dataBuffer.Append(payload);
            }
            // Other field names (event, id, retry) are ignored.
        }
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
