"""
CrewAI Adapter — bridges CrewAI's memory system to Rune Providers.

CrewAI uses a cognitive memory model with encode, recall, consolidate,
and forget operations. This adapter maps those to Rune's RuneMemoryProvider
for persistent, verifiable memory on BNBChain.

Usage:
    from nexus_core.rune_providers import create_provider
    from nexus_core.adapters.crewai import RuneCrewStorage

    rune = create_provider(mode="local")
    storage = RuneCrewStorage(rune.memory, agent_id="my-crew-agent")

    # Use with CrewAI
    from crewai.memory import Memory
    memory = Memory(storage=storage)

CrewAI interface mapping:
    CrewAI                       Rune
    ─────────────────────────    ──────────────────────────
    memory.encode(content)   →   memory.add()
    memory.recall(query)     →   memory.search()
    memory.forget(id)        →   memory.delete()
    memory.consolidate()     →   (future: merge similar memories)
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from ..core.providers import RuneMemoryProvider
from .registry import AdapterRegistry


class RuneCrewStorage:
    """
    CrewAI-compatible memory storage backed by Rune.

    CrewAI's Memory class delegates storage operations to a pluggable
    storage backend. This class implements that interface using Rune's
    RuneMemoryProvider for on-chain persistence.

    Architecture:
        CrewAI Memory.encode()
            → RuneCrewStorage.save()
                → RuneMemoryProvider.add()
                    → Greenfield (full content) + BSC (memory root hash)

    All memories persist across crew runs, machines, and runtimes.
    """

    def __init__(
        self,
        memory_provider: RuneMemoryProvider,
        agent_id: str = "crewai-agent",
    ):
        """
        Args:
            memory_provider: Rune memory provider for persistence.
            agent_id: Agent identifier (maps to ERC-8004 tokenId).
        """
        self._provider = memory_provider
        self._agent_id = agent_id

    # ── CrewAI RAGStorage / LTMStorage interface ──────────────────

    def save(
        self,
        value: Any,
        metadata: Optional[dict] = None,
        agent: Optional[str] = None,
    ) -> str:
        """
        Save a memory (CrewAI encode operation).

        Args:
            value: Content to memorize (string or dict).
            metadata: Optional metadata (scope, importance, categories).
            agent: Optional agent name override.

        Returns:
            Memory ID.
        """
        content = str(value) if not isinstance(value, str) else value
        aid = agent or self._agent_id

        return _run_sync(self._provider.add(
            content=content,
            agent_id=aid,
            metadata=metadata or {},
        ))

    def search(
        self,
        query: str,
        limit: int = 5,
        score_threshold: float = 0.0,
        agent: Optional[str] = None,
    ) -> list[dict]:
        """
        Search memories (CrewAI recall operation).

        Args:
            query: Search query.
            limit: Maximum results.
            score_threshold: Minimum relevance score.
            agent: Optional agent name override.

        Returns:
            List of memory dicts with 'content', 'score', 'metadata'.
        """
        aid = agent or self._agent_id

        entries = _run_sync(self._provider.search(
            query=query,
            agent_id=aid,
            top_k=limit,
        ))

        results = []
        for entry in entries:
            if entry.score >= score_threshold:
                results.append({
                    "id": entry.memory_id,
                    "content": entry.content,
                    "score": entry.score,
                    "metadata": entry.metadata,
                    "created_at": entry.created_at,
                })
        return results

    def reset(self, agent: Optional[str] = None) -> None:
        """
        Clear all memories (CrewAI reset operation).

        Warning: This deletes all memories for the agent.
        """
        aid = agent or self._agent_id
        entries = _run_sync(self._provider.list_all(aid))
        for entry in entries:
            _run_sync(self._provider.delete(entry.memory_id, aid))

    def delete(self, memory_id: str, agent: Optional[str] = None) -> None:
        """Delete a specific memory (CrewAI forget operation)."""
        aid = agent or self._agent_id
        _run_sync(self._provider.delete(memory_id, aid))


class RuneCrewCheckpointStorage:
    """
    CrewAI-compatible task output storage backed by Rune.

    Persists CrewAI task outputs (raw text, JSON, Pydantic models)
    as versioned artifacts on BNBChain.

    Usage:
        from nexus_core.adapters.crewai import RuneCrewCheckpointStorage

        storage = RuneCrewCheckpointStorage(rune.artifacts, agent_id="my-crew")
        # CrewAI can use this to persist task results
    """

    def __init__(self, artifact_provider, agent_id: str = "crewai-agent"):
        self._provider = artifact_provider
        self._agent_id = agent_id

    def save(self, task_id: str, output: Any) -> int:
        """Save a task output as a versioned artifact."""
        data = str(output).encode("utf-8") if not isinstance(output, bytes) else output
        return _run_sync(self._provider.save(
            filename=f"task_{task_id}_output",
            data=data,
            agent_id=self._agent_id,
            content_type="application/json",
        ))

    def load(self, task_id: str, version: Optional[int] = None) -> Optional[str]:
        """Load a task output."""
        artifact = _run_sync(self._provider.load(
            filename=f"task_{task_id}_output",
            agent_id=self._agent_id,
            version=version,
        ))
        if artifact is None:
            return None
        return artifact.data.decode("utf-8")


# ── Helpers ─────────────────────────────────────────────────────────


def _run_sync(coro):
    """Run an async coroutine from synchronous code."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result(timeout=60)


AdapterRegistry.register("crewai", RuneCrewStorage)
