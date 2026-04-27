namespace RuneDesktop.Core.Models;

/// <summary>
/// Represents the role of a message participant in a chat conversation.
/// </summary>
public enum ChatMessageRole
{
    /// <summary>
    /// Message from the end user.
    /// </summary>
    User,

    /// <summary>
    /// Message from the AI assistant/agent.
    /// </summary>
    Assistant,

    /// <summary>
    /// System-generated message (instructions, context, etc).
    /// </summary>
    System
}

/// <summary>
/// Represents a tool call invoked by the assistant.
/// </summary>
public record ToolCall
{
    /// <summary>
    /// Unique identifier for this tool invocation.
    /// </summary>
    public required string Id { get; init; }

    /// <summary>
    /// Name of the tool being called.
    /// </summary>
    public required string Function { get; init; }

    /// <summary>
    /// Input arguments to the tool, typically JSON-serialized.
    /// </summary>
    public required string Arguments { get; init; }

    /// <summary>
    /// Result returned by the tool, if execution is complete.
    /// </summary>
    public string? Result { get; init; }
}

/// <summary>
/// Represents a single message in a chat conversation.
/// Used for display and history tracking in the UI and agent engine.
/// </summary>
public record ChatMessage
{
    /// <summary>
    /// Role/sender of the message (User, Assistant, or System).
    /// </summary>
    public required ChatMessageRole Role { get; init; }

    /// <summary>
    /// Text content of the message.
    /// </summary>
    public required string Content { get; init; }

    /// <summary>
    /// Timestamp when this message was created (UTC).
    /// </summary>
    public DateTime Timestamp { get; init; }

    /// <summary>
    /// Tool calls made by the assistant during this message (if any).
    /// </summary>
    public List<ToolCall> ToolCalls { get; init; } = [];

    /// <summary>
    /// Whether this message is currently being streamed/generated.
    /// Used for UI feedback to show pending responses.
    /// </summary>
    public bool IsStreaming { get; init; }

    /// <summary>
    /// Optional token usage statistics from the LLM (prompt tokens, completion tokens, total).
    /// </summary>
    public TokenUsage? Usage { get; init; }

    /// <summary>
    /// Creates a new ChatMessage with automatic timestamp in UTC.
    /// </summary>
    /// <param name="role">Message role.</param>
    /// <param name="content">Message content.</param>
    /// <returns>A new ChatMessage with Timestamp set to current UTC time.</returns>
    public static ChatMessage Create(ChatMessageRole role, string content)
    {
        return new ChatMessage
        {
            Role = role,
            Content = content,
            Timestamp = DateTime.UtcNow,
            ToolCalls = [],
            IsStreaming = false
        };
    }

    /// <summary>
    /// Creates a new user message.
    /// </summary>
    public static ChatMessage User(string content) => Create(ChatMessageRole.User, content);

    /// <summary>
    /// Creates a new assistant message.
    /// </summary>
    public static ChatMessage Assistant(string content) => Create(ChatMessageRole.Assistant, content);

    /// <summary>
    /// Creates a new system message.
    /// </summary>
    public static ChatMessage System(string content) => Create(ChatMessageRole.System, content);

    /// <summary>
    /// Marks this message as currently streaming.
    /// </summary>
    public ChatMessage AsStreaming() => this with { IsStreaming = true };

    /// <summary>
    /// Marks this message as complete (finished streaming).
    /// </summary>
    public ChatMessage AsComplete() => this with { IsStreaming = false };

    /// <summary>
    /// Adds a tool call to this message.
    /// </summary>
    public ChatMessage WithToolCall(ToolCall call)
    {
        var toolCalls = new List<ToolCall>(ToolCalls) { call };
        return this with { ToolCalls = toolCalls };
    }

    /// <summary>
    /// Sets token usage statistics for this message.
    /// </summary>
    public ChatMessage WithUsage(TokenUsage usage) => this with { Usage = usage };
}

/// <summary>
/// Token usage statistics from an LLM API call.
/// </summary>
public record TokenUsage
{
    /// <summary>
    /// Number of tokens in the prompt/input.
    /// </summary>
    public required int PromptTokens { get; init; }

    /// <summary>
    /// Number of tokens in the completion/response.
    /// </summary>
    public required int CompletionTokens { get; init; }

    /// <summary>
    /// Total tokens used (prompt + completion).
    /// </summary>
    public int TotalTokens => PromptTokens + CompletionTokens;
}
