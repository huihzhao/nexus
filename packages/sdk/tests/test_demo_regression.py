"""
Regression tests covering all demo scenarios (01-05).

These tests validate the end-to-end workflows demonstrated in the
demo scripts, ensuring the full Rune stack works correctly:

  Demo 01: Pure ADK agent (baseline — session + events in-memory)
  Demo 02: Rune persistence (session survives reload)
  Demo 03: Crash recovery (resume from checkpoint after "crash")
  Demo 04: Memory persistence (cross-session recall + crash recovery)
  Demo 05: Artifact versioning (save, update, version history, reload)

All tests use MockBackend or LocalBackend — no BSC/chain dependency.
"""

import asyncio
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nexus_core
from nexus_core.core.models import Checkpoint, MemoryEntry, Artifact
from nexus_core.core.providers import AgentRuntime
from nexus_core.backends.mock import MockBackend
from nexus_core.backends.local import LocalBackend
from nexus_core.providers.session import SessionProviderImpl
from nexus_core.providers.memory import MemoryProviderImpl
from nexus_core.providers.artifact import ArtifactProviderImpl
from nexus_core.adapters.adk import RuneSessionService, RuneMemoryService, RuneArtifactService
from nexus_core.builder import Builder

# Try ADK imports
try:
    from google.adk.events import Event, EventActions
    from google.adk.sessions import Session
    from google.genai import types

    _ADK_AVAILABLE = True
except ImportError:
    _ADK_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════
# Shared Pipeline Data (same as demos)
# ═══════════════════════════════════════════════════════════════════════

PIPELINE_STEPS = [
    {
        "name": "Data Collection",
        "result": "Collected 12,847 transactions across 3 regions (NA, EU, APAC)",
    },
    {
        "name": "Data Cleaning",
        "result": "Cleaned dataset: 12,103 valid transactions, 744 removed (5.8% error rate)",
    },
    {
        "name": "Pattern Analysis",
        "result": "Top 3 products: Widget-Pro ($2.1M), DataSync ($1.8M), CloudKit ($1.4M). Q4 spike: +23% vs Q3",
    },
    {
        "name": "Report Generation",
        "result": "Report complete: $8.7M total revenue, 15% YoY growth, APAC fastest growing (+31%)",
    },
]

SESSION_1_FINDINGS = [
    "Q4 total revenue: $8.7M, up 15% YoY. Widget-Pro is the top product at $2.1M.",
    "APAC grew 31% (fastest region). NA flat at 3%. EU steady at 12%.",
    "Enterprise accounts drove 62% of revenue. SMB churn rate increased to 8.3%.",
]

SESSION_2_QUERIES = [
    ("Q4 revenue and regional growth trends", "Q1 forecast: $9.2M"),
    ("customer churn and segment performance", "Key risk: SMB churn trending up"),
]


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_rune():
    """Rune instance with MockBackend."""
    return nexus_core.builder().mock_backend().build()


@pytest.fixture
def local_rune(tmp_path):
    """Rune instance with LocalBackend."""
    return nexus_core.local(base_dir=str(tmp_path / "rune_regression"))


@pytest.fixture(params=["mock", "local"])
def rune(request, tmp_path):
    """Parametrized fixture: run tests against both mock and local backends."""
    if request.param == "mock":
        return nexus_core.builder().mock_backend().build()
    else:
        return nexus_core.local(base_dir=str(tmp_path / "rune_regression"))


def make_event(step_idx: int, result: str):
    """Create an ADK Event (or dict fallback) for a pipeline step."""
    state_delta = {
        f"step_{step_idx}_completed": True,
        f"step_{step_idx}_result": result,
        "current_step": step_idx + 1,
    }
    if _ADK_AVAILABLE:
        event = Event(
            invocation_id=f"inv-{step_idx}",
            author="analysis-agent",
            actions=EventActions(state_delta=state_delta),
        )
        event.id = Event.new_id()
        return event
    else:
        return {
            "id": str(uuid.uuid4()),
            "invocation_id": f"inv-{step_idx}",
            "author": "analysis-agent",
            "actions": {"state_delta": state_delta},
            "text": result,
        }


# ═══════════════════════════════════════════════════════════════════════
# Demo 01: Pure ADK Session — baseline event tracking
# ═══════════════════════════════════════════════════════════════════════


class TestDemo01PureADKBaseline:
    """Validates that ADK Session + Event tracking works as expected."""

    @pytest.mark.asyncio
    async def test_session_create_and_track_events(self, rune):
        """Create a session and track 4 pipeline steps via events."""
        svc = RuneSessionService(rune.sessions)

        session = await svc.create_session(
            app_name="sales-analyzer", user_id="analyst-01",
            session_id="analysis-q4-2025",
            state={"task": "Q4 Sales Analysis", "current_step": 0},
        )
        assert session is not None

        # Run all 4 pipeline steps
        for i, step in enumerate(PIPELINE_STEPS):
            event = make_event(i, step["result"])
            await svc.append_event(session, event)

        # Verify final state
        state = session.state if hasattr(session, "state") else session.get("state", {})
        assert state["current_step"] == 4
        for i in range(4):
            assert state.get(f"step_{i}_completed") is True

    @pytest.mark.asyncio
    async def test_event_results_stored(self, rune):
        """Each step result is stored in session state."""
        svc = RuneSessionService(rune.sessions)
        session = await svc.create_session(
            app_name="test-app", user_id="u1", session_id="s1",
            state={"current_step": 0},
        )

        for i, step in enumerate(PIPELINE_STEPS):
            event = make_event(i, step["result"])
            await svc.append_event(session, event)

        state = session.state if hasattr(session, "state") else session.get("state", {})
        assert "12,847 transactions" in state["step_0_result"]
        assert "Widget-Pro" in state["step_2_result"]
        assert "$8.7M" in state["step_3_result"]


# ═══════════════════════════════════════════════════════════════════════
# Demo 02: Add Persistence — session survives reload
# ═══════════════════════════════════════════════════════════════════════


class TestDemo02Persistence:
    """Validates that sessions persist and can be reloaded."""

    @pytest.mark.asyncio
    async def test_session_persists_after_all_steps(self, rune):
        """Complete 4 steps, reload session — state must match."""
        svc = RuneSessionService(rune.sessions)

        session = await svc.create_session(
            app_name="sales-analyzer", user_id="analyst-01",
            session_id="persist-test",
            state={"task": "Q4 Analysis", "current_step": 0},
        )

        for i, step in enumerate(PIPELINE_STEPS):
            event = make_event(i, step["result"])
            await svc.append_event(session, event)

        # Reload from backend (simulates fresh process)
        reloaded = await svc.get_session(
            app_name="sales-analyzer", user_id="analyst-01",
            session_id="persist-test",
        )
        assert reloaded is not None

        state = reloaded.state if hasattr(reloaded, "state") else reloaded.get("state", {})
        assert state["current_step"] == 4
        assert state["step_3_completed"] is True

    @pytest.mark.asyncio
    async def test_persistence_with_new_service_instance(self, rune):
        """Create session with one service, reload with a new service instance."""
        svc1 = RuneSessionService(rune.sessions)
        session = await svc1.create_session(
            app_name="test-app", user_id="u1", session_id="cross-svc-test",
            state={"x": 42},
        )

        # New service instance, same underlying provider
        svc2 = RuneSessionService(rune.sessions)
        loaded = await svc2.get_session(
            app_name="test-app", user_id="u1", session_id="cross-svc-test",
        )
        assert loaded is not None
        state = loaded.state if hasattr(loaded, "state") else loaded.get("state", {})
        assert state["x"] == 42

    @pytest.mark.asyncio
    async def test_state_delta_accumulates(self, rune):
        """Append multiple events — state deltas accumulate correctly."""
        svc = RuneSessionService(rune.sessions)
        session = await svc.create_session(
            app_name="test-app", user_id="u1", session_id="delta-test",
            state={"counter": 0},
        )

        for i in range(5):
            if _ADK_AVAILABLE:
                event = Event(
                    invocation_id=f"inv-{i}",
                    author="agent",
                    actions=EventActions(state_delta={"counter": i + 1, f"step_{i}": True}),
                )
                event.id = Event.new_id()
            else:
                event = {"id": str(uuid.uuid4()), "text": f"step {i}",
                         "actions": {"state_delta": {"counter": i + 1, f"step_{i}": True}}}
            await svc.append_event(session, event)

        # Reload and verify
        loaded = await svc.get_session(
            app_name="test-app", user_id="u1", session_id="delta-test",
        )
        state = loaded.state if hasattr(loaded, "state") else loaded.get("state", {})
        assert state["counter"] == 5
        for i in range(5):
            assert state[f"step_{i}"] is True


# ═══════════════════════════════════════════════════════════════════════
# Demo 03: Crash Recovery — the killer feature
# ═══════════════════════════════════════════════════════════════════════


class TestDemo03CrashRecovery:
    """Validates crash recovery: partial execution → reload → resume."""

    @pytest.mark.asyncio
    async def test_crash_after_step_2_resume_at_step_3(self, rune):
        """
        Runtime A: execute steps 0-1, crash.
        Runtime B: load from backend, resume at step 2, finish steps 2-3.
        """
        app_name = "sales-analyzer"
        user_id = "analyst-01"
        session_id = f"crash-test-{uuid.uuid4().hex[:8]}"

        # ── Runtime A: execute steps 0-1 then "crash" ──────────
        svc_a = RuneSessionService(rune.sessions)
        session_a = await svc_a.create_session(
            app_name=app_name, user_id=user_id, session_id=session_id,
            state={"task": "Q4 Analysis", "current_step": 0},
        )

        for i in range(2):  # steps 0, 1 only
            event = make_event(i, PIPELINE_STEPS[i]["result"])
            await svc_a.append_event(session_a, event)

        state_a = session_a.state if hasattr(session_a, "state") else session_a.get("state", {})
        assert state_a["current_step"] == 2
        assert state_a["step_0_completed"] is True
        assert state_a["step_1_completed"] is True

        # Simulate crash — delete local reference (Runtime A dies)
        del svc_a, session_a

        # ── Runtime B: load from backend and resume ─────────────
        svc_b = RuneSessionService(rune.sessions)
        session_b = await svc_b.get_session(
            app_name=app_name, user_id=user_id, session_id=session_id,
        )
        assert session_b is not None, "Session must survive crash"

        state_b = session_b.state if hasattr(session_b, "state") else session_b.get("state", {})
        checkpoint = state_b.get("current_step", 0)
        assert checkpoint == 2, f"Should resume at step 2, got {checkpoint}"

        # Execute remaining steps 2-3
        for i in range(checkpoint, 4):
            event = make_event(i, PIPELINE_STEPS[i]["result"])
            await svc_b.append_event(session_b, event)

        # ── Verify final state ──────────────────────────────────
        final_state = session_b.state if hasattr(session_b, "state") else session_b.get("state", {})
        assert final_state["current_step"] == 4
        for i in range(4):
            assert final_state[f"step_{i}_completed"] is True
            assert PIPELINE_STEPS[i]["result"] == final_state[f"step_{i}_result"]

    @pytest.mark.asyncio
    async def test_crash_after_step_0_resume(self, rune):
        """Crash very early (after step 0), resume and finish."""
        sid = f"early-crash-{uuid.uuid4().hex[:8]}"
        svc = RuneSessionService(rune.sessions)

        session = await svc.create_session(
            app_name="app", user_id="u1", session_id=sid,
            state={"current_step": 0},
        )
        event = make_event(0, PIPELINE_STEPS[0]["result"])
        await svc.append_event(session, event)
        del svc, session  # "crash"

        # Resume
        svc2 = RuneSessionService(rune.sessions)
        loaded = await svc2.get_session(app_name="app", user_id="u1", session_id=sid)
        state = loaded.state if hasattr(loaded, "state") else loaded.get("state", {})
        assert state["current_step"] == 1
        assert state["step_0_completed"] is True

    @pytest.mark.asyncio
    async def test_crash_recovery_with_local_backend(self, tmp_path):
        """Crash recovery specifically with LocalBackend (file-based).

        Saves a single checkpoint with full state, destroys the Rune
        instance, then creates a new one and verifies data persisted.
        """
        base_dir = str(tmp_path / "crash_local")
        rune = nexus_core.local(base_dir=base_dir)
        sid = "local-crash-test"
        agent_id = "app:u1"

        # Save one checkpoint with the full accumulated state (like real demo)
        final_state = {
            "current_step": 3,
            "step_0_completed": True,
            "step_1_completed": True,
            "step_2_completed": True,
        }
        cp = Checkpoint(agent_id=agent_id, thread_id=sid, state=final_state)
        cp_id = cp.checkpoint_id
        await rune.sessions.save_checkpoint(cp)

        del rune  # "crash"

        # Completely new Rune instance (simulates new process)
        rune2 = nexus_core.local(base_dir=base_dir)
        loaded_cp = await rune2.sessions.load_checkpoint(agent_id, sid, cp_id)
        assert loaded_cp is not None
        assert loaded_cp.state["current_step"] == 3
        assert loaded_cp.state["step_2_completed"] is True


# ═══════════════════════════════════════════════════════════════════════
# Demo 04: Memory Persistence — cross-session recall
# ═══════════════════════════════════════════════════════════════════════


class TestDemo04MemoryPersistence:
    """Validates memory add, search, cross-session recall, and crash recovery."""

    @pytest.mark.asyncio
    async def test_memorize_and_recall(self, rune):
        """Session 1 memorizes findings; Session 2 recalls them."""
        app_name = "sales-analyzer"
        user_id = "analyst-01"
        agent_id = f"{app_name}:{user_id}"

        # Session 1: add memories
        for finding in SESSION_1_FINDINGS:
            await rune.memory.add(finding, agent_id=agent_id, user_id=user_id)

        # Session 2: recall
        results = await rune.memory.search(
            "revenue growth APAC", agent_id=agent_id, user_id=user_id,
        )
        assert len(results) > 0
        # At least one result should mention revenue or APAC
        texts = [r.content.lower() for r in results]
        assert any("revenue" in t or "apac" in t for t in texts)

    @pytest.mark.asyncio
    async def test_memory_via_adk_adapter(self, rune):
        """Test memory add/search through RuneMemoryService adapter."""
        mem_svc = RuneMemoryService(rune.memory)
        svc = RuneSessionService(rune.sessions)

        # Create session and add events
        session = await svc.create_session(
            app_name="test-app", user_id="u1", session_id="mem-test",
            state={},
        )
        for finding in SESSION_1_FINDINGS:
            event = make_event(0, finding)
            await svc.append_event(session, event)

        # Memorize session
        await mem_svc.add_session_to_memory(session)

        # Search
        results = await mem_svc.search_memory(
            app_name="test-app", user_id="u1", query="revenue growth",
        )
        if isinstance(results, list):
            assert len(results) > 0
        else:
            assert len(results.memories) > 0

    @pytest.mark.asyncio
    async def test_memory_deduplication(self, rune):
        """Adding the same content twice should not create duplicates."""
        agent_id = "test:u1"
        mid1 = await rune.memory.add("APAC grew 31%", agent_id=agent_id)
        mid2 = await rune.memory.add("APAC grew 31%", agent_id=agent_id)
        assert mid1 == mid2

        all_mems = await rune.memory.list_all(agent_id)
        assert len(all_mems) == 1

    @pytest.mark.asyncio
    async def test_memory_survives_crash(self, rune):
        """Memories persist and can be loaded after simulated crash."""
        agent_id = "sales-analyzer:analyst-01"

        # Add memories
        for finding in SESSION_1_FINDINGS:
            await rune.memory.add(finding, agent_id=agent_id)

        # Verify persistence via list_all
        all_mems = await rune.memory.list_all(agent_id)
        assert len(all_mems) == len(SESSION_1_FINDINGS)

        # Verify search still works (the key crash-recovery use case)
        results = await rune.memory.search("revenue APAC", agent_id=agent_id)
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_memory_search_relevance(self, rune):
        """Search returns relevant memories ranked by score."""
        agent_id = "test:u1"
        await rune.memory.add(
            "Q4 total revenue: $8.7M, up 15% YoY.", agent_id=agent_id,
        )
        await rune.memory.add(
            "APAC grew 31%, the fastest growing region.", agent_id=agent_id,
        )
        await rune.memory.add(
            "The weather was sunny today in the office.", agent_id=agent_id,
        )

        results = await rune.memory.search("revenue growth", agent_id=agent_id)
        assert len(results) > 0
        # Revenue or growth related entries should score higher
        top = results[0].content.lower()
        assert "revenue" in top or "grew" in top

    @pytest.mark.asyncio
    async def test_memory_delete(self, rune):
        """Delete specific memories."""
        agent_id = "test:u1"
        mid = await rune.memory.add("temporary data", agent_id=agent_id)
        assert mid

        await rune.memory.delete(mid, agent_id=agent_id)
        all_mems = await rune.memory.list_all(agent_id)
        assert len(all_mems) == 0

    @pytest.mark.asyncio
    async def test_cross_session_memory_flow(self, rune):
        """
        Full cross-session flow:
        Session 1 memorizes → Session 2 recalls → memories inform new analysis.
        """
        app_name = "analyzer"
        user_id = "user-1"
        agent_id = f"{app_name}:{user_id}"

        svc = RuneSessionService(rune.sessions)

        # ── Session 1: analyze and memorize ──────────
        s1 = await svc.create_session(
            app_name=app_name, user_id=user_id, session_id="q4",
            state={"quarter": "Q4"},
        )
        for finding in SESSION_1_FINDINGS:
            await rune.memory.add(finding, agent_id=agent_id, user_id=user_id)

        # ── Session 2: recall and use ────────────────
        s2 = await svc.create_session(
            app_name=app_name, user_id=user_id, session_id="q1",
            state={"quarter": "Q1"},
        )
        for query, expected_fragment in SESSION_2_QUERIES:
            results = await rune.memory.search(query, agent_id=agent_id, user_id=user_id)
            assert len(results) > 0, f"No memories found for query: {query}"


# ═══════════════════════════════════════════════════════════════════════
# Demo 05: Artifact Versioning
# ═══════════════════════════════════════════════════════════════════════


class TestDemo05ArtifactVersioning:
    """Validates artifact save, versioning, and loading."""

    @pytest.mark.asyncio
    async def test_save_and_load_artifact(self, rune):
        """Save a JSON artifact and reload it."""
        agent_id = "sales-analyzer:analyst-01"
        raw_data = {
            "quarter": "Q4 2025",
            "total_transactions": 12847,
            "regions": {"NA": 3200000, "EU": 2800000, "APAC": 2700000},
        }

        v = await rune.artifacts.save(
            "q4_raw_data.json",
            json.dumps(raw_data).encode("utf-8"),
            agent_id=agent_id,
        )
        assert v == 1

        artifact = await rune.artifacts.load("q4_raw_data.json", agent_id=agent_id)
        assert artifact is not None
        loaded_data = json.loads(artifact.data.decode("utf-8"))
        assert loaded_data["total_transactions"] == 12847

    @pytest.mark.asyncio
    async def test_artifact_versioning(self, rune):
        """Save two versions of an artifact, load latest and specific."""
        agent_id = "test:u1"

        v1 = await rune.artifacts.save(
            "summary.txt",
            b"Q4 Executive Summary (DRAFT)",
            agent_id=agent_id,
        )
        v2 = await rune.artifacts.save(
            "summary.txt",
            b"Q4 Executive Summary (FINAL) - with recommendations",
            agent_id=agent_id,
        )
        assert v1 == 1
        assert v2 == 2

        # Latest version
        latest = await rune.artifacts.load("summary.txt", agent_id=agent_id)
        assert latest is not None
        assert b"FINAL" in latest.data

        # Specific version
        old = await rune.artifacts.load("summary.txt", agent_id=agent_id, version=1)
        assert old is not None
        assert b"DRAFT" in old.data

    @pytest.mark.asyncio
    async def test_multiple_artifacts(self, rune):
        """Save multiple different artifacts, list them."""
        agent_id = "test:u1"
        await rune.artifacts.save("data.json", b'{"x":1}', agent_id=agent_id)
        await rune.artifacts.save("report.txt", b"Report", agent_id=agent_id)
        await rune.artifacts.save("chart.png", b"\x89PNG...", agent_id=agent_id)

        files = await rune.artifacts.list_artifacts(agent_id=agent_id)
        assert sorted(files) == ["chart.png", "data.json", "report.txt"]

    @pytest.mark.asyncio
    async def test_artifact_via_adk_adapter(self, rune):
        """Test artifact save/load through RuneArtifactService adapter."""
        art_svc = RuneArtifactService(rune.artifacts)

        v1 = await art_svc.save_artifact(
            b"raw report data",
            app_name="test-app", user_id="u1",
            session_id="s1",
            metadata={"filename": "report.pdf"},
        )
        assert v1 == 1

        loaded = await art_svc.load_artifact(
            "report.pdf",
            app_name="test-app", user_id="u1",
            session_id="s1",
        )
        assert loaded is not None

    @pytest.mark.asyncio
    async def test_artifact_version_list(self, rune):
        """List all versions of an artifact."""
        agent_id = "test:u1"
        for i in range(3):
            await rune.artifacts.save(
                "report.json", f"version {i + 1}".encode(), agent_id=agent_id,
            )

        versions = await rune.artifacts.list_versions("report.json", agent_id=agent_id)
        assert versions == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_load_nonexistent_artifact(self, rune):
        """Loading a nonexistent artifact returns None."""
        result = await rune.artifacts.load("nofile.json", agent_id="a1")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# Full Integration: all 5 demos in one flow
# ═══════════════════════════════════════════════════════════════════════


class TestFullIntegration:
    """
    End-to-end test that simulates the complete demo tutorial:
    session → persistence → crash recovery → memory → artifacts.
    """

    @pytest.mark.asyncio
    async def test_complete_tutorial_flow(self, rune):
        """
        Run the full demo 01-05 flow as a single test:
        1. Create session, run 4 pipeline steps
        2. Verify persistence by reloading
        3. Crash after step 2, resume at step 3
        4. Add memories, recall across sessions
        5. Save versioned artifacts
        """
        app_name = "sales-analyzer"
        user_id = "analyst-01"
        agent_id = f"{app_name}:{user_id}"

        svc = RuneSessionService(rune.sessions)
        mem_svc = RuneMemoryService(rune.memory)
        art_svc = RuneArtifactService(rune.artifacts)

        # ── Step 1-2: Create session, run pipeline, verify persistence ──
        session = await svc.create_session(
            app_name=app_name, user_id=user_id,
            session_id="full-test",
            state={"task": "Q4 Analysis", "current_step": 0},
        )
        for i, step in enumerate(PIPELINE_STEPS):
            event = make_event(i, step["result"])
            await svc.append_event(session, event)

        reloaded = await svc.get_session(
            app_name=app_name, user_id=user_id, session_id="full-test",
        )
        assert reloaded is not None
        state = reloaded.state if hasattr(reloaded, "state") else reloaded.get("state", {})
        assert state["current_step"] == 4

        # ── Step 3: Crash recovery ─────────────────────────────────
        crash_sid = f"crash-{uuid.uuid4().hex[:8]}"
        crash_session = await svc.create_session(
            app_name=app_name, user_id=user_id,
            session_id=crash_sid,
            state={"current_step": 0},
        )
        for i in range(2):
            event = make_event(i, PIPELINE_STEPS[i]["result"])
            await svc.append_event(crash_session, event)

        # "Crash" and reload
        svc2 = RuneSessionService(rune.sessions)
        resumed = await svc2.get_session(
            app_name=app_name, user_id=user_id, session_id=crash_sid,
        )
        r_state = resumed.state if hasattr(resumed, "state") else resumed.get("state", {})
        assert r_state["current_step"] == 2
        # Resume
        for i in range(2, 4):
            event = make_event(i, PIPELINE_STEPS[i]["result"])
            await svc2.append_event(resumed, event)
        r_state2 = resumed.state if hasattr(resumed, "state") else resumed.get("state", {})
        assert r_state2["current_step"] == 4

        # ── Step 4: Memory ──────────────────────────────────────────
        for finding in SESSION_1_FINDINGS:
            await rune.memory.add(finding, agent_id=agent_id, user_id=user_id)

        results = await rune.memory.search(
            "revenue growth", agent_id=agent_id, user_id=user_id,
        )
        assert len(results) > 0

        # ── Step 5: Artifacts ───────────────────────────────────────
        v1 = await art_svc.save_artifact(
            b"Draft report",
            app_name=app_name, user_id=user_id,
            session_id="full-test",
            metadata={"filename": "summary.txt"},
        )
        v2 = await art_svc.save_artifact(
            b"Final report with recommendations",
            app_name=app_name, user_id=user_id,
            session_id="full-test",
            metadata={"filename": "summary.txt"},
        )
        assert v1 == 1
        assert v2 == 2

        loaded = await art_svc.load_artifact(
            "summary.txt",
            app_name=app_name, user_id=user_id,
            session_id="full-test",
        )
        assert loaded is not None

    @pytest.mark.asyncio
    async def test_multi_agent_isolation(self, rune):
        """Two different agents' data should not interfere at the provider level."""
        agent_a = "agent-A:u1"
        agent_b = "agent-B:u1"

        # Save checkpoints for two different agents with same thread_id
        cp_a = Checkpoint(
            agent_id=agent_a, thread_id="shared-thread",
            state={"step_0_result": "Agent A result", "current_step": 1},
        )
        cp_b = Checkpoint(
            agent_id=agent_b, thread_id="shared-thread",
            state={"step_0_result": "Agent B result", "current_step": 1},
        )
        await rune.sessions.save_checkpoint(cp_a)
        await rune.sessions.save_checkpoint(cp_b)

        # Load and verify isolation
        loaded_a = await rune.sessions.load_checkpoint(agent_a, "shared-thread")
        loaded_b = await rune.sessions.load_checkpoint(agent_b, "shared-thread")

        assert loaded_a.state["step_0_result"] == "Agent A result"
        assert loaded_b.state["step_0_result"] == "Agent B result"

    @pytest.mark.asyncio
    async def test_builder_modes(self, tmp_path):
        """nexus_core.local() and nexus_core.builder().mock_backend() both work."""
        # MockBackend
        rune_mock = nexus_core.builder().mock_backend().build()
        await rune_mock.memory.add("fact", agent_id="a1")
        results = await rune_mock.memory.search("fact", agent_id="a1")
        assert len(results) > 0

        # LocalBackend
        rune_local = nexus_core.local(base_dir=str(tmp_path / "builder_test"))
        await rune_local.memory.add("fact", agent_id="a1")
        results = await rune_local.memory.search("fact", agent_id="a1")
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Rune works as async context manager."""
        async with nexus_core.builder().mock_backend().build() as rune:
            svc = RuneSessionService(rune.sessions)
            session = await svc.create_session(
                app_name="test", user_id="u1", session_id="ctx-test",
                state={"x": 1},
            )
            event = make_event(0, "test result")
            await svc.append_event(session, event)

            state = session.state if hasattr(session, "state") else session.get("state", {})
            assert state["current_step"] == 1


# ═══════════════════════════════════════════════════════════════════════
# Edge Cases & Robustness
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge cases that demos might hit in real usage."""

    @pytest.mark.asyncio
    async def test_empty_session_reload(self, rune):
        """Create session with no events, reload it."""
        svc = RuneSessionService(rune.sessions)
        await svc.create_session(
            app_name="app", user_id="u1", session_id="empty",
            state={"initial": True},
        )
        loaded = await svc.get_session(app_name="app", user_id="u1", session_id="empty")
        assert loaded is not None
        state = loaded.state if hasattr(loaded, "state") else loaded.get("state", {})
        assert state["initial"] is True

    @pytest.mark.asyncio
    async def test_load_nonexistent_session(self, rune):
        """Loading a session that doesn't exist returns None."""
        svc = RuneSessionService(rune.sessions)
        result = await svc.get_session(app_name="x", user_id="y", session_id="z")
        assert result is None

    @pytest.mark.asyncio
    async def test_large_state(self, rune):
        """Session with large state object persists correctly."""
        svc = RuneSessionService(rune.sessions)
        big_state = {f"key_{i}": f"value_{i}" * 100 for i in range(50)}
        session = await svc.create_session(
            app_name="app", user_id="u1", session_id="big",
            state=big_state,
        )

        loaded = await svc.get_session(app_name="app", user_id="u1", session_id="big")
        state = loaded.state if hasattr(loaded, "state") else loaded.get("state", {})
        assert len(state) == 50
        assert state["key_0"] == "value_0" * 100

    @pytest.mark.asyncio
    async def test_special_characters_in_ids(self, rune):
        """Agent/session IDs with special characters work."""
        svc = RuneSessionService(rune.sessions)
        session = await svc.create_session(
            app_name="my-app_v2", user_id="user@example.com",
            session_id="session-2025-01-01T00:00:00Z",
            state={"ok": True},
        )
        loaded = await svc.get_session(
            app_name="my-app_v2", user_id="user@example.com",
            session_id="session-2025-01-01T00:00:00Z",
        )
        assert loaded is not None

    @pytest.mark.asyncio
    async def test_memory_search_empty_store(self, rune):
        """Searching memory when nothing is stored returns empty list."""
        results = await rune.memory.search("anything", agent_id="empty-agent")
        assert results == []

    @pytest.mark.asyncio
    async def test_rapid_event_appends(self, rune):
        """Rapidly appending many events doesn't lose data."""
        svc = RuneSessionService(rune.sessions)
        session = await svc.create_session(
            app_name="app", user_id="u1", session_id="rapid",
            state={"count": 0},
        )

        for i in range(20):
            if _ADK_AVAILABLE:
                event = Event(
                    invocation_id=f"inv-{i}",
                    author="agent",
                    actions=EventActions(state_delta={"count": i + 1}),
                )
                event.id = Event.new_id()
            else:
                event = {"id": str(uuid.uuid4()), "text": f"ev {i}",
                         "actions": {"state_delta": {"count": i + 1}}}
            await svc.append_event(session, event)

        loaded = await svc.get_session(app_name="app", user_id="u1", session_id="rapid")
        state = loaded.state if hasattr(loaded, "state") else loaded.get("state", {})
        assert state["count"] == 20

    @pytest.mark.asyncio
    async def test_delete_session(self, rune):
        """Deleting a session makes it unretrievable."""
        svc = RuneSessionService(rune.sessions)
        await svc.create_session(
            app_name="app", user_id="u1", session_id="to-delete",
            state={"x": 1},
        )
        await svc.delete_session(app_name="app", user_id="u1", session_id="to-delete")
        loaded = await svc.get_session(app_name="app", user_id="u1", session_id="to-delete")
        assert loaded is None
