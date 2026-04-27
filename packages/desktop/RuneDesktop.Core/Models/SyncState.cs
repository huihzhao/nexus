namespace RuneDesktop.Core.Models;

/// <summary>
/// Represents the synchronization state between the local device and the server.
/// Tracks progress and metadata for syncing events and profile updates.
/// </summary>
public record SyncState
{
    /// <summary>
    /// Last event ID that was successfully synced to the server.
    /// Used to resume sync operations and avoid re-uploading events.
    /// </summary>
    public long LastSyncId { get; init; }

    /// <summary>
    /// Timestamp of the most recent successful sync operation (UTC).
    /// </summary>
    public DateTime LastSyncTime { get; init; }

    /// <summary>
    /// Server URL used for sync operations (e.g., "https://api.runeprotocol.io").
    /// </summary>
    public required string ServerUrl { get; init; }

    /// <summary>
    /// Unique identifier for this device. Used by the server to track device-specific state.
    /// </summary>
    public required string DeviceId { get; init; }

    /// <summary>
    /// Number of events currently pending sync (not yet uploaded).
    /// Updated when events are appended or synced.
    /// </summary>
    public int PendingEventCount { get; init; }

    /// <summary>
    /// Timestamp when the sync state was last updated (UTC).
    /// </summary>
    public DateTime UpdatedAt { get; init; }

    /// <summary>
    /// Optional error message from the last failed sync attempt.
    /// Cleared on successful sync.
    /// </summary>
    public string? LastSyncError { get; init; }

    /// <summary>
    /// Creates a new SyncState with default/initial values.
    /// </summary>
    /// <param name="serverUrl">Server URL for sync operations.</param>
    /// <param name="deviceId">Unique device identifier.</param>
    /// <returns>A new SyncState with all timestamps set to current UTC time and zero pending events.</returns>
    public static SyncState Create(string serverUrl, string deviceId)
    {
        var now = DateTime.UtcNow;
        return new SyncState
        {
            LastSyncId = 0,
            LastSyncTime = now,
            ServerUrl = serverUrl,
            DeviceId = deviceId,
            PendingEventCount = 0,
            UpdatedAt = now,
            LastSyncError = null
        };
    }

    /// <summary>
    /// Records a successful sync operation.
    /// </summary>
    /// <param name="lastSyncId">The highest event ID that was synced.</param>
    /// <param name="pendingCount">Number of events now pending sync.</param>
    /// <returns>A new SyncState with updated sync information and cleared error.</returns>
    public SyncState MarkSyncSuccess(long lastSyncId, int pendingCount = 0)
    {
        return this with
        {
            LastSyncId = lastSyncId,
            LastSyncTime = DateTime.UtcNow,
            PendingEventCount = pendingCount,
            UpdatedAt = DateTime.UtcNow,
            LastSyncError = null
        };
    }

    /// <summary>
    /// Records a failed sync operation with an error message.
    /// </summary>
    /// <param name="errorMessage">Description of the sync failure.</param>
    /// <returns>A new SyncState with the error message recorded.</returns>
    public SyncState MarkSyncFailure(string errorMessage)
    {
        return this with
        {
            UpdatedAt = DateTime.UtcNow,
            LastSyncError = errorMessage
        };
    }

    /// <summary>
    /// Updates the pending event count.
    /// </summary>
    /// <param name="count">Number of events awaiting sync.</param>
    /// <returns>A new SyncState with updated pending count.</returns>
    public SyncState SetPendingEventCount(int count)
    {
        return this with
        {
            PendingEventCount = count,
            UpdatedAt = DateTime.UtcNow
        };
    }

    /// <summary>
    /// Determines if a sync operation is needed.
    /// </summary>
    public bool HasPendingEvents => PendingEventCount > 0;

    /// <summary>
    /// Determines if the last sync operation failed.
    /// </summary>
    public bool HasError => !string.IsNullOrEmpty(LastSyncError);

    /// <summary>
    /// Gets the time elapsed since the last successful sync.
    /// </summary>
    public TimeSpan TimeSinceLastSync => DateTime.UtcNow - LastSyncTime;

    /// <summary>
    /// Determines if a sync is considered stale (last sync was more than 5 minutes ago).
    /// </summary>
    public bool IsSyncStale => TimeSinceLastSync.TotalMinutes > 5;
}
