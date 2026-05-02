"""Tests for ``nexus_core.memory.episodes`` — Phase J episodic
memory namespace.

The contract pinned here:

1. Working state mutations (upsert / remove) are cheap and don't
   bump version.
2. ``commit()`` snapshots working → new immutable version.
3. ``rollback()`` restores both pointer AND working file.
4. ``upsert`` replaces by session_id (latest summary wins).
5. Reads (`all`/`recent`/`search`/`get_by_session`) work off the
   working state.
6. Schema is forward-compatible — unknown fields go into ``extra``.
7. Audit query: ``get_version()`` reads any historical snapshot.
"""

from __future__ import annotations

import json
import time

import pytest

import nexus_core
from nexus_core.memory import Episode, EpisodesStore


# ── Episode dataclass ────────────────────────────────────────────────


def test_episode_to_dict_roundtrip():
    ep = Episode(
        session_id="s1",
        started_at=1730000000.0,
        ended_at=1730003600.0,
        summary="Tokyo restaurant chat",
        topics=["food", "japan"],
        key_event_ids=[42, 47],
        outcome="success",
        mood="engaged",
    )
    d = ep.to_dict()
    ep2 = Episode.from_dict(d)
    assert ep2.session_id == "s1"
    assert ep2.summary == "Tokyo restaurant chat"
    assert ep2.outcome == "success"


def test_episode_from_dict_preserves_unknown_fields_in_extra():
    """Forward-compat: a future schema bumps add fields, current
    impl swallows them into ``extra`` rather than crashing."""
    ep = Episode.from_dict({
        "session_id": "s1",
        "future_field": "future_value",
        "another": 42,
    })
    assert ep.session_id == "s1"
    assert ep.extra == {"future_field": "future_value", "another": 42}


def test_episode_is_active_when_no_ended_at():
    assert Episode(session_id="x", started_at=1.0).is_active()
    assert not Episode(session_id="x", started_at=1.0, ended_at=2.0).is_active()


# ── EpisodesStore — fresh + upsert ──────────────────────────────────


def test_fresh_store_is_empty(tmp_path):
    s = EpisodesStore(tmp_path)
    assert s.all() == []
    assert len(s) == 0
    assert s.current_version() is None


def test_upsert_adds_new_episode(tmp_path):
    s = EpisodesStore(tmp_path)
    ep = Episode(session_id="s1", started_at=1.0, summary="hi")
    s.upsert(ep)
    assert len(s) == 1
    assert s.get_by_session("s1").summary == "hi"


def test_upsert_replaces_existing_session(tmp_path):
    """Critical: re-upserting same session_id replaces, doesn't
    append. Sessions extend over time and get re-summarised; we
    don't want N copies of the same session."""
    s = EpisodesStore(tmp_path)
    s.upsert(Episode(session_id="s1", started_at=1.0, summary="initial"))
    s.upsert(Episode(session_id="s1", started_at=1.0, summary="final", outcome="success"))
    assert len(s) == 1
    assert s.get_by_session("s1").summary == "final"
    assert s.get_by_session("s1").outcome == "success"


def test_remove_deletes_from_working(tmp_path):
    s = EpisodesStore(tmp_path)
    s.upsert(Episode(session_id="s1", started_at=1.0))
    s.upsert(Episode(session_id="s2", started_at=2.0))
    assert s.remove("s1") is True
    assert len(s) == 1
    assert s.get_by_session("s1") is None
    assert s.get_by_session("s2") is not None


def test_remove_returns_false_when_not_found(tmp_path):
    s = EpisodesStore(tmp_path)
    assert s.remove("nonexistent") is False


# ── Working state writes don't bump version ─────────────────────────


def test_upsert_does_not_create_version(tmp_path):
    """Cheap appends: 100 upserts and we still have zero versions
    until commit."""
    s = EpisodesStore(tmp_path)
    for i in range(50):
        s.upsert(Episode(session_id=f"s{i}", started_at=float(i)))
    assert s.current_version() is None
    assert len(s.history()) == 0
    assert len(s) == 50


# ── commit + rollback flow ──────────────────────────────────────────


def test_commit_snapshots_working_to_v0001(tmp_path):
    s = EpisodesStore(tmp_path)
    s.upsert(Episode(session_id="s1", started_at=1.0))
    label = s.commit()
    assert label == "v0001"
    assert s.current_version() == "v0001"


def test_rollback_restores_working_state(tmp_path):
    """After rollback, reads see the rolled-back state."""
    s = EpisodesStore(tmp_path)

    s.upsert(Episode(session_id="s1", started_at=1.0, summary="v1 only"))
    s.commit()                          # v0001

    s.upsert(Episode(session_id="s2", started_at=2.0, summary="added"))
    s.upsert(Episode(session_id="s1", started_at=1.0, summary="v1 modified"))
    s.commit()                          # v0002 — has s1 modified + s2

    assert len(s) == 2
    assert s.get_by_session("s1").summary == "v1 modified"

    s.rollback("v0001")
    assert len(s) == 1                  # s2 disappeared
    assert s.get_by_session("s1").summary == "v1 only"
    assert s.get_by_session("s2") is None


def test_rollback_to_empty_initial_clears_working(tmp_path):
    """If the rollback target was the empty state, working file
    should be cleared too — not retain stale entries."""
    s = EpisodesStore(tmp_path)
    s.upsert(Episode(session_id="s1", started_at=1.0))
    initial_label = s.commit()
    s.upsert(Episode(session_id="s2", started_at=2.0))
    s.commit()
    s.upsert(Episode(session_id="s3", started_at=3.0))

    assert len(s) == 3
    s.rollback(initial_label)
    assert len(s) == 1
    assert s.get_by_session("s1") is not None
    assert s.get_by_session("s2") is None
    assert s.get_by_session("s3") is None


# ── Reads ───────────────────────────────────────────────────────────


def test_recent_orders_by_started_at_desc(tmp_path):
    s = EpisodesStore(tmp_path)
    s.upsert(Episode(session_id="old", started_at=1.0))
    s.upsert(Episode(session_id="middle", started_at=2.0))
    s.upsert(Episode(session_id="newest", started_at=3.0))

    recent = s.recent(limit=2)
    assert [e.session_id for e in recent] == ["newest", "middle"]


def test_search_finds_substring_in_summary_topics_mood(tmp_path):
    s = EpisodesStore(tmp_path)
    s.upsert(Episode(
        session_id="s1", started_at=1.0,
        summary="Tokyo restaurant recommendations",
        topics=["travel"],
    ))
    s.upsert(Episode(
        session_id="s2", started_at=2.0,
        summary="Sympy bug debugging",
        topics=["code"],
    ))
    s.upsert(Episode(
        session_id="s3", started_at=3.0,
        summary="general chat", topics=["small_talk"],
        mood="frustrated",
    ))

    # Match in summary
    hits = s.search("Tokyo")
    assert {e.session_id for e in hits} == {"s1"}

    # Match in topics
    hits = s.search("code")
    assert {e.session_id for e in hits} == {"s2"}

    # Match in mood
    hits = s.search("frustrated")
    assert {e.session_id for e in hits} == {"s3"}

    # No match
    assert s.search("nonexistent") == []
    # Empty query → no results (avoid matching everything)
    assert s.search("") == []


# ── Audit / history ─────────────────────────────────────────────────


def test_history_summarises_all_committed_versions(tmp_path):
    s = EpisodesStore(tmp_path)
    s.upsert(Episode(session_id="s1", started_at=1.0))
    s.commit()
    s.upsert(Episode(session_id="s2", started_at=2.0))
    s.commit()
    s.upsert(Episode(session_id="s3", started_at=3.0))
    s.commit()

    h = s.history()
    assert len(h) == 3
    assert [e["version"] for e in h] == ["v0001", "v0002", "v0003"]
    assert [e["episode_count"] for e in h] == [1, 2, 3]


def test_get_version_reads_historical_snapshot(tmp_path):
    """Audit: 'what did the agent know at v0002?' query."""
    s = EpisodesStore(tmp_path)

    s.upsert(Episode(session_id="s1", started_at=1.0, summary="v1"))
    s.commit()                          # v0001
    s.upsert(Episode(session_id="s2", started_at=2.0))
    s.commit()                          # v0002
    s.upsert(Episode(session_id="s3", started_at=3.0))
    s.commit()                          # v0003

    eps_at_v2 = s.get_version("v0002")
    assert eps_at_v2 is not None
    assert {e.session_id for e in eps_at_v2} == {"s1", "s2"}

    assert s.get_version("v9999") is None


# ── Persistence across instances ────────────────────────────────────


def test_state_persists_across_instances(tmp_path):
    """A new EpisodesStore opened on the same directory sees the
    same working state and version chain."""
    s1 = EpisodesStore(tmp_path)
    s1.upsert(Episode(session_id="s1", started_at=1.0, summary="foo"))
    s1.commit()
    s1.upsert(Episode(session_id="s2", started_at=2.0))

    s2 = EpisodesStore(tmp_path)
    assert len(s2) == 2
    assert s2.current_version() == "v0001"
    assert s2.get_by_session("s2") is not None


def test_bootstrap_seeds_working_from_committed_when_only_versions_exist(tmp_path):
    """Edge case: working file gone but committed versions exist
    (e.g. cache wipe). Open should restore from current version."""
    s = EpisodesStore(tmp_path)
    s.upsert(Episode(session_id="s1", started_at=1.0))
    s.commit()

    # Simulate working file loss
    (tmp_path / "episodes" / "_working.json").unlink()

    s2 = EpisodesStore(tmp_path)
    assert len(s2) == 1
    assert s2.get_by_session("s1") is not None


# ── Schema stamping ─────────────────────────────────────────────────


def test_working_file_carries_schema_string(tmp_path):
    """An external auditor reading {tmp}/episodes/_working.json
    should see the schema field — pin it to BEP-Nexus."""
    s = EpisodesStore(tmp_path)
    s.upsert(Episode(session_id="s1", started_at=1.0))

    raw = json.loads((tmp_path / "episodes" / "_working.json").read_text())
    assert raw["schema"] == "nexus.memory.episodes.v1"
    assert "episodes" in raw


# ── Public API surface ──────────────────────────────────────────────


def test_top_level_exports():
    assert nexus_core.Episode is Episode
    assert nexus_core.EpisodesStore is EpisodesStore
