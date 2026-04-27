"""
Shared CLI utilities for Rune demo scripts.

Extracts the duplicated ``create_state_manager()`` and ``.env`` loading
logic into a single reusable module.
"""

import os
import sys
from pathlib import Path
from typing import Optional


def load_dotenv() -> None:
    """Load .env file from CWD or project root (same logic as the JS helper)."""
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]
    for p in candidates:
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                eq = line.find("=")
                if eq == -1:
                    continue
                key = line[:eq].strip()
                val = line[eq + 1:].strip()
                if key not in os.environ:
                    os.environ[key] = val
            return


def create_state_manager(args):
    """
    Create a StateManager based on CLI arguments.

    Supports ``--mode local`` (file-based mock) and ``--mode testnet``
    (real BSC + Greenfield).

    Args:
        args: argparse.Namespace with at least ``.mode`` and optionally
              ``.private_key``, ``.agent_state``, ``.task_manager``,
              ``.rpc_url``, ``.state_dir``.
    """
    from .state import StateManager

    if getattr(args, "mode", "local") == "testnet":
        env_network = os.environ.get("NEXUS_NETWORK", "bsc-testnet")
        net_prefix = "MAINNET" if "mainnet" in env_network else "TESTNET"

        private_key = getattr(args, "private_key", None) or os.environ.get("NEXUS_PRIVATE_KEY")
        agent_state = (
            getattr(args, "agent_state", None)
            or os.environ.get(f"NEXUS_{net_prefix}_AGENT_STATE_ADDRESS")
            or os.environ.get("NEXUS_AGENT_STATE_ADDRESS")
        )
        task_manager = (
            getattr(args, "task_manager", None)
            or os.environ.get(f"NEXUS_{net_prefix}_TASK_MANAGER_ADDRESS")
            or os.environ.get("NEXUS_TASK_MANAGER_ADDRESS")
        )
        rpc_url = (
            getattr(args, "rpc_url", None)
            or os.environ.get(f"NEXUS_{net_prefix}_RPC")
            or os.environ.get("NEXUS_BSC_RPC", "https://data-seed-prebsc-1-s1.bnbchain.org:8545")
        )

        if not private_key:
            print("  ERROR: --private-key or NEXUS_PRIVATE_KEY required for testnet mode")
            print("  Tip: Use Keystore to create a wallet first:")
            print('    from nexus_core.keystore import Keystore')
            print('    ks = Keystore(password="my-pass")')
            print('    print(ks.export_private_key())')
            sys.exit(1)

        if not agent_state:
            print("  ERROR: --agent-state or NEXUS_AGENT_STATE_ADDRESS required")
            print("  Deploy contracts first: cd contracts && npx hardhat run scripts/deploy.js --network bscTestnet")
            sys.exit(1)

        return StateManager(
            rpc_url=rpc_url,
            private_key=private_key,
            agent_state_address=agent_state,
            task_manager_address=task_manager,
        )
    else:
        state_dir = getattr(args, "state_dir", "/tmp/bnbchain_rune_state")
        return StateManager(base_dir=state_dir, mode="local")


def add_state_manager_args(parser) -> None:
    """Add common StateManager CLI arguments to an argparse parser."""
    parser.add_argument("--mode", choices=["local", "testnet"], default="local",
                        help="local = file-based mock, testnet = real BSC + Greenfield")
    parser.add_argument("--state-dir", default="/tmp/bnbchain_rune_state",
                        help="Base directory for local mode state storage")
    parser.add_argument("--private-key", help="Wallet private key (or set NEXUS_PRIVATE_KEY)")
    parser.add_argument("--agent-state", help="AgentStateExtension contract address")
    parser.add_argument("--task-manager", help="TaskStateManager contract address")
    parser.add_argument("--rpc-url", help="BSC RPC endpoint")
