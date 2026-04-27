"""
Tests for the refactored Rune Protocol architecture.

Tests cover all layers:
  1. MockBackend (Strategy)
  2. LocalBackend (Strategy)
  3. Provider implementations (Session, Memory, Artifact, Task)
  4. Builder + Facade (Rune.local, Rune.builder)
  5. Framework adapters (ADK, LangGraph, CrewAI)
  6. Full integration workflow
  7. AdapterRegistry
"""

import asyncio
import os
import tempfile
import pytest

from nexus_core.core.models import Checkpoint, MemoryEntry, Artifact
from nexus_core.core.backend import StorageBackend
from nexus_core.core.providers import (
    RuneProvider, RuneSessionProvider, RuneMemoryProvider,
    RuneArtifactProvider, RuneTaskProvider,
)
from nexus_core.core.flush import FlushPolicy, FlushBuffer, WriteAheadLog
from nexus_core.backends.mock import MockBackend
from nexus_core.backends.local import LocalBackend
from nexus_core.providers.session import SessionProviderImpl
from nexus_core.providers.memory import MemoryProviderImpl
from nexus_core.providers.artifact import ArtifactProviderImpl
from nexus_core.providers.task import TaskProviderImpl
from nexus_core.builder import Rune, RuneBuilder
from nexus_core.adapters.registry import AdapterRegistry


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_backend():
    return MockBackend()


@pytest.fixture
def local_backend(tmp_path):
    return LocalBackend(base_dir=str(tmp_path / "rune_test"))


@pytest.fixture
def session_provider(mock_backend):
    return SessionProviderImpl(mock_backend)


@pytest.fixture
def memory_provider(mock_backend):
    return MemoryProviderImpl(mock_backend)


@pytest.fixture
def artifact_provider(mock_backend):
    return ArtifactProviderImpl(mock_backend)


@pytest.fixture
def task_provider(mock_backend):
    return TaskProviderImpl(mock_backend)


# ═══════════════════════════════════════════════════════════════════════
# 1. Data Models
# ═══════════════════════════════════════════════════════════════════════


class TestModels:
    def test_checkpoint_auto_id(self):
        cp = Checkpoint(agent_id="a1", thread_id="t1", state={"step": 1})
        assert cp.checkpoint_id  # auto-generated
        assert cp.created_at > 0

    def test_checkpoint_roundtrip(self):
        cp = Checkpoint(
            checkpoint_id="cp-1", thread_id="t1", agent_id="a1",
            state={"x": 42}, metadata={"fw": "test"}, parent_id="cp-0",
        )
        d = cp.to_dict()
        cp2 = Checkpoint.from_dict(d)
        assert cp2.checkpoint_id == "cp-1"
        assert cp2.state == {"x": 42}
        assert cp2.parent_id == "cp-0"

    def test_memory_entry_auto_id(self):
        entry = MemoryEntry(content="hello", agent_id="a1")
        assert entry.memory_id
        assert entry.created_at > 0

    def test_artifact_defaults(self):
        art = Artifact(filename="test.json", data=b'{"a":1}')
        assert art.version == 0
        assert art.created_at > 0


# ═══════════════════════════════════════════════════════════════════════
# 2. MockBackend
# ═══════════════════════════════════════════════════════════════════════


class TestMockBackend:
    @pytest.mark.asyncio
    async def test_json_store_load(self, mock_backend):
        h = await mock_backend.store_json("test/path.json", {"key": "value"})
        assert h  # non-empty hash
        data = await mock_backend.load_json("test/path.json")
        assert data == {"key": "value"}

    @pytest.mark.asyncio
    async def test_blob_store_load(self, mock_backend):
        h = await mock_backend.store_blob("test/blob.bin", b"hello world")
        assert h
        data = await mock_backend.load_blob("test/blob.bin")
        assert data == b"hello world"

    @pytest.mark.asyncio
    async def test_anchor_resolve(self, mock_backend):
        await mock_backend.anchor("agent-1", "abc123", "state")
        result = await mock_backend.resolve("agent-1", "state")
        assert result == "abc123"

    @pytest.mark.asyncio
    async def test_resolve_nonexistent(self, mock_backend):
        result = await mock_backend.resolve("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_paths(self, mock_backend):
        await mock_backend.store_json("agents/a1/sessions/s1.json", {"x": 1})
        await mock_backend.store_json("agents/a1/sessions/s2.json", {"x": 2})
        await mock_backend.store_json("agents/a2/sessions/s3.json", {"x": 3})
        paths = await mock_backend.list_paths("agents/a1/")
        assert len(paths) == 2

    @pytest.mark.asyncio
    async def test_delete(self, mock_backend):
        await mock_backend.store_json("test/del.json", {"x": 1})
        deleted = await mock_backend.delete("test/del.json")
        assert deleted is True
        assert await mock_backend.load_json("test/del.json") is None

    def test_reset(self, mock_backend):
        asyncio.run(mock_backend.store_json("a.json", {"x": 1}))
        mock_backend.reset()
        assert asyncio.run(mock_backend.load_json("a.json")) is None


# ═══════════════════════════════════════════════════════════════════════
# 3. LocalBackend
# ═══════════════════════════════════════════════════════════════════════


class TestLocalBackend:
    @pytest.mark.asyncio
    async def test_json_roundtrip(self, local_backend):
        h = await local_backend.store_json("agents/a1/test.json", {"x": 42})
        assert h
        data = await local_backend.load_json("agents/a1/test.json")
        assert data == {"x": 42}

    @pytest.mark.asyncio
    async def test_blob_roundtrip(self, local_backend):
        h = await local_backend.store_blob("agents/a1/blob.bin", b"binary data")
        assert h
        data = await local_backend.load_blob("agents/a1/blob.bin")
        assert data == b"binary data"

    @pytest.mark.asyncio
    async def test_anchor_resolve(self, local_backend):
        await local_backend.anchor("agent-1", "hash123", "state")
        result = await local_backend.resolve("agent-1", "state")
        assert result == "hash123"

    @pytest.mark.asyncio
    async def test_load_nonexistent(self, local_backend):
        assert await local_backend.load_json("nonexistent.json") is None
        assert await local_backend.load_blob("nonexistent.bin") is None


# ═══════════════════════════════════════════════════════════════════════
# 4. Session Provider
# ═══════════════════════════════════════════════════════════════════════


class TestSessionProvider:
    @pytest.mark.asyncio
    async def test_save_and_load(self, session_provider):
        cp = Checkpoint(
            agent_id="agent-1", thread_id="thread-1",
            state={"step": 3, "result": "done"},
        )
        cp_id = await session_provider.save_checkpoint(cp)
        assert cp_id == cp.checkpoint_id

        loaded = await session_provider.load_checkpoint("agent-1", "thread-1")
        assert loaded is not None
        assert loaded.state == {"step": 3, "result": "done"}

    @pytest.mark.asyncio
    async def test_parent_linking(self, session_provider):
        cp1 = Checkpoint(agent_id="a1", thread_id="t1", state={"step": 1})
        cp2 = Checkpoint(agent_id="a1", thread_id="t1", state={"step": 2})
        await session_provider.save_checkpoint(cp1)
        await session_provider.save_checkpoint(cp2)

        loaded = await session_provider.load_checkpoint("a1", "t1")
        assert loaded.parent_id == cp1.checkpoint_id

    @pytest.mark.asyncio
    async def test_list_checkpoints(self, session_provider):
        for i in range(5):
            cp = Checkpoint(agent_id="a1", thread_id="t1", state={"step": i})
            await session_provider.save_checkpoint(cp)

        cps = await session_provider.list_checkpoints("a1", "t1")
        assert len(cps) == 5

    @pytest.mark.asyncio
    async def test_delete_checkpoint(self, session_provider):
        cp = Checkpoint(agent_id="a1", thread_id="t1", state={"x": 1})
        await session_provider.save_checkpoint(cp)
        await session_provider.delete_checkpoint("a1", "t1")
        loaded = await session_provider.load_checkpoint("a1", "t1")
        assert loaded is None

    @pytest.mark.asyncio
    async def test_load_specific_checkpoint(self, session_provider):
        cp1 = Checkpoint(agent_id="a1", thread_id="t1", state={"step": 1})
        cp2 = Checkpoint(agent_id="a1", thread_id="t1", state={"step": 2})
        await session_provider.save_checkpoint(cp1)
        await session_provider.save_checkpoint(cp2)

        loaded = await session_provider.load_checkpoint("a1", "t1", cp1.checkpoint_id)
        assert loaded.state == {"step": 1}

    @pytest.mark.asyncio
    async def test_load_nonexistent(self, session_provider):
        result = await session_provider.load_checkpoint("no-agent", "no-thread")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# 5. Memory Provider
# ═══════════════════════════════════════════════════════════════════════


class TestMemoryProvider:
    @pytest.mark.asyncio
    async def test_add_and_search(self, memory_provider):
        await memory_provider.add("Revenue grew 31% in Q4", agent_id="a1")
        await memory_provider.add("APAC was the fastest region", agent_id="a1")
        await memory_provider.add("Weather was sunny today", agent_id="a1")

        results = await memory_provider.search("revenue growth", agent_id="a1")
        assert len(results) > 0
        # Revenue-related entry should score highest
        assert "revenue" in results[0].content.lower() or "grew" in results[0].content.lower()

    @pytest.mark.asyncio
    async def test_deduplication(self, memory_provider):
        mid1 = await memory_provider.add("same content", agent_id="a1")
        mid2 = await memory_provider.add("same content", agent_id="a1")
        assert mid1 == mid2  # deduped

        all_mems = await memory_provider.list_all("a1")
        assert len(all_mems) == 1

    @pytest.mark.asyncio
    async def test_delete(self, memory_provider):
        mid = await memory_provider.add("to be deleted", agent_id="a1")
        await memory_provider.delete(mid, agent_id="a1")
        all_mems = await memory_provider.list_all("a1")
        assert len(all_mems) == 0

    @pytest.mark.asyncio
    async def test_list_all(self, memory_provider):
        await memory_provider.add("fact 1", agent_id="a1")
        await memory_provider.add("fact 2", agent_id="a1")
        all_mems = await memory_provider.list_all("a1")
        assert len(all_mems) == 2

    @pytest.mark.asyncio
    async def test_search_empty(self, memory_provider):
        results = await memory_provider.search("anything", agent_id="a1")
        assert results == []

    @pytest.mark.asyncio
    async def test_cold_start_loading(self, mock_backend):
        """Test that memories persist and can be reloaded."""
        mp1 = MemoryProviderImpl(mock_backend)
        await mp1.add("persisted fact", agent_id="a1")

        # New provider instance, same backend
        mp2 = MemoryProviderImpl(mock_backend)
        loaded = await mp2.load_from_chain("a1")
        assert loaded == 1

        results = await mp2.search("persisted", agent_id="a1")
        assert len(results) == 1


# ═══════════════════════════════════════════════════════════════════════
# 6. Artifact Provider
# ═══════════════════════════════════════════════════════════════════════


class TestArtifactProvider:
    @pytest.mark.asyncio
    async def test_save_and_load(self, artifact_provider):
        data = b'{"report": "Q4 results"}'
        version = await artifact_provider.save("report.json", data, agent_id="a1")
        assert version == 1

        artifact = await artifact_provider.load("report.json", agent_id="a1")
        assert artifact is not None
        assert artifact.data == data
        assert artifact.version == 1

    @pytest.mark.asyncio
    async def test_versioning(self, artifact_provider):
        v1 = await artifact_provider.save("r.json", b"v1", agent_id="a1")
        v2 = await artifact_provider.save("r.json", b"v2", agent_id="a1")
        assert v1 == 1
        assert v2 == 2

        # Latest
        latest = await artifact_provider.load("r.json", agent_id="a1")
        assert latest.data == b"v2"

        # Specific version
        old = await artifact_provider.load("r.json", agent_id="a1", version=1)
        assert old.data == b"v1"

    @pytest.mark.asyncio
    async def test_list_artifacts(self, artifact_provider):
        await artifact_provider.save("a.json", b"a", agent_id="a1")
        await artifact_provider.save("b.json", b"b", agent_id="a1")
        files = await artifact_provider.list_artifacts(agent_id="a1")
        assert sorted(files) == ["a.json", "b.json"]

    @pytest.mark.asyncio
    async def test_list_versions(self, artifact_provider):
        await artifact_provider.save("r.json", b"v1", agent_id="a1")
        await artifact_provider.save("r.json", b"v2", agent_id="a1")
        versions = await artifact_provider.list_versions("r.json", agent_id="a1")
        assert versions == [1, 2]

    @pytest.mark.asyncio
    async def test_load_nonexistent(self, artifact_provider):
        result = await artifact_provider.load("nofile.json", agent_id="a1")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# 7. Task Provider
# ═══════════════════════════════════════════════════════════════════════


class TestTaskProvider:
    @pytest.mark.asyncio
    async def test_create_task(self, task_provider):
        record = await task_provider.create_task("task-1", "agent-1")
        assert record["task_id"] == "task-1"
        assert record["status"] == "pending"
        assert record["version"] == 0

    @pytest.mark.asyncio
    async def test_update_task(self, task_provider):
        await task_provider.create_task("task-1", "agent-1")
        updated = await task_provider.update_task(
            "task-1", {"progress": 50}, status="running",
        )
        assert updated["status"] == "running"
        assert updated["version"] == 1

    @pytest.mark.asyncio
    async def test_get_task(self, task_provider):
        await task_provider.create_task("task-1", "agent-1")
        record = await task_provider.get_task("task-1")
        assert record is not None
        assert record["agent_id"] == "agent-1"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, task_provider):
        result = await task_provider.get_task("no-task")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# 8. Builder + Facade
# ═══════════════════════════════════════════════════════════════════════


class TestBuilder:
    def test_rune_local(self, tmp_path):
        rune = Rune.local(base_dir=str(tmp_path / "test_local"))
        assert isinstance(rune, RuneProvider)
        assert isinstance(rune.sessions, RuneSessionProvider)
        assert isinstance(rune.memory, RuneMemoryProvider)
        assert isinstance(rune.artifacts, RuneArtifactProvider)
        assert isinstance(rune.tasks, RuneTaskProvider)

    def test_builder_mock(self):
        rune = Rune.builder().mock_backend().build()
        assert isinstance(rune, RuneProvider)

    def test_builder_chain(self):
        rune = (
            Rune.builder()
            .mock_backend()
            .flush_policy(FlushPolicy.sync_every())
            .runtime_id("test-runtime")
            .build()
        )
        assert isinstance(rune, RuneProvider)

    def test_builder_default_backend(self, tmp_path):
        # When no backend set, defaults to LocalBackend
        os.chdir(tmp_path)
        rune = Rune.builder().build()
        assert isinstance(rune, RuneProvider)

    @pytest.mark.asyncio
    async def test_full_workflow(self):
        """Builder → save checkpoint → add memory → save artifact → load all."""
        rune = Rune.builder().mock_backend().build()

        # Session
        cp = Checkpoint(agent_id="a1", thread_id="t1", state={"step": 1})
        await rune.sessions.save_checkpoint(cp)
        loaded = await rune.sessions.load_checkpoint("a1", "t1")
        assert loaded.state == {"step": 1}

        # Memory
        await rune.memory.add("important fact", agent_id="a1")
        results = await rune.memory.search("important", agent_id="a1")
        assert len(results) > 0

        # Artifact
        version = await rune.artifacts.save("out.json", b'{"x":1}', agent_id="a1")
        assert version == 1
        art = await rune.artifacts.load("out.json", agent_id="a1")
        assert art.data == b'{"x":1}'

        # Task
        task = await rune.tasks.create_task("task-1", "a1")
        assert task["status"] == "pending"


# ═══════════════════════════════════════════════════════════════════════
# 9. Adapter Registry
# ═══════════════════════════════════════════════════════════════════════


class TestAdapterRegistry:
    def setup_method(self):
        AdapterRegistry.clear()

    def test_register_and_get(self):
        class DummyAdapter:
            pass
        AdapterRegistry.register("dummy", DummyAdapter)
        assert AdapterRegistry.get("dummy") is DummyAdapter

    def test_available(self):
        class A: pass
        class B: pass
        AdapterRegistry.register("a", A)
        AdapterRegistry.register("b", B)
        assert sorted(AdapterRegistry.available()) == ["a", "b"]

    def test_unknown_framework(self):
        with pytest.raises(ValueError, match="Unknown framework"):
            AdapterRegistry.get("nonexistent")


# ═══════════════════════════════════════════════════════════════════════
# 10. LangGraph Adapter
# ═══════════════════════════════════════════════════════════════════════


class TestLangGraphAdapter:
    @pytest.mark.asyncio
    async def test_put_and_get(self, session_provider):
        from nexus_core.adapters.langgraph import RuneCheckpointer

        ckpt = RuneCheckpointer(session_provider, agent_id="lg-agent")
        config = {"configurable": {"thread_id": "conv-1"}}

        checkpoint = {
            "v": 1,
            "id": "cp-001",
            "ts": "2025-01-01T00:00:00Z",
            "channel_values": {"messages": ["hello"]},
            "channel_versions": {},
            "versions_seen": {},
            "pending_sends": [],
        }

        result = await ckpt.aput(config, checkpoint, metadata={"source": "test"})
        assert "configurable" in result

        loaded = await ckpt.aget_tuple(config)
        assert loaded is not None
        assert loaded["checkpoint"]["channel_values"]["messages"] == ["hello"]

    @pytest.mark.asyncio
    async def test_list(self, session_provider):
        from nexus_core.adapters.langgraph import RuneCheckpointer

        ckpt = RuneCheckpointer(session_provider, agent_id="lg-agent")
        config = {"configurable": {"thread_id": "conv-1"}}

        for i in range(3):
            checkpoint = {
                "v": 1, "id": f"cp-{i}",
                "ts": f"2025-01-0{i+1}T00:00:00Z",
                "channel_values": {"step": i},
                "channel_versions": {},
                "versions_seen": {},
                "pending_sends": [],
            }
            await ckpt.aput(config, checkpoint)

        results = []
        async for item in ckpt.alist(config):
            results.append(item)
        assert len(results) == 3


# ═══════════════════════════════════════════════════════════════════════
# 11. CrewAI Adapter
# ═══════════════════════════════════════════════════════════════════════


class TestCrewAIAdapter:
    def test_save_and_search(self, memory_provider):
        from nexus_core.adapters.crewai import RuneCrewStorage

        storage = RuneCrewStorage(memory_provider, agent_id="crew-1")
        mid = storage.save("APAC revenue grew 31%")
        assert mid

        results = storage.search("revenue growth")
        assert len(results) > 0

    def test_delete(self, memory_provider):
        from nexus_core.adapters.crewai import RuneCrewStorage

        storage = RuneCrewStorage(memory_provider, agent_id="crew-1")
        mid = storage.save("temp data")
        storage.delete(mid)

    def test_reset(self, memory_provider):
        from nexus_core.adapters.crewai import RuneCrewStorage

        storage = RuneCrewStorage(memory_provider, agent_id="crew-1")
        storage.save("fact 1")
        storage.save("fact 2")
        storage.reset()

    def test_checkpoint_storage(self, artifact_provider):
        from nexus_core.adapters.crewai import RuneCrewCheckpointStorage

        cs = RuneCrewCheckpointStorage(artifact_provider, agent_id="crew-1")
        version = cs.save("task-1", {"result": "analysis complete"})
        assert version == 1

        loaded = cs.load("task-1")
        assert loaded is not None
        assert "analysis complete" in loaded


# ═══════════════════════════════════════════════════════════════════════
# 12. ADK Adapter
# ═══════════════════════════════════════════════════════════════════════


class TestADKAdapter:
    @pytest.mark.asyncio
    async def test_session_create_and_get(self, session_provider):
        from nexus_core.adapters.adk import RuneSessionService

        svc = RuneSessionService(session_provider)
        session = await svc.create_session(app_name="test", user_id="user-1")
        assert session is not None

        if isinstance(session, dict):
            sid = session["id"]
        else:
            sid = session.id

        loaded = await svc.get_session(app_name="test", user_id="user-1", session_id=sid)
        assert loaded is not None

    @pytest.mark.asyncio
    async def test_session_delete(self, session_provider):
        from nexus_core.adapters.adk import RuneSessionService

        svc = RuneSessionService(session_provider)
        session = await svc.create_session(app_name="test", user_id="user-1")
        sid = session["id"] if isinstance(session, dict) else session.id
        await svc.delete_session(app_name="test", user_id="user-1", session_id=sid)
        loaded = await svc.get_session(app_name="test", user_id="user-1", session_id=sid)
        assert loaded is None

    @pytest.mark.asyncio
    async def test_memory_add_and_search(self, memory_provider):
        from nexus_core.adapters.adk import RuneMemoryService

        mem_svc = RuneMemoryService(memory_provider)

        # Add via dict-based session (no ADK installed)
        session = {
            "app_name": "test", "user_id": "user-1",
            "events": [{"text": "Revenue grew 25% in APAC"}],
        }
        await mem_svc.add_session_to_memory(session)

        results = await mem_svc.search_memory(
            app_name="test", user_id="user-1", query="revenue",
        )
        # Results could be list or SearchMemoryResponse
        if isinstance(results, list):
            assert len(results) > 0
        else:
            assert len(results.memories) > 0

    @pytest.mark.asyncio
    async def test_artifact_save_and_load(self, artifact_provider):
        from nexus_core.adapters.adk import RuneArtifactService

        art_svc = RuneArtifactService(artifact_provider)
        version = await art_svc.save_artifact(
            b"report data", app_name="test", user_id="user-1",
            metadata={"filename": "report.pdf"},
        )
        assert version == 1

        loaded = await art_svc.load_artifact(
            "report.pdf", app_name="test", user_id="user-1",
        )
        assert loaded is not None


# ═══════════════════════════════════════════════════════════════════════
# 13. FlushPolicy (from core)
# ═══════════════════════════════════════════════════════════════════════


class TestFlushPolicy:
    def test_defaults(self):
        policy = FlushPolicy()
        assert policy.every_n_events == 5
        assert policy.interval_seconds == 30.0

    def test_sync_every(self):
        policy = FlushPolicy.sync_every()
        assert policy.every_n_events == 1
        assert policy.wal_enabled is False

    def test_manual(self):
        policy = FlushPolicy.manual()
        assert policy.every_n_events == 0
        assert policy.interval_seconds == 0

    def test_aggressive(self):
        policy = FlushPolicy.aggressive()
        assert policy.every_n_events == 20


# ═══════════════════════════════════════════════════════════════════════
# 14. Context Manager
# ═══════════════════════════════════════════════════════════════════════


class TestContextManager:
    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        async with Rune.builder().mock_backend().build() as rune:
            cp = Checkpoint(agent_id="a1", thread_id="t1", state={"x": 1})
            await rune.sessions.save_checkpoint(cp)
            loaded = await rune.sessions.load_checkpoint("a1", "t1")
            assert loaded.state == {"x": 1}
