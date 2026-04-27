"""
Rune Protocol — Provider ABCs + AgentRuntime Facade.

Provider ABCs define WHAT operations are available (domain logic).
They depend on StorageBackend for HOW data is stored.

    SessionProvider   — checkpoint / resume / crash recovery
    MemoryProvider    — cross-session knowledge persistence
    ArtifactProvider  — versioned output storage
    TaskProvider      — task lifecycle tracking (A2A)
    AgentRuntime          — Facade bundling all four providers

Framework adapters (ADK, LangGraph, CrewAI) consume these interfaces.
They never see StorageBackend directly.

Design:
    - Provider ABCs = Template Method (define the contract)
    - AgentRuntime  = Facade (single entry point for users)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from .models import (
    Artifact, Checkpoint, MemoryCompact, MemoryEntry,
    Impression, ImpressionSummary, NetworkStats,
)


# ═══════════════════════════════════════════════════════════════════════
# Session Provider
# ═══════════════════════════════════════════════════════════════════════


class SessionProvider(ABC):
    """
    Framework-agnostic session/checkpoint persistence.

    Maps to:
      - Google ADK:  BaseSessionService
      - LangGraph:   BaseCheckpointSaver (put/get_tuple/list)
      - CrewAI:      Task state persistence
      - AutoGen:     Agent state checkpointing
    """

    @abstractmethod
    async def save_checkpoint(self, checkpoint: Checkpoint) -> str:
        """Save a state checkpoint. Returns the checkpoint_id."""
        ...

    @abstractmethod
    async def load_checkpoint(
        self,
        agent_id: str,
        thread_id: str,
        checkpoint_id: Optional[str] = None,
    ) -> Optional[Checkpoint]:
        """Load a checkpoint. If checkpoint_id is None, loads the latest."""
        ...

    @abstractmethod
    async def list_checkpoints(
        self,
        agent_id: str,
        thread_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[Checkpoint]:
        """List checkpoints for an agent, optionally filtered by thread."""
        ...

    @abstractmethod
    async def delete_checkpoint(
        self,
        agent_id: str,
        thread_id: str,
        checkpoint_id: Optional[str] = None,
    ) -> None:
        """Delete a checkpoint (or all checkpoints for a thread)."""
        ...

    async def flush(self, agent_id: str) -> None:
        """Force-flush any buffered state to storage."""
        pass


# ═══════════════════════════════════════════════════════════════════════
# Memory Provider
# ═══════════════════════════════════════════════════════════════════════


class MemoryProvider(ABC):
    """
    Framework-agnostic long-term memory persistence.

    Maps to:
      - Google ADK:  BaseMemoryService
      - LangGraph:   Store (put/get/search)
      - CrewAI:      Memory (encode, recall, forget)
      - AutoGen:     Memory protocol (add, query, clear)
    """

    @abstractmethod
    async def add(
        self,
        content: str,
        agent_id: str,
        user_id: str = "",
        metadata: Optional[dict] = None,
    ) -> str:
        """Store a memory entry. Returns the memory_id."""
        ...

    @abstractmethod
    async def search(
        self,
        query: str,
        agent_id: str,
        user_id: str = "",
        top_k: int = 5,
    ) -> list[MemoryEntry]:
        """Search memories by semantic similarity."""
        ...

    @abstractmethod
    async def delete(self, memory_id: str, agent_id: str) -> None:
        """Delete a specific memory entry."""
        ...

    @abstractmethod
    async def list_all(self, agent_id: str) -> list[MemoryEntry]:
        """List all memories for an agent."""
        ...

    # ── Progressive retrieval (three-layer architecture) ──────────

    async def search_compact(
        self,
        query: str,
        agent_id: str,
        user_id: str = "",
        top_k: int = 20,
    ) -> list[MemoryCompact]:
        """
        Layer 1: Return lightweight memory summaries (~50-100 tokens each).

        LLMs scan this compact index to decide which memories are relevant,
        then fetch full content via get_by_ids(). This saves ~10x tokens
        compared to loading full memories every time.

        Default implementation wraps search() for backward compatibility.
        """
        entries = await self.search(query, agent_id, user_id, top_k)
        return [e.compact() for e in entries]

    async def get_by_ids(
        self,
        memory_ids: list[str],
        agent_id: str,
    ) -> list[MemoryEntry]:
        """
        Layer 2: Fetch full memory content for selected IDs.

        Called after search_compact() with only the IDs the LLM
        determined are relevant. Returns full MemoryEntry objects.

        Default implementation scans list_all() for backward compatibility.
        """
        all_entries = await self.list_all(agent_id)
        id_set = set(memory_ids)
        return [e for e in all_entries if e.memory_id in id_set]

    async def bulk_add(
        self,
        entries: list[dict],
        agent_id: str,
        user_id: str = "",
    ) -> list[str]:
        """Add multiple memories with a single index write.

        Each entry dict must have 'content' and optionally 'metadata'.
        Default implementation loops add(). Concrete providers can
        override to batch the index update (N+1 writes instead of 2N).
        """
        ids = []
        for item in entries:
            content = item.get("content", "")
            if not content:
                continue
            mid = await self.add(content, agent_id, user_id, item.get("metadata"))
            ids.append(mid)
        return ids

    # ── Capacity management ──────────────────────────────────────

    async def count(self, agent_id: str) -> int:
        """Return total memory count for an agent.

        Default implementation delegates to list_all().
        Concrete providers can override for O(1) from index.
        """
        entries = await self.list_all(agent_id)
        return len(entries)

    async def bulk_delete(self, memory_ids: list[str], agent_id: str) -> int:
        """Delete multiple memories. Returns count actually deleted.

        Default implementation loops delete(). Concrete providers
        (e.g. ChainBackend) can override for single-batch writes.
        """
        deleted = 0
        for mid in memory_ids:
            await self.delete(mid, agent_id)
            deleted += 1
        return deleted

    async def replace(
        self,
        memory_id: str,
        new_content: str,
        agent_id: str,
        metadata: Optional[dict] = None,
    ) -> str:
        """Update a memory's content in-place. Returns memory_id.

        Default implementation deletes + re-adds. Concrete providers
        can override for atomic update.
        """
        await self.delete(memory_id, agent_id)
        return await self.add(new_content, agent_id, metadata=metadata)

    async def get_least_accessed(
        self,
        agent_id: str,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        """Return memories with lowest access_count (eviction candidates).

        Default implementation sorts list_all() by access_count.
        """
        entries = await self.list_all(agent_id)
        entries.sort(key=lambda m: (m.access_count, m.created_at))
        return entries[:limit]

    async def flush(self, agent_id: str) -> None:
        """Force-flush memories to storage."""
        pass

    async def load_from_chain(self, agent_id: str) -> int:
        """Cold-start: load memories from chain. Returns count loaded."""
        return 0


# ═══════════════════════════════════════════════════════════════════════
# Artifact Provider
# ═══════════════════════════════════════════════════════════════════════


class ArtifactProvider(ABC):
    """
    Framework-agnostic artifact (versioned output) persistence.

    Maps to:
      - Google ADK:  BaseArtifactService
      - LangGraph:   (artifacts are state values)
      - CrewAI:      TaskOutput
    """

    @abstractmethod
    async def save(
        self,
        filename: str,
        data: bytes,
        agent_id: str,
        session_id: str = "",
        content_type: str = "",
        metadata: Optional[dict] = None,
    ) -> int:
        """Save an artifact. Returns the version number."""
        ...

    @abstractmethod
    async def load(
        self,
        filename: str,
        agent_id: str,
        session_id: str = "",
        version: Optional[int] = None,
    ) -> Optional[Artifact]:
        """Load an artifact. If version is None, loads the latest."""
        ...

    @abstractmethod
    async def list_artifacts(
        self,
        agent_id: str,
        session_id: str = "",
    ) -> list[str]:
        """List all artifact filenames for an agent/session."""
        ...

    @abstractmethod
    async def list_versions(
        self,
        filename: str,
        agent_id: str,
        session_id: str = "",
    ) -> list[int]:
        """List all versions of a specific artifact."""
        ...

    async def rollback(
        self,
        filename: str,
        agent_id: str,
        to_version: int,
        session_id: str = "",
    ) -> int:
        """Rollback to a previous version. Returns new version number.

        Creates a NEW version with the content of to_version,
        preserving full history (never deletes versions).
        Default implementation loads old version and re-saves.
        """
        old = await self.load(filename, agent_id, session_id, version=to_version)
        if old is None:
            raise ValueError(f"Version {to_version} not found for {filename}")
        return await self.save(
            filename, old.data, agent_id, session_id,
            old.content_type, {"rollback_from": to_version},
        )


# ═══════════════════════════════════════════════════════════════════════
# Task Provider
# ═══════════════════════════════════════════════════════════════════════


class TaskProvider(ABC):
    """
    Framework-agnostic task lifecycle tracking.

    Maps to:
      - A2A Protocol: TaskStore
      - LangGraph:    Thread status tracking
      - CrewAI:       Task execution lifecycle
    """

    @abstractmethod
    async def create_task(
        self,
        task_id: str,
        agent_id: str,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Create a new task. Returns task record."""
        ...

    @abstractmethod
    async def update_task(
        self,
        task_id: str,
        state: dict,
        status: str = "running",
    ) -> dict:
        """Update task state. Returns updated record."""
        ...

    @abstractmethod
    async def get_task(self, task_id: str) -> Optional[dict]:
        """Get task record by ID."""
        ...


# ═══════════════════════════════════════════════════════════════════════
# Impression Provider (Social Protocol)
# ═══════════════════════════════════════════════════════════════════════


class ImpressionProvider(ABC):
    """
    Framework-agnostic impression persistence (Social Protocol).

    The 5th Rune provider — manages agent-to-agent impressions
    formed through gossip sessions. Designed with relational query
    semantics (by target, by source, mutual, ranked) rather than
    content-similarity search.

    Impressions are:
      - Asymmetric: A's impression of B ≠ B's impression of A
      - Cumulative: multiple sessions → multiple impressions
      - Multi-dimensional: 5 scored dimensions + overall compatibility
    """

    @abstractmethod
    async def record(self, impression: Impression) -> str:
        """Store a new impression after a gossip session. Returns impression_id."""
        ...

    @abstractmethod
    async def get_impressions_of(
        self,
        target_agent: str,
        agent_id: str,
        limit: int = 10,
    ) -> list[Impression]:
        """
        All impressions this agent (agent_id) has formed about target_agent.
        Ordered by recency.
        """
        ...

    @abstractmethod
    async def get_impressions_from(
        self,
        agent_id: str,
        limit: int = 20,
    ) -> list[Impression]:
        """
        All impressions others have formed about this agent.
        The 'inbound' view — how the network sees you.
        """
        ...

    @abstractmethod
    async def get_compatibility(
        self,
        agent_a: str,
        agent_b: str,
    ) -> Optional[float]:
        """
        Latest compatibility score between two agents (A's view of B).
        Returns None if they've never interacted.
        """
        ...

    @abstractmethod
    async def get_top_matches(
        self,
        agent_id: str,
        top_k: int = 10,
        min_score: float = 0.0,
        dimension: Optional[str] = None,
    ) -> list[ImpressionSummary]:
        """
        Agents ranked by compatibility with this agent.
        Optionally filter by a specific dimension.
        """
        ...

    @abstractmethod
    async def get_mutual(
        self,
        agent_id: str,
        min_score: float = 0.5,
    ) -> list[tuple]:
        """
        Agents with mutual positive impressions.
        Returns list of (other_agent_id, my_score_of_them, their_score_of_me).
        """
        ...

    @abstractmethod
    async def get_network_stats(self, agent_id: str) -> NetworkStats:
        """Aggregated social statistics for an agent."""
        ...


# ═══════════════════════════════════════════════════════════════════════
# Facade: AgentRuntime
# ═══════════════════════════════════════════════════════════════════════


class AgentRuntime:
    """
    Facade: the single object users interact with.

    Bundles all five providers behind a clean interface.
    No internal implementation details are exposed.

    Usage:
        rune = nexus_core.local()

        await rune.sessions.save_checkpoint(checkpoint)
        entries = await rune.memory.search("revenue trends", agent_id="my-agent")
        version = await rune.artifacts.save("report.json", data, agent_id="my-agent")
        await rune.impressions.record(impression)
    """

    def __init__(
        self,
        sessions: SessionProvider,
        memory: MemoryProvider,
        artifacts: ArtifactProvider,
        tasks: TaskProvider,
        impressions: Optional[ImpressionProvider] = None,
        backend: Optional[Any] = None,
    ):
        self.sessions: SessionProvider = sessions
        self.memory: MemoryProvider = memory
        self.artifacts: ArtifactProvider = artifacts
        self.tasks: TaskProvider = tasks
        self.impressions: Optional[ImpressionProvider] = impressions
        self._backend = backend  # held for lifecycle (close/flush)

    async def close(self) -> None:
        """Drain pending writes and release resources.

        Calls backend.close() which, for ChainBackend, waits for
        pending Greenfield writes to finish before shutting down.
        """
        if self._backend is not None and hasattr(self._backend, "close"):
            await self._backend.close()

    async def __aenter__(self) -> "AgentRuntime":
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()
