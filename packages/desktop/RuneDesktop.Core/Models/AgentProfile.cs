namespace RuneDesktop.Core.Models;

/// <summary>
/// Represents the identity and configuration of a Rune Protocol agent.
/// Maps to the agent's blockchain identity and authentication credentials.
/// </summary>
public record AgentProfile
{
    /// <summary>
    /// Unique identifier for this agent (typically a UUID or account ID from the server).
    /// </summary>
    public required string AgentId { get; init; }

    /// <summary>
    /// Human-readable name for the agent.
    /// </summary>
    public required string Name { get; init; }

    /// <summary>
    /// ERC-8004 token ID representing this agent on the blockchain.
    /// </summary>
    public required string Erc8004TokenId { get; init; }

    /// <summary>
    /// Blockchain network identifier (e.g., "mainnet", "testnet", "avalanche", "ethereum").
    /// </summary>
    public required string Network { get; init; }

    /// <summary>
    /// Wallet address associated with this agent.
    /// </summary>
    public required string WalletAddress { get; init; }

    /// <summary>
    /// Timestamp when this profile was created (UTC).
    /// </summary>
    public DateTime CreatedAt { get; init; }

    /// <summary>
    /// Optional URL to the agent's avatar or profile image.
    /// </summary>
    public string? AvatarUrl { get; init; }

    /// <summary>
    /// Optional bio or description of the agent.
    /// </summary>
    public string? Bio { get; init; }

    /// <summary>
    /// Timestamp when this profile was last updated (UTC).
    /// </summary>
    public DateTime UpdatedAt { get; init; }

    /// <summary>
    /// Creates a new AgentProfile with automatic timestamps in UTC.
    /// </summary>
    /// <param name="agentId">Agent identifier.</param>
    /// <param name="name">Agent name.</param>
    /// <param name="erc8004TokenId">ERC-8004 token ID.</param>
    /// <param name="network">Blockchain network.</param>
    /// <param name="walletAddress">Wallet address.</param>
    /// <returns>A new AgentProfile with CreatedAt and UpdatedAt set to current UTC time.</returns>
    public static AgentProfile Create(
        string agentId,
        string name,
        string erc8004TokenId,
        string network,
        string walletAddress)
    {
        var now = DateTime.UtcNow;
        return new AgentProfile
        {
            AgentId = agentId,
            Name = name,
            Erc8004TokenId = erc8004TokenId,
            Network = network,
            WalletAddress = walletAddress,
            CreatedAt = now,
            UpdatedAt = now
        };
    }

    /// <summary>
    /// Updates this profile with new information.
    /// </summary>
    /// <param name="name">New name (optional).</param>
    /// <param name="avatarUrl">New avatar URL (optional).</param>
    /// <param name="bio">New bio (optional).</param>
    /// <returns>A new AgentProfile with updated fields and current UTC timestamp.</returns>
    public AgentProfile Update(string? name = null, string? avatarUrl = null, string? bio = null)
    {
        return this with
        {
            Name = name ?? Name,
            AvatarUrl = avatarUrl ?? AvatarUrl,
            Bio = bio ?? Bio,
            UpdatedAt = DateTime.UtcNow
        };
    }

    /// <summary>
    /// Gets a short identifier suitable for display (first 8 characters of AgentId).
    /// </summary>
    public string ShortId => AgentId.Length > 8 ? AgentId[..8] : AgentId;

    /// <summary>
    /// Gets a short wallet address for display (first 6 and last 4 characters).
    /// </summary>
    public string ShortWalletAddress =>
        WalletAddress.Length > 10
            ? $"{WalletAddress[..6]}...{WalletAddress[^4..]}"
            : WalletAddress;
}
