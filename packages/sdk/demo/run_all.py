#!/usr/bin/env python3
"""
Rune Protocol — Complete Tutorial

Runs all 7 demo steps in sequence, showing the progressive migration
from a pure ADK agent to a fully on-chain agent with persistence,
crash recovery, memory, artifacts, A2A coordination, and progressive
memory retrieval.

Usage:
    # Default: BSC testnet
    python demo/run_all.py

    # Quick local test (zero setup)
    python demo/run_all.py --mode local
"""

import argparse
import os
import subprocess
import sys
import time


DEMOS = [
    ("01_pure_adk_agent.py", "Pure ADK Agent (baseline)"),
    ("02_add_persistence.py", "Add On-Chain Persistence"),
    ("03_crash_recovery.py", "Crash Recovery"),
    ("04_add_memory.py", "Add Persistent Memory"),
    ("05_add_artifacts.py", "Add Versioned Artifacts"),
    ("06_agent_to_agent.py", "Agent-to-Agent (A2A) Coordination"),
    ("07_progressive_memory.py", "Progressive Memory Retrieval"),
]


def main():
    parser = argparse.ArgumentParser(
        description="Rune Protocol — Run all 7 demo steps"
    )
    parser.add_argument(
        "--mode", choices=["local", "testnet"], default="local",
        help="local = file-based mock (zero setup), testnet = real BSC + Greenfield",
    )
    parser.add_argument(
        "--step", type=int, default=0,
        help="Run only a specific step (1-6). 0 = run all.",
    )
    parser.add_argument(
        "--timeout", type=int, default=0,
        help="Timeout per step in seconds. 0 = auto (60 for local, 300 for testnet).",
    )
    args = parser.parse_args()

    # Auto-detect timeout based on mode
    step_timeout = args.timeout or (300 if args.mode == "testnet" else 60)

    mode_label = "BSC Testnet + Greenfield" if args.mode == "testnet" else "Local (file-based mock)"

    print(f"""
+======================================================================+
|                                                                      |
|  Rune Protocol — From ADK Agent to On-Chain Agent                    |
|                                                                      |
|  "Runtime Is Temporary, Identity Is Eternal."                        |
|                                                                      |
|  This tutorial shows how to migrate an existing Google ADK agent     |
|  to BNBChain, one step at a time. Each step changes only 1-2 lines  |
|  of code. Your agent logic stays exactly the same.                   |
|                                                                      |
|  Step 1: Pure ADK agent (baseline — nothing persisted)               |
|  Step 2: Swap SessionService (state persists to chain)               |
|  Step 3: Crash recovery (prove state survives runtime failure)       |
|  Step 4: Swap MemoryService (cross-session knowledge on chain)       |
|  Step 5: Swap ArtifactService (versioned outputs on chain)           |
|  Step 6: Agent-to-Agent (A2A) coordination on chain                  |
|  Step 7: Progressive memory retrieval (token-efficient recall)       |
|                                                                      |
|  Mode: {mode_label:<55}|
|                                                                      |
+======================================================================+
    """)

    demo_dir = os.path.dirname(os.path.abspath(__file__))

    # Filter to specific step if requested
    if args.step > 0:
        if args.step > len(DEMOS):
            print(f"  ERROR: --step must be 1-{len(DEMOS)}")
            sys.exit(1)
        demos_to_run = [DEMOS[args.step - 1]]
    else:
        demos_to_run = DEMOS

    passed = 0
    failed = 0

    for i, (filename, description) in enumerate(demos_to_run):
        step_num = args.step if args.step > 0 else i + 1
        filepath = os.path.join(demo_dir, filename)

        total_steps = len(DEMOS)
        print(f"\n{'=' * 70}")
        print(f"  STEP {step_num}/{total_steps}: {description}")
        print(f"  File: demo/{filename}")
        print(f"{'=' * 70}")

        # Build command — Step 1 has no --mode flag
        cmd = [sys.executable, filepath]
        if step_num > 1:
            cmd.extend(["--mode", args.mode])

        t0 = time.time()
        try:
            result = subprocess.run(
                cmd,
                cwd=os.path.dirname(demo_dir),
                timeout=step_timeout,
            )
            elapsed = time.time() - t0
            if result.returncode == 0:
                passed += 1
                print(f"\n  Step {step_num}: PASSED ({elapsed:.1f}s)")
            else:
                failed += 1
                print(f"\n  Step {step_num}: FAILED (exit code {result.returncode}, {elapsed:.1f}s)")
        except subprocess.TimeoutExpired:
            elapsed = time.time() - t0
            failed += 1
            print(f"\n  Step {step_num}: TIMEOUT (>{step_timeout}s, elapsed {elapsed:.1f}s)")
        except Exception as e:
            failed += 1
            print(f"\n  Step {step_num}: ERROR ({e})")

        if i < len(demos_to_run) - 1:
            time.sleep(0.5)

    # ── Summary ─────────────────────────────────────────────────────
    total = passed + failed
    print(f"""

{'=' * 70}
  TUTORIAL COMPLETE
{'=' * 70}

  Results: {passed}/{total} steps passed{"" if failed == 0 else f" ({failed} failed)"}

  What you learned:
    Step 1: A standard ADK agent (baseline)
    Step 2: Swap SessionService → state persists to BNBChain
    Step 3: Crash recovery → agent survives runtime failure
    Step 4: Swap MemoryService → cross-session knowledge on chain
    Step 5: Swap ArtifactService → versioned outputs on chain
    Step 6: Agent-to-Agent (A2A) → multi-agent coordination on chain
    Step 7: Progressive retrieval → 3-layer token-efficient memory recall

  The key insight: migrating an ADK agent to BNBChain requires
  changing only the service initialization (1-2 lines per service).
  Your agent logic stays exactly the same.

  For real BSC testnet:
    1. Get tBNB from https://www.bnbchain.org/en/testnet-faucet
    2. Set RUNE_PRIVATE_KEY in .env
    3. Deploy contracts: cd contracts && npx hardhat run scripts/deploy.js --network bscTestnet
    4. Run: python demo/run_all.py --mode testnet
    """)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
