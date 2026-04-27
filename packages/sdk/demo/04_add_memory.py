#!/usr/bin/env python3
"""
Step 4: Add Persistent Memory — Agent Learns Across Sessions

Same data analysis agent, now with RuneMemoryService (ADK-compatible).
The agent remembers insights from previous analyses and uses them
to inform new ones. Memory persists via Rune's provider layer.

This demo shows:
  1. Session 1: Analyze Q4 sales data, memorize key findings
  2. Session 2: Analyze Q1 forecast, recall Q4 insights for context
  3. Runtime crash + recovery: memories survive on new machine

The swap:
    # Before (ADK default):
    from google.adk.memory import InMemoryMemoryService
    memory_service = InMemoryMemoryService()

    # After (Rune):
    from nexus_core import Rune
    from nexus_core.adapters.adk import RuneMemoryService
    rune = nexus_core.local()
    memory_service = RuneMemoryService(rune.memory)

Usage:
    python demo/04_add_memory.py
    python demo/04_add_memory.py --mode local
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

import nexus_core
from nexus_core.adapters.adk import RuneSessionService, RuneMemoryService
from nexus_core.cli_utils import load_dotenv

# ── Two analysis sessions with different datasets ───────────────────

SESSION_1_STEPS = [
    {
        "name": "Q4 Revenue Analysis",
        "finding": "Q4 total revenue: $8.7M, up 15% YoY. Widget-Pro is the top product at $2.1M.",
    },
    {
        "name": "Regional Breakdown",
        "finding": "APAC grew 31% (fastest region). NA flat at 3%. EU steady at 12%.",
    },
    {
        "name": "Customer Segments",
        "finding": "Enterprise accounts drove 62% of revenue. SMB churn rate increased to 8.3%.",
    },
]

SESSION_2_STEPS = [
    {
        "name": "Q1 Forecast Preparation",
        "query": "Q4 revenue and regional growth trends",
        "finding": "Q1 forecast: $9.2M (6% QoQ growth) based on APAC momentum and new product launches.",
    },
    {
        "name": "Risk Assessment",
        "query": "customer churn and segment performance",
        "finding": "Key risk: SMB churn trending up. Recommendation: allocate 20% more retention budget to SMB.",
    },
]


async def run_session_1(session_svc, memory_svc, app_name, user_id):
    """Session 1: Analyze Q4 data and memorize findings."""
    print("\n  " + "=" * 58)
    print("  SESSION 1: Q4 Sales Analysis")
    print("  Agent will memorize key findings for future use.")
    print("  " + "=" * 58)

    session = await session_svc.create_session(
        app_name=app_name, user_id=user_id, session_id="q4-analysis",
        state={"quarter": "Q4 2025"},
    )

    for i, step in enumerate(SESSION_1_STEPS):
        print(f"\n  Step {i + 1}/3: {step['name']}")
        time.sleep(0.3)
        print(f"    Finding: {step['finding']}")

        # Record event
        event = Event(
            invocation_id=f"s1-inv-{i}",
            author="analysis-agent",
            actions=EventActions(
                state_delta={f"s1_step_{i}": step["finding"]},
            ),
        )
        event.id = Event.new_id()
        await session_svc.append_event(session, event)

        # Memorize the finding (via ADK-compatible interface)
        await memory_svc.add_session_to_memory(session)
        print(f"    [Rune] Finding memorized to chain")

    print(f"\n  Session 1 complete. Memories persisted via Rune.")


async def run_session_2(session_svc, memory_svc, app_name, user_id):
    """Session 2: Analyze Q1 forecast, recalling Q4 insights."""
    print("\n\n  " + "=" * 58)
    print("  SESSION 2: Q1 Forecast")
    print("  Agent recalls Q4 findings to inform the forecast.")
    print("  " + "=" * 58)

    session = await session_svc.create_session(
        app_name=app_name, user_id=user_id, session_id="q1-forecast",
        state={"quarter": "Q1 2026"},
    )

    for i, step in enumerate(SESSION_2_STEPS):
        print(f"\n  Step {i + 1}/2: {step['name']}")

        # Recall relevant memories from previous sessions
        print(f"    Recalling memories for: \"{step['query']}\"")
        results = await memory_svc.search_memory(
            app_name=app_name, user_id=user_id, query=step["query"],
        )

        # Handle both list results and SearchMemoryResponse
        memories = results if isinstance(results, list) else getattr(results, 'memories', [])
        if memories:
            print(f"    Recalled {len(memories)} relevant memories:")
            for mem in memories[:3]:
                if isinstance(mem, dict):
                    text = mem.get("content", "?")
                elif hasattr(mem, 'content') and hasattr(mem.content, 'parts'):
                    text = mem.content.parts[0].text if mem.content.parts else "?"
                else:
                    text = getattr(mem, 'content', str(mem))
                print(f"      - {str(text)[:70]}...")
        else:
            print(f"    No relevant memories found.")

        time.sleep(0.3)
        print(f"    Finding: {step['finding']}")

        event = Event(
            invocation_id=f"s2-inv-{i}",
            author="analysis-agent",
            actions=EventActions(
                state_delta={f"s2_step_{i}": step["finding"]},
            ),
        )
        event.id = Event.new_id()
        await session_svc.append_event(session, event)

    print(f"\n  Session 2 complete. Q4 insights informed Q1 forecast.")


async def demo_memory_survives_crash(rune, app_name, user_id):
    """Show that memory survives a simulated runtime crash."""
    print("\n\n  " + "=" * 58)
    print("  CRASH RECOVERY: Memory survives runtime failure")
    print("  " + "=" * 58)

    print("\n  Simulating runtime crash...")
    print("  (In reality: process dies, new machine spins up)")
    time.sleep(0.5)

    # Create a completely new memory service (simulates new machine)
    memory_svc_2 = RuneMemoryService(rune.memory)

    # Load memories from chain (cold start)
    agent_id = f"{app_name}:{user_id}"
    count = await rune.memory.load_from_chain(agent_id)
    print(f"\n  New runtime loaded {count} memories from Rune backend.")

    # Recall on the new machine
    results = await memory_svc_2.search_memory(
        app_name=app_name, user_id=user_id, query="revenue growth APAC",
    )

    memories = results if isinstance(results, list) else getattr(results, 'memories', [])
    print(f"  Search for 'revenue growth APAC' on new machine:")
    for mem in memories[:3]:
        if isinstance(mem, dict):
            text = mem.get("content", "?")
        elif hasattr(mem, 'content') and hasattr(mem.content, 'parts'):
            text = mem.content.parts[0].text if mem.content.parts else "?"
        else:
            text = getattr(mem, 'content', str(mem))
        print(f"    - {str(text)[:70]}...")

    if memories:
        print(f"\n  Memory survived the crash! Agent knowledge is preserved.")
    else:
        print(f"\n  (No matches for this query, but {count} memories were loaded)")


def create_rune(args):
    """Create Rune provider based on CLI mode."""
    if args.mode == "testnet":
        load_dotenv()
        private_key = os.environ.get("NEXUS_PRIVATE_KEY")
        if not private_key:
            print("  ERROR: NEXUS_PRIVATE_KEY required for testnet mode")
            sys.exit(1)
        return nexus_core.testnet(private_key=private_key)
    else:
        state_dir = getattr(args, 'state_dir', '/tmp/rune_demo_04')
        if os.path.exists(state_dir):
            shutil.rmtree(state_dir)
        return nexus_core.local(base_dir=state_dir)


async def main():
    parser = argparse.ArgumentParser(description="Step 4: Add Persistent Memory")
    parser.add_argument("--mode", choices=["local", "testnet"], default="local")
    parser.add_argument("--state-dir", default="/tmp/rune_demo_04")
    args = parser.parse_args()

    log_level = logging.INFO if args.mode == "testnet" else logging.WARNING
    logging.basicConfig(level=log_level, format="  [%(name)s] %(message)s")

    mode_label = "BSC Testnet" if args.mode == "testnet" else "Local mock"

    print(f"""
+======================================================================+
|                                                                      |
|  Step 4: Add Persistent Memory                                       |
|                                                                      |
|  The agent remembers insights across sessions and runtimes.          |
|                                                                      |
|  Swap: InMemoryMemoryService -> RuneMemoryService                    |
|  Same ADK interface (add_session_to_memory, search_memory).          |
|  Memories persist via Rune instead of evaporating on exit.           |
|                                                                      |
|  Mode: {mode_label:<55}|
|                                                                      |
+======================================================================+
    """)

    rune = create_rune(args)
    app_name = "sales-analyzer"
    user_id = "analyst-01"

    # ── Services (new Rune architecture) ────────────────────────────
    session_svc = RuneSessionService(rune.sessions)
    memory_svc = RuneMemoryService(rune.memory)

    # ── Session 1: Analyze and memorize ─────────────────────────────
    await run_session_1(session_svc, memory_svc, app_name, user_id)

    # ── Session 2: Recall and analyze ───────────────────────────────
    await run_session_2(session_svc, memory_svc, app_name, user_id)

    # ── Crash recovery ──────────────────────────────────────────────
    await demo_memory_survives_crash(rune, app_name, user_id)

    # ── Summary ─────────────────────────────────────────────────────
    print(f"""
  +----------------------------------------------------------+
  |  WHAT CHANGED (diff from Step 2):                        |
  |                                                          |
  |  + from nexus_core.adapters.adk import RuneMemorySvc |
  |                                                          |
  |  - memory_svc = InMemoryMemoryService()                  |
  |  + memory_svc = RuneMemoryService(rune.memory)           |
  |                                                          |
  |  Same ADK interface:                                     |
  |    add_session_to_memory() / search_memory()             |
  |                                                          |
  |  Now memories persist via Rune and survive crashes.       |
  |                                                          |
  |  Next: 05_add_artifacts.py — store versioned outputs     |
  +----------------------------------------------------------+
    """)


if __name__ == "__main__":
    asyncio.run(main())
