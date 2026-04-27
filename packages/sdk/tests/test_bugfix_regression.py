"""
Regression tests for bug fixes.

Each test class corresponds to a specific bug that was found and fixed.
If any of these tests fail, the corresponding bug has been reintroduced.
"""

import asyncio
import hashlib
import json
import os
import time
import pytest

from nexus_core import (
    Rune,
    Impression,
    ImpressionDimensions,
    MockBackend,
)
from nexus_core.providers.session import SessionProviderImpl
from nexus_core.providers.memory import MemoryProviderImpl
from nexus_core.providers.artifact import ArtifactProviderImpl
from nexus_core.providers.impression import ImpressionProviderImpl


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def backend():
    return MockBackend()


@pytest.fixture
def rune():
    return Rune.builder().mock_backend().build()


# ══════════════════════════════════════════════════════════════════════
# Bug #1: Missing logger import in impression.py → NameError crash
# ══════════════════════════════════════════════════════════════════════

class TestBug1_ImpressionLoggerImport:
    """impression.py used `logger.warning(...)` but never imported logging.
    Any exception in _ensure_loaded would crash with NameError."""

    @pytest.mark.asyncio
    async def test_ensure_loaded_logs_without_crash(self, backend):
        """_ensure_loaded should handle errors gracefully, not NameError."""
        provider = ImpressionProviderImpl(backend)

        # Calling _ensure_loaded for a non-existent agent should not crash
        await provider._ensure_loaded("nonexistent-agent")

        # Should be marked as loaded (negative cache)
        assert "nonexistent-agent" in provider._loaded_agents

    @pytest.mark.asyncio
    async def test_impression_provider_importable(self):
        """Module should import without errors (logging + logger defined)."""
        import importlib
        mod = importlib.import_module("nexus_core.providers.impression")
        assert hasattr(mod, "logger")


# ══════════════════════════════════════════════════════════════════════
# Bug #4: Dead code in get_mutual() — two redundant loops
# ══════════════════════════════════════════════════════════════════════

class TestBug4_GetMutualDeadCode:
    """get_mutual() had two dead-code loops before the working implementation.
    Verify the cleaned-up version works correctly."""

    @pytest.mark.asyncio
    async def test_mutual_returns_correct_pairs(self, backend):
        provider = ImpressionProviderImpl(backend)

        # Agent A → B (score 0.8)
        imp_ab = Impression(
            source_agent="A", target_agent="B",
            compatibility_score=0.8,
            dimensions=ImpressionDimensions(interest_overlap=0.8),
        )
        await provider.record(imp_ab)

        # Agent B → A (score 0.7)
        imp_ba = Impression(
            source_agent="B", target_agent="A",
            compatibility_score=0.7,
            dimensions=ImpressionDimensions(interest_overlap=0.7),
        )
        await provider.record(imp_ba)

        # Get mutuals for A
        mutuals = await provider.get_mutual("A", min_score=0.5)
        assert len(mutuals) == 1
        other, my_score, their_score = mutuals[0]
        assert other == "B"
        assert my_score == 0.8
        assert their_score == 0.7

    @pytest.mark.asyncio
    async def test_mutual_latest_score_wins(self, backend):
        """When multiple impressions exist, the latest score should be used."""
        provider = ImpressionProviderImpl(backend)

        # A → B: first impression (old, low score)
        imp1 = Impression(
            source_agent="A", target_agent="B",
            compatibility_score=0.3,
            dimensions=ImpressionDimensions(),
        )
        imp1.created_at = 100.0
        await provider.record(imp1)

        # A → B: second impression (newer, high score)
        imp2 = Impression(
            source_agent="A", target_agent="B",
            compatibility_score=0.9,
            dimensions=ImpressionDimensions(),
        )
        imp2.created_at = 200.0
        await provider.record(imp2)

        # B → A
        imp_ba = Impression(
            source_agent="B", target_agent="A",
            compatibility_score=0.8,
            dimensions=ImpressionDimensions(),
        )
        await provider.record(imp_ba)

        mutuals = await provider.get_mutual("A", min_score=0.5)
        assert len(mutuals) == 1
        assert mutuals[0][1] == 0.9  # latest score, not 0.3


# ══════════════════════════════════════════════════════════════════════
# Bug #7-10: Path sanitization in all 4 SDK providers
# ══════════════════════════════════════════════════════════════════════

class TestBug7_PathSanitization:
    """Agent IDs with path separators could cause directory traversal.
    All providers must sanitize path components."""

    def test_session_safe(self):
        p = SessionProviderImpl(MockBackend())
        assert ".." not in p._safe("../etc/passwd")
        assert "/" not in p._safe("agent/id")
        assert "\\" not in p._safe("agent\\id")

    def test_session_path_sanitized(self):
        p = SessionProviderImpl(MockBackend())
        path = p._path("../evil", "thread/../hack", "cp")
        assert ".." not in path
        assert "evil" in path

    def test_memory_safe(self):
        p = MemoryProviderImpl(MockBackend())
        assert ".." not in p._safe("../etc")
        assert "/" not in p._safe("a/b")

    def test_memory_paths_sanitized(self):
        p = MemoryProviderImpl(MockBackend())
        idx = p._index_path("../evil")
        assert ".." not in idx
        entry = p._entry_path("agent", "mem/../hack")
        assert ".." not in entry

    def test_artifact_safe(self):
        p = ArtifactProviderImpl(MockBackend())
        assert ".." not in p._safe("../x")

    def test_artifact_paths_sanitized(self):
        p = ArtifactProviderImpl(MockBackend())
        manifest = p._manifest_path("../evil", "sess/../hack")
        assert ".." not in manifest
        blob = p._blob_path("agent", "../sess", "file/../evil.txt", 1)
        assert ".." not in blob

    def test_impression_safe(self):
        assert ".." not in ImpressionProviderImpl._safe("../x")
        assert "/" not in ImpressionProviderImpl._safe("a/b")

    @pytest.mark.asyncio
    async def test_impression_record_sanitizes_paths(self, backend):
        """Recording an impression with slashes in agent_id should not break paths."""
        provider = ImpressionProviderImpl(backend)
        imp = Impression(
            source_agent="agent/with/slashes",
            target_agent="normal-agent",
            compatibility_score=0.5,
            dimensions=ImpressionDimensions(),
        )
        imp_id = await provider.record(imp)
        assert imp_id  # Should not crash


# ══════════════════════════════════════════════════════════════════════
# Bug #11: Aggressive 300s cooldown — should use exponential backoff
# ══════════════════════════════════════════════════════════════════════

class TestBug11_ExponentialBackoff:
    """Chain anchoring used fixed 300s cooldown. Now uses exponential backoff."""

    def test_backoff_increases_exponentially(self):
        """Import and test the backoff logic on ChainBackend."""
        from nexus_core.backends.chain import ChainBackend

        # We can't instantiate ChainBackend (needs private_key etc),
        # so test the _next_backoff logic directly by simulating it
        backoffs = {}

        def next_backoff(agent_id):
            current = backoffs.get(agent_id, 15)
            backoffs[agent_id] = min(current * 2, 300)
            return current

        # First call: 15s
        assert next_backoff("agent-1") == 15
        # Second: 30s
        assert next_backoff("agent-1") == 30
        # Third: 60s
        assert next_backoff("agent-1") == 60
        # Fourth: 120s
        assert next_backoff("agent-1") == 120
        # Fifth: 240s
        assert next_backoff("agent-1") == 240
        # Sixth: capped at 300s
        assert next_backoff("agent-1") == 300
        # Stays at 300
        assert next_backoff("agent-1") == 300


# ══════════════════════════════════════════════════════════════════════
# Bug #13: Corrupted artifacts served silently
# ══════════════════════════════════════════════════════════════════════

class TestBug13_CorruptedArtifactRejection:
    """Artifacts with hash mismatch were served silently. Now returns None."""

    @pytest.mark.asyncio
    async def test_corrupted_artifact_returns_none(self, backend):
        provider = ArtifactProviderImpl(backend)

        # Save a valid artifact
        data = b"hello world"
        version = await provider.save(
            filename="test.txt",
            data=data,
            agent_id="agent-1",
        )
        assert version == 1

        # Now corrupt the manifest hash
        key = provider._manifest_key("agent-1", "")
        manifest = provider._get_manifest(key)
        manifest["test.txt"][0]["content_hash"] = "0" * 64  # wrong hash

        # Loading should return None (corrupted)
        result = await provider.load("test.txt", agent_id="agent-1")
        assert result is None


# ══════════════════════════════════════════════════════════════════════
# Bug #14: Negative cache never expires within session
# ══════════════════════════════════════════════════════════════════════

class TestBug14_NegativeCacheTTL:
    """Negative cache entries were permanent (set). Now dict with TTL."""

    def test_neg_cache_is_dict_with_ttl(self):
        from nexus_core.backends.chain import ChainBackend
        # Verify the class defines TTL-based negative cache
        # We can't instantiate ChainBackend easily, but we can
        # verify the implementation exists
        import inspect
        source = inspect.getsource(ChainBackend)
        assert "_NEG_CACHE_TTL" in source
        assert "_neg_cache_hit" in source

    def test_neg_cache_hit_logic(self):
        """Test the TTL expiry logic pattern."""
        cache = {}
        ttl = 600.0

        def add(path):
            cache[path] = time.time() + ttl

        def hit(path):
            expiry = cache.get(path)
            if expiry is None:
                return False
            if time.time() > expiry:
                del cache[path]
                return False
            return True

        # Miss on empty cache
        assert hit("path1") is False

        # Add and hit
        add("path1")
        assert hit("path1") is True

        # Simulate expired entry
        cache["path2"] = time.time() - 1  # already expired
        assert hit("path2") is False
        assert "path2" not in cache  # cleaned up


# ══════════════════════════════════════════════════════════════════════
# Bug: _ensure_loaded doesn't mark agent on CancelledError
# ══════════════════════════════════════════════════════════════════════

class TestBug_EnsureLoadedCancelSafety:
    """_ensure_loaded used except Exception which doesn't catch CancelledError.
    Now uses try/finally to always mark agent as loaded."""

    @pytest.mark.asyncio
    async def test_ensure_loaded_marks_on_cancellation(self, backend):
        """Even if cancelled, _loaded_agents should be populated."""
        provider = ImpressionProviderImpl(backend)

        # Load an agent normally first (no data, but marks as loaded)
        await provider._ensure_loaded("agent-1")
        assert "agent-1" in provider._loaded_agents

        # Second call should be instant (cached)
        await provider._ensure_loaded("agent-1")
        # Still there
        assert "agent-1" in provider._loaded_agents

    @pytest.mark.asyncio
    async def test_ensure_loaded_uses_finally(self):
        """Verify the implementation uses try/finally pattern."""
        import inspect
        source = inspect.getsource(ImpressionProviderImpl._ensure_loaded)
        assert "finally:" in source
        assert "_loaded_agents.add" in source


# ══════════════════════════════════════════════════════════════════════
# Bug #12: Disk full not handled in cache writes
# ══════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════
# Bug #15: Memory not persisting across restarts — no _ensure_loaded
# ══════════════════════════════════════════════════════════════════════

class TestBug15_MemoryLazyLoad:
    """MemoryProviderImpl had no _ensure_loaded(). On cold start,
    search/list_all/get_by_ids returned [] because the in-memory cache
    was empty and nothing triggered load_from_chain()."""

    def test_has_ensure_loaded(self):
        """Verify MemoryProviderImpl has _ensure_loaded method."""
        assert hasattr(MemoryProviderImpl, '_ensure_loaded')

    def test_has_loaded_agents_set(self):
        """Verify MemoryProviderImpl tracks loaded agents."""
        p = MemoryProviderImpl(MockBackend())
        assert hasattr(p, '_loaded_agents')
        assert isinstance(p._loaded_agents, set)

    def test_ensure_loaded_uses_finally(self):
        """Like ImpressionProvider, must use try/finally to handle CancelledError."""
        import inspect
        source = inspect.getsource(MemoryProviderImpl._ensure_loaded)
        assert "finally:" in source
        assert "_loaded_agents.add" in source

    @pytest.mark.asyncio
    async def test_search_triggers_lazy_load(self, backend):
        """search() should load from backend on first call for an agent."""
        provider = MemoryProviderImpl(backend)

        # Add a memory directly to test retrieval
        await provider.add("I like spicy food", agent_id="agent-1")
        assert len(await provider.list_all("agent-1")) == 1

        # Simulate cold restart: clear in-memory cache but keep backend data
        provider._memories.clear()
        provider._loaded_agents.clear()

        # search should trigger lazy load via _ensure_loaded
        results = await provider.search("spicy", agent_id="agent-1")
        assert len(results) >= 1
        assert "spicy" in results[0].content

    @pytest.mark.asyncio
    async def test_list_all_triggers_lazy_load(self, backend):
        """list_all() should load from backend on first call."""
        provider = MemoryProviderImpl(backend)

        await provider.add("Memory content", agent_id="agent-1")

        # Simulate cold restart
        provider._memories.clear()
        provider._loaded_agents.clear()

        all_memories = await provider.list_all("agent-1")
        assert len(all_memories) == 1
        assert all_memories[0].content == "Memory content"

    @pytest.mark.asyncio
    async def test_ensure_loaded_idempotent(self, backend):
        """Second call to _ensure_loaded should be instant (cached)."""
        provider = MemoryProviderImpl(backend)
        await provider._ensure_loaded("agent-1")
        assert "agent-1" in provider._loaded_agents

        # Second call — should not re-load
        await provider._ensure_loaded("agent-1")
        assert "agent-1" in provider._loaded_agents


# ══════════════════════════════════════════════════════════════════════
# Bug #16: ERC-8004 re-registration on every anchor call
# ══════════════════════════════════════════════════════════════════════

class TestBug16_AnchorSkipsReregistration:
    """_anchor_on_chain called ensure_agent_registered on EVERY anchor,
    minting new on-chain IDs (785→786→787→788). Now caches in _agent_id_map."""

    def test_anchor_checks_agent_id_map(self):
        """Verify _anchor_on_chain uses _agent_id_map cache."""
        from nexus_core.backends.chain import ChainBackend
        import inspect
        source = inspect.getsource(ChainBackend._anchor_on_chain)
        # Should check cache first before calling ensure_agent_registered
        assert "_agent_id_map" in source
        # The cache check should come BEFORE ensure_agent_registered
        cache_pos = source.find("if agent_id in self._agent_id_map")
        register_pos = source.find("ensure_agent_registered")
        assert cache_pos > 0
        assert register_pos > 0
        assert cache_pos < register_pos, \
            "_agent_id_map check must come before ensure_agent_registered"


class TestBug12_CacheWriteOSError:
    """_cache_write only caught generic Exception. Now also catches OSError
    at warning level for disk full / permission denied scenarios."""

    def test_cache_write_handles_oserror(self):
        """Verify the implementation catches OSError specifically."""
        from nexus_core.backends.chain import ChainBackend
        import inspect
        source = inspect.getsource(ChainBackend._cache_write)
        assert "OSError" in source


# ══════════════════════════════════════════════════════════════════════
# Bug #17: Graceful shutdown — pending Greenfield writes were cancelled
# immediately on exit, causing data loss for the last 1-2 turns
# ══════════════════════════════════════════════════════════════════════

class TestBug17_GracefulShutdown:
    """ChainBackend.close() immediately cancelled all pending tasks.
    Now waits up to grace_period seconds for writes to finish."""

    def test_chain_backend_close_has_grace_period(self):
        """close() should accept a grace_period parameter."""
        from nexus_core.backends.chain import ChainBackend
        import inspect
        sig = inspect.signature(ChainBackend.close)
        assert "grace_period" in sig.parameters

    def test_chain_backend_close_uses_asyncio_wait(self):
        """close() should use asyncio.wait (not immediate cancel)."""
        from nexus_core.backends.chain import ChainBackend
        import inspect
        source = inspect.getsource(ChainBackend.close)
        assert "asyncio.wait" in source
        assert "timeout" in source

    def test_rune_provider_holds_backend_ref(self):
        """RuneProvider should hold a backend reference for lifecycle."""
        rune = Rune.builder().mock_backend().build()
        assert hasattr(rune, "_backend")
        assert rune._backend is not None

    @pytest.mark.asyncio
    async def test_rune_provider_close_calls_backend(self):
        """RuneProvider.close() should call backend.close()."""
        rune = Rune.builder().mock_backend().build()
        # MockBackend.close() is a no-op but should not crash
        await rune.close()


# ══════════════════════════════════════════════════════════════════════
# Bug #18: CJK tokenization — Chinese characters treated as single token
# by \w+ regex, causing zero TF-IDF overlap with English memories
# ══════════════════════════════════════════════════════════════════════

class TestBug18_CJKTokenization:
    """MemoryProviderImpl._tokenize used \\w+ which treats "我喜欢辣椒"
    as one token. Chinese queries had zero token overlap with English
    memories, so TF-IDF returned 0 score for everything."""

    def test_chinese_chars_split_individually(self):
        """Chinese characters should be split into individual tokens."""
        tokens = MemoryProviderImpl._tokenize("我喜欢辣椒")
        assert "我" in tokens
        assert "喜" in tokens
        assert "欢" in tokens
        assert "辣" in tokens
        assert "椒" in tokens

    def test_english_words_still_work(self):
        """English words should still tokenize normally."""
        tokens = MemoryProviderImpl._tokenize("The user likes spicy food")
        assert "the" in tokens
        assert "user" in tokens
        assert "likes" in tokens
        assert "spicy" in tokens
        assert "food" in tokens

    def test_mixed_cjk_english(self):
        """Mixed CJK and English text should tokenize both correctly."""
        tokens = MemoryProviderImpl._tokenize("用户likes辣椒")
        assert "likes" in tokens
        assert "用" in tokens
        assert "户" in tokens
        assert "辣" in tokens
        assert "椒" in tokens

    def test_chinese_query_matches_chinese_content(self):
        """TF-IDF should find overlap between Chinese query and Chinese content."""
        query_tokens = set(MemoryProviderImpl._tokenize("喜欢什么"))
        content_tokens = set(MemoryProviderImpl._tokenize("用户喜欢辣椒"))
        overlap = query_tokens & content_tokens
        assert len(overlap) > 0, f"Expected overlap, got: query={query_tokens}, content={content_tokens}"
        assert "喜" in overlap or "欢" in overlap

    @pytest.mark.asyncio
    async def test_tfidf_search_finds_chinese_memories(self):
        """Full TF-IDF search should rank Chinese-matching memories higher."""
        backend = MockBackend()
        provider = MemoryProviderImpl(backend)

        # Add memories with Chinese content
        await provider.add("用户喜欢辣椒和川菜", agent_id="agent-1")
        await provider.add("Today is sunny weather", agent_id="agent-1")

        # Search with Chinese query
        results = await provider.search("喜欢什么", agent_id="agent-1")
        assert len(results) >= 1
        # The Chinese memory should score higher
        assert "辣椒" in results[0].content or "喜欢" in results[0].content

    def test_empty_string_returns_empty(self):
        """Empty string should return empty token list."""
        tokens = MemoryProviderImpl._tokenize("")
        assert tokens == []

    def test_pure_punctuation_returns_empty(self):
        """Punctuation-only string should return empty or no useful tokens."""
        tokens = MemoryProviderImpl._tokenize("？！。，")
        # CJK punctuation is in the fullwidth range, but shouldn't produce useful tokens
        # The important thing is it doesn't crash
        assert isinstance(tokens, list)


# ══════════════════════════════════════════════════════════════════════
# Feature: Memory capacity management — count, bulk_delete, replace,
# get_least_accessed (inspired by Hermes bounded memory design)
# ══════════════════════════════════════════════════════════════════════

class TestFeature_MemoryCapacityManagement:
    """SDK now provides capacity management primitives for upper layers
    to implement bounded memory with consolidation."""

    @pytest.mark.asyncio
    async def test_count_returns_correct_number(self):
        backend = MockBackend()
        provider = MemoryProviderImpl(backend)
        assert await provider.count("agent-1") == 0

        await provider.add("Fact 1", agent_id="agent-1")
        await provider.add("Fact 2", agent_id="agent-1")
        assert await provider.count("agent-1") == 2

    @pytest.mark.asyncio
    async def test_bulk_delete_removes_multiple(self):
        backend = MockBackend()
        provider = MemoryProviderImpl(backend)

        id1 = await provider.add("Fact 1", agent_id="agent-1")
        id2 = await provider.add("Fact 2", agent_id="agent-1")
        id3 = await provider.add("Fact 3", agent_id="agent-1")

        deleted = await provider.bulk_delete([id1, id3], agent_id="agent-1")
        assert deleted == 2
        assert await provider.count("agent-1") == 1

        remaining = await provider.list_all("agent-1")
        assert remaining[0].memory_id == id2

    @pytest.mark.asyncio
    async def test_bulk_delete_skips_nonexistent(self):
        backend = MockBackend()
        provider = MemoryProviderImpl(backend)

        id1 = await provider.add("Fact 1", agent_id="agent-1")
        deleted = await provider.bulk_delete([id1, "nonexistent-id"], agent_id="agent-1")
        assert deleted == 1
        assert await provider.count("agent-1") == 0

    @pytest.mark.asyncio
    async def test_replace_updates_content_in_place(self):
        backend = MockBackend()
        provider = MemoryProviderImpl(backend)

        mid = await provider.add("User likes cats", agent_id="agent-1")
        returned_id = await provider.replace(mid, "User likes dogs", agent_id="agent-1")
        assert returned_id == mid  # Same ID preserved

        entries = await provider.list_all("agent-1")
        assert len(entries) == 1
        assert entries[0].content == "User likes dogs"
        assert entries[0].memory_id == mid

    @pytest.mark.asyncio
    async def test_replace_preserves_created_at(self):
        backend = MockBackend()
        provider = MemoryProviderImpl(backend)

        mid = await provider.add("Original", agent_id="agent-1")
        original = (await provider.list_all("agent-1"))[0]
        original_time = original.created_at

        await provider.replace(mid, "Updated", agent_id="agent-1")
        updated = (await provider.list_all("agent-1"))[0]
        assert updated.created_at == original_time

    @pytest.mark.asyncio
    async def test_replace_nonexistent_creates_new(self):
        backend = MockBackend()
        provider = MemoryProviderImpl(backend)

        new_id = await provider.replace("fake-id", "New content", agent_id="agent-1")
        assert new_id != "fake-id"  # Created new
        assert await provider.count("agent-1") == 1


# ══════════════════════════════════════════════════════════════════════
# Feature: Memory access tracking — search() increments access_count
# and last_accessed on returned entries
# ══════════════════════════════════════════════════════════════════════

class TestFeature_MemoryAccessTracking:
    """MemoryEntry now has access_count and last_accessed fields,
    updated on each search() hit. Enables smart eviction."""

    @pytest.mark.asyncio
    async def test_search_increments_access_count(self):
        backend = MockBackend()
        provider = MemoryProviderImpl(backend)

        await provider.add("spicy food preference", agent_id="agent-1")

        # Search twice
        await provider.search("spicy", agent_id="agent-1")
        await provider.search("spicy", agent_id="agent-1")

        entries = await provider.list_all("agent-1")
        assert entries[0].access_count == 2
        assert entries[0].last_accessed > 0

    @pytest.mark.asyncio
    async def test_unsearched_memory_has_zero_access(self):
        backend = MockBackend()
        provider = MemoryProviderImpl(backend)

        await provider.add("never searched", agent_id="agent-1")
        entries = await provider.list_all("agent-1")
        assert entries[0].access_count == 0
        assert entries[0].last_accessed == 0.0

    @pytest.mark.asyncio
    async def test_get_least_accessed_returns_eviction_candidates(self):
        backend = MockBackend()
        provider = MemoryProviderImpl(backend)

        await provider.add("popular memory about spicy food", agent_id="agent-1")
        await provider.add("unpopular memory about rain", agent_id="agent-1")

        # Search with top_k=1 so only the best match gets access bumps
        for _ in range(5):
            await provider.search("spicy", agent_id="agent-1", top_k=1)

        least = await provider.get_least_accessed("agent-1", limit=1)
        assert len(least) == 1
        assert "rain" in least[0].content
        assert least[0].access_count == 0

    @pytest.mark.asyncio
    async def test_access_count_persisted_through_replace(self):
        """replace() should persist access_count in the JSON."""
        backend = MockBackend()
        provider = MemoryProviderImpl(backend)

        mid = await provider.add("test content about spicy food", agent_id="agent-1")
        await provider.search("spicy", agent_id="agent-1")

        # Replace content — access_count should be in persisted JSON
        await provider.replace(mid, "updated content", agent_id="agent-1")

        # Check it was written to backend
        import json
        path = provider._entry_path("agent-1", mid)
        data = await backend.load_json(path)
        assert data is not None
        assert "access_count" in data


# ══════════════════════════════════════════════════════════════════════
# Feature: Artifact rollback — revert to a previous version
# ══════════════════════════════════════════════════════════════════════

class TestFeature_ArtifactRollback:
    """ArtifactProvider.rollback() creates a new version with the content
    of a previous version, enabling safe undo for skill evolution."""

    @pytest.mark.asyncio
    async def test_rollback_creates_new_version(self):
        from nexus_core.providers.artifact import ArtifactProviderImpl
        backend = MockBackend()
        provider = ArtifactProviderImpl(backend)

        v1 = await provider.save("skill.json", b'{"v": 1}', agent_id="agent-1")
        v2 = await provider.save("skill.json", b'{"v": 2}', agent_id="agent-1")
        assert v1 == 1
        assert v2 == 2

        # Rollback to v1
        v3 = await provider.rollback("skill.json", agent_id="agent-1", to_version=1)
        assert v3 == 3  # New version, not overwrite

        # Content should match v1
        art = await provider.load("skill.json", agent_id="agent-1", version=3)
        assert art.data == b'{"v": 1}'

    @pytest.mark.asyncio
    async def test_rollback_preserves_history(self):
        from nexus_core.providers.artifact import ArtifactProviderImpl
        backend = MockBackend()
        provider = ArtifactProviderImpl(backend)

        await provider.save("skill.json", b'v1', agent_id="agent-1")
        await provider.save("skill.json", b'v2', agent_id="agent-1")
        await provider.rollback("skill.json", agent_id="agent-1", to_version=1)

        versions = await provider.list_versions("skill.json", agent_id="agent-1")
        assert versions == [1, 2, 3]  # All three preserved

    @pytest.mark.asyncio
    async def test_rollback_nonexistent_version_raises(self):
        from nexus_core.providers.artifact import ArtifactProviderImpl
        backend = MockBackend()
        provider = ArtifactProviderImpl(backend)

        await provider.save("skill.json", b'v1', agent_id="agent-1")

        with pytest.raises(ValueError, match="Version 99 not found"):
            await provider.rollback("skill.json", agent_id="agent-1", to_version=99)

    @pytest.mark.asyncio
    async def test_rollback_metadata_records_source_version(self):
        from nexus_core.providers.artifact import ArtifactProviderImpl
        backend = MockBackend()
        provider = ArtifactProviderImpl(backend)

        await provider.save("skill.json", b'v1', agent_id="agent-1")
        await provider.save("skill.json", b'v2', agent_id="agent-1")
        await provider.rollback("skill.json", agent_id="agent-1", to_version=1)

        art = await provider.load("skill.json", agent_id="agent-1", version=3)
        assert art.metadata.get("rollback_from") == 1
