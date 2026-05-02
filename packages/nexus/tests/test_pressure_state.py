"""pressure_state() contract — Phase C1.

Each evolver must expose a `pressure_state()` method returning a
dict with the standard shape the Pressure Dashboard endpoint
aggregates. Locks in:

  * Required keys (evolver / layer / accumulator / threshold / unit
    / status / fed_by / last_fired_at / details)
  * Layer assignment matches the BEP-Nexus pyramid (L0 persona
    slowest, L1 facts/knowledge mid, L2 skills capability, L4 audit)
  * Status transitions ("warming" → "ready" → "fired_recently")
  * fed_by lineage is correct so the lineage card can render the
    causal arrows between layers
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import nexus_core
from nexus_core.memory import EventLog


_REQUIRED_KEYS = {
    "evolver", "layer", "accumulator", "threshold",
    "unit", "status", "fed_by", "last_fired_at", "details",
}


@pytest.fixture
def rune():
    return nexus_core.builder().mock_backend().build()


@pytest.fixture
def event_log(tmp_path):
    return EventLog(base_dir=str(tmp_path / "elog"), agent_id="agent-1")


def _assert_shape(state: dict, *, expected_layer: str, expected_evolver: str):
    """All evolvers' pressure_state() must satisfy the same contract."""
    assert isinstance(state, dict)
    missing = _REQUIRED_KEYS - set(state.keys())
    assert not missing, f"missing keys in {expected_evolver}: {missing}"
    assert state["evolver"] == expected_evolver
    assert state["layer"] == expected_layer
    assert isinstance(state["accumulator"], (int, float))
    assert isinstance(state["threshold"], (int, float))
    assert state["unit"] != ""
    assert state["status"] in (
        "live", "warming", "ready", "fired_recently", "idle",
    )
    assert isinstance(state["fed_by"], list)
    assert isinstance(state["details"], dict)


# ── L1: MemoryEvolver — fires every turn ─────────────────────────


def test_memory_evolver_pressure_state_is_live(rune):
    from nexus.evolution.memory_evolver import MemoryEvolver
    me = MemoryEvolver(rune, "agent-1", AsyncMock())
    s = me.pressure_state()
    _assert_shape(s, expected_layer="L1", expected_evolver="MemoryEvolver")
    assert s["unit"] == "turns"
    assert s["status"] == "live"
    assert "chat.turn" in s["fed_by"]


# ── L1: EventLogCompactor — accumulator is events_since_last ─────


def test_event_log_compactor_pressure_state_warming(event_log, tmp_path):
    from nexus_core.memory.compactor import EventLogCompactor
    from nexus_core.memory import CuratedMemory
    cm = CuratedMemory(base_dir=str(tmp_path / "cm"))
    comp = EventLogCompactor(
        event_log=event_log,
        curated_memory=cm,
        projection_fn=AsyncMock(return_value="x"),
        compact_interval=20,
    )
    # Add a few events — gauge below threshold ⇒ "warming".
    for _ in range(5):
        event_log.append("user_message", "hi")
    s = comp.pressure_state()
    _assert_shape(
        s, expected_layer="L1", expected_evolver="EventLogCompactor",
    )
    assert s["unit"] == "events"
    assert s["threshold"] == 20
    assert s["accumulator"] == 5  # 5 events since "last compact" (which is 0)
    assert s["status"] == "warming"


def test_event_log_compactor_status_ready_at_threshold(event_log, tmp_path):
    from nexus_core.memory.compactor import EventLogCompactor
    from nexus_core.memory import CuratedMemory
    cm = CuratedMemory(base_dir=str(tmp_path / "cm"))
    comp = EventLogCompactor(
        event_log=event_log,
        curated_memory=cm,
        projection_fn=AsyncMock(return_value="x"),
        compact_interval=10,
    )
    for _ in range(12):
        event_log.append("user_message", "hi")
    s = comp.pressure_state()
    assert s["status"] == "ready", (
        "delta ≥ threshold must surface as 'ready' so the UI gauge "
        "shows ⏳ next-fire indicator"
    )


# ── L1: KnowledgeCompiler — accumulator is fact count ─────────────


def test_knowledge_compiler_pressure_state(rune, tmp_path):
    from nexus.evolution.knowledge_compiler import KnowledgeCompiler
    from nexus_core.memory import KnowledgeStore
    ks = KnowledgeStore(base_dir=str(tmp_path / "kn"))
    kc = KnowledgeCompiler(
        rune, "agent-1", AsyncMock(), knowledge_store=ks,
    )
    s = kc.pressure_state(fact_count=5, min_memories=10)
    _assert_shape(
        s, expected_layer="L1", expected_evolver="KnowledgeCompiler",
    )
    assert s["unit"] == "facts"
    assert s["accumulator"] == 5
    assert s["threshold"] == 10
    assert s["status"] == "warming"
    # Lineage: KnowledgeCompiler is fed by MemoryEvolver's facts.
    assert "MemoryEvolver" in s["fed_by"]


def test_knowledge_compiler_ready_at_threshold(rune):
    from nexus.evolution.knowledge_compiler import KnowledgeCompiler
    kc = KnowledgeCompiler(rune, "agent-1", AsyncMock())
    s = kc.pressure_state(fact_count=15, min_memories=10)
    assert s["status"] == "ready"
    assert s["accumulator"] == 15


# ── L2: SkillEvolver — per-topic accumulator ──────────────────────


def test_skill_evolver_pressure_state_at_L2(rune):
    from nexus.evolution.skill_evolver import SkillEvolver
    se = SkillEvolver(rune, "agent-1", AsyncMock())
    se._topic_counts = {"travel": 1, "code": 2}
    s = se.pressure_state()
    _assert_shape(
        s, expected_layer="L2", expected_evolver="SkillEvolver",
    )
    assert s["unit"] == "topic_count"
    # Primary topic = closest to (but not yet at) threshold.
    assert s["details"]["primary_topic"] == "code"
    assert s["accumulator"] == 2
    # Per-topic breakdown for UI's stacked gauge.
    assert s["details"]["topics"]["travel"]["count"] == 1
    assert s["details"]["topics"]["code"]["ready"] is False


# ── L0: PersonaEvolver — time + drift dual triggers ───────────────


def test_persona_evolver_pressure_state_at_L0(rune):
    from nexus.evolution.persona_evolver import PersonaEvolver
    pe = PersonaEvolver(rune, "agent-1", AsyncMock())
    s = pe.pressure_state(cadence_days=30, drift_threshold=0.7, drift_score=0.0)
    _assert_shape(
        s, expected_layer="L0", expected_evolver="PersonaEvolver",
    )
    assert s["unit"] == "ratio"
    assert s["threshold"] == 1.0
    # Fed by everything below — that's the AHE pyramid.
    for upstream in ("MemoryEvolver", "KnowledgeCompiler", "SkillEvolver"):
        assert upstream in s["fed_by"]


def test_persona_pressure_dominant_signal_drift(rune):
    """Drift > time ratio ⇒ details.dominant_signal == 'drift', and
    accumulator picks the larger ratio so the gauge fills based on
    the more-pressing trigger."""
    from nexus.evolution.persona_evolver import PersonaEvolver
    pe = PersonaEvolver(rune, "agent-1", AsyncMock())
    # Just-evolved (last_ts now), but drift critical.
    pe._evolution_history.append({
        "timestamp": __import__("time").time(),
        "version": 1,
    })
    s = pe.pressure_state(
        cadence_days=30, drift_threshold=0.7, drift_score=0.65,
    )
    assert s["details"]["dominant_signal"] == "drift"
    # accumulator should reflect the high drift ratio (~0.93)
    assert s["accumulator"] > 0.85


def test_persona_pressure_clamps_above_one(rune):
    from nexus.evolution.persona_evolver import PersonaEvolver
    pe = PersonaEvolver(rune, "agent-1", AsyncMock())
    s = pe.pressure_state(
        cadence_days=30, drift_threshold=0.7, drift_score=10.0,
    )
    # Clamped to [0,1] so the UI gauge never overflows.
    assert s["accumulator"] == 1.0
    assert s["status"] == "ready"
