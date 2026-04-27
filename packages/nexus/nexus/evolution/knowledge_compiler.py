"""
KnowledgeCompiler — Compile scattered memories into structured knowledge articles.

Inspired by claude-memory-compiler (Karpathy's LLM Knowledge Base architecture):
  Raw memories (fragments) → cluster by topic → LLM synthesizes → structured articles

Benefits:
  - Higher information density (1 article replaces ~10-20 raw memories in context)
  - More useful for retrieval (structured knowledge > scattered fragments)
  - Higher value on-chain (verifiable knowledge graph, not just raw observations)

Storage:
  Articles are stored as versioned Rune Artifacts (knowledge_articles.json).
  Each compilation creates a new version with change notes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from nexus_core import RuneProvider
from .memory_evolver import _robust_json_parse

logger = logging.getLogger(__name__)

# ── Prompts ──────────────────────────────────────────────────────

CLUSTER_PROMPT = """Analyze these memories and group them into topic clusters.

Memories:
{memories}

Return a JSON object mapping topic names to lists of memory indices (0-based).
Topics should be descriptive (e.g., "food_preferences", "travel_plans", "work_habits").

Rules:
- Each memory should belong to at most one cluster
- Skip memories that don't fit any meaningful cluster
- Minimum 2 memories per cluster (singletons aren't worth an article)
- Maximum 8 clusters
- Topic names should be snake_case

Example: {{"food_preferences": [0, 3, 7], "travel_plans": [1, 4, 5]}}

Return ONLY valid JSON, no markdown fences."""


COMPILE_PROMPT = """Synthesize these related memories into a structured knowledge article.

Topic: {topic}

Raw memories:
{memories}

Existing article (if updating):
{existing_article}

Write a concise knowledge article (100-200 words) that:
1. Summarizes the key patterns and facts from these memories
2. Highlights preferences, habits, or recurring themes
3. Notes any changes or evolution over time
4. Is written in third person ("The user prefers..." not "You prefer...")

Return a JSON object:
{{
  "title": "Short descriptive title",
  "summary": "1-sentence overview",
  "content": "The full article text",
  "key_facts": ["fact1", "fact2", ...],
  "tags": ["tag1", "tag2", ...],
  "memory_count": {memory_count},
  "confidence": 0.0-1.0
}}

Return ONLY valid JSON, no markdown fences."""


class KnowledgeCompiler:
    """
    Compiles scattered memories into structured knowledge articles.

    Triggered during reflection cycles (every N turns) by the EvolutionEngine.
    Articles are stored as versioned Rune Artifacts, creating a
    verifiable knowledge graph on-chain.
    """

    def __init__(self, rune: RuneProvider, agent_id: str, llm_fn: Any):
        self.rune = rune
        self.agent_id = agent_id
        self.llm_fn = llm_fn
        self._articles: dict[str, dict] = {}
        self._last_compiled_at: float = 0.0
        self._compilation_count: int = 0
        self._dirty: bool = False  # True when locally modified — triggers merge on load
        self._lock = asyncio.Lock()  # Protects load/save from concurrent access

    async def load_articles(self) -> dict[str, dict]:
        """Load existing knowledge articles from storage."""
        try:
            art = await self.rune.artifacts.load(
                "knowledge_articles.json", agent_id=self.agent_id,
            )
            if art:
                data = json.loads(art.data.decode())
                remote_articles = data.get("articles", {})
                remote_count = data.get("compilation_count", 0)
                remote_time = data.get("last_compiled_at", 0.0)

                if self._dirty:
                    # Merge: remote first, then local overwrites (local wins on conflict)
                    merged = {**remote_articles}
                    merged.update(self._articles)  # local takes precedence
                    self._articles = merged
                    self._compilation_count = max(self._compilation_count, remote_count)
                    self._last_compiled_at = max(self._last_compiled_at, remote_time)
                    self._dirty = False  # Merge complete — state is now consistent
                    logger.info(
                        "Knowledge merged: %d remote + %d local → %d total",
                        len(remote_articles), len(self._articles) - len(remote_articles),
                        len(self._articles),
                    )
                else:
                    self._articles = remote_articles
                    self._compilation_count = remote_count
                    self._last_compiled_at = remote_time
        except Exception:
            if not self._dirty:
                self._articles = {}
        return self._articles

    async def _save_articles(self):
        """Persist knowledge articles as a versioned artifact."""
        data = json.dumps({
            "articles": self._articles,
            "compilation_count": self._compilation_count,
            "last_compiled_at": self._last_compiled_at,
            "article_count": len(self._articles),
        }, indent=2, ensure_ascii=False)
        await self.rune.artifacts.save(
            filename="knowledge_articles.json",
            data=data.encode(),
            agent_id=self.agent_id,
            content_type="application/json",
            metadata={
                "type": "evolution_artifact",
                "subtype": "knowledge_base",
                "compilation": self._compilation_count,
            },
        )

    async def compile(self, min_memories: int = 10) -> dict:
        """
        Main compilation pipeline:
          1. Load all raw memories
          2. Cluster by topic (LLM)
          3. Synthesize each cluster into a knowledge article (LLM)
          4. Store as versioned artifact

        Args:
            min_memories: Minimum memories required to trigger compilation.

        Returns:
            Compilation result with stats and new/updated articles.
        """
        await self.load_articles()

        # ── 1. Load all raw memories ──
        all_memories = await self.rune.memory.list_all(self.agent_id)
        if len(all_memories) < min_memories:
            return {
                "status": "skipped",
                "reason": f"Only {len(all_memories)} memories (need {min_memories})",
                "articles_count": len(self._articles),
            }

        memory_texts = []
        for m in all_memories:
            cat = m.metadata.get("category", "")
            imp = m.metadata.get("importance", 3)
            memory_texts.append(f"[{cat}, importance={imp}] {m.content}")

        # ── 2. Cluster memories by topic ──
        clusters = await self._cluster_memories(memory_texts)
        if not clusters:
            return {
                "status": "no_clusters",
                "reason": "LLM found no meaningful topic clusters",
                "articles_count": len(self._articles),
            }

        # ── 3. Compile each cluster into an article ──
        new_articles = []
        updated_articles = []

        for topic, indices in clusters.items():
            # Gather memory texts for this cluster
            cluster_memories = [
                memory_texts[i] for i in indices
                if i < len(memory_texts)
            ]
            if len(cluster_memories) < 2:
                continue

            existing = self._articles.get(topic, {}).get("content", "")

            article = await self._compile_article(
                topic, cluster_memories, existing,
            )
            if not article:
                continue

            article["updated_at"] = time.time()
            article["memory_indices"] = indices

            if topic in self._articles:
                article["version"] = self._articles[topic].get("version", 0) + 1
                updated_articles.append(topic)
            else:
                article["version"] = 1
                article["created_at"] = time.time()
                new_articles.append(topic)

            self._articles[topic] = article

        self._dirty = True  # Mark as locally modified — background load will merge

        # ── 4. Persist ──
        self._compilation_count += 1
        self._last_compiled_at = time.time()
        await self._save_articles()

        result = {
            "status": "compiled",
            "compilation": self._compilation_count,
            "total_memories": len(all_memories),
            "clusters_found": len(clusters),
            "new_articles": new_articles,
            "updated_articles": updated_articles,
            "total_articles": len(self._articles),
        }

        logger.info(
            f"Knowledge compiled: {len(new_articles)} new, "
            f"{len(updated_articles)} updated, "
            f"{len(self._articles)} total articles"
        )
        return result

    async def _cluster_memories(self, memory_texts: list[str]) -> dict[str, list[int]]:
        """Use LLM to cluster memories into topics."""
        # Format as numbered list for index reference
        numbered = "\n".join(
            f"[{i}] {text}" for i, text in enumerate(memory_texts)
        )

        prompt = CLUSTER_PROMPT.format(memories=numbered)

        try:
            raw = await self.llm_fn(prompt)
            clusters = _robust_json_parse(raw)
        except Exception as e:
            logger.warning(f"Memory clustering failed: {e}")
            return {}

        if not isinstance(clusters, dict):
            return {}

        # Validate: each value should be a list of ints
        valid = {}
        for topic, indices in clusters.items():
            if isinstance(indices, list) and len(indices) >= 2:
                valid_indices = [
                    i for i in indices
                    if isinstance(i, int) and 0 <= i < len(memory_texts)
                ]
                if len(valid_indices) >= 2:
                    valid[topic] = valid_indices

        return valid

    async def _compile_article(
        self,
        topic: str,
        memories: list[str],
        existing_article: str = "",
    ) -> Optional[dict]:
        """Synthesize a cluster of memories into a knowledge article."""
        prompt = COMPILE_PROMPT.format(
            topic=topic,
            memories="\n".join(f"- {m}" for m in memories),
            existing_article=existing_article or "None (new article)",
            memory_count=len(memories),
        )

        try:
            raw = await self.llm_fn(prompt)
            article = _robust_json_parse(raw)
        except Exception as e:
            logger.warning(f"Article compilation failed for {topic}: {e}")
            return None

        if not isinstance(article, dict) or "content" not in article:
            return None

        return article

    def get_article(self, topic: str) -> Optional[dict]:
        """Get a specific knowledge article by topic."""
        return self._articles.get(topic)

    def get_all_articles(self) -> dict[str, dict]:
        """Get all knowledge articles."""
        return dict(self._articles)

    def get_context_from_cache(self, query: str, max_articles: int = 3) -> str:
        """
        Get relevant knowledge from in-memory cache ONLY.

        Never triggers a load — if articles aren't loaded yet, returns empty.
        This is the non-blocking version used during chat.
        """
        return self._match_articles(query, max_articles)

    async def get_context_for_query(self, query: str, max_articles: int = 3) -> str:
        """
        Get relevant knowledge articles for a query.

        Simple keyword matching against article titles, tags, and key_facts.
        Returns formatted context string for injection into LLM prompt.
        """
        if not self._articles:
            await self.load_articles()

        return self._match_articles(query, max_articles)

    def _match_articles(self, query: str, max_articles: int = 3) -> str:
        """Pure in-memory keyword matching against loaded articles."""
        if not self._articles:
            return ""

        query_words = set(query.lower().split())
        scored = []

        for topic, article in self._articles.items():
            # Score by keyword overlap with title, tags, key_facts
            searchable = (
                topic.replace("_", " ") + " " +
                article.get("title", "") + " " +
                " ".join(article.get("tags", [])) + " " +
                " ".join(article.get("key_facts", []))
            ).lower()

            article_words = set(searchable.split())
            overlap = len(query_words & article_words)
            if overlap > 0:
                scored.append((overlap, topic, article))

        if not scored:
            return ""

        scored.sort(reverse=True)
        parts = ["## Compiled Knowledge"]
        for _, topic, article in scored[:max_articles]:
            parts.append(f"\n### {article.get('title', topic)}")
            parts.append(article.get("content", ""))

        return "\n".join(parts)

    async def get_stats(self) -> dict:
        """Return compilation statistics."""
        if not self._articles:
            await self.load_articles()

        return {
            "total_articles": len(self._articles),
            "compilation_count": self._compilation_count,
            "last_compiled_at": self._last_compiled_at,
            "topics": list(self._articles.keys()),
            "articles": {
                topic: {
                    "title": a.get("title", ""),
                    "version": a.get("version", 1),
                    "memory_count": a.get("memory_count", 0),
                    "confidence": a.get("confidence", 0),
                }
                for topic, a in self._articles.items()
            },
        }
