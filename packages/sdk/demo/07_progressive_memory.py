#!/usr/bin/env python3
"""
Step 7: Progressive Memory Retrieval — Token-Efficient Recall

Demonstrates the 3-layer progressive memory retrieval API inspired by
claude-mem. Instead of dumping all search results into LLM context,
Rune returns lightweight summaries first, lets you (or your LLM) pick
the relevant ones, then fetches full content for selected IDs only.

This demo shows:
  1. Store 20 diverse memories (simulating weeks of agent activity)
  2. Traditional search() → returns all full entries (~10K tokens)
  3. Progressive retrieval → compact summaries (~1K) → select → full (~1.5K)
  4. Side-by-side token comparison

The API:
    # Layer 1: Lightweight summaries (~50-100 tokens each)
    compacts = await rune.memory.search_compact("query", agent_id, top_k=20)
    # → [MemoryCompact(memory_id, preview, category, importance, score)]

    # Layer 2: Your agent / LLM selects relevant IDs
    selected = [c.memory_id for c in compacts if c.importance >= 4]

    # Layer 3: Full content for selected only
    full = await rune.memory.get_by_ids(selected, agent_id)

Usage:
    python demo/07_progressive_memory.py
    python demo/07_progressive_memory.py --mode local
"""

import argparse
import asyncio
import logging
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nexus_core
from nexus_core.cli_utils import load_dotenv

# ── Simulated agent memories (accumulated over weeks of use) ────────

MEMORIES = [
    # Food preferences (detailed, paragraph-length — realistic for extracted insights)
    ("User's favorite cuisine is Japanese, especially sushi and ramen. They frequently order salmon nigiri and spicy tuna rolls. For ramen, they prefer tonkotsu style with extra chashu pork. They mentioned dining at Tsukiji Outer Market during their last Japan trip and wanting to return.", "preference", 5),
    ("User is allergic to shellfish including shrimp, crab, and lobster. This is a critical safety constraint — any restaurant recommendations must exclude shellfish-heavy options. They carry an EpiPen. Cross-contamination is also a concern so they avoid seafood buffets entirely.", "preference", 5),
    ("User prefers spicy food, particularly Sichuan cuisine with numbing peppercorn flavors. They rate dishes on a 1-10 spice scale and usually order level 7-8. Favorites include mapo tofu, dan dan noodles, and water-boiled fish. They also enjoy Korean kimchi jjigae and Thai green curry.", "preference", 4),
    ("User dislikes overly sweet desserts and avoids most Western-style cakes. They prefer Japanese wagashi, matcha-flavored desserts, or fresh fruit. They mentioned that Italian tiramisu is the one exception to their no-sweet rule.", "preference", 3),
    # Travel
    ("User is planning a Tokyo trip for March 2026, targeting the cherry blossom season (late March to early April). They want a 10-day itinerary covering Shinjuku, Shibuya, Akihabara, Asakusa, and day trips to Kamakura and Hakone. Budget is approximately $3000 excluding flights.", "fact", 4),
    ("User prefers window seats on flights and always requests extra legroom. They are a Gold member on ANA and have accumulated 45,000 miles. For long-haul flights they bring noise-canceling headphones and prefer overnight departures to maximize sleep.", "preference", 2),
    ("User has Global Entry and TSA PreCheck for US customs which expedites international arrivals. They also have a NEXUS card for Canada-US travel. Their passport expires in September 2027 so it's valid for the planned trips.", "fact", 2),
    ("User visited Seoul last summer for two weeks and particularly loved the Gangnam and Itaewon districts. They stayed at a hanok guesthouse in Bukchon and said it was the highlight of the trip. They want to return for the Jinhae Cherry Blossom Festival in April.", "fact", 3),
    # Work
    ("User works at BNB Chain as a senior blockchain developer focused on smart contract infrastructure. They have been with the team for 2 years and previously worked at Ethereum Foundation on EIP research. Their current project involves agent-to-agent communication protocols.", "fact", 4),
    ("User's development team primarily uses Python for backend services and tooling, Solidity for smart contracts, and TypeScript for frontend dashboards. They follow a trunk-based development workflow with automated CI/CD via GitHub Actions and deploy to BSC testnet weekly.", "fact", 3),
    ("User prefers VSCode with Vim keybindings and has a custom configuration with 15+ extensions including Solidity syntax highlighting, GitHub Copilot, and a custom BNB Chain snippets pack. They also use tmux for terminal multiplexing and zsh with starship prompt.", "preference", 2),
    ("User is actively researching AI agent frameworks including Google ADK, LangGraph, CrewAI, and AutoGen for a new project that will integrate AI agents with blockchain state management. They have prototyped with ADK and LangGraph and prefer ADK's type safety.", "fact", 4),
    # Health & fitness
    ("User runs 5K every morning at 6:30 AM before work, following a structured training plan that increases distance by 10% per week. They use a Garmin Forerunner 265 for tracking and their current average pace is 5:15/km. They run rain or shine and only skip for injury.", "fact", 3),
    ("User is training for a half marathon in June 2026 — the San Francisco Half Marathon. Their target finish time is under 1:50. They are following Hal Higdon's Intermediate 1 plan and currently doing long runs of 15km on weekends. They have a running coach who reviews their Garmin data.", "fact", 3),
    ("User tracks macros daily using MyFitnessPal and follows a high-protein diet targeting 150g protein per day. They meal prep on Sundays, usually grilled chicken, brown rice, and roasted vegetables. Post-run recovery shake is whey protein with banana and almond butter.", "preference", 2),
    # Entertainment
    ("User is an avid sci-fi reader with a particular love for Liu Cixin's Three-Body Problem trilogy. They have read the entire Remembrance of Earth's Past series twice in both English and Chinese. Other favorites include Asimov's Foundation, Dune by Herbert, and Project Hail Mary by Andy Weir.", "preference", 3),
    ("User is currently watching Shogun on FX and considers it the best TV show of 2024. They watch 2-3 episodes per week, usually on Friday evenings. They also follow The Bear, Severance, and are waiting for the next season of House of the Dragon.", "fact", 2),
    ("User plays chess online on chess.com with a rapid rating of approximately 1500 and a blitz rating of 1350. They play 2-3 games daily during lunch break. They are studying the Sicilian Defense (Najdorf variation) and follow GothamChess and Daniel Naroditsky tutorials.", "fact", 2),
    # Finance
    ("User is dollar-cost averaging into ETH and BNB with weekly buys of $200 each through Binance. Their total crypto portfolio is approximately $45,000 split across ETH (40%), BNB (35%), BTC (15%), and altcoins (10%). They use a Ledger Nano X for cold storage.", "fact", 3),
    ("User's monthly budget for dining out is around $500, split between work lunches ($200) and weekend dinners ($300). They track expenses in a spreadsheet and typically overspend in months with travel. Their favorite splurge restaurants are omakase sushi places in the $150-200 range.", "fact", 2),
]


def estimate_tokens(text: str) -> int:
    """Rough token estimate (1 token ≈ 4 chars for English)."""
    return len(text) // 4


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
        state_dir = getattr(args, 'state_dir', '/tmp/rune_demo_07')
        if os.path.exists(state_dir):
            shutil.rmtree(state_dir)
        return nexus_core.local(base_dir=state_dir)


async def main():
    parser = argparse.ArgumentParser(description="Step 7: Progressive Memory Retrieval")
    parser.add_argument("--mode", choices=["local", "testnet"], default="local")
    parser.add_argument("--state-dir", default="/tmp/rune_demo_07")
    args = parser.parse_args()

    log_level = logging.INFO if args.mode == "testnet" else logging.WARNING
    logging.basicConfig(level=log_level, format="  [%(name)s] %(message)s")

    mode_label = "BSC Testnet" if args.mode == "testnet" else "Local mock"

    print(f"""
+======================================================================+
|                                                                      |
|  Step 7: Progressive Memory Retrieval                                |
|                                                                      |
|  Traditional: search() returns full entries → wastes tokens          |
|  Progressive: search_compact() → select → get_by_ids()              |
|                                                                      |
|  Inspired by claude-mem's 3-layer architecture.                      |
|  Saves ~80% of tokens on memory retrieval.                           |
|                                                                      |
|  Mode: {mode_label:<55}|
|                                                                      |
+======================================================================+
    """)

    rune = create_rune(args)
    agent_id = "demo-agent"

    # ── Phase 1: Populate memories ─────────────────────────────────
    print("  " + "=" * 58)
    print("  PHASE 1: Storing 20 memories (simulating weeks of activity)")
    print("  " + "=" * 58)

    for i, (content, category, importance) in enumerate(MEMORIES):
        await rune.memory.add(
            content=content,
            agent_id=agent_id,
            metadata={"category": category, "importance": importance},
        )
        if (i + 1) % 5 == 0:
            print(f"    Stored {i + 1}/20 memories...")

    print(f"    Done. {len(MEMORIES)} memories in Rune backend.\n")

    # ── Phase 2: Traditional search ────────────────────────────────
    print("  " + "=" * 58)
    print("  PHASE 2: Traditional search() — full entries")
    print("  " + "=" * 58)

    query = "sushi restaurant recommendations for Tokyo trip"
    print(f'\n  Query: "{query}"')

    t0 = time.time()
    traditional = await rune.memory.search(query, agent_id=agent_id, top_k=20)
    t_trad = time.time() - t0

    total_tokens_trad = 0
    print(f"\n  Results ({len(traditional)} full entries — ALL sent to LLM):")
    for i, entry in enumerate(traditional):
        tokens = estimate_tokens(entry.content)
        total_tokens_trad += tokens
        marker = "***" if entry.metadata.get("importance", 0) >= 4 else "   "
        print(f"    {marker} [{entry.metadata.get('category', '?'):>10}] "
              f"imp={entry.metadata.get('importance', '?')} "
              f"({tokens:>3} tok) {entry.content[:60]}...")

    print(f"\n  Total tokens sent to LLM: ~{total_tokens_trad} tokens")
    print(f"  Time: {t_trad*1000:.0f}ms")

    # ── Phase 3: Progressive retrieval ─────────────────────────────
    print(f"\n\n  " + "=" * 58)
    print("  PHASE 3: Progressive Retrieval — 3 layers")
    print("  " + "=" * 58)

    # Layer 1: Compact summaries
    print(f"\n  Layer 1: search_compact() → lightweight summaries")
    t0 = time.time()
    compacts = await rune.memory.search_compact(query, agent_id=agent_id, top_k=20)
    t_compact = time.time() - t0

    total_tokens_compact = 0
    print(f"  Got {len(compacts)} compact summaries:")
    for i, c in enumerate(compacts):
        # Compact tokens: just preview + metadata (no full content, no UUID in prompt)
        tokens = estimate_tokens(f"{c.preview} [{c.category}, imp={c.importance}]")
        total_tokens_compact += tokens
        print(f"    [{c.category:>10}] imp={c.importance} score={c.score:.2f}  "
              f"{c.preview[:55]}...")

    print(f"\n  Compact index: ~{total_tokens_compact} tokens ({t_compact*1000:.0f}ms)")

    # Layer 2: Select relevant (simulating what an LLM would do)
    print(f"\n  Layer 2: LLM selects relevant memories")
    print(f"  (Simulating: pick food + travel + work memories, importance >= 3)")

    selected_ids = []
    for c in compacts:
        # Simple heuristic selection (in production, your LLM does this)
        relevant_categories = {"preference", "fact"}
        relevant_keywords = {"sushi", "tokyo", "japanese", "food", "trip", "travel", "bnb"}
        preview_lower = c.preview.lower()
        is_relevant = (
            c.category in relevant_categories
            and c.importance >= 3
            and any(kw in preview_lower for kw in relevant_keywords)
        )
        if is_relevant:
            selected_ids.append(c.memory_id)
            print(f"    ✓ Selected: {c.preview[:60]}...")

    print(f"\n  Selected {len(selected_ids)}/{len(compacts)} memories")

    # Layer 3: Fetch full content for selected only
    print(f"\n  Layer 3: get_by_ids() → full content for {len(selected_ids)} entries")
    t0 = time.time()
    full_entries = await rune.memory.get_by_ids(selected_ids, agent_id=agent_id)
    t_full = time.time() - t0

    total_tokens_full = 0
    for entry in full_entries:
        tokens = estimate_tokens(entry.content)
        total_tokens_full += tokens
        print(f"    [{entry.metadata.get('category', '?'):>10}] "
              f"imp={entry.metadata.get('importance', '?')} "
              f"({tokens:>3} tok) {entry.content}")

    total_progressive = total_tokens_compact + total_tokens_full
    print(f"\n  Full entries: ~{total_tokens_full} tokens ({t_full*1000:.0f}ms)")

    # ── Phase 4: Comparison ────────────────────────────────────────
    print(f"\n\n  " + "=" * 58)
    print("  COMPARISON: Traditional vs Progressive")
    print("  " + "=" * 58)

    savings = (1 - total_progressive / max(total_tokens_trad, 1)) * 100

    print(f"""
  ┌──────────────────────────────────────────────────────────┐
  │  Approach          │  Tokens to LLM  │  Entries fetched  │
  ├──────────────────────────────────────────────────────────┤
  │  Traditional       │  ~{total_tokens_trad:<14}│  {len(traditional):<18}│
  │  search()          │  (all full)      │  (all full)        │
  ├──────────────────────────────────────────────────────────┤
  │  Progressive       │  ~{total_progressive:<14}│  {len(compacts)} compact           │
  │  search_compact()  │  ({total_tokens_compact} index       │  + {len(full_entries)} full            │
  │  + get_by_ids()    │   + {total_tokens_full} selected)  │  = {len(full_entries)} relevant        │
  └──────────────────────────────────────────────────────────┘

  Token savings: ~{savings:.0f}%
  Relevant entries: {len(full_entries)} (vs {len(traditional)} unfiltered)

  The LLM gets ONLY the memories that matter, using a fraction
  of the token budget. This matters at scale — agents with 100s
  or 1000s of memories save significantly.
    """)

    # ── Summary ────────────────────────────────────────────────────
    print(f"""
  +----------------------------------------------------------+
  |  HOW IT WORKS                                            |
  |                                                          |
  |  # Layer 1: Lightweight summaries                        |
  |  compacts = await rune.memory.search_compact(            |
  |      "query", agent_id, top_k=20                         |
  |  )                                                       |
  |  # → MemoryCompact: id, preview, category, importance    |
  |                                                          |
  |  # Layer 2: LLM picks relevant IDs                       |
  |  selected = llm_select(compacts)                         |
  |                                                          |
  |  # Layer 3: Full content for selected only               |
  |  full = await rune.memory.get_by_ids(selected, agent_id) |
  |                                                          |
  |  Next: Use this in your agent for token-efficient recall  |
  +----------------------------------------------------------+
    """)


if __name__ == "__main__":
    asyncio.run(main())
