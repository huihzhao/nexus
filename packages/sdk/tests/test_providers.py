"""
Tests for framework-agnostic Rune Providers.

Tests the provider interfaces, concrete implementations, and Rune factory.
Uses the new architecture: nexus_core.local() / MockBackend / provider impls.
"""

import asyncio
import os
import shutil
import pytest
from pathlib import Path

import nexus_core
from nexus_core import (
    Checkpoint,
    MemoryEntry,
    Artifact,
    MockBackend,
    SessionProviderImpl,
    MemoryProviderImpl,
    ArtifactProviderImpl,
    TaskProviderImpl,
)


# ── Fixtures ────────────────────────────────────────────────────────

TEST_DIR = "/tmp/rune_test_providers"


@pytest.fixture(autouse=True)
def clean_state():
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)
    yield
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)


@pytest.fixture
def rune():
    """Create a Rune instance with MockBackend for fast, isolated tests."""
    return nexus_core.builder().mock_backend().build()


@pytest.fixture
def sessions(rune):
    return rune.sessions


@pytest.fixture
def memory(rune):
    return rune.memory


@pytest.fixture
def artifacts(rune):
    return rune.artifacts


@pytest.fixture
def tasks(rune):
    return rune.tasks


# ── Data Model Tests ────────────────────────────────────────────────


class TestCheckpoint:
    def test_auto_id(self):
        cp = Checkpoint(thread_id="t1", agent_id="a1")
        assert cp.checkpoint_id  # auto-generated
        assert cp.created_at > 0

    def test_explicit_id(self):
        cp = Checkpoint(checkpoint_id="my-id", thread_id="t1", agent_id="a1")
        assert cp.checkpoint_id == "my-id"

    def test_state_default(self):
        cp = Checkpoint()
        assert cp.state == {}
        assert cp.metadata == {}


class TestMemoryEntryModel:
    def test_auto_id(self):
        entry = MemoryEntry(content="hello", agent_id="a1")
        assert entry.memory_id
        assert entry.created_at > 0

    def test_explicit_fields(self):
        entry = MemoryEntry(
            memory_id="m1", content="test", agent_id="a1",
            score=0.95, metadata={"key": "val"},
        )
        assert entry.score == 0.95
        assert entry.metadata["key"] == "val"


class TestArtifactModel:
    def test_defaults(self):
        art = Artifact(filename="report.json", data=b"hello")
        assert art.version == 0
        assert art.created_at > 0


# ── Session Provider Tests ──────────────────────────────────────────


class TestSessionProvider:
    def test_save_and_load(self, sessions):
        cp = Checkpoint(
            thread_id="thread-1",
            agent_id="test-agent",
            state={"step": 1, "data": "hello"},
        )
        result_id = asyncio.run(sessions.save_checkpoint(cp))
        assert result_id == cp.checkpoint_id

        loaded = asyncio.run(sessions.load_checkpoint("test-agent", "thread-1"))
        assert loaded is not None
        assert loaded.state["step"] == 1
        assert loaded.state["data"] == "hello"

    def test_load_latest(self, sessions):
        for i in range(3):
            cp = Checkpoint(
                thread_id="thread-1",
                agent_id="test-agent",
                state={"step": i},
            )
            asyncio.run(sessions.save_checkpoint(cp))

        latest = asyncio.run(sessions.load_checkpoint("test-agent", "thread-1"))
        assert latest.state["step"] == 2

    def test_load_specific_id(self, sessions):
        ids = []
        for i in range(3):
            cp = Checkpoint(
                thread_id="thread-1",
                agent_id="test-agent",
                state={"step": i},
            )
            asyncio.run(sessions.save_checkpoint(cp))
            ids.append(cp.checkpoint_id)

        loaded = asyncio.run(sessions.load_checkpoint(
            "test-agent", "thread-1", ids[1],
        ))
        assert loaded.state["step"] == 1

    def test_list_checkpoints(self, sessions):
        for i in range(5):
            cp = Checkpoint(
                thread_id="thread-1",
                agent_id="test-agent",
                state={"step": i},
            )
            asyncio.run(sessions.save_checkpoint(cp))

        all_cps = asyncio.run(sessions.list_checkpoints("test-agent"))
        assert len(all_cps) == 5

        filtered = asyncio.run(sessions.list_checkpoints(
            "test-agent", thread_id="thread-1", limit=2,
        ))
        assert len(filtered) == 2

    def test_delete_checkpoint(self, sessions):
        cp = Checkpoint(
            thread_id="thread-1",
            agent_id="test-agent",
            state={"step": 1},
        )
        asyncio.run(sessions.save_checkpoint(cp))
        asyncio.run(sessions.delete_checkpoint("test-agent", "thread-1"))

        loaded = asyncio.run(sessions.load_checkpoint("test-agent", "thread-1"))
        assert loaded is None

    def test_parent_linking(self, sessions):
        cp1 = Checkpoint(
            thread_id="thread-1", agent_id="test-agent", state={"step": 1},
        )
        cp2 = Checkpoint(
            thread_id="thread-1", agent_id="test-agent", state={"step": 2},
        )
        asyncio.run(sessions.save_checkpoint(cp1))
        asyncio.run(sessions.save_checkpoint(cp2))

        loaded = asyncio.run(sessions.load_checkpoint("test-agent", "thread-1"))
        assert loaded.parent_id == cp1.checkpoint_id

    def test_load_nonexistent(self, sessions):
        loaded = asyncio.run(sessions.load_checkpoint("test-agent", "no-thread"))
        assert loaded is None

    def test_metadata_preserved(self, sessions):
        cp = Checkpoint(
            thread_id="t1", agent_id="test-agent",
            state={"x": 1},
            metadata={"framework": "test", "version": "1.0"},
        )
        asyncio.run(sessions.save_checkpoint(cp))
        loaded = asyncio.run(sessions.load_checkpoint("test-agent", "t1"))
        assert loaded.metadata["framework"] == "test"


# ── Memory Provider Tests ───────────────────────────────────────────


class TestMemoryProvider:
    def test_add_and_search(self, memory):
        mem_id = asyncio.run(memory.add(
            "APAC revenue grew 31% in Q4",
            agent_id="test-agent",
            user_id="user-1",
        ))
        assert mem_id

        results = asyncio.run(memory.search(
            "revenue growth", agent_id="test-agent", user_id="user-1",
        ))
        assert len(results) > 0
        assert any("APAC" in r.content for r in results)

    def test_list_all(self, memory):
        asyncio.run(memory.add("fact 1", agent_id="test-agent"))
        asyncio.run(memory.add("fact 2", agent_id="test-agent"))

        all_mems = asyncio.run(memory.list_all("test-agent"))
        assert len(all_mems) == 2

    def test_delete(self, memory):
        mem_id = asyncio.run(memory.add("to delete", agent_id="test-agent"))
        asyncio.run(memory.delete(mem_id, "test-agent"))

        all_mems = asyncio.run(memory.list_all("test-agent"))
        assert len(all_mems) == 0

    def test_flush(self, memory):
        asyncio.run(memory.add("persist me", agent_id="test-agent"))
        asyncio.run(memory.flush("test-agent"))
        # Should not raise


# ── Artifact Provider Tests ─────────────────────────────────────────


class TestArtifactProvider:
    def test_save_and_load(self, artifacts):
        version = asyncio.run(artifacts.save(
            filename="report.json",
            data=b'{"revenue": 8700000}',
            agent_id="test-agent",
            session_id="s1",
        ))
        assert version == 1

        artifact = asyncio.run(artifacts.load(
            "report.json", agent_id="test-agent", session_id="s1",
        ))
        assert artifact is not None
        assert artifact.data == b'{"revenue": 8700000}'
        assert artifact.version == 1

    def test_versioning(self, artifacts):
        v1 = asyncio.run(artifacts.save(
            "doc.txt", b"draft", agent_id="test-agent",
        ))
        v2 = asyncio.run(artifacts.save(
            "doc.txt", b"final", agent_id="test-agent",
        ))
        assert v1 == 1
        assert v2 == 2

        # Load latest
        latest = asyncio.run(artifacts.load("doc.txt", agent_id="test-agent"))
        assert latest.data == b"final"

        # Load specific version
        draft = asyncio.run(artifacts.load(
            "doc.txt", agent_id="test-agent", version=1,
        ))
        assert draft.data == b"draft"

    def test_list_artifacts(self, artifacts):
        asyncio.run(artifacts.save("a.txt", b"a", agent_id="test-agent"))
        asyncio.run(artifacts.save("b.txt", b"b", agent_id="test-agent"))

        names = asyncio.run(artifacts.list_artifacts(agent_id="test-agent"))
        assert "a.txt" in names
        assert "b.txt" in names

    def test_list_versions(self, artifacts):
        asyncio.run(artifacts.save("doc.txt", b"v1", agent_id="test-agent"))
        asyncio.run(artifacts.save("doc.txt", b"v2", agent_id="test-agent"))
        asyncio.run(artifacts.save("doc.txt", b"v3", agent_id="test-agent"))

        versions = asyncio.run(artifacts.list_versions(
            "doc.txt", agent_id="test-agent",
        ))
        assert versions == [1, 2, 3]

    def test_load_nonexistent(self, artifacts):
        result = asyncio.run(artifacts.load("nope.txt", agent_id="test-agent"))
        assert result is None


# ── Task Provider Tests ─────────────────────────────────────────────


class TestTaskProvider:
    def test_create_and_get(self, tasks):
        record = asyncio.run(tasks.create_task("task-1", "test-agent"))
        assert record["task_id"] == "task-1"
        assert record["status"] == "pending"

        loaded = asyncio.run(tasks.get_task("task-1"))
        assert loaded is not None
        assert loaded["status"] == "pending"

    def test_update_task(self, tasks):
        asyncio.run(tasks.create_task("task-1", "test-agent"))
        updated = asyncio.run(tasks.update_task(
            "task-1", {"progress": 50}, status="running",
        ))
        assert updated["status"] == "running"
        assert updated["version"] == 1

    def test_get_nonexistent(self, tasks):
        result = asyncio.run(tasks.get_task("no-task"))
        assert result is None


# ── Factory Tests ───────────────────────────────────────────────────


class TestRuneFactory:
    def test_mock_backend(self):
        rune = nexus_core.builder().mock_backend().build()
        assert rune.sessions is not None
        assert rune.memory is not None
        assert rune.artifacts is not None
        assert rune.tasks is not None

    def test_local_backend(self):
        rune = nexus_core.local(base_dir=TEST_DIR)
        assert rune.sessions is not None
        assert rune.memory is not None
        assert rune.artifacts is not None
        assert rune.tasks is not None

    def test_full_workflow(self):
        rune = nexus_core.builder().mock_backend().build()

        # Save checkpoint
        cp = Checkpoint(
            thread_id="t1", agent_id="workflow-agent",
            state={"step": 1},
        )
        asyncio.run(rune.sessions.save_checkpoint(cp))

        # Add memory
        asyncio.run(rune.memory.add(
            "important fact", agent_id="workflow-agent",
        ))

        # Save artifact
        asyncio.run(rune.artifacts.save(
            "output.txt", b"result", agent_id="workflow-agent",
        ))

        # Verify all persisted
        loaded_cp = asyncio.run(rune.sessions.load_checkpoint(
            "workflow-agent", "t1",
        ))
        assert loaded_cp.state["step"] == 1

        memories = asyncio.run(rune.memory.list_all("workflow-agent"))
        assert len(memories) == 1

        artifacts = asyncio.run(rune.artifacts.list_artifacts(
            agent_id="workflow-agent",
        ))
        assert "output.txt" in artifacts


# ── LangGraph Adapter Tests ────────────────────────────────────────


class TestLangGraphAdapter:
    def test_put_and_get(self, sessions):
        from nexus_core.adapters.langgraph import RuneCheckpointer

        checkpointer = RuneCheckpointer(sessions, agent_id="test-agent")

        config = {"configurable": {"thread_id": "conv-1"}}
        checkpoint = {"v": 1, "id": "cp-1", "channel_values": {"messages": ["hi"]}}

        result_config = checkpointer.put(config, checkpoint, metadata={"step": 0})
        assert result_config["configurable"]["thread_id"] == "conv-1"
        assert result_config["configurable"]["checkpoint_id"] == "cp-1"

        # Get it back
        tuple_result = checkpointer.get_tuple(config)
        assert tuple_result is not None
        assert tuple_result["checkpoint"]["channel_values"]["messages"] == ["hi"]

    def test_list(self, sessions):
        from nexus_core.adapters.langgraph import RuneCheckpointer

        checkpointer = RuneCheckpointer(sessions, agent_id="test-agent")

        config = {"configurable": {"thread_id": "conv-1"}}
        for i in range(3):
            checkpointer.put(
                config,
                {"v": 1, "id": f"cp-{i}", "step": i},
                metadata={"step": i},
            )

        results = list(checkpointer.list(config))
        assert len(results) == 3

    def test_get_nonexistent(self, sessions):
        from nexus_core.adapters.langgraph import RuneCheckpointer

        checkpointer = RuneCheckpointer(sessions, agent_id="test-agent")

        config = {"configurable": {"thread_id": "no-thread"}}
        result = checkpointer.get_tuple(config)
        assert result is None

    def test_parent_config(self, sessions):
        from nexus_core.adapters.langgraph import RuneCheckpointer

        checkpointer = RuneCheckpointer(sessions, agent_id="test-agent")

        config = {"configurable": {"thread_id": "conv-1"}}
        checkpointer.put(config, {"v": 1, "id": "cp-0", "step": 0})
        config2 = {"configurable": {"thread_id": "conv-1", "checkpoint_id": "cp-0"}}
        checkpointer.put(config2, {"v": 1, "id": "cp-1", "step": 1})

        tuple_result = checkpointer.get_tuple(config)
        assert tuple_result is not None
        if tuple_result.get("parent_config"):
            assert tuple_result["parent_config"]["configurable"]["checkpoint_id"]


# ── CrewAI Adapter Tests ───────────────────────────────────────────


class TestCrewAIAdapter:
    def test_save_and_search(self, memory):
        from nexus_core.adapters.crewai import RuneCrewStorage

        storage = RuneCrewStorage(memory, agent_id="test-agent")

        mem_id = storage.save("Revenue grew 31% in APAC region")
        assert mem_id

        results = storage.search("APAC revenue growth")
        assert len(results) > 0
        assert any("APAC" in r["content"] for r in results)

    def test_delete(self, memory):
        from nexus_core.adapters.crewai import RuneCrewStorage

        storage = RuneCrewStorage(memory, agent_id="test-agent")

        mem_id = storage.save("temporary memory")
        storage.delete(mem_id)

        results = storage.search("temporary memory")
        assert len(results) == 0

    def test_reset(self, memory):
        from nexus_core.adapters.crewai import RuneCrewStorage

        storage = RuneCrewStorage(memory, agent_id="test-agent")

        storage.save("memory 1")
        storage.save("memory 2")
        storage.reset()

        results = storage.search("memory")
        assert len(results) == 0

    def test_checkpoint_storage(self, artifacts):
        from nexus_core.adapters.crewai import RuneCrewCheckpointStorage

        storage = RuneCrewCheckpointStorage(artifacts, agent_id="test-agent")

        version = storage.save("task-1", {"result": "analysis complete"})
        assert version == 1

        output = storage.load("task-1")
        assert output is not None
        assert "analysis complete" in output
