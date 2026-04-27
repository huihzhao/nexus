// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title IIdentityRegistry
 * @notice Minimal interface for ERC-8004 Identity Registry (deployed by BNBChain).
 *         We only need the read methods to verify agent ownership.
 *         Full contract deployed at:
 *           BSC Testnet: 0x8004A818BFB912233c491871b3d84c89A494BD9e
 *           BSC Mainnet: 0xfA09B3397fAC75424422C4D28b1729E3D4f659D7
 *
 *         The real contract is an ERC-721 Upgradeable with 31 functions.
 *         We only reference the subset needed for permission checks.
 */
interface IIdentityRegistry {
    /// @notice Returns the owner of the agent NFT (ERC-721 ownerOf).
    ///         Reverts if tokenId does not exist — use this to check existence.
    function ownerOf(uint256 tokenId) external view returns (address);

    /// @notice Returns the number of tokens owned by an address
    function balanceOf(address owner) external view returns (uint256);

    /// @notice Returns the dedicated wallet address for an agent
    function getAgentWallet(uint256 agentId) external view returns (address);

    /// @notice Returns the agent URI (off-chain metadata file)
    function tokenURI(uint256 tokenId) external view returns (string memory);
}
