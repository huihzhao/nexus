"""
MemoryProviderImpl — concrete RuneMemoryProvider backed by StorageBackend.

Domain logic:
  - Local semantic search (TF-IDF based, no LLM required)
  - Memory deduplication
  - Persistence to backend + on-chain anchoring
  - Cold-start loading from backend

Storage layout:
    agents/{agent_id}/memory/index.json     — memory index
    agents/{agent_id}/memory/{memory_id}.json — individual entries
"""

from __future__ import annotations

import logging
import math
import re
import time
import uuid
from collections import Counter
from typing import Optional

from ..core.backend import StorageBackend
from ..core.models import MemoryCompact, MemoryEntry
from ..core.providers import RuneMemoryProvider

logger = logging.getLogger(__name__)


class MemoryProviderImpl(RuneMemoryProvider):
    """
    Concrete memory provider with local semantic search.

    Uses TF-IDF based similarity for search (no external LLM needed).
    All memories are persisted via StorageBackend.

    Lazy-loads memories from backend on first access per agent_id
    (same pattern as ImpressionProviderImpl._ensure_loaded).
    """

    def __init__(
        self,
        backend: StorageBackend,
        runtime_id: Optional[str] = None,
    ):
        self._backend = backend
        self._runtime_id = runtime_id or f"runtime-{uuid.uuid4().hex[:8]}"
        # In-memory store: agent_id -> {memory_id: MemoryEntry}
        self._memories: dict[str, dict[str, MemoryEntry]] = {}
        # Track which agents have been loaded (positive + negative cache)
        self._loaded_agents: set[str] = set()
        # Track entries whose access_count/last_accessed changed since last persist.
        # flush() only writes these instead of all entries (avoids shutdown write storm).
        self._dirty_entries: dict[str, set[str]] = {}  # agent_id -> {memory_id, ...}

    @staticmethod
    def _safe(value: str) -> str:
        """Sanitize a path component to prevent directory traversal."""
        return value.replace("/", "__").replace("\\", "__").replace("..", "__")

    def _index_path(self, agent_id: str) -> str:
        return f"agents/{self._safe(agent_id)}/memory/index.json"

    def _entry_path(self, agent_id: str, memory_id: str) -> str:
        return f"agents/{self._safe(agent_id)}/memory/{self._safe(memory_id)}.json"

    # ── Lazy loading (cold-start) ──────────────────────────────────

    async def _ensure_loaded(self, agent_id: str) -> None:
        """Lazy-load memories for an agent from backend on first access.

        Uses _loaded_agents as both positive and negative cache:
        once checked (hit or miss), won't re-query during this session.

        Uses try/finally to ensure _loaded_agents is set even when
        the task is cancelled (CancelledError bypasses except Exception
        in Python 3.9+).
        """
        if agent_id in self._loaded_agents:
            return

        try:
            loaded = await self.load_from_chain(agent_id)
            if loaded > 0:
                logger.info("Lazy-loaded %d memories for agent %s", loaded, agent_id)
        except Exception as e:
            logger.warning("Memory lazy-load failed for %s: %s", agent_id, e)
        finally:
            self._loaded_agents.add(agent_id)

    async def add(
        self,
        content: str,
        agent_id: str,
        user_id: str = "",
        metadata: Optional[dict] = None,
    ) -> str:
        await self._ensure_loaded(agent_id)
        if agent_id not in self._memories:
            self._memories[agent_id] = {}

        # Deduplication: skip if identical content exists
        for existing in self._memories[agent_id].values():
            if existing.content == content:
                return existing.memory_id

        entry = MemoryEntry(
            content=content,
            agent_id=agent_id,
            user_id=user_id,
            metadata=metadata or {},
        )

        self._memories[agent_id][entry.memory_id] = entry

        # Persist entry
        path = self._entry_path(agent_id, entry.memory_id)
        await self._backend.store_json(path, {
            "memory_id": entry.memory_id,
            "content": entry.content,
            "agent_id": entry.agent_id,
            "user_id": entry.user_id,
            "metadata": entry.metadata,
            "created_at": entry.created_at,
            "access_count": entry.access_count,
            "last_accessed": entry.last_accessed,
        })

        # Update index
        await self._save_index(agent_id)

        return entry.memory_id

    async def bulk_add(
        self,
        entries: list[dict],
        agent_id: str,
        user_id: str = "",
    ) -> list[str]:
        """Add multiple memories with a single index write.

        Each entry dict must have 'content' and optionally 'metadata'.
        Writes each entry to backend individually (Greenfield doesn't support
        batch PUTs) but only writes index.json ONCE at the end.

        This reduces Greenfield writes from 2N (N entries + N index updates)
        to N+1 (N entries + 1 index update). For a typical extraction of 4
        memories, that's 5 writes instead of 8.
        """
        await self._ensure_loaded(agent_id)
        if agent_id not in self._memories:
            self._memories[agent_id] = {}

        added_ids = []
        for item in entries:
            content = item.get("content", "")
            if not content:
                continue
            metadata = item.get("metadata", {})

            # Deduplication
            dup = False
            for existing in self._memories[agent_id].values():
                if existing.content == content:
                    added_ids.append(existing.memory_id)
                    dup = True
                    break
            if dup:
                continue

            entry = MemoryEntry(
                content=content,
                agent_id=agent_id,
                user_id=user_id,
                metadata=metadata,
            )
            self._memories[agent_id][entry.memory_id] = entry

            # Persist entry (but NOT index — deferred to end)
            path = self._entry_path(agent_id, entry.memory_id)
            await self._backend.store_json(path, {
                "memory_id": entry.memory_id,
                "content": entry.content,
                "agent_id": entry.agent_id,
                "user_id": entry.user_id,
                "metadata": entry.metadata,
                "created_at": entry.created_at,
                "access_count": entry.access_count,
                "last_accessed": entry.last_accessed,
            })
            added_ids.append(entry.memory_id)

        # Single index write for all new entries
        if added_ids:
            await self._save_index(agent_id)

        return added_ids

    async def search(
        self,
        query: str,
        agent_id: str,
        user_id: str = "",
        top_k: int = 5,
    ) -> list[MemoryEntry]:
        await self._ensure_loaded(agent_id)
        if agent_id not in self._memories:
            return []

        memories = list(self._memories[agent_id].values())
        if user_id:
            memories = [m for m in memories if m.user_id == user_id]

        if not memories:
            return []

        # TF-IDF based search
        scored = self._tfidf_search(query, memories, top_k)

        # Track access on the original entries (scored are copies with score set).
        # Mark them dirty so flush() persists the updated counts at shutdown.
        # We no longer write each entry immediately — this caused a write storm
        # when search() was called repeatedly during a session (e.g., 107 pending
        # writes at shutdown). Instead, flush() batches all dirty entries.
        now = time.time()
        if agent_id not in self._dirty_entries:
            self._dirty_entries[agent_id] = set()
        for entry_copy in scored:
            original = self._memories[agent_id].get(entry_copy.memory_id)
            if original:
                original.access_count += 1
                original.last_accessed = now
                self._dirty_entries[agent_id].add(original.memory_id)

        return scored

    async def delete(self, memory_id: str, agent_id: str) -> None:
        await self._ensure_loaded(agent_id)
        if agent_id in self._memories:
            self._memories[agent_id].pop(memory_id, None)
            path = self._entry_path(agent_id, memory_id)
            await self._backend.delete(path)
            await self._save_index(agent_id)

    async def list_all(self, agent_id: str) -> list[MemoryEntry]:
        await self._ensure_loaded(agent_id)
        if agent_id not in self._memories:
            return []
        return list(self._memories[agent_id].values())

    # ── Capacity management (overrides for efficiency) ─────────

    async def count(self, agent_id: str) -> int:
        """O(1) count from in-memory index."""
        await self._ensure_loaded(agent_id)
        if agent_id not in self._memories:
            return 0
        return len(self._memories[agent_id])

    async def bulk_delete(self, memory_ids: list[str], agent_id: str) -> int:
        """Delete multiple memories with single index write."""
        await self._ensure_loaded(agent_id)
        if agent_id not in self._memories:
            return 0

        deleted = 0
        for mid in memory_ids:
            if mid in self._memories[agent_id]:
                self._memories[agent_id].pop(mid)
                path = self._entry_path(agent_id, mid)
                await self._backend.delete(path)
                deleted += 1

        if deleted > 0:
            await self._save_index(agent_id)
        return deleted

    async def replace(
        self,
        memory_id: str,
        new_content: str,
        agent_id: str,
        metadata: Optional[dict] = None,
    ) -> str:
        """Atomic in-place update — preserves memory_id and created_at."""
        await self._ensure_loaded(agent_id)
        if agent_id not in self._memories or memory_id not in self._memories[agent_id]:
            # Memory doesn't exist — add as new
            return await self.add(new_content, agent_id, metadata=metadata)

        existing = self._memories[agent_id][memory_id]
        existing.content = new_content
        if metadata is not None:
            existing.metadata = metadata

        # Persist updated entry
        path = self._entry_path(agent_id, memory_id)
        await self._backend.store_json(path, {
            "memory_id": existing.memory_id,
            "content": existing.content,
            "agent_id": existing.agent_id,
            "user_id": existing.user_id,
            "metadata": existing.metadata,
            "created_at": existing.created_at,
            "access_count": existing.access_count,
            "last_accessed": existing.last_accessed,
        })
        return memory_id

    async def get_least_accessed(
        self,
        agent_id: str,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        """O(n log n) from in-memory cache — no backend call needed."""
        await self._ensure_loaded(agent_id)
        if agent_id not in self._memories:
            return []
        entries = list(self._memories[agent_id].values())
        entries.sort(key=lambda m: (m.access_count, m.created_at))
        return entries[:limit]

    # ── Progressive retrieval (overrides for efficiency) ──────────

    async def search_compact(
        self,
        query: str,
        agent_id: str,
        user_id: str = "",
        top_k: int = 20,
    ) -> list[MemoryCompact]:
        """Efficient compact search — runs TF-IDF but returns lightweight summaries."""
        entries = await self.search(query, agent_id, user_id, top_k)
        return [e.compact() for e in entries]

    async def get_by_ids(
        self,
        memory_ids: list[str],
        agent_id: str,
    ) -> list[MemoryEntry]:
        """Direct lookup by IDs — O(1) per ID from in-memory cache."""
        await self._ensure_loaded(agent_id)
        if agent_id not in self._memories:
            return []
        id_set = set(memory_ids)
        return [
            self._memories[agent_id][mid]
            for mid in memory_ids
            if mid in self._memories[agent_id]
        ]

    async def flush(self, agent_id: str) -> None:
        """Persist dirty entries to backend — only those modified since last persist.

        Instead of writing ALL entries (which caused a shutdown write storm of
        107+ pending writes), we only write entries in _dirty_entries. This
        typically reduces writes from O(total_memories) to O(accessed_memories),
        e.g., 5-10 writes instead of 107.
        """
        await self._ensure_loaded(agent_id)
        if agent_id not in self._memories:
            return

        dirty = self._dirty_entries.get(agent_id, set())
        if not dirty:
            logger.debug("flush: no dirty entries for %s", agent_id)
            return

        persisted = 0
        for mid in list(dirty):
            entry = self._memories[agent_id].get(mid)
            if not entry:
                continue
            try:
                path = self._entry_path(agent_id, mid)
                await self._backend.store_json(path, {
                    "memory_id": entry.memory_id,
                    "content": entry.content,
                    "agent_id": entry.agent_id,
                    "user_id": entry.user_id,
                    "metadata": entry.metadata,
                    "created_at": entry.created_at,
                    "access_count": entry.access_count,
                    "last_accessed": entry.last_accessed,
                })
                persisted += 1
            except Exception as e:
                logger.warning("flush: failed to persist %s: %s", mid, e)

        # Clear dirty set after flush
        self._dirty_entries[agent_id] = set()
        logger.info("flush: persisted %d/%d dirty entries for %s", persisted, len(dirty), agent_id)

    async def load_from_chain(self, agent_id: str) -> int:
        """Load memories from backend (cold start)."""
        index_path = self._index_path(agent_id)
        index_data = await self._backend.load_json(index_path)
        if not index_data:
            return 0

        if agent_id not in self._memories:
            self._memories[agent_id] = {}

        loaded = 0
        for mid in index_data.get("memory_ids", []):
            if mid in self._memories[agent_id]:
                continue
            entry_path = self._entry_path(agent_id, mid)
            entry_data = await self._backend.load_json(entry_path)
            if entry_data:
                entry = MemoryEntry(
                    memory_id=entry_data.get("memory_id", mid),
                    content=entry_data.get("content", ""),
                    agent_id=entry_data.get("agent_id", agent_id),
                    user_id=entry_data.get("user_id", ""),
                    metadata=entry_data.get("metadata", {}),
                    created_at=entry_data.get("created_at", 0.0),
                    access_count=entry_data.get("access_count", 0),
                    last_accessed=entry_data.get("last_accessed", 0.0),
                )
                self._memories[agent_id][entry.memory_id] = entry
                loaded += 1

        return loaded

    # ── Internal: index management ──────────────────────────────────

    async def _save_index(self, agent_id: str) -> None:
        """Save memory index to backend + anchor on-chain."""
        if agent_id not in self._memories:
            return
        memory_ids = list(self._memories[agent_id].keys())
        index_path = self._index_path(agent_id)
        content_hash = await self._backend.store_json(index_path, {
            "agent_id": agent_id,
            "memory_ids": memory_ids,
            "count": len(memory_ids),
            "updated_at": time.time(),
        })
        await self._backend.anchor(agent_id, content_hash, namespace="memory")

    # ── Internal: TF-IDF search ─────────────────────────────────────

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Multilingual tokenizer: word-level for Latin scripts, character-level for CJK.

        CJK characters (Chinese, Japanese, Korean) have no whitespace between words,
        so we split them into individual characters (unigrams). Latin/ASCII words
        are tokenized by whitespace as usual. Both types are lowercased.

        This enables TF-IDF matching between Chinese queries and Chinese/English
        memories, e.g. query "喜欢" matches memory containing "喜欢辣椒".
        """
        text = text.lower()
        tokens = []
        # Match either CJK characters individually or ASCII/Latin word sequences
        for match in re.finditer(r'[\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\uff00-\uffef]|\w+', text):
            token = match.group()
            # CJK single-char tokens are already split; ASCII words come through as-is
            if len(token) == 1 and '\u4e00' <= token <= '\u9fff':
                tokens.append(token)
            elif any('\u4e00' <= c <= '\u9fff' for c in token):
                # Mixed CJK string — split into individual chars for CJK,
                # accumulate ASCII segments as words
                ascii_buf = []
                for c in token:
                    if '\u4e00' <= c <= '\u9fff':
                        if ascii_buf:
                            tokens.append("".join(ascii_buf))
                            ascii_buf.clear()
                        tokens.append(c)
                    elif c.isalnum():
                        ascii_buf.append(c)
                if ascii_buf:
                    tokens.append("".join(ascii_buf))
            else:
                tokens.append(token)
        return tokens

    @staticmethod
    def _tfidf_search(
        query: str,
        memories: list[MemoryEntry],
        top_k: int,
    ) -> list[MemoryEntry]:
        """Rank memories by TF-IDF cosine similarity to query."""
        query_tokens = MemoryProviderImpl._tokenize(query)
        if not query_tokens:
            # No searchable tokens — return by recency rather than arbitrary order
            sorted_by_time = sorted(memories, key=lambda m: m.created_at, reverse=True)
            return sorted_by_time[:top_k]

        # Build document frequency
        doc_freq: Counter = Counter()
        doc_tokens = []
        for mem in memories:
            tokens = MemoryProviderImpl._tokenize(mem.content)
            doc_tokens.append(tokens)
            for token in set(tokens):
                doc_freq[token] += 1

        n_docs = len(memories)
        results = []

        for i, mem in enumerate(memories):
            tokens = doc_tokens[i]
            if not tokens:
                continue

            # TF for document
            tf_doc = Counter(tokens)
            # TF for query
            tf_query = Counter(query_tokens)

            # Cosine similarity with TF-IDF weighting
            score = 0.0
            norm_doc = 0.0
            norm_query = 0.0

            all_terms = set(query_tokens) | set(tokens)
            for term in all_terms:
                idf = math.log(1 + n_docs / (1 + doc_freq.get(term, 0)))
                w_doc = (tf_doc.get(term, 0) / len(tokens)) * idf
                w_query = (tf_query.get(term, 0) / len(query_tokens)) * idf
                score += w_doc * w_query
                norm_doc += w_doc ** 2
                norm_query += w_query ** 2

            if norm_doc > 0 and norm_query > 0:
                score /= math.sqrt(norm_doc) * math.sqrt(norm_query)

            entry_copy = MemoryEntry(
                memory_id=mem.memory_id,
                content=mem.content,
                agent_id=mem.agent_id,
                user_id=mem.user_id,
                metadata=mem.metadata,
                score=score,
                created_at=mem.created_at,
            )
            results.append(entry_copy)

        results.sort(key=lambda m: m.score, reverse=True)
        return results[:top_k]
