"""ProjectionMemory — task-conditioned projection over EventLog.

At decision time, takes the full event log and produces a structured memory view
via a single LLM call. No intermediate summarization, no mutable state.

Based on DPM (arXiv:2604.20158):
  π(E, T, B) → M
  E = event log, T = task/query, B = budget (chars)

The projection extracts:
  - FACTS: concrete verifiable claims with [event_index] citations
  - CONTEXT: relevant background for the current query
  - USER_PROFILE: known preferences and style
"""

from __future__ import annotations

import logging
from typing import Callable, Awaitable

from nexus_core.memory import EventLog

logger = logging.getLogger(__name__)

PROJECTION_PROMPT = """You are producing a memory view from an event log for the current conversation.

EVENTS:
{events}

CURRENT QUERY: {query}
BUDGET: {budget} characters maximum

Extract the most relevant information for answering the current query.
Output three sections in this exact order:

FACTS
- Key facts from the events, each citing [event_index]. Be specific.

CONTEXT
- Background context relevant to the current query.

USER_PROFILE
- Known user preferences, communication style, interests.

Stay within the budget. Prioritize facts that directly relate to the query.
If the log is empty or has no relevant content, output "No relevant context found."
"""


class ProjectionMemory:
    """Task-conditioned projection over an EventLog.

    Makes exactly ONE LLM call at decision time to extract relevant context.
    No background processing, no mutable state.
    """

    def __init__(self, event_log: EventLog, llm_fn: Callable[..., Awaitable[str]]):
        """
        Args:
            event_log: The append-only event log
            llm_fn: Async LLM completion function (prompt -> response)
        """
        self._log = event_log
        self._llm = llm_fn
        self._last_projection: str = ""
        self._last_query: str = ""

    async def project(self, query: str, budget: int = 3000,
                      session_id: str = None) -> str:
        """Project a memory view from the event log for the given query.

        This is the π(E, T, B) function from DPM.
        Makes exactly one LLM call.

        Args:
            query: The current user query / task
            budget: Maximum characters for the projection output
            session_id: Optional session filter

        Returns:
            Structured memory view (FACTS + CONTEXT + USER_PROFILE)
        """
        # Get trajectory from event log
        trajectory = self._log.get_trajectory(session_id=session_id, max_chars=80000)

        if not trajectory.strip():
            self._last_projection = ""
            return ""

        event_count = self._log.count(session_id=session_id)
        logger.info("Projecting memory: %d events, query='%s', budget=%d",
                     event_count, query[:50], budget)

        prompt = PROJECTION_PROMPT.format(
            events=trajectory,
            query=query,
            budget=budget,
        )

        try:
            result = await self._llm(prompt, temperature=0.0)
            self._last_projection = result.strip()
            self._last_query = query
            logger.info("Projection complete: %d chars", len(self._last_projection))
            return self._last_projection
        except Exception as e:
            logger.warning("Projection failed: %s", e)
            return ""

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        """Search the event log (FTS5) without LLM — for quick recall."""
        events = self._log.search(query, limit=limit)
        return [
            {"index": e.index, "type": e.event_type, "content": e.content[:200]}
            for e in events
        ]

    @property
    def last_projection(self) -> str:
        return self._last_projection

    @property
    def event_count(self) -> int:
        return self._log.count()
