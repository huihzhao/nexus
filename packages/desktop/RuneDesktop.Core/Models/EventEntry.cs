namespace RuneDesktop.Core.Models;

/// <summary>
/// Represents a single event in the Rune Protocol event log.
/// Events are immutable records of user actions, agent responses, and system events.
/// Mirrors the Python EventLog model for cross-platform compatibility.
/// </summary>
public record EventEntry
{
    /// <summary>
    /// Unique auto-incremented identifier for this event entry.
    /// </summary>
    public long Id { get; init; }

    /// <summary>
    /// Type of event (e.g., "user_message", "assistant_response", "function_call", "metadata_update").
    /// </summary>
    public required string EventType { get; init; }

    /// <summary>
    /// Main content payload of the event. Can be user input, assistant reply, or structured data.
    /// </summary>
    public required string Content { get; init; }

    /// <summary>
    /// Session identifier linking related events together (e.g., a single conversation).
    /// </summary>
    public required string SessionId { get; init; }

    /// <summary>
    /// Additional metadata stored as JSON. Can include tool calls, model parameters, or custom data.
    /// </summary>
    public string? Metadata { get; init; }

    /// <summary>
    /// Timestamp when this event was created (UTC).
    /// </summary>
    public DateTime CreatedAt { get; init; }

    /// <summary>
    /// Server sync ID after successful upload. Null indicates event has not been synced yet.
    /// Used to avoid re-uploading the same events.
    /// </summary>
    public long? SyncId { get; init; }

    /// <summary>
    /// Creates a new EventEntry with automatic timestamp in UTC.
    /// </summary>
    /// <param name="eventType">Type of event.</param>
    /// <param name="content">Event content payload.</param>
    /// <param name="sessionId">Session identifier.</param>
    /// <param name="metadata">Optional JSON metadata.</param>
    /// <returns>A new EventEntry with CreatedAt set to current UTC time.</returns>
    public static EventEntry Create(
        string eventType,
        string content,
        string sessionId,
        string? metadata = null)
    {
        return new EventEntry
        {
            Id = 0,
            EventType = eventType,
            Content = content,
            SessionId = sessionId,
            Metadata = metadata,
            CreatedAt = DateTime.UtcNow,
            SyncId = null
        };
    }

    /// <summary>
    /// Marks this event as synced with the server.
    /// </summary>
    /// <param name="syncId">The server-assigned sync ID.</param>
    /// <returns>A new EventEntry with the SyncId set.</returns>
    public EventEntry MarkSynced(long syncId)
    {
        return this with { SyncId = syncId };
    }

    /// <summary>
    /// Determines if this event has been synced to the server.
    /// </summary>
    public bool IsSynced => SyncId.HasValue;
}
