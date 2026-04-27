// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title MockIdentityRegistry
 * @notice Minimal mock of ERC-8004 IdentityRegistry for local testing.
 *         Mirrors the real contract's interface (no exists(), no totalSupply()).
 *         In production, use the real BNBChain-deployed contract.
 */
contract MockIdentityRegistry {
    mapping(uint256 => address) private _owners;
    mapping(uint256 => address) private _wallets;

    function mint(address to, uint256 tokenId) external {
        require(_owners[tokenId] == address(0), "Already minted");
        _owners[tokenId] = to;
    }

    function ownerOf(uint256 tokenId) external view returns (address) {
        require(_owners[tokenId] != address(0), "ERC721: invalid token ID");
        return _owners[tokenId];
    }

    function balanceOf(address owner) external view returns (uint256) {
        uint256 count = 0;
        // Simple scan — fine for testing (not gas-efficient for production)
        for (uint256 i = 1; i <= 10000; i++) {
            if (_owners[i] == owner) count++;
        }
        return count;
    }

    function getAgentWallet(uint256 agentId) external view returns (address) {
        return _wallets[agentId];
    }

    function tokenURI(uint256 /* tokenId */) external pure returns (string memory) {
        return "";
    }
}
