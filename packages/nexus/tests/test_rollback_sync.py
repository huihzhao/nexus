"""Phase D: typed-store rollback IS the rollback.

Phase O.6 added per-evolver ``apply_rollback`` callbacks to fix a
divergence between the typed store and the legacy artifact:

    Before Phase O.6:
      1. Persona evolves v2 → v3 (typed store + legacy artifact both updated)
      2. Verdict reverts → typed store rolls back to v2
      3. Legacy ``persona.json`` artifact STILL HAS v3
      4. ``self._current_persona`` STILL HAS v3
      5. UI shows v2 from typed store
      6. Agent's NEXT chat uses v3 — divergence

    Phase O.6 fix: VerdictRunner invoked the evolver's
    ``apply_rollback`` to re-sync the legacy artifact + cache.

Phase D went further: it deleted the legacy artifacts entirely.
The typed store IS the only state, so a typed-store rollback
flips the active version and the next ``load_*`` call sees it.
``apply_rollback`` and ``rollback_handlers`` are gone.

These tests pin the new invariant: after a typed-store rollback,
chat-time reads (``load_persona`` / ``load_skills`` / ``load_articles``)
return the rolled-back content automatically — no extra step.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

import nexus_core
from nexus_core.memory import (
    EventLog,
    SkillsStore, LearnedSkill,
    PersonaStore, PersonaVersion,
    KnowledgeStore, KnowledgeArticle,
)


@pytest.fixture
def rune():
    return nexus_core.builder().mock_backend().build()


@pytest.fixture
def event_log(tmp_path):
    return EventLog(base_dir=str(tmp_path / "elog"), agent_id="agent-1")


# ── PersonaEvolver ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persona_rollback_propagates_to_load_persona(rune, tmp_path):
    """After typed PersonaStore rollback, load_persona returns the
    rolled-back text on the next call. Phase D made apply_rollback
    unnecessary."""
    from nexus.evolution.persona_evolver import PersonaEvolver

    persona_store = PersonaStore(base_dir=tmp_path / "persona-store")
    persona_store.propose_version(PersonaVersion(persona_text="v1 text"))
    persona_store.propose_version(PersonaVersion(persona_text="v2 text"))

    evolver = PersonaEvolver(
        rune, "agent-1", AsyncMock(),
        persona_store=persona_store,
    )
    assert (await evolver.load_persona("default")) == "v2 text"

    # Verdict reverts
    persona_store.rollback("v0001")

    # Next load returns rolled-back text — no apply_rollback needed.
    assert (await evolver.load_persona("default")) == "v1 text"


def test_persona_evolver_has_no_apply_rollback():
    from nexus.evolution.persona_evolver import PersonaEvolver
    assert not hasattr(PersonaEvolver, "apply_rollback")


# ── SkillEvolver ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skill_rollback_propagates_to_load_skills(rune, tmp_path):
    """A typed SkillsStore rollback is reflected in the next
    load_skills() projection — no apply_rollback needed."""
    from nexus.evolution.skill_evolver import SkillEvolver

    skills_store = SkillsStore(base_dir=tmp_path / "skills-store")
    skills_store.upsert(LearnedSkill(skill_name="alpha", strategy="v1 strategy"))
    skills_store.commit()
    skills_store.upsert(LearnedSkill(skill_name="alpha", strategy="v2 strategy"))
    skills_store.upsert(LearnedSkill(skill_name="beta", strategy="new in v2"))
    skills_store.commit()

    evolver = SkillEvolver(
        rune, "agent-1", AsyncMock(), skills_store=skills_store,
    )
    cache = await evolver.load_skills()
    assert cache["alpha"]["best_strategy"] == "v2 strategy"
    assert "beta" in cache

    # Revert to v0001 — beta disappears, alpha returns to v1 strategy.
    skills_store.rollback("v0001")

    cache = await evolver.load_skills()
    assert cache["alpha"]["best_strategy"] == "v1 strategy"
    assert "beta" not in cache


def test_skill_evolver_has_no_apply_rollback():
    from nexus.evolution.skill_evolver import SkillEvolver
    assert not hasattr(SkillEvolver, "apply_rollback")


@pytest.mark.asyncio
async def test_skill_rollback_preserves_operational_fields(rune, tmp_path):
    """times_used / last_used / lessons[] are part of the typed
    schema (Phase D Step 2), so a typed rollback brings back the
    operational state from the rolled-back version too."""
    from nexus.evolution.skill_evolver import SkillEvolver

    skills_store = SkillsStore(base_dir=tmp_path / "skills-store")
    skills_store.upsert(LearnedSkill(
        skill_name="alpha",
        strategy="v1",
        times_used=5,
        last_used=1234.0,
    ))
    skills_store.commit()
    skills_store.upsert(LearnedSkill(
        skill_name="alpha",
        strategy="v2",
        times_used=20,  # bumped after more use
    ))
    skills_store.commit()

    evolver = SkillEvolver(
        rune, "agent-1", AsyncMock(), skills_store=skills_store,
    )
    cache = await evolver.load_skills()
    assert cache["alpha"]["times_used"] == 20

    skills_store.rollback("v0001")
    cache = await evolver.load_skills()
    # The typed store's v0001 had times_used=5, so the projection
    # reflects that — typed schema owns operational state now.
    assert cache["alpha"]["times_used"] == 5
    assert cache["alpha"]["best_strategy"] == "v1"


# ── KnowledgeCompiler ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_knowledge_rollback_propagates_to_load_articles(rune, tmp_path):
    from nexus.evolution.knowledge_compiler import KnowledgeCompiler

    knowledge_store = KnowledgeStore(base_dir=tmp_path / "knowledge-store")
    knowledge_store.upsert(KnowledgeArticle(
        title="topic_a", content="v1 content",
    ))
    knowledge_store.commit()
    knowledge_store.upsert(KnowledgeArticle(
        title="topic_a", content="v2 content",
    ))
    knowledge_store.upsert(KnowledgeArticle(
        title="topic_b", content="v2 only",
    ))
    knowledge_store.commit()

    compiler = KnowledgeCompiler(
        rune, "agent-1", AsyncMock(), knowledge_store=knowledge_store,
    )
    arts = await compiler.load_articles()
    assert arts["topic_a"]["content"] == "v2 content"
    assert "topic_b" in arts

    knowledge_store.rollback("v0001")
    arts = await compiler.load_articles()
    assert arts["topic_a"]["content"] == "v1 content"
    assert "topic_b" not in arts


def test_knowledge_compiler_has_no_apply_rollback():
    from nexus.evolution.knowledge_compiler import KnowledgeCompiler
    assert not hasattr(KnowledgeCompiler, "apply_rollback")


# ── VerdictRunner ────────────────────────────────────────────────────


def test_verdict_runner_no_rollback_handlers_kw():
    """Phase D removed the rollback_handlers parameter."""
    import inspect
    from nexus.evolution.verdict_runner import VerdictRunner
    sig = inspect.signature(VerdictRunner.__init__)
    assert "rollback_handlers" not in sig.parameters


def test_verdict_runner_no_rollback_handlers_attribute():
    """The runtime attribute that drove the legacy dispatch is gone."""
    from nexus.evolution.verdict_runner import VerdictRunner
    from nexus_core.memory import EventLog
    import tempfile
    runner = VerdictRunner(
        EventLog(base_dir=tempfile.mkdtemp(), agent_id="x"),
    )
    assert not hasattr(runner, "rollback_handlers")
