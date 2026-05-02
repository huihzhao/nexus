"""Phase D 续 (#157): MemoryEvolver writes only to FactsStore.

Phase J.7 had a dual-write contract (legacy ``rune.memory`` +
typed ``FactsStore``). Phase D 续 collapsed it: FactsStore is now
the single source of truth.

These tests pin the new contract:

* extracted memories → ``Fact`` rows in FactsStore
* category mapping (LLM → FactsStore vocabulary) is total + valid
* "skill"-tagged extractions are mapped to ``context`` (per the
  Phase D 续 design decision: facts about user's skills are still
  facts about the user, distinct from the agent's own learned
  strategies which live in SkillsStore)
* importance is clamped to FactsStore's [1, 5] domain
* there is no second write path — FactsStore is canonical
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

import nexus_core
from nexus_core.memory import FactsStore
from nexus.evolution.memory_evolver import MemoryEvolver, _MEMORY_TO_FACT_CATEGORY


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def rune():
    return nexus_core.builder().mock_backend().build()


@pytest.fixture
def facts_store(tmp_path):
    return FactsStore(base_dir=str(tmp_path / "facts_dual_write"))


# ── Mapping invariants ───────────────────────────────────────────────


def test_mapping_covers_legacy_categories():
    """Every legacy category is mapped to a FactsStore category.
    Phase D 续 removed the None-skip path: every extracted memory
    becomes a Fact (skill-tagged ones land in ``context``)."""
    legacy = {"preference", "fact", "decision_pattern", "style", "skill", "relationship"}
    assert legacy.issubset(_MEMORY_TO_FACT_CATEGORY.keys())
    # No category is opted out anymore — all map to a non-None target.
    for src in legacy:
        assert _MEMORY_TO_FACT_CATEGORY[src] is not None


def test_mapping_targets_are_valid_fact_categories():
    """Every mapping target must be a legal FactsStore category."""
    valid = {"preference", "fact", "constraint", "goal", "context"}
    for src, tgt in _MEMORY_TO_FACT_CATEGORY.items():
        if tgt is None:
            continue
        assert tgt in valid, f"{src!r} maps to invalid FactsStore category {tgt!r}"


def test_skill_category_maps_to_context():
    """Phase D 续 decision: ``skill``-tagged extractions are facts
    *about the user* and belong in FactsStore. SkillsStore tracks
    the agent's own learned strategies, which is a different thing."""
    assert _MEMORY_TO_FACT_CATEGORY["skill"] == "context"


# ── Single-write happy path ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_writes_to_facts_store(rune, facts_store):
    """Extracted memories show up as Fact entries with correct mapped
    category, importance, and source metadata."""
    llm_fn = AsyncMock(return_value=json.dumps([
        {"content": "User prefers tea over coffee.",  "category": "preference", "importance": 4},
        {"content": "User lives in Tokyo.",            "category": "fact",       "importance": 5},
        {"content": "User uses TDD-first style.",      "category": "style",      "importance": 3},
    ]))
    evolver = MemoryEvolver(rune, "agent-1", llm_fn, facts_store=facts_store)

    out = await evolver.extract_and_store(
        conversation=[{"role": "user", "content": "hi"}],
    )
    assert len(out) == 3

    facts = facts_store.all()
    assert len(facts) == 3

    by_content = {f.content: f for f in facts}
    assert by_content["User prefers tea over coffee."].category == "preference"
    assert by_content["User prefers tea over coffee."].importance == 4
    assert by_content["User lives in Tokyo."].category == "fact"
    assert by_content["User lives in Tokyo."].importance == 5
    # "style" maps to "context"
    assert by_content["User uses TDD-first style."].category == "context"

    # source metadata captured for audit / future evolver verdicts
    for f in facts:
        assert f.extra.get("source") == "memory_evolver"
        assert "extraction_round" in f.extra
        assert "original_category" in f.extra


@pytest.mark.asyncio
async def test_skill_category_lands_in_facts(rune, facts_store):
    """Phase D 续: skill-tagged extractions map to context, NOT
    skipped — the FactsStore is canonical and skipping would lose
    real user-facing data."""
    llm_fn = AsyncMock(return_value=json.dumps([
        {"content": "User wants to learn Rust.", "category": "skill", "importance": 3},
        {"content": "User loves dogs.",            "category": "fact",  "importance": 4},
    ]))
    evolver = MemoryEvolver(rune, "agent-1", llm_fn, facts_store=facts_store)

    await evolver.extract_and_store(conversation=[{"role": "user", "content": "hi"}])

    facts = facts_store.all()
    contents = {f.content for f in facts}
    assert "User loves dogs." in contents
    # The skill-tagged extraction now ends up in facts (mapped to
    # ``context``), where pre-Phase-D-续 it was dropped.
    assert "User wants to learn Rust." in contents
    by_content = {f.content: f for f in facts}
    assert by_content["User wants to learn Rust."].category == "context"
    assert by_content["User wants to learn Rust."].extra.get("original_category") == "skill"


@pytest.mark.asyncio
async def test_importance_is_clamped_into_facts_domain(rune, facts_store):
    """Extraction prompt allows 1-5 but be defensive against LLM
    returning out-of-range values."""
    llm_fn = AsyncMock(return_value=json.dumps([
        {"content": "Low.",  "category": "fact", "importance": 0},
        {"content": "High.", "category": "fact", "importance": 9},
    ]))
    evolver = MemoryEvolver(rune, "agent-1", llm_fn, facts_store=facts_store)

    await evolver.extract_and_store(conversation=[{"role": "user", "content": "hi"}])
    by_content = {f.content: f for f in facts_store.all()}
    assert by_content["Low."].importance == 1
    assert by_content["High."].importance == 5


# ── Single-source-of-truth ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_facts_store_synthesises_scratch(rune):
    """Phase D 续: facts_store is auto-synthesised when not passed
    (matches the pattern in PersonaEvolver / SkillEvolver). The
    legacy ``_dual_write_facts`` helper is gone."""
    llm_fn = AsyncMock(return_value=json.dumps([
        {"content": "Some fact.", "category": "fact", "importance": 3},
    ]))
    evolver = MemoryEvolver(rune, "agent-1", llm_fn)  # no facts_store kwarg
    out = await evolver.extract_and_store(
        conversation=[{"role": "user", "content": "hi"}],
    )
    assert len(out) == 1
    # The auto-synthesised store has the fact.
    assert any(f.content == "Some fact." for f in evolver.facts_store.all())
    # The dual-write helper is gone.
    assert not hasattr(evolver, "_dual_write_facts")


def test_no_dual_write_helper_on_class():
    """Static contract: Phase D 续 removed ``_dual_write_facts``."""
    assert not hasattr(MemoryEvolver, "_dual_write_facts")
