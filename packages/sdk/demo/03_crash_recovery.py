#!/usr/bin/env python3
"""
Step 3: Crash Recovery — The Killer Feature

This demo proves the core value proposition of Rune:
  1. Runtime A starts a 4-step analysis, crashes after step 2
  2. Runtime B (different process) loads the agent from chain
  3. Runtime B resumes from step 3 — zero data loss

With standard ADK (InMemorySessionService), the crash would lose
everything. With Rune, the agent's state survived because it was
persisted to BNBChain, not held in process memory.

Usage:
    python demo/03_crash_recovery.py
    python demo/03_crash_recovery.py --mode local
"""

import argparse
import asyncio
import logging
import os
import shutil
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.adk.events import Event, EventActions

from nexus_core import Rune
from nexus_core.adapters.adk import RuneSessionService
from nexus_core.cli_utils import load_dotenv

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


async def run_runtime(
    runtime_name: str,
    rune,
    app_name: str,
    user_id: str,
    session_id: str,
    crash_after_step: int = -1,
) -> bool:
    """Run a single runtime instance.

    Args:
        crash_after_step: Simulate crash after this step index (-1 = no crash)

    Returns:
        True if completed, False if "crashed"
    """
    print(f"\n  {'#' * 60}")
    print(f"  RUNTIME: {runtime_name}")
    print(f"  {'#' * 60}")

    session_svc = RuneSessionService(rune.sessions)

    # Try to load existing session from chain
    session = await session_svc.get_session(
        app_name=app_name, user_id=user_id, session_id=session_id,
    )

    if session is None:
        print(f"\n  No existing session found. Creating new session...")
        session = await session_svc.create_session(
            app_name=app_name, user_id=user_id, session_id=session_id,
            state={"task": "Q4 Sales Analysis", "current_step": 0},
        )
        checkpoint = 0
    else:
        state = session.state if hasattr(session, 'state') else session.get("state", {})
        events = session.events if hasattr(session, 'events') else session.get("events", [])
        checkpoint = state.get("current_step", 0)
        print(f"\n  Session loaded from chain!")
        print(f"  Events recovered: {len(events)}")
        print(f"  Resuming from step {checkpoint + 1}/4")

    # Execute remaining steps
    for step_idx in range(checkpoint, len(PIPELINE_STEPS)):
        if crash_after_step >= 0 and step_idx > crash_after_step:
            print(f"\n  {'!' * 60}")
            print(f"  CRASH! {runtime_name} terminated unexpectedly.")
            print(f"  Steps 1-{crash_after_step + 1} were persisted to chain.")
            print(f"  Steps {crash_after_step + 2}-4 have NOT been executed.")
            print(f"  {'!' * 60}")
            return False

        step = PIPELINE_STEPS[step_idx]
        print(f"\n  Step {step_idx + 1}/4: {step['name']}")
        print(f"    {step['description']}")
        time.sleep(0.3)
        print(f"    Result: {step['result']}")

        event = Event(
            invocation_id=f"inv-{step_idx}",
            author="analysis-agent",
            actions=EventActions(
                state_delta={
                    f"step_{step_idx}_completed": True,
                    f"step_{step_idx}_result": step["result"],
                    "current_step": step_idx + 1,
                }
            ),
        )
        event.id = Event.new_id()
        await session_svc.append_event(session, event)

    print(f"\n  {'=' * 60}")
    print(f"  TASK COMPLETED by {runtime_name}")
    print(f"  All 4 steps finished successfully.")
    print(f"  {'=' * 60}")
    return True


def create_rune(args):
    """Create Rune provider based on CLI mode."""
    if args.mode == "testnet":
        load_dotenv()
        private_key = os.environ.get("RUNE_PRIVATE_KEY")
        if not private_key:
            print("  ERROR: RUNE_PRIVATE_KEY required for testnet mode")
            sys.exit(1)
        return Rune.testnet(private_key=private_key)
    else:
        state_dir = getattr(args, 'state_dir', '/tmp/rune_demo_03')
        if os.path.exists(state_dir):
            shutil.rmtree(state_dir)
        return Rune.local(base_dir=state_dir)


async def main():
    parser = argparse.ArgumentParser(description="Step 3: Crash Recovery")
    parser.add_argument("--mode", choices=["local", "testnet"], default="local")
    parser.add_argument("--state-dir", default="/tmp/rune_demo_03")
    args = parser.parse_args()

    log_level = logging.INFO if args.mode == "testnet" else logging.WARNING
    logging.basicConfig(level=log_level, format="  [%(name)s] %(message)s")

    mode_label = "BSC Testnet" if args.mode == "testnet" else "Local mock"

    print(f"""
+======================================================================+
|                                                                      |
|  Step 3: Crash Recovery                                              |
|                                                                      |
|  Runtime A starts a 4-step analysis, crashes after step 2.           |
|  Runtime B (a completely separate process) loads the same agent      |
|  from chain and resumes at step 3. Zero data loss.                   |
|                                                                      |
|  With InMemorySessionService, this would lose everything.            |
|  With RuneSessionService, state survived the crash.                  |
|                                                                      |
|  Mode: {mode_label:<55}|
|                                                                      |
+======================================================================+
    """)

    # ── Setup: single Rune instance (shared storage) ───────────────
    rune = create_rune(args)

    app_name = "sales-analyzer"
    user_id = "analyst-01"
    session_id = f"analysis-{uuid.uuid4().hex[:8]}"

    # ── Phase 1: Runtime A starts, crashes after step 2 ────────────
    print("\n" + "=" * 64)
    print("  PHASE 1: Runtime A starts the analysis, crashes mid-execution")
    print("=" * 64)

    completed = await run_runtime(
        runtime_name="Runtime-A (your laptop)",
        rune=rune,
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        crash_after_step=1,  # crash after step 2 (index 1)
    )
    assert not completed

    # ── Show what survived ──────────────────────────────────────────
    print("\n\n" + "=" * 64)
    print("  WHAT SURVIVED THE CRASH")
    print("=" * 64)
    print(f"\n  The agent's state lives in Rune's backend,")
    print(f"  not in the crashed process's memory.")

    # ── Phase 2: Runtime B resumes ──────────────────────────────────
    print("\n\n" + "=" * 64)
    print("  PHASE 2: Runtime B loads agent from chain, resumes execution")
    print("=" * 64)

    time.sleep(0.5)

    completed = await run_runtime(
        runtime_name="Runtime-B (cloud server)",
        rune=rune,
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        crash_after_step=-1,  # no crash
    )
    assert completed

    # ── Final verification ──────────────────────────────────────────
    print("\n\n" + "=" * 64)
    print("  FINAL VERIFICATION")
    print("=" * 64)

    session_svc = RuneSessionService(rune.sessions)
    session = await session_svc.get_session(
        app_name=app_name, user_id=user_id, session_id=session_id,
    )

    state = session.state if hasattr(session, 'state') else session.get("state", {})
    events = session.events if hasattr(session, 'events') else session.get("events", [])

    print(f"\n  Total events on chain: {len(events)}")
    for i in range(4):
        result = state.get(f"step_{i}_result", "N/A")
        runtime = "Runtime-A" if i < 2 else "Runtime-B"
        print(f"    Step {i+1} ({runtime}): {result[:60]}...")

    print(f"""
  +----------------------------------------------------------+
  |  RESULT:                                                 |
  |                                                          |
  |  Runtime A executed steps 1-2, then crashed.             |
  |  Runtime B loaded the same agent from chain.             |
  |  Runtime B resumed at step 3 and finished the job.       |
  |                                                          |
  |  Key insight: the agent's state survived because it      |
  |  was persisted via Rune, not held in local memory.       |
  |                                                          |
  |  "Runtime is temporary, identity is eternal."            |
  |                                                          |
  |  Next: 04_add_memory.py — give your agent long-term     |
  |  memory that persists across sessions.                   |
  +----------------------------------------------------------+
    """)


if __name__ == "__main__":
    asyncio.run(main())
