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
