using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace RuneDesktop.Core.Services;

/// <summary>
/// Result of a /api/v1/chain/register-agent call. The server always
/// returns a structured body (even on chain failures) so we model this
/// as a record with a status string rather than throwing.
/// </summary>
public record ChainAgentResult
{
    [JsonPropertyName("agent_id")]
    public string AgentId { get; init; } = "";

    [JsonPropertyName("tx_hash")]
    public string? TxHash { get; init; }

    /// <summary>"registered" | "pending" | "failed"</summary>
    [JsonPropertyName("status")]
    public string Status { get; init; } = "pending";

    /// <summary>Local-only field used when the request itself blew up
    /// before the server could respond. Not on the wire.</summary>
    public string? ErrorMessage { get; init; }
}

/// <summary>
/// /api/v1/chain/me response. Mirrors server's ChainAgentInfo.
/// </summary>
public record ChainAgentInfo
{
    [JsonPropertyName("agent_id")]
    public string AgentId { get; init; } = "";

    [JsonPropertyName("user_id")]
    public string UserId { get; init; } = "";

    [JsonPropertyName("agent_name")]
    public string AgentName { get; init; } = "";

    [JsonPropertyName("created_at")]
    public string CreatedAt { get; init; } = "";

    [JsonPropertyName("metadata")]
    public ChainAgentMetadata? Metadata { get; init; }

    /// <summary>True iff the server has a real ERC-8004 token id for this user.</summary>
    public bool IsOnChain => Metadata?.OnChain ?? false;
}

public record ChainAgentMetadata
{
    [JsonPropertyName("on_chain")]
    public bool OnChain { get; init; }

    [JsonPropertyName("register_tx")]
    public string? RegisterTx { get; init; }

    [JsonPropertyName("network")]
    public string Network { get; init; } = "";
}

/// <summary>
/// One row from /api/v1/sync/anchors. Mirrors server's SyncAnchorEntry.
///
/// Status values:
///   pending               — work scheduled, not finished yet
///   stored_only           — Greenfield ok, BSC anchor skipped (no chain config)
///   anchored              — Greenfield + BSC both ok
///   awaiting_registration — Greenfield ok, user has no ERC-8004 token id
///   failed                — current attempt failed; daemon will retry
///   failed_permanent      — exhausted retries; manual recovery needed
/// </summary>
public record SyncAnchorEntry
{
    [JsonPropertyName("anchor_id")]
    public long AnchorId { get; init; }

    [JsonPropertyName("first_sync_id")]
    public long FirstSyncId { get; init; }

    [JsonPropertyName("last_sync_id")]
    public long LastSyncId { get; init; }

    [JsonPropertyName("event_count")]
    public int EventCount { get; init; }

    [JsonPropertyName("content_hash")]
    public string ContentHash { get; init; } = "";

    [JsonPropertyName("greenfield_path")]
    public string? GreenfieldPath { get; init; }

    [JsonPropertyName("bsc_tx_hash")]
    public string? BscTxHash { get; init; }

    [JsonPropertyName("status")]
    public string Status { get; init; } = "";

    [JsonPropertyName("error")]
    public string? Error { get; init; }

    [JsonPropertyName("created_at")]
    public string CreatedAt { get; init; } = "";

    [JsonPropertyName("updated_at")]
    public string UpdatedAt { get; init; } = "";

    [JsonPropertyName("retry_count")]
    public int RetryCount { get; init; }

    /// <summary>Convenient short hex for UI display ("a1b2c3…").</summary>
    public string ShortHash =>
        ContentHash.Length > 8 ? ContentHash[..8] + "…" : ContentHash;

    public string ShortTx =>
        string.IsNullOrEmpty(BscTxHash) ? "" :
        (BscTxHash.Length > 10 ? BscTxHash[..10] + "…" : BscTxHash);
}

// ── Agent State / Memory / Timeline (server agent_state.py) ──────────

/// <summary>
/// One slice of the user's recent agent activity. Mirrors server's
/// TimelineItem shape. The desktop's Activity Stream panel consumes a
/// list of these as the "live brain" of the sidebar.
///
/// Kinds the server emits today:
///   chat.user / chat.assistant
///   file.attached / file.distilled
///   memory.compact
///   anchor.pending / anchor.anchored / anchor.failed /
///   anchor.failed_permanent / anchor.awaiting_registration / anchor.stored_only
/// </summary>
public record ActivityItem
{
    [JsonPropertyName("kind")]
    public string Kind { get; init; } = "";

    [JsonPropertyName("timestamp")]
    public string Timestamp { get; init; } = "";

    [JsonPropertyName("summary")]
    public string Summary { get; init; } = "";

    [JsonPropertyName("sync_id")]
    public long? SyncId { get; init; }

    [JsonPropertyName("anchor_id")]
    public long? AnchorId { get; init; }

    [JsonPropertyName("metadata")]
    public Dictionary<string, System.Text.Json.JsonElement> Metadata { get; init; } = new();
}

/// <summary>
/// One memory_compact projection — the "memory snapshot" the sidebar
/// renders inside the Memories slide-over panel. Aligns with SDK's DPM
/// memory_compact event shape.
/// </summary>
public record MemoryEntry
{
    [JsonPropertyName("sync_id")]
    public long SyncId { get; init; }

    [JsonPropertyName("content")]
    public string Content { get; init; } = "";

    [JsonPropertyName("first_sync_id")]
    public long? FirstSyncId { get; init; }

    [JsonPropertyName("last_sync_id")]
    public long? LastSyncId { get; init; }

    [JsonPropertyName("event_count")]
    public int EventCount { get; init; }

    [JsonPropertyName("char_count")]
    public int CharCount { get; init; }

    [JsonPropertyName("created_at")]
    public string CreatedAt { get; init; } = "";
}

/// <summary>Server snapshot of the user's overall agent state (sidebar header).</summary>
public record AgentStateSnapshot
{
    [JsonPropertyName("user_id")]
    public string UserId { get; init; } = "";

    [JsonPropertyName("chain_agent_id")]
    public long? ChainAgentId { get; init; }

    [JsonPropertyName("chain_register_tx")]
    public string? ChainRegisterTx { get; init; }

    [JsonPropertyName("network")]
    public string Network { get; init; } = "";

    [JsonPropertyName("on_chain")]
    public bool OnChain { get; init; }

    [JsonPropertyName("memory_count")]
    public int MemoryCount { get; init; }

    [JsonPropertyName("anchored_count")]
    public int AnchoredCount { get; init; }

    [JsonPropertyName("pending_anchor_count")]
    public int PendingAnchorCount { get; init; }

    [JsonPropertyName("failed_anchor_count")]
    public int FailedAnchorCount { get; init; }

    [JsonPropertyName("total_anchor_count")]
    public int TotalAnchorCount { get; init; }

    [JsonPropertyName("last_anchor")]
    public Dictionary<string, System.Text.Json.JsonElement>? LastAnchor { get; init; }

    [JsonPropertyName("server_time")]
    public string ServerTime { get; init; } = "";
}


// ── Phase J.9: typed memory namespaces ─────────────────────────────


/// <summary>One row in the namespace summary list — counts + version
/// pointers for a single Phase J namespace store.</summary>
public record NamespaceSummary
{
    [JsonPropertyName("name")]
    public string Name { get; init; } = "";

    [JsonPropertyName("item_count")]
    public int ItemCount { get; init; }

    [JsonPropertyName("current_version")]
    public string? CurrentVersion { get; init; }

    [JsonPropertyName("version_count")]
    public int VersionCount { get; init; }
}

/// <summary>Aggregated read across all five Phase J namespaces. The
/// <c>Items</c> dictionary keys mirror <c>Name</c> on each summary so
/// the UI can correlate a card header with its detail rows.</summary>
public record NamespacesResponse
{
    [JsonPropertyName("namespaces")]
    public List<NamespaceSummary> Namespaces { get; init; } = [];

    [JsonPropertyName("items")]
    public Dictionary<string, List<Dictionary<string, System.Text.Json.JsonElement>>> Items { get; init; }
        = new();
}


// ── Phase O.5: evolution timeline ──────────────────────────────────


/// <summary>One row of the evolution timeline — proposal, verdict, or revert.</summary>
public record EvolutionEvent
{
    [JsonPropertyName("index")]
    public long Index { get; init; }

    [JsonPropertyName("timestamp")]
    public double Timestamp { get; init; }

    /// <summary>"evolution_proposal" | "evolution_verdict" | "evolution_revert"</summary>
    [JsonPropertyName("kind")]
    public string Kind { get; init; } = "";

    [JsonPropertyName("edit_id")]
    public string EditId { get; init; } = "";

    [JsonPropertyName("evolver")]
    public string Evolver { get; init; } = "";

    [JsonPropertyName("target_namespace")]
    public string TargetNamespace { get; init; } = "";

    /// <summary>Only present on verdict rows: "kept" | "kept_with_warning" | "reverted".</summary>
    [JsonPropertyName("decision")]
    public string? Decision { get; init; }

    [JsonPropertyName("change_summary")]
    public string ChangeSummary { get; init; } = "";

    [JsonPropertyName("content")]
    public string Content { get; init; } = "";
}

/// <summary>Response for /api/v1/agent/evolution/verdicts.</summary>
public record EvolutionTimelineResponse
{
    [JsonPropertyName("proposals")]
    public int Proposals { get; init; }

    [JsonPropertyName("verdicts")]
    public int Verdicts { get; init; }

    [JsonPropertyName("reverts")]
    public int Reverts { get; init; }

    [JsonPropertyName("events")]
    public List<EvolutionEvent> Events { get; init; } = [];

    /// <summary>edit_ids that have a proposal but no verdict yet.</summary>
    [JsonPropertyName("pending")]
    public List<string> Pending { get; init; } = [];
}

/// <summary>Snapshot of which Greenfield paths are still un-synced.
/// ``PendingPaths`` is the list of objects currently sitting in the
/// chain backend's WAL — i.e. local-only or in-flight. The desktop
/// uses this to badge each Workdir file: ✅ synced (not in list),
/// ⏳ pending (in list).</summary>
public record SyncStatusResponse
{
    [JsonPropertyName("pending_paths")]
    public List<string> PendingPaths { get; init; } = [];

    [JsonPropertyName("wal_entry_count")]
    public int WalEntryCount { get; init; }

    [JsonPropertyName("bucket")]
    public string Bucket { get; init; } = "";

    /// <summary>Phase Q audit fix #4: how many background Greenfield
    /// writes have failed since this twin process started. Surfaced in
    /// the cognition panel as a warning when > 0.</summary>
    [JsonPropertyName("write_failure_count")]
    public int WriteFailureCount { get; init; }

    /// <summary>Most recent failure metadata (path / error / wall time)
    /// or null when all writes have succeeded.</summary>
    [JsonPropertyName("last_write_error")]
    public Dictionary<string, System.Text.Json.JsonElement>? LastWriteError { get; init; }

    /// <summary>Phase Q audit fix #5: best-known liveness of the
    /// Greenfield daemon. Watchdog flips False within ~30s of the
    /// daemon going silent; the cognition panel shows a "daemon dead"
    /// badge when this is False.</summary>
    [JsonPropertyName("daemon_alive")]
    public bool DaemonAlive { get; init; } = true;
}


/// <summary>Server-side user profile — what the user signed up as.
/// Used by the desktop to show the real display name + user_id in the
/// top-right pill instead of the passkey-fallback "Passkey User".</summary>
public record UserProfileResponse
{
    [JsonPropertyName("user_id")]
    public string UserId { get; init; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; init; } = "";

    [JsonPropertyName("created_at")]
    public string CreatedAt { get; init; } = "";
}


/// <summary>One row of the agent's inner-monologue / thinking trace
/// rendered in the desktop's 🧠 Thinking panel.</summary>
public record ThinkingStep
{
    [JsonPropertyName("sync_id")]
    public long SyncId { get; init; }

    [JsonPropertyName("timestamp")]
    public string Timestamp { get; init; } = "";

    /// <summary>Stable kind string the UI uses to pick an icon. One of
    /// heard / checked / recalled / decided / responded / violated /
    /// compacted / evolving / evolved / reverted.</summary>
    [JsonPropertyName("kind")]
    public string Kind { get; init; } = "";

    [JsonPropertyName("label")]
    public string Label { get; init; } = "";

    [JsonPropertyName("content")]
    public string Content { get; init; } = "";

    [JsonPropertyName("metadata")]
    public Dictionary<string, System.Text.Json.JsonElement> Metadata { get; init; } = new();
}

/// <summary>Response for /api/v1/agent/thinking.</summary>
public record ThinkingResponse
{
    [JsonPropertyName("steps")]
    public List<ThinkingStep> Steps { get; init; } = [];

    [JsonPropertyName("total")]
    public int Total { get; init; }
}


/// <summary>Result of POST /api/v1/agent/evolution/{edit_id}/{revert,approve}.</summary>
public record EvolutionDecisionResult
{
    [JsonPropertyName("edit_id")]
    public string EditId { get; init; } = "";

    [JsonPropertyName("decision")]
    public string Decision { get; init; } = "";

    [JsonPropertyName("rolled_back_from")]
    public string RolledBackFrom { get; init; } = "";

    [JsonPropertyName("rolled_back_to")]
    public string RolledBackTo { get; init; } = "";

    [JsonPropertyName("target_namespace")]
    public string TargetNamespace { get; init; } = "";

    [JsonPropertyName("note")]
    public string Note { get; init; } = "";
}


/// <summary>One evolver's pressure gauge — the data the desktop's
/// Pressure Dashboard binds to.
///
/// Mirrors the Python ``EvolutionPressureItem`` shape from
/// nexus_server.agent_state. ``threshold`` may serialise as a JSON
/// number or as a sentinel for live-mode evolvers (no threshold);
/// the UI checks ``Status == "live"`` to decide whether to render
/// a percentage-fill gauge or a flat live-stream indicator.</summary>
public record EvolutionPressureItem
{
    [JsonPropertyName("evolver")]
    public string Evolver { get; init; } = "";

    [JsonPropertyName("layer")]
    public string Layer { get; init; } = "";

    [JsonPropertyName("accumulator")]
    public double Accumulator { get; init; }

    /// <summary>Target threshold. May be Infinity for live evolvers
    /// — System.Text.Json deserialises that to ``double.PositiveInfinity``,
    /// which we render as "live" rather than as 0%.</summary>
    [JsonPropertyName("threshold")]
    public double Threshold { get; init; }

    [JsonPropertyName("unit")]
    public string Unit { get; init; } = "";

    [JsonPropertyName("status")]
    public string Status { get; init; } = "";

    [JsonPropertyName("fed_by")]
    public List<string> FedBy { get; init; } = [];

    [JsonPropertyName("last_fired_at")]
    public double? LastFiredAt { get; init; }

    [JsonPropertyName("details")]
    public Dictionary<string, System.Text.Json.JsonElement> Details { get; init; } = new();
}


/// <summary>Response for GET /api/v1/agent/evolution/pressure.</summary>
public record EvolutionPressureResponse
{
    [JsonPropertyName("evolvers")]
    public List<EvolutionPressureItem> Evolvers { get; init; } = [];

    /// <summary>Per-evolver 24h hourly bucket counts. Keys are evolver
    /// names ("PersonaEvolver", "MemoryEvolver", …); each value is a
    /// 24-element list of fire counts (oldest first). Missing entries
    /// mean the evolver had zero firings in the window — UI should
    /// render an empty sparkline rather than treat absence as error.
    /// </summary>
    [JsonPropertyName("histogram_24h")]
    public Dictionary<string, List<int>> Histogram24h { get; init; } = new();

    /// <summary>Phase D 续 / #159: recent verdict events (kept /
    /// reverted) for the dashboard's verdict feed, newest-first.</summary>
    [JsonPropertyName("recent_verdicts")]
    public List<EvolutionVerdictItem> RecentVerdicts { get; init; } = [];
}

/// <summary>One verdict event for the Pressure Dashboard's verdict feed.</summary>
public record EvolutionVerdictItem
{
    [JsonPropertyName("edit_id")]
    public string EditId { get; init; } = string.Empty;

    [JsonPropertyName("evolver")]
    public string Evolver { get; init; } = string.Empty;

    [JsonPropertyName("target_namespace")]
    public string TargetNamespace { get; init; } = string.Empty;

    [JsonPropertyName("decision")]
    public string Decision { get; init; } = "(unknown)";

    [JsonPropertyName("timestamp")]
    public double Timestamp { get; init; }

    [JsonPropertyName("regression_score")]
    public double RegressionScore { get; init; }

    [JsonPropertyName("abc_drift_delta")]
    public double AbcDriftDelta { get; init; }

    [JsonPropertyName("evidence")]
    public string Evidence { get; init; } = string.Empty;

    [JsonPropertyName("change_summary")]
    public string ChangeSummary { get; init; } = string.Empty;
}

// ── Brain panel: Chain status (Phase D 续 / #159) ────────────────────

/// <summary>Per-namespace on-chain mirror state. ``Status`` is one of
/// "local" / "mirrored" / "anchored".</summary>
public record NamespaceChainStatus
{
    [JsonPropertyName("namespace")]
    public string Namespace { get; init; } = string.Empty;

    [JsonPropertyName("status")]
    public string Status { get; init; } = "local";

    [JsonPropertyName("version")]
    public string? Version { get; init; }

    [JsonPropertyName("last_commit_at")]
    public double? LastCommitAt { get; init; }

    [JsonPropertyName("last_anchor_at")]
    public double? LastAnchorAt { get; init; }

    [JsonPropertyName("mirrored")]
    public bool Mirrored { get; init; }
}

public record ChainHealthCard
{
    [JsonPropertyName("wal_queue_size")]
    public int WalQueueSize { get; init; }

    [JsonPropertyName("daemon_alive")]
    public bool DaemonAlive { get; init; } = true;

    [JsonPropertyName("last_daemon_ok")]
    public double? LastDaemonOk { get; init; }

    [JsonPropertyName("greenfield_ready")]
    public bool GreenfieldReady { get; init; }

    [JsonPropertyName("bsc_ready")]
    public bool BscReady { get; init; }
}

public record ChainStatusResponse
{
    [JsonPropertyName("namespaces")]
    public List<NamespaceChainStatus> Namespaces { get; init; } = [];

    [JsonPropertyName("health")]
    public ChainHealthCard Health { get; init; } = new();
}

// ── Brain panel: Learning summary (Phase D 续 / #159) ────────────────

public record TimelineDay
{
    [JsonPropertyName("day")]
    public string Day { get; init; } = string.Empty;

    [JsonPropertyName("facts")]
    public int Facts { get; init; }

    [JsonPropertyName("skills")]
    public int Skills { get; init; }

    [JsonPropertyName("knowledge")]
    public int Knowledge { get; init; }

    [JsonPropertyName("persona")]
    public int Persona { get; init; }

    [JsonPropertyName("episodes")]
    public int Episodes { get; init; }
}

public record JustLearnedItem
{
    [JsonPropertyName("kind")]
    public string Kind { get; init; } = string.Empty;

    [JsonPropertyName("content")]
    public string Content { get; init; } = string.Empty;

    [JsonPropertyName("category")]
    public string Category { get; init; } = string.Empty;

    [JsonPropertyName("importance")]
    public int Importance { get; init; } = 3;

    [JsonPropertyName("timestamp")]
    public double Timestamp { get; init; }

    [JsonPropertyName("version")]
    public string? Version { get; init; }

    [JsonPropertyName("chain_status")]
    public string ChainStatus { get; init; } = "local";
}

public record DataFlowStage
{
    [JsonPropertyName("evolver")]
    public string Evolver { get; init; } = string.Empty;

    [JsonPropertyName("layer")]
    public string Layer { get; init; } = string.Empty;

    [JsonPropertyName("status")]
    public string Status { get; init; } = "live";

    [JsonPropertyName("accumulator")]
    public double Accumulator { get; init; }

    [JsonPropertyName("threshold")]
    public double Threshold { get; init; }

    [JsonPropertyName("unit")]
    public string Unit { get; init; } = string.Empty;

    [JsonPropertyName("fed_by")]
    public List<string> FedBy { get; init; } = [];

    [JsonPropertyName("last_fired_at")]
    public double? LastFiredAt { get; init; }
}

public record LearningSummaryResponse
{
    [JsonPropertyName("window_days")]
    public int WindowDays { get; init; } = 7;

    [JsonPropertyName("timeline")]
    public List<TimelineDay> Timeline { get; init; } = [];

    [JsonPropertyName("just_learned")]
    public List<JustLearnedItem> JustLearned { get; init; } = [];

    [JsonPropertyName("data_flow")]
    public List<DataFlowStage> DataFlow { get; init; } = [];
}
