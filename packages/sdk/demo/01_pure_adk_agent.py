#!/usr/bin/env python3
"""
Step 1: Pure ADK Agent — No Rune, No Blockchain

This is a standard Google ADK agent running a 4-step data analysis pipeline.
Everything lives in memory — if the process dies, all state is lost.

This is your starting point. The next demos show how to add Rune
capabilities one step at a time, without changing your agent logic.

Usage:
    python demo/01_pure_adk_agent.py
"""

import asyncio
import time

# ── Standard Google ADK imports ─────────────────────────────────────
from google.adk.events import Event, EventActions
from google.adk.sessions import InMemorySessionService, Session

# ── Simulated Data Analysis Pipeline ────────────────────────────────
# In production, each step would call an LLM or external API.
# We use deterministic steps to keep the demo self-contained.

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
    """Run the complete 4-step analysis pipeline."""

    # Create a session to track this analysis run
    session = await session_service.create_session(
        app_name=app_name, user_id=user_id, session_id=session_id,
        state={"task": "Q4 Sales Analysis", "current_step": 0},
    )

    print(f"\n  Session created: {session.id}")
    print(f"  Backend: InMemorySessionService (ADK default)")
    print(f"  Persistence: NONE — data lives in process memory only\n")

    for i, step in enumerate(PIPELINE_STEPS):
        print(f"  Step {i + 1}/4: {step['name']}")
        print(f"    {step['description']}")
        time.sleep(0.3)  # simulate work
        print(f"    Result: {step['result']}")

        # Record this step as an ADK event
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
        print(f"    [ADK] Event saved to in-memory session\n")

    return session


async def main():
    print("""
+======================================================================+
|                                                                      |
|  Step 1: Pure ADK Agent                                              |
|                                                                      |
|  A standard Google ADK data analysis agent.                          |
|  Everything in memory — nothing persisted to disk or chain.          |
|                                                                      |
|  This is your baseline. Next steps add Rune capabilities.            |
|                                                                      |
+======================================================================+
    """)

    # ── Standard ADK setup ──────────────────────────────────────────
    session_service = InMemorySessionService()

    session = await run_analysis(
        session_service,
        app_name="sales-analyzer",
        user_id="analyst-01",
        session_id="analysis-q4-2025",
    )

    # ── Show final state ────────────────────────────────────────────
    print("  " + "=" * 60)
    print("  ANALYSIS COMPLETE")
    print("  " + "=" * 60)
    print(f"  Steps completed: {session.state.get('current_step', 0)}/4")
    print(f"  Session events:  {len(session.events)}")
    print()

    # ── The problem ─────────────────────────────────────────────────
    print("  " + "-" * 60)
    print("  THE PROBLEM:")
    print("  " + "-" * 60)
    print("  This session lives only in Python process memory.")
    print("  If the process crashes, all 4 steps of work are lost.")
    print("  There's no way to resume, verify, or share this state.")
    print()
    print("  Next: 02_add_persistence.py — swap ONE line to persist")
    print("  your agent's state to the blockchain.")
    print()


if __name__ == "__main__":
    asyncio.run(main())
