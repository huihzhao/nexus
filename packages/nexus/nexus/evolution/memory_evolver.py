"""
MemoryEvolver — Extract insights from conversations and persist as memories.

Memory management strategy (inspired by Hermes Agent):
  - Bounded capacity: configurable hard limit (default 500 memories per agent)
  - Smart eviction: when capacity is reached, consolidate least-accessed memories
  - Consolidation: LLM merges similar low-value memories into summaries
  - Access tracking: SDK tracks access_count per memory for eviction decisions
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from nexus_core import RuneProvider
from nexus_core.utils import robust_json_parse as _sdk_robust_json_parse
from nexus_core.utils.json_parse import extract_balanced as _extract_balanced

logger = logging.getLogger(__name__)

# ── Consolidation Prompt ───────────────────────────────────────

CONSOLIDATION_PROMPT = """You are consolidating agent memories to save space.

These memories are the LEAST accessed and may contain overlapping or low-value information.
Merge them into fewer, higher-quality summary memories.

Memories to consolidate:
{memories}

Rules:
- Merge similar memories into concise summaries
- Drop truly trivial information
- Preserve any unique, important facts
- Each consolidated memory should be 1-2 sentences
- Aim to reduce count by at least 50%

Return a JSON array of consolidated memories:
[
  {{"content": "Consolidated insight", "category": "fact", "importance": 3}},
  ...
]

Return ONLY valid JSON array, no markdown fences."""


# ── JSON parsing: delegate to SDK shared utility ─────────────
# Kept as module-level name so existing `from .memory_evolver import _robust_json_parse`
# continues to work in skill_evolver, skill_evaluator, knowledge_compiler, and tests.
_robust_json_parse = _sdk_robust_json_parse


# Legacy: the following large block has been replaced by nexus_core.utils.robust_json_parse.
# The old code is removed; _robust_json_parse above is a re-export alias.
MEMORY_SELECT_PROMPT = """You are selecting relevant memories for a conversation.

Given a user query and a list of memory summaries, return the IDs of memories
that are relevant to the query. Be selective — only pick genuinely useful ones.

User query: {query}

Memory summaries:
{summaries}

Return ONLY a JSON array of memory_id strings, e.g. ["id1", "id2"].
If none are relevant, return [].
No markdown fences."""


EXTRACTION_PROMPT = """Analyze the following conversation and extract key insights.

Return a JSON array of memory objects. Each object has:
- "content": The insight as a concise statement (1-2 sentences)
- "category": One of "preference", "fact", "decision_pattern", "style", "skill", "relationship"
- "importance": 1-5 (5 = critical to remember)

Rules:
- Only extract genuinely new, meaningful insights
- Skip trivial/obvious information
- Prefer specific facts over vague observations
- Max {max_memories} items
- If nothing meaningful to extract, return an empty array []

Conversation:
{conversation}

Existing memories (avoid duplicates):
{existing_memories}

Return ONLY valid JSON array, no markdown fences."""


class MemoryEvolver:
    """Extracts and persists insights from conversations.

    Memory capacity management:
      - Hard limit (default 500) prevents unbounded growth
      - When capacity is reached, consolidate least-accessed memories
      - Consolidation uses LLM to merge similar low-value memories
      - Access tracking (from SDK) drives smart eviction decisions
    """

    # Capacity defaults (can be overridden via constructor)
    DEFAULT_MAX_MEMORIES = 500
    CONSOLIDATION_BATCH_SIZE = 20  # How many to consolidate at once
    CONSOLIDATION_TRIGGER_RATIO = 0.9  # Trigger at 90% capacity

    def __init__(
        self,
        rune: RuneProvider,
        agent_id: str,
        llm_fn: Any,
        max_memories: int = DEFAULT_MAX_MEMORIES,
    ):
        self.rune = rune
        self.agent_id = agent_id
        self.llm_fn = llm_fn
        self.max_memories = max_memories
        self._extraction_count = 0
        self._consolidation_count = 0

    async def extract_and_store(
        self,
        conversation: list[dict],
        max_memories: int = 5,
    ) -> list[dict]:
        existing = await self.rune.memory.list_all(self.agent_id)
        # Check ALL existing memories for dedup (not just last 20).
        # Previous limit of 20 caused duplicate extraction for older memories
        # and could suppress legitimate new memories that overlapped with
        # memories outside the window.
        existing_texts = [e.content for e in existing]

        convo_text = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Twin'}: {m['content']}"
            for m in conversation[-10:]
        )

        prompt = EXTRACTION_PROMPT.format(
            conversation=convo_text,
            existing_memories=json.dumps(existing_texts, ensure_ascii=False) if existing_texts else "[]",
            max_memories=max_memories,
        )

        try:
            raw = await self.llm_fn(prompt)
            logger.debug(
                "Memory extraction: LLM raw response (%d chars): %.200s%s",
                len(raw), raw, "..." if len(raw) > 200 else "",
            )
            memories = _robust_json_parse(raw)
        except (json.JSONDecodeError, Exception) as e:
            raw_preview = repr(raw[:300]) if 'raw' in dir() and raw else "<no response>"
            logger.warning(
                "Memory extraction failed: %s | LLM response (%s chars): %s",
                e, len(raw) if 'raw' in dir() and raw else 0, raw_preview,
            )
            return []

        if not isinstance(memories, list):
            logger.warning(
                "Memory extraction: expected list, got %s: %.100s",
                type(memories).__name__, repr(memories),
            )
            return []

        # Build batch for bulk_add — single index write instead of N
        batch = []
        for mem in memories:
            if not isinstance(mem, dict) or "content" not in mem:
                continue
            content = mem["content"]
            category = mem.get("category", "fact")
            importance = mem.get("importance", 3)
            batch.append({
                "content": content,
                "metadata": {
                    "category": category,
                    "importance": importance,
                    "source": "conversation_extraction",
                    "extraction_round": self._extraction_count,
                },
                "_category": category,
                "_importance": importance,
            })

        stored = []
        if batch:
            memory_ids = await self.rune.memory.bulk_add(
                entries=batch,
                agent_id=self.agent_id,
            )
            for mid, item in zip(memory_ids, batch):
                stored.append({
                    "memory_id": mid,
                    "content": item["content"],
                    "category": item["_category"],
                    "importance": item["_importance"],
                })
                logger.info(f"Stored memory [{item['_category']}]: {item['content'][:60]}...")

        self._extraction_count += 1

        # ── Capacity check: consolidate if approaching limit ──
        if stored:
            await self._check_and_consolidate()

        return stored

    # ── Memory Capacity Management ─────────────────────────────

    async def _check_and_consolidate(self) -> int:
        """Check memory count and consolidate if approaching capacity.

        Returns the number of memories freed (0 if no consolidation needed).
        Uses SDK primitives: count(), get_least_accessed(), bulk_delete(), add().
        """
        current_count = await self.rune.memory.count(self.agent_id)
        trigger_threshold = int(self.max_memories * self.CONSOLIDATION_TRIGGER_RATIO)

        if current_count < trigger_threshold:
            return 0

        logger.info(
            "Memory capacity %d/%d (%.0f%%) — triggering consolidation",
            current_count, self.max_memories,
            current_count / self.max_memories * 100,
        )

        # Get least-accessed memories as consolidation candidates
        candidates = await self.rune.memory.get_least_accessed(
            self.agent_id,
            limit=self.CONSOLIDATION_BATCH_SIZE,
        )

        if len(candidates) < 3:
            logger.info("Too few candidates for consolidation (%d)", len(candidates))
            return 0

        # Use LLM to consolidate
        freed = await self._consolidate_memories(candidates)
        self._consolidation_count += 1

        logger.info(
            "Consolidation #%d: %d memories → freed %d slots (now %d/%d)",
            self._consolidation_count,
            len(candidates),
            freed,
            current_count - freed,
            self.max_memories,
        )
        return freed

    async def _consolidate_memories(self, candidates: list) -> int:
        """Merge a batch of low-value memories into fewer summaries.

        SAFETY: Add consolidated summaries FIRST, then delete originals.
        If the LLM produces empty/invalid results, originals are preserved.
        This prevents memory loss from LLM failures during consolidation.

        Returns net memories freed (deleted - added).
        """
        memories_text = "\n".join(
            f"- [{m.metadata.get('category', 'unknown')}] "
            f"(accessed {m.access_count}x) {m.content}"
            for m in candidates
        )

        prompt = CONSOLIDATION_PROMPT.format(memories=memories_text)

        try:
            raw = await self.llm_fn(prompt)
            logger.debug(
                "Memory consolidation: LLM raw response (%d chars): %.200s%s",
                len(raw), raw, "..." if len(raw) > 200 else "",
            )
            consolidated = _robust_json_parse(raw)
        except Exception as e:
            raw_preview = repr(raw[:300]) if 'raw' in dir() and raw else "<no response>"
            logger.warning(
                "Memory consolidation LLM failed: %s | LLM response: %s", e, raw_preview,
            )
            # SAFE FALLBACK: do NOT delete anything — just log the failure.
            # Previous behavior deleted bottom half, which caused silent memory loss.
            return 0

        if not isinstance(consolidated, list):
            return 0

        # STEP 1: Add consolidated summaries FIRST (before any deletion)
        valid_summaries = [
            mem for mem in consolidated
            if isinstance(mem, dict) and mem.get("content")
        ]

        # Safety gate: require at least 1 valid summary before deleting originals.
        # Without this, LLM producing garbage JSON would delete all originals.
        if not valid_summaries:
            logger.warning(
                "Consolidation produced 0 valid summaries from %d candidates — aborting",
                len(candidates),
            )
            return 0

        # Use bulk_add for single index write (saves N-1 redundant index PUTs)
        batch = [
            {
                "content": mem["content"],
                "metadata": {
                    "category": mem.get("category", "consolidated"),
                    "importance": mem.get("importance", 3),
                    "source": "consolidation",
                    "consolidation_round": self._consolidation_count,
                    "merged_count": len(candidates),
                },
            }
            for mem in valid_summaries
        ]
        added_ids = await self.rune.memory.bulk_add(batch, self.agent_id)
        added = len(added_ids)

        # STEP 2: Only delete originals after summaries are stored
        original_ids = [m.memory_id for m in candidates]
        deleted = await self.rune.memory.bulk_delete(original_ids, self.agent_id)

        logger.info(
            "Consolidation: %d originals → %d summaries (freed %d slots)",
            len(candidates), added, deleted - added,
        )
        return deleted - added

    async def force_consolidate(self, batch_size: int = 0) -> int:
        """Manually trigger consolidation regardless of capacity.

        Useful for maintenance or testing.
        Returns net memories freed.
        """
        size = batch_size or self.CONSOLIDATION_BATCH_SIZE
        candidates = await self.rune.memory.get_least_accessed(
            self.agent_id, limit=size,
        )
        if len(candidates) < 3:
            return 0
        return await self._consolidate_memories(candidates)

    async def recall_relevant(self, query: str, top_k: int = 5) -> list[dict]:
        """
        Progressive memory retrieval (inspired by claude-mem's 3-layer arch):

          Layer 1: search_compact() → lightweight summaries (~50-100 tokens each)
          Layer 2: LLM selects relevant IDs from the compact index
          Layer 3: get_by_ids() → full content for selected memories only

        Falls back to direct search() if LLM selection fails.
        """
        # ── Layer 1: Get compact index ──
        compacts = await self.rune.memory.search_compact(
            query=query, agent_id=self.agent_id, top_k=top_k * 4,
        )

        if not compacts:
            # TF-IDF found no matching tokens (common for cross-language queries,
            # e.g. Chinese query against English-stored memories). Fall back to
            # returning the most recent memories so the LLM still has context.
            all_memories = await self.rune.memory.list_all(self.agent_id)
            if not all_memories:
                return []
            # Sort by creation time (newest first) and take top_k
            all_memories.sort(key=lambda m: m.created_at, reverse=True)
            return [
                {
                    "content": e.content,
                    "category": e.metadata.get("category", "unknown"),
                    "importance": e.metadata.get("importance", 3),
                    "score": 0.0,
                }
                for e in all_memories[:top_k]
            ]

        # Skip LLM selection unless there are significantly more results than
        # needed.  The LLM call adds 2-3s latency (Gemini API), which exceeds
        # the 2s timeout in get_context_for_query.  TF-IDF ranking from
        # search_compact is good enough for moderate result sets.
        if len(compacts) <= top_k * 3:
            entries = await self.rune.memory.get_by_ids(
                [c.memory_id for c in compacts], self.agent_id,
            )
            return [
                {
                    "content": e.content,
                    "category": e.metadata.get("category", "unknown"),
                    "importance": e.metadata.get("importance", 3),
                    "score": e.score,
                }
                for e in entries
            ]

        # ── Layer 2: LLM selects relevant memories ──
        summaries_text = "\n".join(
            f"- [{c.memory_id}] ({c.category}, importance={c.importance}) {c.preview}"
            for c in compacts
        )

        try:
            prompt = MEMORY_SELECT_PROMPT.format(
                query=query,
                summaries=summaries_text,
            )
            raw = await self.llm_fn(prompt)
            logger.debug(
                "Memory selection: LLM raw response (%d chars): %.200s%s",
                len(raw), raw, "..." if len(raw) > 200 else "",
            )
            selected_ids = _robust_json_parse(raw)
            if not isinstance(selected_ids, list):
                selected_ids = []
            # Limit to top_k
            selected_ids = selected_ids[:top_k]
        except Exception as e:
            raw_preview = repr(raw[:300]) if 'raw' in dir() and raw else "<no response>"
            logger.warning(
                "LLM memory selection failed, falling back: %s | LLM response: %s",
                e, raw_preview,
            )
            # Fallback: take top-scored compacts
            selected_ids = [c.memory_id for c in compacts[:top_k]]

        if not selected_ids:
            selected_ids = [c.memory_id for c in compacts[:top_k]]

        # ── Layer 3: Fetch full content ──
        entries = await self.rune.memory.get_by_ids(selected_ids, self.agent_id)
        return [
            {
                "content": e.content,
                "category": e.metadata.get("category", "unknown"),
                "importance": e.metadata.get("importance", 3),
                "score": e.score,
            }
            for e in entries
        ]

    async def get_stats(self) -> dict:
        count = await self.rune.memory.count(self.agent_id)
        all_memories = await self.rune.memory.list_all(self.agent_id)
        categories = {}
        for m in all_memories:
            cat = m.metadata.get("category", "unknown")
            categories[cat] = categories.get(cat, 0) + 1
        return {
            "total_memories": count,
            "max_memories": self.max_memories,
            "capacity_pct": round(count / self.max_memories * 100, 1) if self.max_memories > 0 else 0,
            "categories": categories,
            "extraction_rounds": self._extraction_count,
            "consolidation_rounds": self._consolidation_count,
        }
