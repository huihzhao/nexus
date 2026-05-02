"""Phase O.2: MemoryEvolver emits evolution_proposal events.

Validates the contract that, when wired to an EventLog, every
``extract_and_store`` call writes an ``evolution_proposal`` event
into the log with the correct schema before the actual store write
happens. The event_log instrumentation is fully opt-in: callers that
don't pass an EventLog get the legacy behaviour unchanged.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

import nexus_core
from nexus_core.memory import EventLog, FactsStore
from nexus.evolution.memory_evolver import MemoryEvolver


@pytest.fixture
def rune():
    return nexus_core.builder().mock_backend().build()


@pytest.fixture
def event_log(tmp_path):
    return EventLog(base_dir=str(tmp_path / "elog"), agent_id="agent-1")


@pytest.fixture
def facts_store(tmp_path):
    return FactsStore(base_dir=str(tmp_path / "facts_store"))


def test_triggered_by_round_trips_through_event_log(event_log):
    """Proposal.triggered_by lineage data must survive the full
    EventLog round trip — evolver writes proposal → event_log row →
    verdict_runner._proposal_from_event reconstructs. UI's lineage
    card is downstream of this read path and silently breaks if the
    field gets dropped anywhere on the way through.
    """
    from nexus_core.evolution import EvolutionProposal
    from nexus.evolution.verdict_runner import _proposal_from_event

    p = EvolutionProposal(
        edit_id="round-trip-1",
        evolver="MemoryEvolver",
        target_namespace="memory.facts",
        target_version_pre="(uncommitted)",
        target_version_post="(uncommitted)",
        triggered_by={
            "trigger_reason": "per_turn_extraction",
            "window": {"start_event_id": 50, "end_event_id": 51},
            "counts": {"events": 1},
        },
    )
    event_log.append(
        event_type="evolution_proposal",
        content="MemoryEvolver → memory.facts: extract+upsert",
        metadata=p.to_event_metadata(),
    )
    rows = [
        e for e in event_log.recent(limit=10)
        if e.event_type == "evolution_proposal"
    ]
    assert len(rows) == 1
    rebuilt = _proposal_from_event(rows[0])
    assert rebuilt is not None
    assert rebuilt.triggered_by == {
        "trigger_reason": "per_turn_extraction",
        "window": {"start_event_id": 50, "end_event_id": 51},
        "counts": {"events": 1},
    }


@pytest.mark.asyncio
async def test_memory_evolver_emits_triggered_by_for_pressure_dashboard(
    rune, event_log, facts_store,
):
    """MemoryEvolver populates ``triggered_by`` so the Pressure
    Dashboard's lineage card can render "caused by N facts in
    extraction round M". Without this the card falls back to "(no
    lineage data)" and breaks the causal chain visualization."""
    from nexus.evolution.memory_evolver import MemoryEvolver

    llm_fn = AsyncMock(return_value=json.dumps([
        {"content": "user likes tea", "category": "preference", "importance": 4},
        {"content": "user lives in Tokyo", "category": "fact", "importance": 5},
    ]))
    me = MemoryEvolver(
        rune, "agent-1", llm_fn,
        event_log=event_log, facts_store=facts_store,
    )
    await me.extract_and_store(conversation=[
        {"role": "user", "content": "I prefer tea, and I live in Tokyo"},
    ])

    proposals = [
        e for e in event_log.recent(limit=20)
        if e.event_type == "evolution_proposal"
    ]
    assert len(proposals) == 1
    md = proposals[0].metadata
    assert md["triggered_by"]["trigger_reason"] == "per_turn_extraction"
    assert md["triggered_by"]["counts"]["facts_in_batch"] == 2


@pytest.mark.asyncio
async def test_skill_evolver_emits_triggered_by_distinguishing_path(
    rune, event_log, tmp_path,
):
    """SkillEvolver has two paths — direct skill detection vs topic
    promotion. The lineage card needs to know which one fired so
    it can render the right causal arrow ("learned from this
    conversation" vs "topic threshold reached after N
    conversations")."""
    from nexus.evolution.skill_evolver import SkillEvolver
    from nexus_core.memory import SkillsStore

    skills_store = SkillsStore(base_dir=str(tmp_path / "skills"))
    llm_fn = AsyncMock(return_value=json.dumps({
        "implicit_tasks": [{
            "skill_name": "code_review", "description": "review PRs",
            "procedure": "1. read diff", "lesson": "test coverage matters",
            "confidence": 0.7, "tags": ["code"],
        }],
        "topic_signals": [],
    }))
    se = SkillEvolver(
        rune, "agent-1", llm_fn,
        event_log=event_log, skills_store=skills_store,
    )
    await se.learn_from_conversation([
        {"role": "user", "content": "review my PR"},
        {"role": "assistant", "content": "sure"},
    ])

    proposals = [
        e for e in event_log.recent(limit=20)
        if e.event_type == "evolution_proposal"
    ]
    assert len(proposals) == 1
    md = proposals[0].metadata
    # Direct conversation path → trigger_reason reflects that.
    assert md["triggered_by"]["trigger_reason"] == "conversation_skill_detected"
    assert md["triggered_by"]["counts"]["skills_learned"] == 1
    assert md["triggered_by"]["counts"]["topic_promotions"] == 0


@pytest.mark.asyncio
async def test_persona_evolver_emits_triggered_by_with_confidence(
    rune, event_log,
):
    """PersonaEvolver's lineage data includes the confidence + delta
    so the Pressure Dashboard can show "v2 evolved with 80%
    confidence, +180 chars" alongside the AHE pyramid arrow."""
    from nexus.evolution.persona_evolver import PersonaEvolver

    llm_fn = AsyncMock(return_value=json.dumps({
        "evolved_persona": "x" * 150,
        "changes_summary": "warmer + more concise",
        "confidence": 0.85,
        "version_notes": "v2",
    }))
    from nexus_core.memory import PersonaStore, PersonaVersion
    persona_store = PersonaStore(base_dir=rune._cache_dir if hasattr(rune, "_cache_dir") else "/tmp/persona-test")
    persona_store.propose_version(PersonaVersion(persona_text="y" * 50))
    pe = PersonaEvolver(
        rune, "agent-1", llm_fn,
        event_log=event_log, persona_store=persona_store,
    )
    await pe.evolve(memories_sample=[], skills_summary={})

    proposals = [
        e for e in event_log.recent(limit=20)
        if e.event_type == "evolution_proposal"
        and (e.metadata or {}).get("evolver") == "PersonaEvolver"
    ]
    assert len(proposals) == 1
    md = proposals[0].metadata
    tb = md["triggered_by"]
    assert tb["trigger_reason"] == "reflection_cycle"
    assert 0.84 < tb["confidence"] <= 0.86
    assert tb["delta_chars"] == 100  # 150 - 50


def test_triggered_by_defaults_empty_for_legacy_proposals(event_log):
    """Old proposals (pre-Phase A+) didn't have triggered_by; the
    runner must reconstruct them with an empty dict, not None or
    KeyError."""
    from nexus.evolution.verdict_runner import _proposal_from_event

    # Simulate a legacy proposal written before triggered_by existed.
    legacy_md = {
        "edit_id": "legacy-1",
        "evolver": "MemoryEvolver",
        "target_namespace": "memory.facts",
        "target_version_pre": "(uncommitted)",
        "target_version_post": "(uncommitted)",
        "evidence_event_ids": [],
        "change_summary": "extract 1 fact",
        "change_diff": [],
        "rollback_pointer": "(uncommitted)",
        "expires_after_events": 100,
        # NOTE: no "triggered_by" key
    }
    event_log.append(
        event_type="evolution_proposal",
        content="legacy",
        metadata=legacy_md,
    )
    rows = [
        e for e in event_log.recent(limit=10)
        if e.event_type == "evolution_proposal"
    ]
    rebuilt = _proposal_from_event(rows[0])
    assert rebuilt is not None
    assert rebuilt.triggered_by == {}, (
        "missing triggered_by must default to empty dict — UI "
        "lineage view degrades to 'no data' rather than crashing"
    )


# ── Happy path ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_emits_one_proposal_per_batch(rune, event_log, facts_store):
    """One ``extract_and_store`` call → exactly one evolution_proposal
    event with the correct evolver / namespace / change_summary."""
    llm_fn = AsyncMock(return_value=json.dumps([
        {"content": "User likes ramen.",  "category": "preference", "importance": 4},
        {"content": "User lives in Osaka.", "category": "fact",       "importance": 5},
    ]))
    evolver = MemoryEvolver(
        rune, "agent-1", llm_fn,
        facts_store=facts_store,
        event_log=event_log,
    )

    await evolver.extract_and_store(
        conversation=[{"role": "user", "content": "hi"}],
    )

    proposals = [e for e in event_log.recent(limit=20)
                 if e.event_type == "evolution_proposal"]
    assert len(proposals) == 1, f"expected 1 proposal event, got {len(proposals)}"

    p = proposals[0]
    assert p.metadata["evolver"] == "MemoryEvolver"
    assert p.metadata["target_namespace"] == "memory.facts"
    assert "extract+upsert 2 memories" in p.metadata["change_summary"]
    # change_diff has one row per extracted memory
    assert len(p.metadata["change_diff"]) == 2
    assert all(d["op"] == "add" for d in p.metadata["change_diff"])


@pytest.mark.asyncio
async def test_proposal_carries_unique_edit_id(rune, event_log, facts_store):
    """Two consecutive extractions emit two proposals with distinct
    ``edit_id``s — required so the verdict scorer can attribute
    observed events to the correct proposal."""
    llm_fn = AsyncMock(return_value=json.dumps([
        {"content": "F1", "category": "fact", "importance": 3},
    ]))
    evolver = MemoryEvolver(
        rune, "agent-1", llm_fn,
        facts_store=facts_store, event_log=event_log,
    )
    await evolver.extract_and_store(conversation=[{"role": "user", "content": "x"}])
    await evolver.extract_and_store(conversation=[{"role": "user", "content": "y"}])

    proposals = [e for e in event_log.recent(limit=20)
                 if e.event_type == "evolution_proposal"]
    assert len(proposals) == 2
    edit_ids = {p.metadata["edit_id"] for p in proposals}
    assert len(edit_ids) == 2, "edit_ids must be unique per proposal"


@pytest.mark.asyncio
async def test_stored_memory_metadata_links_back_to_proposal(rune, event_log, facts_store):
    """Each stored Fact's ``extra`` carries the
    ``evolution_edit_id`` from the proposal that introduced it.
    This is what makes the verdict scorer's later attribution
    possible: observe regression → trace fact key → look up edit_id
    → find proposal in event log.

    Phase D 续: facts_store is canonical (no rune.memory writeback).
    """
    llm_fn = AsyncMock(return_value=json.dumps([
        {"content": "trace me", "category": "fact", "importance": 3},
    ]))
    evolver = MemoryEvolver(
        rune, "agent-1", llm_fn,
        facts_store=facts_store, event_log=event_log,
    )
    await evolver.extract_and_store(conversation=[{"role": "user", "content": "x"}])

    proposals = [e for e in event_log.recent(limit=20)
                 if e.event_type == "evolution_proposal"]
    assert len(proposals) == 1
    edit_id = proposals[0].metadata["edit_id"]

    facts = facts_store.all()
    assert len(facts) == 1
    assert facts[0].extra.get("evolution_edit_id") == edit_id


# ── Empty-batch + opt-in semantics ──────────────────────────────────


@pytest.mark.asyncio
async def test_no_proposal_when_extraction_is_empty(rune, event_log, facts_store):
    """LLM returned ``[]`` → no batch → no proposal event."""
    llm_fn = AsyncMock(return_value="[]")
    evolver = MemoryEvolver(
        rune, "agent-1", llm_fn,
        facts_store=facts_store, event_log=event_log,
    )
    await evolver.extract_and_store(conversation=[{"role": "user", "content": "x"}])

    proposals = [e for e in event_log.recent(limit=20)
                 if e.event_type == "evolution_proposal"]
    assert proposals == []


@pytest.mark.asyncio
async def test_no_event_log_means_no_proposal_emitted(rune, facts_store):
    """When EventLog is not wired in, the evolver behaves exactly
    as it did pre-Phase O.2 — no events emitted, the facts_store
    write still runs, no exception. Phase D 续: facts_store is
    canonical."""
    llm_fn = AsyncMock(return_value=json.dumps([
        {"content": "legacy fact", "category": "fact", "importance": 3},
    ]))
    evolver = MemoryEvolver(rune, "agent-1", llm_fn, facts_store=facts_store)
    out = await evolver.extract_and_store(
        conversation=[{"role": "user", "content": "x"}],
    )
    assert len(out) == 1
    facts = facts_store.all()
    assert len(facts) == 1
    # No edit_id stamped on extra when no event log was provided
    assert "evolution_edit_id" not in facts[0].extra


# ── Failure isolation ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_event_log_failure_does_not_break_extraction(rune, facts_store, monkeypatch):
    """If EventLog.append raises (disk full, etc.), the extraction
    still completes — proposal emission is best-effort, not a
    blocking dependency."""
    class BoomLog:
        def append(self, *a, **kw):
            raise RuntimeError("disk full")

    llm_fn = AsyncMock(return_value=json.dumps([
        {"content": "still fine", "category": "fact", "importance": 3},
    ]))
    evolver = MemoryEvolver(
        rune, "agent-1", llm_fn,
        facts_store=facts_store, event_log=BoomLog(),
    )
    out = await evolver.extract_and_store(
        conversation=[{"role": "user", "content": "x"}],
    )
    # Legacy bulk_add path still succeeds
    assert len(out) == 1
    assert out[0]["content"] == "still fine"


# ── PersonaEvolver instrumentation ───────────────────────────────────


@pytest.mark.asyncio
async def test_persona_evolve_emits_proposal(rune, event_log, tmp_path):
    """PersonaEvolver.evolve emits exactly one evolution_proposal
    event with target_namespace="memory.persona" and a change_diff
    capturing prev_len / post_len."""
    from nexus.evolution.persona_evolver import PersonaEvolver
    from nexus_core.memory import PersonaStore

    persona_store = PersonaStore(base_dir=str(tmp_path / "persona"))
    # First persona version on disk so target_version_pre has a label.
    from nexus_core.memory import PersonaVersion
    persona_store.propose_version(PersonaVersion(persona_text="initial"))

    evolved_text = "You are a thoughtful, concise digital twin who " * 5
    llm_fn = AsyncMock(return_value=json.dumps({
        "evolved_persona": evolved_text,
        "changes_summary": "tightened tone",
        "confidence": 0.8,
        "version_notes": "v2",
    }))
    pe = PersonaEvolver(
        rune, "agent-1", llm_fn,
        event_log=event_log,
        persona_store=persona_store,
    )
    await pe.load_persona("Initial baseline persona text " * 3)
    result = await pe.evolve(memories_sample=["m1", "m2"], skills_summary={})

    assert "error" not in result
    assert "evolution_edit_id" in result and result["evolution_edit_id"]

    proposals = [e for e in event_log.recent(limit=10)
                 if e.event_type == "evolution_proposal"]
    assert len(proposals) == 1
    p = proposals[0]
    assert p.metadata["evolver"] == "PersonaEvolver"
    assert p.metadata["target_namespace"] == "memory.persona"
    assert p.metadata["edit_id"] == result["evolution_edit_id"]

    diffs = p.metadata["change_diff"]
    assert len(diffs) == 1
    assert diffs[0]["op"] == "replace"
    assert diffs[0]["field"] == "persona_text"
    assert diffs[0]["post_len"] == len(evolved_text)


@pytest.mark.asyncio
async def test_persona_evolve_no_event_log_means_no_proposal(rune):
    """Persona evolver without event_log → no event emitted, no error.

    Phase D: persona text now lives in the typed PersonaStore. We
    verify the typed store advanced rather than checking a removed
    in-memory cache (``_current_persona`` is gone).
    """
    from nexus.evolution.persona_evolver import PersonaEvolver

    llm_fn = AsyncMock(return_value=json.dumps({
        "evolved_persona": "x" * 100,
        "changes_summary": "something",
        "confidence": 0.7,
    }))
    pe = PersonaEvolver(rune, "agent-1", llm_fn)  # no event_log
    await pe.load_persona("old")
    result = await pe.evolve(memories_sample=[], skills_summary={})
    # No event_log → edit_id is empty string but evolution still succeeds
    assert result.get("evolution_edit_id") == ""
    # Typed store now owns the active persona.
    assert pe.persona_store.current() is not None
    assert pe.persona_store.current().persona_text == "x" * 100


# ── SkillEvolver instrumentation ─────────────────────────────────────


@pytest.mark.asyncio
async def test_skill_learn_emits_proposal(rune, event_log, tmp_path):
    """SkillEvolver.learn_from_conversation emits one
    evolution_proposal per batch with target_namespace=memory.skills."""
    from nexus.evolution.skill_evolver import SkillEvolver
    from nexus_core.memory import SkillsStore

    skills_store = SkillsStore(base_dir=str(tmp_path / "skills"))
    llm_fn = AsyncMock(return_value=json.dumps({
        "implicit_tasks": [
            {
                "skill_name": "travel_planning",
                "description": "Plan multi-day trips",
                "procedure": "## steps\n1. Ask dates\n2. Ask budget",
                "lesson": "Always confirm dates first",
                "confidence": 0.7,
                "tags": ["travel"],
            },
        ],
        "topic_signals": [],
    }))
    se = SkillEvolver(
        rune, "agent-1", llm_fn,
        event_log=event_log,
        skills_store=skills_store,
    )
    learned = await se.learn_from_conversation(
        conversation=[
            {"role": "user", "content": "plan a trip to Tokyo"},
            {"role": "assistant", "content": "let me help"},
        ],
    )
    assert len(learned) == 1
    assert learned[0]["evolution_edit_id"]

    proposals = [e for e in event_log.recent(limit=20)
                 if e.event_type == "evolution_proposal"]
    assert len(proposals) == 1
    p = proposals[0]
    assert p.metadata["evolver"] == "SkillEvolver"
    assert p.metadata["target_namespace"] == "memory.skills"
    assert p.metadata["edit_id"] == learned[0]["evolution_edit_id"]
    assert len(p.metadata["change_diff"]) == 1
    assert p.metadata["change_diff"][0]["skill_name"] == "travel_planning"


@pytest.mark.asyncio
async def test_skill_learn_no_event_log_no_proposal(rune):
    """No event_log → SkillEvolver still upserts skills, just no events."""
    from nexus.evolution.skill_evolver import SkillEvolver

    llm_fn = AsyncMock(return_value=json.dumps({
        "implicit_tasks": [
            {"skill_name": "x", "description": "y", "procedure": "z",
             "lesson": "l", "confidence": 0.5, "tags": []},
        ],
        "topic_signals": [],
    }))
    se = SkillEvolver(rune, "agent-1", llm_fn)  # no event_log
    learned = await se.learn_from_conversation(
        conversation=[{"role": "user", "content": "x"}],
    )
    assert len(learned) == 1
    assert "evolution_edit_id" not in learned[0]


@pytest.mark.asyncio
async def test_skill_learn_dual_writes_typed_store(rune, tmp_path):
    """Regression for the bug where Activity showed "learn 1 skill(s)"
    but the desktop "Skills 0 items" memory-namespace panel stayed
    empty: SkillEvolver was only writing to the legacy
    skills_registry.json artifact and never mirroring into the typed
    SkillsStore that /api/v1/agent/memory/namespaces reads.

    Verifies that learning a skill via the conversation path leaves
    a corresponding LearnedSkill in the typed store with its fields
    projected from the legacy cache entry (description, strategy,
    last_lesson, confidence, tags).
    """
    from nexus.evolution.skill_evolver import SkillEvolver
    from nexus_core.memory import SkillsStore

    skills_store = SkillsStore(base_dir=str(tmp_path / "skills"))
    assert len(skills_store.all()) == 0  # empty before

    llm_fn = AsyncMock(return_value=json.dumps({
        "implicit_tasks": [
            {
                "skill_name": "code_review",
                "description": "Review PRs for correctness + perf",
                "procedure": "## steps\n1. Read diff\n2. Check tests",
                "lesson": "User cares about test coverage",
                "confidence": 0.75,
                "tags": ["code", "review"],
            },
        ],
        "topic_signals": [],
    }))
    se = SkillEvolver(
        rune, "agent-1", llm_fn, skills_store=skills_store,
    )
    learned = await se.learn_from_conversation(
        conversation=[
            {"role": "user", "content": "review my PR"},
            {"role": "assistant", "content": "sure, here's my review"},
        ],
    )
    assert len(learned) == 1

    # The typed store should now reflect the learned skill — this is
    # what the /memory/namespaces endpoint reads to produce the
    # "Skills N items · M versions" pill.
    typed = skills_store.all()
    assert len(typed) == 1, (
        "SkillEvolver did not dual-write into typed SkillsStore — "
        "the desktop memory-namespace panel will show 0 items."
    )
    sk = typed[0]
    assert sk.skill_name == "code_review"
    assert sk.description == "Review PRs for correctness + perf"
    assert "1. Read diff" in sk.strategy
    assert sk.last_lesson == "User cares about test coverage"
    assert 0.7 < sk.confidence <= 0.8
    assert set(sk.tags) >= {"code", "review"}
    # Counters should reflect that this learn was a "success" outcome.
    assert sk.success_count == 1
    assert sk.failure_count == 0


@pytest.mark.asyncio
async def test_skill_topic_promotion_dual_writes_typed_store(rune, tmp_path):
    """SkillEvolver has a second path beyond explicit ``implicit_tasks``:
    repeated topic signals get promoted to a synthetic skill once they
    cross ``_topic_skill_threshold``. Verifies that path also calls
    through ``_upsert_skill`` so the dual-write applies — otherwise
    topic-promoted skills would still be invisible in the namespace
    panel.
    """
    from nexus.evolution.skill_evolver import SkillEvolver
    from nexus_core.memory import SkillsStore

    skills_store = SkillsStore(base_dir=str(tmp_path / "skills"))
    se = SkillEvolver(rune, "agent-1", AsyncMock(), skills_store=skills_store)
    # Drive promotion by jamming the topic count above the threshold
    # in one shot — same effect as 3 conversations all about "travel".
    se._accumulate_topics([
        {"topic": "travel", "evidence": "trip 1"},
        {"topic": "travel", "evidence": "trip 2"},
        {"topic": "travel", "evidence": "trip 3"},
    ])
    # Phase D: typed-store writes flush via _save_skills_unlocked
    # (batched at the end of learn_from_conversation). For this
    # focused test we trigger the flush manually.
    await se._save_skills_unlocked()
    typed = skills_store.all()
    assert len(typed) == 1, (
        "Topic-promoted skill not mirrored to typed store — the "
        "namespace panel will miss skills derived from topic "
        "frequency accumulation."
    )
    assert typed[0].skill_name == "travel"
    assert "topic_expertise" in typed[0].tags


@pytest.mark.asyncio
async def test_persona_evolve_dual_writes_typed_store(rune, tmp_path):
    """Regression for the same disconnect class as SkillEvolver:
    PersonaEvolver was writing only to the legacy ``persona.json``
    artifact and never bumping the typed PersonaStore, so the
    desktop "Persona N versions" pill stayed at 0 and the evolution
    timeline UI couldn't render new entries.
    """
    from nexus.evolution.persona_evolver import PersonaEvolver
    from nexus_core.memory import PersonaStore

    persona_store = PersonaStore(base_dir=str(tmp_path / "ps"))
    assert len(persona_store) == 0  # empty before

    llm_fn = AsyncMock(return_value=json.dumps({
        "evolved_persona": "x" * 100,
        "changes_summary": "warmer tone",
        "confidence": 0.8,
        "version_notes": "v0.2",
    }))
    pe = PersonaEvolver(
        rune, "agent-1", llm_fn, persona_store=persona_store,
    )
    await pe.load_persona("old persona")
    result = await pe.evolve(memories_sample=[], skills_summary={})

    # The typed store should now have a new version.
    assert len(persona_store) == 1, (
        "PersonaEvolver did not dual-write into typed PersonaStore — "
        "the desktop persona panel will show 0 versions."
    )
    typed_version = persona_store.current_version()
    assert typed_version is not None
    assert result.get("typed_version") == typed_version

    current = persona_store.current()
    assert current is not None
    assert current.persona_text == "x" * 100
    assert current.changes_summary == "warmer tone"
    assert 0.79 < current.confidence <= 0.81
    assert current.version_notes == "v0.2"


@pytest.mark.asyncio
async def test_knowledge_compile_writes_typed_store(rune, tmp_path):
    """Phase D 续: KnowledgeCompiler reads facts from FactsStore
    (canonical), writes articles to KnowledgeStore (canonical).
    No more dual-write — typed stores are the only paths.
    """
    from nexus.evolution.knowledge_compiler import KnowledgeCompiler
    from nexus_core.memory import Fact, FactsStore, KnowledgeStore

    knowledge_store = KnowledgeStore(base_dir=str(tmp_path / "kn"))
    facts_store = FactsStore(base_dir=str(tmp_path / "facts"))
    assert len(knowledge_store) == 0

    # Seed the FactsStore with enough facts to clear min_memories.
    for i in range(12):
        facts_store.upsert(Fact(
            content="user likes travel", category="preference", importance=4,
        ))

    compiler = KnowledgeCompiler(
        rune, "agent-1", AsyncMock(return_value="{}"),
        knowledge_store=knowledge_store,
        facts_store=facts_store,
    )

    async def fake_cluster(_):
        return {"travel": [0, 1]}

    async def fake_compile(topic, mems, existing):
        return {
            "title": f"User's {topic}",
            "summary": f"Summary about {topic}",
            "content": f"Long content about {topic}.",
            "key_facts": [f"likes {topic}"],
            "tags": [topic],
            "confidence": 0.7,
        }

    compiler._cluster_memories = fake_cluster
    compiler._compile_article = fake_compile

    result = await compiler.compile(min_memories=10)
    assert result["status"] == "compiled"
    assert "travel" in result["new_articles"]

    articles = knowledge_store.all()
    assert len(articles) == 1
    a = articles[0]
    assert a.title == "User's travel"
    assert "travel" in a.tags
    assert a.confidence == 0.7


@pytest.mark.asyncio
async def test_skill_learn_typed_store_failure_logs_but_does_not_lose_skill(
    rune, tmp_path,
):
    """Phase D: typed store IS the source of truth — but a transient
    upsert failure shouldn't lose the just-learned skill from the
    in-memory projection. Subsequent ``_save_skills_unlocked`` calls
    will retry, and a successful one rehydrates the typed store.
    """
    from nexus.evolution.skill_evolver import SkillEvolver
    from nexus_core.memory import SkillsStore

    real_store = SkillsStore(base_dir=str(tmp_path / "skills"))

    class FlakyStore:
        """Wrap a real store, raising on the next upsert call."""
        def __init__(self, inner):
            self._inner = inner
            self._raise_next = True

        def current_version(self):
            return self._inner.current_version()

        def all(self):
            return self._inner.all()

        @property
        def base_dir(self):
            return self._inner.base_dir

        def upsert(self, x):
            if self._raise_next:
                self._raise_next = False
                raise RuntimeError("disk full")
            return self._inner.upsert(x)

        def commit(self):
            return self._inner.commit()

    llm_fn = AsyncMock(return_value=json.dumps({
        "implicit_tasks": [
            {"skill_name": "x", "description": "d", "procedure": "p",
             "lesson": "l", "confidence": 0.5, "tags": []},
        ],
        "topic_signals": [],
    }))
    se = SkillEvolver(
        rune, "agent-1", llm_fn, skills_store=FlakyStore(real_store),
    )
    learned = await se.learn_from_conversation(
        conversation=[{"role": "user", "content": "x"}],
    )
    # The skill is in the projection even though typed-store
    # upsert failed once. Retry by triggering save again.
    assert len(learned) == 1
    assert "x" in se._skills_cache
    await se._save_skills_unlocked()
    assert any(s.skill_name == "x" for s in real_store.all())


# ── KnowledgeCompiler instrumentation ────────────────────────────────


@pytest.mark.asyncio
async def test_knowledge_compile_emits_proposal(rune, event_log, tmp_path):
    """KnowledgeCompiler.compile emits one evolution_proposal per
    successful compilation, with diff entries listing topic-level
    add/update operations."""
    from nexus.evolution.knowledge_compiler import KnowledgeCompiler
    from nexus_core.memory import KnowledgeStore

    knowledge_store = KnowledgeStore(base_dir=str(tmp_path / "kn"))

    # Phase D 续: seed FactsStore (canonical), not rune.memory.
    from nexus_core.memory import Fact, FactsStore
    facts_store = FactsStore(base_dir=str(tmp_path / "facts"))
    for i in range(12):
        facts_store.upsert(Fact(
            content=f"travel fact {i}", category="fact", importance=3,
        ))

    cluster_response = json.dumps({"travel": list(range(12))})
    article_response = json.dumps({
        "title": "Travel preferences",
        "summary": "User loves Tokyo",
        "content": "Long-form synthesis...",
        "key_facts": ["likes Tokyo"],
        "tags": ["travel"],
    })
    call_idx = {"n": 0}

    async def fake_llm(prompt: str) -> str:
        call_idx["n"] += 1
        if call_idx["n"] == 1:
            return cluster_response
        return article_response

    kc = KnowledgeCompiler(
        rune, "agent-1", fake_llm,
        event_log=event_log,
        knowledge_store=knowledge_store,
        facts_store=facts_store,
    )
    result = await kc.compile(min_memories=10)
    assert result["status"] == "compiled"
    assert result.get("evolution_edit_id"), "expected an edit_id when articles changed"

    proposals = [e for e in event_log.recent(limit=20)
                 if e.event_type == "evolution_proposal"]
    assert len(proposals) == 1
    p = proposals[0]
    assert p.metadata["evolver"] == "KnowledgeCompiler"
    assert p.metadata["target_namespace"] == "memory.knowledge"
    # Topic appears in change_diff
    diffs = p.metadata["change_diff"]
    assert any(d.get("topic") == "travel" for d in diffs)


@pytest.mark.asyncio
async def test_knowledge_compile_skips_proposal_when_nothing_changed(rune, event_log):
    """If compile() skips (not enough memories), no event is emitted."""
    from nexus.evolution.knowledge_compiler import KnowledgeCompiler

    kc = KnowledgeCompiler(
        rune, "agent-1", AsyncMock(return_value="{}"),
        event_log=event_log,
    )
    result = await kc.compile(min_memories=10)
    assert result["status"] == "skipped"
    proposals = [e for e in event_log.recent(limit=20)
                 if e.event_type == "evolution_proposal"]
    assert proposals == []
