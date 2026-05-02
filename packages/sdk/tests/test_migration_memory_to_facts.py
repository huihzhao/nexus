"""Tests for one-shot memory→facts migration (Phase D 续 #2)."""

from __future__ import annotations

import asyncio

import pytest

from nexus_core.backends import MockBackend
from nexus_core.memory.facts import Fact, FactsStore
from nexus_core.migrations.memory_to_facts import migrate


@pytest.fixture
def facts_store(tmp_path):
    return FactsStore(base_dir=tmp_path / "facts")


@pytest.fixture
def backend():
    return MockBackend()


async def _seed_legacy(backend: MockBackend, agent_id: str, entries: list[dict]):
    """Helper: write entries the way legacy MemoryProvider would."""
    for e in entries:
        mid = e["memory_id"]
        await backend.store_json(
            f"agents/{agent_id}/memory/{mid}.json", e,
        )
    # Index file too — the migration should skip it.
    await backend.store_json(
        f"agents/{agent_id}/memory/index.json",
        {"agent_id": agent_id, "memory_ids": [e["memory_id"] for e in entries]},
    )


@pytest.mark.asyncio
async def test_migrates_basic_entries(backend, facts_store):
    await _seed_legacy(backend, "agent-1", [
        {
            "memory_id": "m1",
            "content": "User likes sushi",
            "metadata": {"category": "preference", "importance": 4},
            "access_count": 7,
            "last_accessed": 1700000000.0,
        },
        {
            "memory_id": "m2",
            "content": "User lives in Tokyo",
            "metadata": {"category": "fact", "importance": 5},
            "access_count": 0,
            "last_accessed": 0.0,
        },
    ])
    n = await migrate(backend, "agent-1", facts_store)
    assert n == 2
    facts = facts_store.all()
    assert len(facts) == 2
    by_content = {f.content: f for f in facts}
    assert by_content["User likes sushi"].category == "preference"
    assert by_content["User likes sushi"].importance == 4
    assert by_content["User likes sushi"].access_count == 7
    assert by_content["User lives in Tokyo"].category == "fact"
    # legacy_memory_id is preserved for audit
    assert by_content["User likes sushi"].extra["legacy_memory_id"] == "m1"


@pytest.mark.asyncio
async def test_migration_is_idempotent(backend, facts_store):
    await _seed_legacy(backend, "agent-1", [
        {"memory_id": "m1", "content": "x",
         "metadata": {"category": "fact", "importance": 3}},
    ])
    n1 = await migrate(backend, "agent-1", facts_store)
    n2 = await migrate(backend, "agent-1", facts_store)
    assert n1 == 1
    assert n2 == 0  # flag file blocks re-migration
    assert facts_store.count() == 1


@pytest.mark.asyncio
async def test_migration_force_re_runs(backend, facts_store):
    await _seed_legacy(backend, "agent-1", [
        {"memory_id": "m1", "content": "x",
         "metadata": {"category": "fact", "importance": 3}},
    ])
    await migrate(backend, "agent-1", facts_store)
    n2 = await migrate(backend, "agent-1", facts_store, force=True)
    assert n2 == 1


@pytest.mark.asyncio
async def test_migration_maps_skill_to_context(backend, facts_store):
    """Phase D 续 decision: skill-tagged extractions land in context
    rather than being dropped."""
    await _seed_legacy(backend, "agent-1", [
        {"memory_id": "m1", "content": "User wants to learn Rust",
         "metadata": {"category": "skill", "importance": 3}},
        {"memory_id": "m2", "content": "User has Global Entry",
         "metadata": {"category": "fact", "importance": 4}},
    ])
    await migrate(backend, "agent-1", facts_store)
    by_content = {f.content: f for f in facts_store.all()}
    assert by_content["User wants to learn Rust"].category == "context"
    assert by_content["User wants to learn Rust"].extra["original_category"] == "skill"


@pytest.mark.asyncio
async def test_migration_skips_index_file(backend, facts_store):
    """Index file under the memory prefix shouldn't become a Fact."""
    await _seed_legacy(backend, "agent-1", [
        {"memory_id": "m1", "content": "real content",
         "metadata": {"category": "fact", "importance": 3}},
    ])
    n = await migrate(backend, "agent-1", facts_store)
    assert n == 1  # only the real entry, not index.json


@pytest.mark.asyncio
async def test_migration_handles_no_data(backend, facts_store):
    """Fresh agent with no legacy entries → 0 migrated, no crash."""
    n = await migrate(backend, "fresh-agent", facts_store)
    assert n == 0


@pytest.mark.asyncio
async def test_migration_skips_malformed(backend, facts_store):
    """Entries missing ``content`` are skipped, not migrated as
    empty Facts."""
    await _seed_legacy(backend, "agent-1", [
        {"memory_id": "m1", "content": "good",
         "metadata": {"category": "fact", "importance": 3}},
        {"memory_id": "m2", "content": "",
         "metadata": {"category": "fact", "importance": 3}},
    ])
    n = await migrate(backend, "agent-1", facts_store)
    assert n == 1
    assert {f.content for f in facts_store.all()} == {"good"}


@pytest.mark.asyncio
async def test_migration_clamps_importance(backend, facts_store):
    """LLM-corrupted importance values clamp to [1, 5]."""
    await _seed_legacy(backend, "agent-1", [
        {"memory_id": "m1", "content": "low",
         "metadata": {"category": "fact", "importance": 0}},
        {"memory_id": "m2", "content": "high",
         "metadata": {"category": "fact", "importance": 9}},
    ])
    await migrate(backend, "agent-1", facts_store)
    by_content = {f.content: f for f in facts_store.all()}
    assert by_content["low"].importance == 1
    assert by_content["high"].importance == 5
