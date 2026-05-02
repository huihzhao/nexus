using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using Avalonia.Controls;
using Avalonia.Platform.Storage;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using RuneDesktop.Core.Models;
using RuneDesktop.Core.Services;

namespace RuneDesktop.UI.ViewModels;

/// <summary>
/// Thin-client chat surface (Rounds 2-A / 2-B / 2-C).
///
/// Pre-refactor this VM owned a per-user <c>RuneEngine</c> that wrote
/// every turn into a local <c>LocalEventLog</c> SQLite file and
/// fire-and-forget pushed events to <c>/sync/push</c>. After the
/// thin-client refactor the desktop holds no chat history of its own:
/// every login pulls the canonical message stream from
/// <c>GET /api/v1/agent/messages</c>, every chat send is a direct
/// <c>POST /api/v1/llm/chat</c>, every file is uploaded once via
/// <c>POST /api/v1/files/upload</c> and referenced by ``file_id`` in
/// the chat request — no inline base64, no local SQLite, no per-user
/// data dirs to manage. Server's Nexus DigitalTwin (gated by
/// USE_TWIN=1) is the single source of truth for messages, memories,
/// anchors, and identity.
/// </summary>
public partial class ChatViewModel : ObservableObject
{
    private readonly ApiClient _api;
    private bool _initialized;

    /// <summary>
    /// Total byte budget across all pending attachments. Mirrors the
    /// server-side <c>MAX_ATTACHMENT_BYTES_TOTAL</c> (100 MB default) so
    /// we reject obviously-too-big batches at attach time rather than
    /// letting the request 413 at send time. The server still has the
    /// final say — operators can lower their cap via env.
    /// </summary>
    public const long MaxAttachmentBytesTotal = 100L * 1024 * 1024;

    [ObservableProperty] private string _inputText = "";
    [ObservableProperty] private bool _isTyping;
    [ObservableProperty] private int _memoryCount;
    [ObservableProperty] private int _skillCount;
    [ObservableProperty] private int _turnCount;
    [ObservableProperty] private string _attachmentError = "";

    /// <summary>
    /// Top-level visibility of the chat surface's left "activity sidebar"
    /// (identity card + state counters + Browse/Memories/Anchors/etc.
    /// + activity stream). Persisted to disk so the user's choice
    /// survives restart. When true, the 320 px column collapses to
    /// 0 width — same pattern as <see cref="CognitionPanelViewModel.IsHidden"/>
    /// for the right column. The user can still get back to memories,
    /// anchors, namespaces etc. via the slide-over <c>DetailPanel</c>
    /// which any other surface (top bar, command palette) can open.
    ///
    /// Toggle from MainWindow's top-bar button (default keyboard
    /// shortcut TBD; currently button-only).
    /// </summary>
    [ObservableProperty] private bool _isActivityHidden;

    // ── Chain / Anchor surface ────────────────────────────────────────
    [ObservableProperty] private string _chainTokenId = "—";
    [ObservableProperty] private string _chainNetwork = "";
    [ObservableProperty] private bool _isOnChain;
    [ObservableProperty] private string _anchorStatusText = "Not anchored yet";
    [ObservableProperty] private string _anchorStatusBadge = "•";
    [ObservableProperty] private string _anchorBadgeColor = "#9AA0A6";
    [ObservableProperty] private string _latestAnchorHash = "";
    [ObservableProperty] private string _latestAnchorTx = "";
    [ObservableProperty] private int _anchoredCount;
    [ObservableProperty] private int _pendingAnchorCount;

    private System.Threading.Timer? _pollTimer;

    public ObservableCollection<ChatMessageViewModel> Messages { get; } = new();

    /// <summary>Files staged on the input bar but not yet sent.</summary>
    public ObservableCollection<PendingAttachmentViewModel> PendingAttachments { get; } = new();

    /// <summary>Live activity stream rendered in the sidebar.</summary>
    public ActivityStreamViewModel Activity { get; }

    /// <summary>Slide-over detail panel (memories / anchors).</summary>
    public DetailPanelViewModel DetailPanel { get; }

    /// <summary>Always-on right column — live thinking + summarized
    /// data + on-chain anchors + Greenfield bucket tree, refreshing
    /// every 2s. Communicates the "self-evolving agent" experience
    /// without requiring the user to open a slide-over.</summary>
    public CognitionPanelViewModel Cognition { get; }

    /// <summary>Empty-state visibility helper for the chat surface.</summary>
    public bool HasNoMessages => Messages.Count == 0;

    /// <summary>True when there's at least one pending attachment chip.</summary>
    public bool HasPendingAttachments => PendingAttachments.Count > 0;

    /// <summary>
    /// Optional injection point for a function that opens the platform
    /// file picker. The View wires this up at AttachedToVisualTree time
    /// (it needs the parent <see cref="TopLevel"/>). Tests can also
    /// substitute a fake to drive the flow without a window.
    /// </summary>
    public Func<Task<IReadOnlyList<IStorageFile>>>? FilePickerProvider { get; set; }

    /// <summary>Active chat thread id. ``""`` = synthetic Default chat
    /// (the user's pre-multi-session conversation). Anything else is a
    /// server-issued ``session_xxxxxxxx`` token returned by
    /// <see cref="ApiClient.CreateSessionAsync"/>.
    ///
    /// Set this BEFORE calling <see cref="LoadHistoryForCurrentSessionAsync"/>
    /// — it threads through to <see cref="ApiClient.GetMessagesAsync"/>
    /// (filter by session_id) and into every outgoing chat request so
    /// twin routes the turn correctly.</summary>
    [ObservableProperty] private string _currentSessionId = "";

    public ChatViewModel(ApiClient api)
    {
        _api = api;
        Activity = new ActivityStreamViewModel(api);
        DetailPanel = new DetailPanelViewModel(api);
        Cognition = new CognitionPanelViewModel(api);
        Messages.CollectionChanged += (_, _) => OnPropertyChanged(nameof(HasNoMessages));
        PendingAttachments.CollectionChanged += (_, _) =>
            OnPropertyChanged(nameof(HasPendingAttachments));
        try { IsActivityHidden = SessionPrefs.LoadActivityHidden(); }
        catch { /* missing prefs file → default visible */ }
    }

    partial void OnIsActivityHiddenChanged(bool value)
    {
        try { SessionPrefs.SaveActivityHidden(value); } catch { /* best-effort */ }
    }

    /// <summary>Top-bar button hook — flips
    /// <see cref="IsActivityHidden"/> and persists. Mirrors the rail's
    /// <c>SessionsVm.ToggleHiddenCommand</c> + the cognition column's
    /// <c>Cognition.ToggleHiddenCommand</c> so the user can collapse
    /// every chrome panel and end up with just the chat surface.</summary>
    [RelayCommand]
    private void ToggleActivityHidden() => IsActivityHidden = !IsActivityHidden;

    /// <summary>Switch the active session. Clears the message list and
    /// repopulates from the server filtered by the new session_id, so
    /// the surface only shows that thread's history. Idempotent.</summary>
    public async Task SwitchSessionAsync(string sessionId)
    {
        if (sessionId == CurrentSessionId && _initialized) return;
        CurrentSessionId = sessionId;
        Messages.Clear();
        TurnCount = 0;
        _initialized = false;
        // Phase A1: scope cognition's thinking stream to the new
        // session so the user only sees current-conversation Turns,
        // not bleed-through from the previous chat thread.
        Cognition.SetCurrentSession(sessionId);
        await LoadHistoryForCurrentSessionAsync();
    }

    /// <summary>Pull just the active session's history. Called by
    /// <see cref="SwitchSessionAsync"/> and on first init.</summary>
    private async Task LoadHistoryForCurrentSessionAsync()
    {
        try
        {
            // session_id="" is a meaningful filter (the synthetic
            // default thread) — we always pass it. ApiClient escapes
            // it correctly into the URL.
            var history = await _api.GetMessagesAsync(
                limit: 200, sessionId: CurrentSessionId);
            foreach (var m in history)
            {
                // Phase Q: server returns structured attachments per
                // message; render them as real chips instead of
                // fallback text in the bubble body.
                var chips = m.Attachments
                    .Select(MessageAttachmentViewModel.FromHistory)
                    .ToList();
                Messages.Add(new ChatMessageViewModel(
                    new ChatMessage
                    {
                        Role = m.Role == "user"
                            ? ChatMessageRole.User
                            : ChatMessageRole.Assistant,
                        Content = m.Content,
                        Timestamp = ParseTimestamp(m.Timestamp),
                    },
                    chips));
            }
            TurnCount = history.Count(m => m.Role == "user");
            _initialized = true;
        }
        catch (Exception ex)
        {
            System.Diagnostics.Debug.WriteLine($"session history load: {ex.Message}");
            _initialized = true; // Don't block chat even if history fetch hiccups.
        }
    }

    [RelayCommand]
    private Task BrowseMemories() => DetailPanel.OpenMemoriesAsync();

    [RelayCommand]
    private Task BrowseAnchors() => DetailPanel.OpenAnchorsAsync();

    /// <summary>Phase D 续 / #159: open the Brain panel — learning
    /// progress + chain status + data flow + just-learned feed.
    /// (Replaces the old typed-namespace dump.)</summary>
    [RelayCommand]
    private Task BrowseNamespaces() => DetailPanel.OpenBrainAsync();

    /// <summary>Phase O.5: open the falsifiable-evolution timeline.</summary>
    [RelayCommand]
    private Task BrowseEvolution() => DetailPanel.OpenEvolutionAsync();

    /// <summary>Open the Progress (planning + activity) panel.</summary>
    [RelayCommand]
    private Task BrowseProgress() => DetailPanel.OpenProgressAsync();

    /// <summary>Open the Work directory (Greenfield bucket tree) panel.</summary>
    [RelayCommand]
    private Task BrowseWorkdir() => DetailPanel.OpenWorkdirAsync();

    /// <summary>Open the agent's inner-monologue / thinking panel.</summary>
    [RelayCommand]
    private Task BrowseThinking() => DetailPanel.OpenThinkingAsync();

    /// <summary>
    /// Pull chat history from the server and bind it into the message
    /// list. Runs on every login — no local cache to invalidate.
    /// Failures are logged but don't block the chat surface; the user
    /// can still send a fresh turn even if history fetch hiccups.
    /// </summary>
    public async Task InitializeAsync()
    {
        if (_initialized) return;
        try
        {
            var history = await _api.GetMessagesAsync(limit: 200);
            foreach (var m in history)
            {
                var chips = m.Attachments
                    .Select(MessageAttachmentViewModel.FromHistory)
                    .ToList();
                Messages.Add(new ChatMessageViewModel(
                    new ChatMessage
                    {
                        Role = m.Role == "user"
                            ? ChatMessageRole.User
                            : ChatMessageRole.Assistant,
                        Content = m.Content,
                        Timestamp = ParseTimestamp(m.Timestamp),
                    },
                    chips));
            }
            TurnCount = history.Count(m => m.Role == "user");
            _initialized = true;
        }
        catch (Exception ex)
        {
            System.Diagnostics.Debug.WriteLine($"history load: {ex.Message}");
            _initialized = true; // Don't block chat even if history load fails
        }

        // Kick off the chain status polling once the chat is wired up.
        StartChainStatusPolling();
        _ = RefreshChainStatusAsync();

        // Activity stream lives alongside chain status — same lifecycle.
        Activity.Start();

        // Always-on cognition column — start polling thinking +
        // summarized + on-chain + workdir on login. Survives across
        // chat sends; only stops on logout / shutdown.
        Cognition.Start();
    }

    private static DateTime ParseTimestamp(string iso)
    {
        return DateTime.TryParse(iso, null,
            System.Globalization.DateTimeStyles.RoundtripKind, out var dt)
            ? dt
            : DateTime.UtcNow;
    }

    /// <summary>
    /// Fire a single refresh of /chain/me + /sync/anchors and update the
    /// observable properties the View binds to. Safe to call repeatedly.
    /// </summary>
    public async Task RefreshChainStatusAsync()
    {
        try
        {
            var info = await _api.GetMyChainAgentInfoAsync();
            if (info is not null)
            {
                IsOnChain = info.IsOnChain;
                ChainNetwork = info.Metadata?.Network ?? "";
                ChainTokenId = info.IsOnChain ? "#" + info.AgentId : "—";
            }

            // After Bug 3 the server-side ``/agent/state`` snapshot is
            // the most accurate counter source — it merges legacy
            // sync_anchors with new twin_chain_events. Fall back to the
            // legacy /sync/anchors list if /state isn't reachable, so
            // the badge stays useful during partial outages.
            var state = await _api.GetAgentStateAsync();
            if (state is not null)
            {
                AnchoredCount = state.AnchoredCount;
                PendingAnchorCount = state.PendingAnchorCount + state.FailedAnchorCount;

                if (state.LastAnchor is { } la)
                {
                    // Server returns last_anchor as a dict (snake_case keys)
                    // rather than a typed model — easier to extend without
                    // breaking the wire schema. Pull the four fields we
                    // need, all null-safe.
                    var contentHash = AnchorStr(la, "content_hash");
                    var bscTx       = AnchorStr(la, "bsc_tx_hash");
                    var status      = AnchorStr(la, "status");
                    var retry       = AnchorInt(la, "retry_count");

                    LatestAnchorHash = contentHash.Length > 0
                        ? contentHash[..Math.Min(8, contentHash.Length)]
                        : "";
                    LatestAnchorTx = bscTx.Length > 0
                        ? bscTx[..Math.Min(10, bscTx.Length)] + "…"
                        : "";
                    (AnchorStatusText, AnchorStatusBadge, AnchorBadgeColor)
                        = MapAnchorStatus(status, retry);
                }
                else if (state.AnchoredCount > 0)
                {
                    (AnchorStatusText, AnchorStatusBadge, AnchorBadgeColor)
                        = ("Anchored on BSC", "✓", "#34A853");
                }
                else
                {
                    AnchorStatusText = "No anchors yet — start chatting";
                    AnchorStatusBadge = "•";
                    AnchorBadgeColor = "#9AA0A6";
                }
            }
            else
            {
                var anchors = await _api.GetSyncAnchorsAsync(limit: 20);
                AnchoredCount = anchors.Count(a => a.Status == "anchored");
                PendingAnchorCount = anchors.Count(a =>
                    a.Status is "pending" or "failed" or "awaiting_registration");

                var latest = anchors.FirstOrDefault();
                if (latest is not null)
                {
                    LatestAnchorHash = latest.ShortHash;
                    LatestAnchorTx = latest.ShortTx;
                    (AnchorStatusText, AnchorStatusBadge, AnchorBadgeColor)
                        = MapAnchorStatus(latest.Status, latest.RetryCount);
                }
                else
                {
                    AnchorStatusText = "No anchors yet — start chatting";
                    AnchorStatusBadge = "•";
                    AnchorBadgeColor = "#9AA0A6";
                }
            }
        }
        catch (Exception ex)
        {
            System.Diagnostics.Debug.WriteLine($"chain status poll: {ex.Message}");
        }
    }

    // ── last_anchor JsonElement helpers ───────────────────────────────
    //
    // /agent/state.last_anchor is shaped as a server-side dict literal
    // (sync_anchors row → keys like content_hash, bsc_tx_hash, status,
    // retry_count). C# deserialises that into Dictionary<string, JsonElement>.
    // These helpers read a single key with full null-safety: missing key,
    // null value, or wrong JsonValueKind all collapse to the empty/zero
    // sentinel rather than throwing.

    private static string AnchorStr(Dictionary<string, JsonElement> dict, string key)
    {
        if (!dict.TryGetValue(key, out var v)) return "";
        return v.ValueKind == JsonValueKind.String ? (v.GetString() ?? "") : "";
    }

    private static int AnchorInt(Dictionary<string, JsonElement> dict, string key)
    {
        if (!dict.TryGetValue(key, out var v)) return 0;
        return v.ValueKind == JsonValueKind.Number && v.TryGetInt32(out var n) ? n : 0;
    }

    private static (string text, string badge, string color) MapAnchorStatus(
        string status, int retryCount)
    {
        return status switch
        {
            "anchored"              => ("Anchored on BSC",          "✓", "#34A853"),
            "stored_only"           => ("Stored (chain disabled)",  "◐", "#FBBC04"),
            "awaiting_registration" => ("Waiting for registration", "⋯", "#FBBC04"),
            "pending"               => ("Anchoring…",               "⏳", "#1A73E8"),
            "failed"                => ($"Retrying ({retryCount})",  "↻", "#FBBC04"),
            "failed_permanent"      => ("Anchor failed",            "✕", "#D93025"),
            _                       => (status,                      "•", "#9AA0A6"),
        };
    }

    private void StartChainStatusPolling()
    {
        _pollTimer?.Dispose();
        // Poll every 15s. Timer callbacks run on a background thread; the
        // VM properties are CommunityToolkit ObservableProperties which
        // marshal back to the UI thread when bindings receive them.
        _pollTimer = new System.Threading.Timer(
            _ => _ = RefreshChainStatusAsync(),
            state: null,
            dueTime: TimeSpan.FromSeconds(15),
            period: TimeSpan.FromSeconds(15));
    }

    public void StopChainStatusPolling()
    {
        _pollTimer?.Dispose();
        _pollTimer = null;
        Activity.Stop();
        Cognition.Stop();
    }

    /// <summary>
    /// Reset every piece of in-memory state so a different user's
    /// session does not see the previous user's chat history, memory
    /// counts, or pending attachments. Call this on every successful
    /// login (via <c>MainViewModel.OnLoginSuccess</c>) AND on logout
    /// (so flicker of the prior user's messages doesn't leak).
    ///
    /// Pre-thin-client this also hot-swapped a per-user RuneEngine
    /// pointing at a per-user SQLite file. After the refactor there's
    /// no local state to swap — every read goes to the server with the
    /// freshly installed bearer token, so resetting in-memory and
    /// re-initialising is the whole job.
    /// </summary>
    public async Task ResetForUserAsync()
    {
        // Stop pollers + close detail panel before mutating state.
        Activity.Stop();
        DetailPanel.IsOpen = false;

        // Reset every reactive surface the old user touched.
        _initialized = false;
        Messages.Clear();
        PendingAttachments.Clear();
        InputText = "";
        IsTyping = false;
        AttachmentError = "";
        TurnCount = 0;
        MemoryCount = 0;
        SkillCount = 0;
        IsOnChain = false;
        ChainTokenId = "—";
        ChainNetwork = "";
        AnchorStatusText = "Not anchored yet";
        AnchorStatusBadge = "•";
        AnchorBadgeColor = "#9CA3AF";
        LatestAnchorHash = "";
        LatestAnchorTx = "";
        AnchoredCount = 0;
        PendingAnchorCount = 0;

        // Re-load: pulls history from the *new* user's twin (server
        // scopes by JWT user_id).
        await InitializeAsync();
    }

    [RelayCommand]
    private async Task SendMessageAsync()
    {
        var text = InputText?.Trim();
        // Allow sending with attachments only (no text required)
        if (string.IsNullOrEmpty(text) && PendingAttachments.Count == 0) return;

        if (!_initialized) await InitializeAsync();

        // Snapshot attachments and clear chips immediately so the UI feels
        // responsive even while the network request is in flight.
        var snapshot = PendingAttachments
            .Select(p => p.Attachment)
            .ToList();
        PendingAttachments.Clear();
        AttachmentError = "";

        // Build the structured attachment chips for the optimistic UI.
        // Phase Q: chips are now real ViewModels rendered as a chip
        // strip in the bubble, NOT prefixed text. The bubble's text
        // shows just what the user typed.
        var chipVms = snapshot.Select(MessageAttachmentViewModel.FromPending).ToList();
        // Optimistic body shown in the bubble. If user typed nothing
        // but attached files, fall back to a placeholder so the
        // bubble has visible content beyond the chips.
        string displayText = text ?? "";
        if (string.IsNullOrEmpty(displayText) && snapshot.Count > 0)
        {
            displayText = ""; // chips alone are visible content
        }

        InputText = "";
        IsTyping = true;

        try
        {
            // Optimistic UI: show the user's bubble with structured
            // chips above the text body. The server-bound payload
            // uses BARE text (no chip) — server attaches structured
            // chip metadata to the persisted user_message event.
            Messages.Add(new ChatMessageViewModel(
                ChatMessage.User(displayText), chipVms));

            var serverBoundText = text ?? "";
            // Build the request. Only send the latest user turn — server's
            // twin reconstructs context from its own EventLog, so threading
            // local history through here would just waste tokens (and
            // create drift between "what the desktop thinks happened" and
            // "what twin's memory says happened", which were the two
            // sources of truth pre-refactor).
            var chatRequest = new ChatRequest
            {
                Messages = new List<ChatMessage> { ChatMessage.User(serverBoundText) },
                SystemPrompt = null,                // server's twin owns persona
                ToolDefinitions = [],
                Attachments = snapshot,
                // Multi-session: pin this turn to the active rail
                // selection. Empty string is fine — server treats it
                // as "twin's default thread" (legacy users).
                SessionId = CurrentSessionId,
            };

            var resp = await _api.SendChatAsync(chatRequest);

            Messages.Add(new ChatMessageViewModel(ChatMessage.Assistant(resp.Reply)));
            TurnCount += 1;
        }
        catch (Exception ex)
        {
            Messages.Add(new ChatMessageViewModel(
                ChatMessage.System($"Error: {ex.Message}")));
        }
        finally
        {
            IsTyping = false;
        }
    }

    /// <summary>Open the platform file picker and stage the chosen files.</summary>
    [RelayCommand]
    private async Task AttachFilesAsync()
    {
        if (FilePickerProvider is null)
        {
            AttachmentError = "File picker not available.";
            return;
        }

        IReadOnlyList<IStorageFile> files;
        try
        {
            files = await FilePickerProvider();
        }
        catch (Exception ex)
        {
            AttachmentError = $"Could not open file picker: {ex.Message}";
            return;
        }

        if (files is null || files.Count == 0) return;
        await ProcessUploadFiles(files);
    }

    /// <summary>True while files are being dragged over the chat
    /// surface — drives the "Drop to attach" overlay in ChatView.</summary>
    [ObservableProperty] private bool _isDraggingOverChat;

    /// <summary>Entry point for the chat surface's drag-and-drop
    /// handler (see ChatView.axaml.cs). Same upload pipeline as the
    /// paperclip button; UI just got a different way of feeding files
    /// into it.</summary>
    public Task HandleDroppedFilesAsync(IEnumerable<IStorageFile> files)
        => ProcessUploadFiles(files.ToList());

    /// <summary>Shared file-staging pipeline. Used by both the
    /// paperclip button and drag-and-drop. Streams bytes to the
    /// server (no in-memory buffering of 100 MB files), enforces the
    /// per-request total cap, and accumulates a "Skipped: ..."
    /// message for any rejected files.</summary>
    private async Task ProcessUploadFiles(IReadOnlyList<IStorageFile> files)
    {
        long currentTotal = PendingAttachments.Sum(p => p.SizeBytes);
        var newlyRejected = new List<string>();

        foreach (var f in files)
        {
            try
            {
                // Round 2-B: stream-upload directly. We DON'T read the
                // whole file into a managed byte[] (avoids 100 MB
                // allocations) — the upload helper streams the
                // IStorageFile content into the multipart body.
                var props = await f.GetBasicPropertiesAsync();
                var size = (long)(props?.Size ?? 0UL);
                if (size == 0)
                {
                    newlyRejected.Add($"{f.Name} (empty)");
                    continue;
                }
                if (currentTotal + size > MaxAttachmentBytesTotal)
                {
                    newlyRejected.Add(
                        $"{f.Name} (would exceed {MaxAttachmentBytesTotal / (1024 * 1024)} MB total)");
                    continue;
                }

                var mime = GuessMime(f.Name);
                FileUploadResponse uploaded;
                await using (var stream = await f.OpenReadAsync())
                {
                    uploaded = await _api.UploadFileAsync(stream, f.Name, mime);
                }

                var attachment = new ChatAttachment
                {
                    Name = uploaded.Name == "" ? f.Name : uploaded.Name,
                    Mime = uploaded.Mime,
                    SizeBytes = uploaded.SizeBytes == 0 ? size : uploaded.SizeBytes,
                    FileId = uploaded.FileId,
                    // No inline content — the server already has the bytes
                    // and the next chat request just references file_id.
                    ContentText = null,
                    ContentBase64 = null,
                };
                PendingAttachments.Add(new PendingAttachmentViewModel(attachment));
                currentTotal += attachment.SizeBytes;
            }
            catch (Exception ex)
            {
                newlyRejected.Add($"{f.Name} ({ex.Message})");
            }
        }

        AttachmentError = newlyRejected.Count == 0
            ? ""
            : "Skipped: " + string.Join("; ", newlyRejected);
    }

    [RelayCommand]
    private void RemoveAttachment(PendingAttachmentViewModel? item)
    {
        if (item is null) return;
        PendingAttachments.Remove(item);
        AttachmentError = "";
    }

    private static string GuessMime(string filename)
    {
        var ext = Path.GetExtension(filename).ToLowerInvariant();
        return ext switch
        {
            ".txt" or ".log" => "text/plain",
            ".md" => "text/markdown",
            ".json" => "application/json",
            ".csv" => "text/csv",
            ".tsv" => "text/tab-separated-values",
            ".xml" => "application/xml",
            ".yml" or ".yaml" => "application/yaml",
            ".py" => "text/x-python",
            ".js" or ".mjs" => "application/javascript",
            ".ts" or ".tsx" => "application/typescript",
            ".cs" => "text/x-csharp",
            ".go" => "text/x-go",
            ".rs" => "text/x-rust",
            ".html" or ".htm" => "text/html",
            ".css" => "text/css",
            ".sh" => "application/x-shellscript",
            ".pdf" => "application/pdf",
            ".png" => "image/png",
            ".jpg" or ".jpeg" => "image/jpeg",
            ".gif" => "image/gif",
            ".webp" => "image/webp",
            ".zip" => "application/zip",
            _ => "application/octet-stream",
        };
    }
}
