"""EventLogCompactor — auto-compact event log into curated memory.

Periodically projects recent events into CuratedMemory when the event log
grows beyond a threshold. The projection result is appended to the EventLog
as a `memory_compact` event (immutable, syncs to Greenfield).

The projection function is injected — SDK doesn't depend on LLM.

Usage:
    from nexus_core.memory import EventLog, CuratedMemory, EventLogCompactor

    log = EventLog(base_dir=".", agent_id="my-agent")
    mem = CuratedMemory(base_dir=".")

    async def my_projection(query, budget):
        return await llm.complete(f"Summarize: {query}")

    compactor = EventLogCompactor(log, mem, projection_fn=my_projection)

    # Check every turn — only triggers when thresholds are met
    if compactor.should_compact(turn_count=20):
        await compactor.compact()
"""

from __future__ import annotations

import logging
from typing import Callable, Awaitable, Optional

from .event_log import EventLog
from .curated import CuratedMemory

logger = logging.getLogger(__name__)


class EventLogCompactor:
    """Auto-compact event log into curated memory.

    Trigger conditions (both must be true):
      - turn_count is a multiple of compact_interval
      - event log trajectory exceeds char_threshold * 0.8

    Projection result is appended to EventLog as a `memory_compact` event,
    ensuring it syncs to Greenfield with everything else.
    """

    def __init__(
        self,
        event_log: EventLog,
        curated_memory: CuratedMemory,
        projection_fn: Callable[..., Awaitable[str]] = None,
        compact_interval: int = 20,
        char_threshold: int = 30000,
    ):
        self._log = event_log
        self._mem = curated_memory
        self._project = projection_fn
        self._interval = compact_interval
        self._threshold = char_threshold
        self._last_compact_turn = 0

    def should_compact(self, turn_count: int) -> bool:
        """Check if compaction should trigger at this turn."""
        if not self._project:
            return False
        if turn_count <= 0 or turn_count % self._interval != 0:
            return False
        if turn_count <= self._last_compact_turn:
            return False

        trajectory = self._log.get_trajectory(max_chars=self._threshold)
        return len(trajectory) > self._threshold * 0.8

    async def compact(self, session_id: str = "") -> bool:
        """Execute compaction: project → EventLog + CuratedMemory.

        Returns True if compaction succeeded.
        """
        if not self._project:
            logger.warning("No projection function set — cannot compact")
            return False

        try:
            projection = await self._project(
                "Summarize the key facts, user preferences, and important context from recent events",
                2000,
            )
            if not projection:
                return False

            # 1. Append projection to EventLog (immutable, syncs to Greenfield)
            self._log.append(
                "memory_compact",
                projection,
                session_id=session_id,
                metadata={
                    "type": "auto_compact",
                    "event_count": self._log.count(),
                },
            )

            # 2. Update CuratedMemory (local derived view)
            self._update_curated(projection)

            self._last_compact_turn = self._log.count()
            logger.info("Compact: %d memory + %d user entries",
                        self._mem.memory_count, self._mem.user_count)
            return True

        except Exception as e:
            logger.warning("Compact failed: %s", e)
            return False

    def _update_curated(self, projection: str) -> None:
        """Parse projection text and update CuratedMemory."""
        user_keywords = [
            "user", "prefers", "likes", "style", "language", "tone",
            "用户", "偏好", "喜欢", "风格", "语言",
        ]
        for line in projection.split("\n"):
            line = line.strip().lstrip("- •·")
            if not line or len(line) < 10:
                continue
            if any(kw in line.lower() for kw in user_keywords):
                self._mem.add_user_info(line)
            else:
                self._mem.add_memory(line)

        self._mem.refresh_snapshot()

    @property
    def last_compact_turn(self) -> int:
        return self._last_compact_turn
