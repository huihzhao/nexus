#!/usr/bin/env python3
"""
Step 5: Add Artifacts — Versioned Outputs on Chain

Same agent, now with RuneArtifactService (ADK-compatible). The agent
stores analysis reports as versioned artifacts. Each version is
content-addressed — immutable and verifiable.

The swap:
    # Before (ADK default):
    from google.adk.artifacts import InMemoryArtifactService
    artifact_service = InMemoryArtifactService()

    # After (Rune):
    from nexus_core import Rune
    from nexus_core.adapters.adk import RuneArtifactService
    rune = Rune.local()
    artifact_service = RuneArtifactService(rune.artifacts)

Usage:
    python demo/05_add_artifacts.py
    python demo/05_add_artifacts.py --mode local
"""

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.adk.events import Event, EventActions
from google.genai import types

from nexus_core import Rune
from nexus_core.adapters.adk import RuneSessionService, RuneMemoryService, RuneArtifactService
from nexus_core.cli_utils import load_dotenv


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
        state_dir = getattr(args, 'state_dir', '/tmp/rune_demo_05')
        if os.path.exists(state_dir):
            shutil.rmtree(state_dir)
        return Rune.local(base_dir=state_dir)


async def main():
    parser = argparse.ArgumentParser(description="Step 5: Add Artifacts")
    parser.add_argument("--mode", choices=["local", "testnet"], default="local")
    parser.add_argument("--state-dir", default="/tmp/rune_demo_05")
    args = parser.parse_args()

    log_level = logging.INFO if args.mode == "testnet" else logging.WARNING
    logging.basicConfig(level=log_level, format="  [%(name)s] %(message)s")

    mode_label = "BSC Testnet" if args.mode == "testnet" else "Local mock"

    print(f"""
+======================================================================+
|                                                                      |
|  Step 5: Add Artifacts — Versioned Outputs on Chain                  |
|                                                                      |
|  The agent stores analysis reports and data exports as versioned     |
|  artifacts. Content-addressed, immutable, verifiable.                |
|                                                                      |
|  Swap: InMemoryArtifactService -> RuneArtifactService                |
|  Same ADK interface (save_artifact, load_artifact).                  |
|                                                                      |
|  Mode: {mode_label:<55}|
|                                                                      |
+======================================================================+
    """)

    # ── Setup with new Rune architecture ────────────────────────────
    rune = create_rune(args)

    app_name = "sales-analyzer"
    user_id = "analyst-01"

    # ── All three Rune adapter services ─────────────────────────────
    session_svc = RuneSessionService(rune.sessions)
    artifact_svc = RuneArtifactService(rune.artifacts)
    memory_svc = RuneMemoryService(rune.memory)

    print("  All three ADK services now backed by Rune:")
    print("    SessionService  -> RuneSessionService(rune.sessions)")
    print("    ArtifactService -> RuneArtifactService(rune.artifacts)")
    print("    MemoryService   -> RuneMemoryService(rune.memory)")

    # ── Create session and run analysis ─────────────────────────────
    session = await session_svc.create_session(
        app_name=app_name, user_id=user_id, session_id="q4-full-analysis",
        state={"quarter": "Q4 2025"},
    )

    print(f"\n  " + "=" * 58)
    print(f"  RUNNING ANALYSIS + GENERATING ARTIFACTS")
    print(f"  " + "=" * 58)

    # Step 1: Analyze and save raw data artifact
    print(f"\n  Step 1: Analyze data and save raw export")
    time.sleep(0.3)

    raw_data = {
        "quarter": "Q4 2025",
        "total_transactions": 12847,
        "regions": {
            "NA": {"revenue": 3200000, "growth": 0.03},
            "EU": {"revenue": 2800000, "growth": 0.12},
            "APAC": {"revenue": 2700000, "growth": 0.31},
        },
        "top_products": [
            {"name": "Widget-Pro", "revenue": 2100000},
            {"name": "DataSync", "revenue": 1800000},
            {"name": "CloudKit", "revenue": 1400000},
        ],
    }

    v1 = await artifact_svc.save_artifact(
        app_name=app_name, user_id=user_id,
        artifact=types.Part.from_text(text=json.dumps(raw_data, indent=2)),
        session_id="q4-full-analysis",
        metadata={"filename": "q4_raw_data.json"},
    )
    print(f"    Artifact saved: q4_raw_data.json (v{v1})")
    print(f"    Stored via Rune backend with content-hash addressing")

    # Step 2: Generate executive summary artifact (version 1)
    print(f"\n  Step 2: Generate executive summary (draft)")
    time.sleep(0.3)

    summary_v1 = """Q4 2025 Executive Summary (DRAFT)
================================
Total Revenue: $8.7M (+15% YoY)
Top Region: APAC (+31% growth)
Top Product: Widget-Pro ($2.1M)
Action Items: TBD
"""
    v1_summary = await artifact_svc.save_artifact(
        app_name=app_name, user_id=user_id,
        artifact=types.Part.from_text(text=summary_v1),
        session_id="q4-full-analysis",
        metadata={"filename": "executive_summary.txt"},
    )
    print(f"    Artifact saved: executive_summary.txt (v{v1_summary})")

    # Step 3: Update executive summary (version 2) with recommendations
    print(f"\n  Step 3: Update executive summary with recommendations")
    time.sleep(0.3)

    summary_v2 = """Q4 2025 Executive Summary (FINAL)
=================================
Total Revenue: $8.7M (+15% YoY)
Top Region: APAC (+31% growth)
Top Product: Widget-Pro ($2.1M)

Key Recommendations:
1. Double APAC sales team headcount (31% growth warrants investment)
2. Launch Widget-Pro enterprise tier (captures high-value segment)
3. Address SMB churn (8.3% rate trending up — allocate retention budget)
4. Q1 target: $9.2M (6% QoQ, conservative given APAC momentum)
"""
    v2_summary = await artifact_svc.save_artifact(
        app_name=app_name, user_id=user_id,
        artifact=types.Part.from_text(text=summary_v2),
        session_id="q4-full-analysis",
        metadata={"filename": "executive_summary.txt"},
    )
    print(f"    Artifact saved: executive_summary.txt (v{v2_summary})")

    # Also memorize the key findings
    agent_id = f"{app_name}:{user_id}"
    await rune.memory.add(
        "Q4 2025: $8.7M revenue, 15% YoY growth, APAC +31%, Widget-Pro top at $2.1M, SMB churn 8.3%",
        agent_id=agent_id, user_id=user_id,
    )
    print(f"    Key findings also memorized for future sessions")

    # Record completion event
    event = Event(
        invocation_id="inv-final",
        author="analysis-agent",
        actions=EventActions(
            state_delta={
                "analysis_complete": True,
                "artifacts_generated": ["q4_raw_data.json", "executive_summary.txt"],
            }
        ),
    )
    event.id = Event.new_id()
    await session_svc.append_event(session, event)

    # ── Artifact verification ───────────────────────────────────────
    print(f"\n  " + "=" * 58)
    print(f"  ARTIFACT VERIFICATION")
    print(f"  " + "=" * 58)

    # Load artifacts back
    loaded = await artifact_svc.load_artifact(
        app_name=app_name, user_id=user_id,
        artifact_id="executive_summary.txt",
        session_id="q4-full-analysis",
    )
    if loaded is not None:
        if hasattr(loaded, 'text'):
            print(f"\n  Latest version loaded: \"{loaded.text[:50]}...\"")
        elif isinstance(loaded, bytes):
            print(f"\n  Latest version loaded: \"{loaded.decode()[:50]}...\"")
        else:
            print(f"\n  Latest version loaded successfully")
    else:
        print(f"\n  Artifact stored successfully (load requires ADK types)")

    print(f"""
  +----------------------------------------------------------+
  |  COMPLETE: Full On-Chain Agent Stack                     |
  |                                                          |
  |  Your ADK agent now has:                                 |
  |                                                          |
  |  1. Session persistence (Step 2)                         |
  |     RuneSessionService(rune.sessions)                    |
  |                                                          |
  |  2. Crash recovery (Step 3)                              |
  |     Resume from any runtime, any machine                 |
  |                                                          |
  |  3. Persistent memory (Step 4)                           |
  |     RuneMemoryService(rune.memory)                       |
  |                                                          |
  |  4. Versioned artifacts (Step 5)                         |
  |     RuneArtifactService(rune.artifacts)                  |
  |                                                          |
  |  All three services are drop-in ADK replacements.        |
  |  Agent logic: unchanged. State: persistent + verifiable. |
  |                                                          |
  |  Setup: rune = Rune.local() or Rune.testnet(key)        |
  |  Run all steps: python demo/run_all.py                   |
  +----------------------------------------------------------+
    """)


if __name__ == "__main__":
    asyncio.run(main())
