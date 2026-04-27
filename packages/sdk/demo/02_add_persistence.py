#!/usr/bin/env python3
"""
Step 2: Add On-Chain Persistence — Swap One Line

Same data analysis agent as Step 1, but now sessions persist to
BNBChain (BSC + Greenfield). The only change: swap
InMemorySessionService → RuneSessionService (via Rune adapter).

Your agent code stays exactly the same. The state layer is now:
  - Greenfield: full session payloads (content-addressed)
  - BSC: 32-byte hash commitment (state_root)
  - WAL: local Write-Ahead Log for crash safety

Usage:
    # Quick local test (no wallet, no chain, zero setup)
    python demo/02_add_persistence.py --mode local

    # BSC testnet (requires .env with NEXUS_PRIVATE_KEY)
    python demo/02_add_persistence.py --mode testnet
"""

import argparse
import asyncio
import logging
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.adk.events import Event, EventActions

# ── THE CHANGE: new Rune imports ───────────────────────────────────
# Before (Step 1):
#     from google.adk.sessions import InMemorySessionService
# After (Step 2):
import nexus_core
from nexus_core.adapters.adk import RuneSessionService
from nexus_core.cli_utils import load_dotenv
# ────────────────────────────────────────────────────────────────────

# Same pipeline as Step 1 — zero changes to agent logic
PIPELINE_STEPS = [
    {
        "name": "Data Collection",
        "description": "Fetching sales transaction data from Q4 2025...",
        "result": "Collected 12,847 transactions across 3 regions (NA, EU, APAC)",
    },
    {
        "name": "Data Cleaning",
        "description": "Removing duplicates, handling missing values...",
        "result": "Cleaned dataset: 12,103 valid transactions, 744 removed (5.8% error rate)",
    },
    {
        "name": "Pattern Analysis",
        "description": "Identifying top-performing products and seasonal trends...",
        "result": "Top 3 products: Widget-Pro ($2.1M), DataSync ($1.8M), CloudKit ($1.4M). Q4 spike: +23% vs Q3",
    },
    {
        "name": "Report Generation",
        "description": "Compiling executive summary with key findings...",
        "result": "Report complete: $8.7M total revenue, 15% YoY growth, APAC fastest growing (+31%)",
    },
]


async def run_analysis(session_service, app_name, user_id, session_id):
    """Run the complete 4-step analysis pipeline.

    This function is IDENTICAL to Step 1. No changes needed.
    The persistence is handled by the session service layer.
    """
    session = await session_service.create_session(
        app_name=app_name, user_id=user_id, session_id=session_id,
        state={"task": "Q4 Sales Analysis", "current_step": 0},
    )

    print(f"\n  Session created: {session['id'] if isinstance(session, dict) else session.id}")
    print(f"  Backend: Rune (LocalBackend / ChainBackend)")
    print(f"  Persistence: Every event → Provider → Backend → chain\n")

    for i, step in enumerate(PIPELINE_STEPS):
        print(f"  Step {i + 1}/4: {step['name']}")
        print(f"    {step['description']}")
        time.sleep(0.3)
        print(f"    Result: {step['result']}")

        event = Event(
            invocation_id=f"inv-{i}",
            author="analysis-agent",
            actions=EventActions(
                state_delta={
                    f"step_{i}_completed": True,
                    f"step_{i}_result": step["result"],
                    "current_step": i + 1,
                }
            ),
        )
        event.id = Event.new_id()
        await session_service.append_event(session, event)
        print(f"    [Rune] Event persisted to chain\n")

    return session


def create_rune(args):
    """Create a Rune provider based on CLI mode."""
    if args.mode == "testnet":
        load_dotenv()
        private_key = os.environ.get("NEXUS_PRIVATE_KEY")
        if not private_key:
            print("  ERROR: NEXUS_PRIVATE_KEY required for testnet mode")
            sys.exit(1)
        return nexus_core.testnet(private_key=private_key)
    else:
        state_dir = getattr(args, 'state_dir', '/tmp/rune_demo_02')
        if os.path.exists(state_dir):
            shutil.rmtree(state_dir)
        return nexus_core.local(base_dir=state_dir)


async def main():
    parser = argparse.ArgumentParser(description="Step 2: Add On-Chain Persistence")
    parser.add_argument("--mode", choices=["local", "testnet"], default="local")
    parser.add_argument("--state-dir", default="/tmp/rune_demo_02")
    args = parser.parse_args()

    # Enable chain logging so on-chain info is visible
    log_level = logging.INFO if args.mode == "testnet" else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="  [%(name)s] %(message)s",
    )

    mode_label = "BSC Testnet + Greenfield" if args.mode == "testnet" else "Local (file-based)"

    print(f"""
+======================================================================+
|                                                                      |
|  Step 2: Add On-Chain Persistence                                    |
|                                                                      |
|  Same agent as Step 1, but sessions now persist to BNBChain.         |
|  The only code change: swap InMemorySessionService                   |
|  for RuneSessionService (via Rune adapter).                          |
|                                                                      |
|  Mode: {mode_label:<55}|
|                                                                      |
+======================================================================+
    """)

    # ── THE CHANGE: three lines replace one ────────────────────────
    # Before (Step 1):
    #     session_service = InMemorySessionService()
    # After (Step 2):
    rune = create_rune(args)
    session_service = RuneSessionService(rune.sessions)
    # ────────────────────────────────────────────────────────────────

    session = await run_analysis(
        session_service,
        app_name="sales-analyzer",
        user_id="analyst-01",
        session_id="analysis-q4-2025",
    )

    # ── Verify persistence ──────────────────────────────────────────
    print("  " + "=" * 60)
    print("  PERSISTENCE VERIFICATION")
    print("  " + "=" * 60)

    # Reload session from chain (simulating a fresh process)
    reloaded = await session_service.get_session(
        app_name="sales-analyzer", user_id="analyst-01",
        session_id="analysis-q4-2025",
    )
    if isinstance(reloaded, dict):
        events = reloaded.get("events", [])
        state = reloaded.get("state", {})
    else:
        events = reloaded.events if reloaded else []
        state = reloaded.state if reloaded else {}

    print(f"\n  Session reloaded from chain:")
    print(f"    Events recovered: {len(events)}")
    print(f"    State keys:       {list(state.keys())}")
    print(f"    Current step:     {state.get('current_step', 0)}/4")

    print(f"""
  +----------------------------------------------------------+
  |  WHAT CHANGED (diff from Step 1):                        |
  |                                                          |
  |  - from google.adk.sessions import InMemorySessionService|
  |  + from nexus_core import Rune                       |
  |  + from nexus_core.adapters.adk import RuneSessionSvc|
  |                                                          |
  |  - session_svc = InMemorySessionService()                |
  |  + rune = nexus_core.local()                                   |
  |  + session_svc = RuneSessionService(rune.sessions)       |
  |                                                          |
  |  Agent logic: ZERO changes.                              |
  |  State now survives process death.                       |
  |                                                          |
  |  Next: 03_crash_recovery.py — prove it survives a crash  |
  +----------------------------------------------------------+
    """)


if __name__ == "__main__":
    asyncio.run(main())
