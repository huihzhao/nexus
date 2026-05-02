"""Conformance tests for ``nexus_core.anchor`` against BEP-Nexus §3
test vectors.

These tests pin the byte-level canonical form + the SHA-256 ``state_root``
hashes published in ``docs/BEP-nexus.md``. Any change that would alter
either is a hard break of the on-chain contract — fail loudly here so a
downstream impl never silently diverges.
"""

from __future__ import annotations

import nexus_core
from nexus_core.anchor import (
    SCHEMA_V1,
    ZERO_DIGEST_HEX,
    AnchorBatch,
    build_anchor_batch,
    canonicalize,
)


# ── BEP-Nexus §"Test Vectors" — Vector 1 ─────────────────────────────


def test_vector_1_empty_manifest_canonical_bytes_match_bep():
    """The canonical form of the empty manifest is byte-stable and
    matches the form pinned in BEP-Nexus §"Test vectors" — Vector 1.
    """
    batch = build_anchor_batch(
        user_id="00000000-0000-0000-0000-000000000000",
        events=[],
        prev_root=ZERO_DIGEST_HEX,
    )
    expected = (
        b'{"events":[],'
        b'"prev_root":"0x0000000000000000000000000000000000000000'
        b'000000000000000000000000",'
        b'"schema":"nexus.sync.batch.v1",'
        b'"sync_ids":[],'
        b'"user_id":"00000000-0000-0000-0000-000000000000"}'
    )
    assert batch.canonicalize() == expected


def test_vector_1_empty_manifest_state_root_matches_bep():
    """Vector 1's pinned SHA-256."""
    batch = build_anchor_batch(
        user_id="00000000-0000-0000-0000-000000000000",
        events=[],
        prev_root=ZERO_DIGEST_HEX,
    )
    assert batch.state_root_hex(prefix=False) == (
        "6b4346d5ddc5e95f816e45ff699289d27e30142f57262e4e3052670055d1957f"
    )
    # Solidity-friendly form
    assert batch.state_root_hex().startswith("0x")
    assert len(batch.state_root_bytes()) == 32


# ── Vector 2 — single user_message event ─────────────────────────────


def test_vector_2_single_user_message_state_root_matches_bep():
    """Vector 2's pinned SHA-256."""
    batch = build_anchor_batch(
        user_id="00000000-0000-0000-0000-000000000001",
        events=[
            {
                "client_created_at": "2026-04-28T00:00:00Z",
                "event_type": "user_message",
                "content": "hello",
                "metadata": {},
                "session_id": "session_20260428",
                "sync_id": 1,
                "server_received_at": "2026-04-28T00:00:01Z",
            },
        ],
        prev_root=ZERO_DIGEST_HEX,
    )
    assert batch.state_root_hex(prefix=False) == (
        "96d596adb771ffa3d019ce6fb741b58041db2d98eab66c6397afa2ff52e9a1e2"
    )


# ── Vector 6 — manifest with evolution_proposal ──────────────────────


def test_vector_6_evolution_proposal_state_root_matches_bep():
    """Vector 6's pinned SHA-256. Confirms our canonical form
    handles the `evolution_proposal` event shape from BEP §3.4."""
    batch = build_anchor_batch(
        user_id="00000000-0000-0000-0000-000000000002",
        events=[
            {
                "client_created_at": "2026-04-28T12:34:56Z",
                "event_type": "evolution_proposal",
                "metadata": {
                    "edit_id": "evo-2026-04-28-001-abc",
                    "evolver": "MemoryEvolver",
                    "target_namespace": "memory.facts",
                    "target_version_pre": "memory/facts/v0041.json",
                    "target_version_post": "memory/facts/v0042.json",
                    "evidence_event_ids": [123, 145, 167],
                    "change_summary": "Added fact: user has peanut allergy",
                    "predicted_fixes": [
                        {"task_kind": "restaurant_recommendation",
                         "reason": "avoid peanut dishes"},
                    ],
                    "predicted_regressions": [],
                    "rollback_pointer": "memory/facts/v0041.json",
                    "expires_after_events": 100,
                },
                "sync_id": 4501,
                "session_id": "session_20260428",
                "server_received_at": "2026-04-28T12:34:57Z",
            },
        ],
        prev_root=ZERO_DIGEST_HEX,
    )
    assert batch.state_root_hex(prefix=False) == (
        "4b3ff7c1e69bd1665afd6db45a72b9fcedd7455515c27dc580815ff5d63216b2"
    )


# ── Vector 7 — manifest with evolution_verdict (kept_with_warning) ───


def test_vector_7_evolution_verdict_state_root_matches_bep():
    """Vector 7's pinned SHA-256. Confirms canonical form for
    `evolution_verdict` events including all sub-arrays from
    BEP §3.4."""
    batch = build_anchor_batch(
        user_id="00000000-0000-0000-0000-000000000002",
        events=[
            {
                "client_created_at": "2026-04-28T18:00:00Z",
                "event_type": "evolution_verdict",
                "metadata": {
                    "edit_id": "evo-2026-04-28-001-abc",
                    "verdict_at_event": 4837,
                    "events_observed": 200,
                    "predicted_fix_match": [
                        {"task_kind": "restaurant_recommendation",
                         "observed_count": 2, "outcome": "fixed"},
                    ],
                    "predicted_fix_miss": [],
                    "predicted_regression_match": [],
                    "predicted_regression_miss": [],
                    "unpredicted_regressions": [
                        {"task_kind": "small_talk",
                         "observed_count": 1,
                         "severity": "low",
                         "evidence": "over-mentioned"},
                    ],
                    "fix_score": 1.0,
                    "regression_score": 0.2,
                    "abc_drift_delta": 0.05,
                    "decision": "kept_with_warning",
                },
                "sync_id": 4838,
                "session_id": "session_20260428",
                "server_received_at": "2026-04-28T18:00:01Z",
            },
        ],
        prev_root=ZERO_DIGEST_HEX,
    )
    assert batch.state_root_hex(prefix=False) == (
        "3fdd568de9f98f0c5d6b5ad9335bfa1cd1162fc00a7e2a8279171f1e83900601"
    )


# ── sync_ids derivation ──────────────────────────────────────────────


def test_build_anchor_batch_extracts_sync_ids_from_events():
    """``sync_ids`` is auto-derived from each event's ``sync_id`` field
    (events without one are silently skipped from the index)."""
    batch = build_anchor_batch(
        user_id="u",
        events=[
            {"event_type": "user_message", "sync_id": 1},
            {"event_type": "tool_call"},  # no sync_id — skipped from index
            {"event_type": "assistant_response", "sync_id": 2},
        ],
    )
    assert batch.sync_ids == [1, 2]
    # All three events are still present for replay
    assert len(batch.events) == 3


# ── prev_root chain ──────────────────────────────────────────────────


def test_prev_root_chains_alter_state_root_even_with_same_events():
    """Two batches with identical events but different ``prev_root``
    MUST produce different ``state_root`` — that's the whole point of
    the hash chain."""
    events = [{"event_type": "user_message", "sync_id": 1}]
    a = build_anchor_batch(user_id="u", events=events, prev_root=ZERO_DIGEST_HEX)
    b = build_anchor_batch(
        user_id="u",
        events=events,
        prev_root="0x" + "ab" * 32,
    )
    assert a.state_root_hex() != b.state_root_hex()


# ── Schema constants ─────────────────────────────────────────────────


def test_schema_constant_pins_v1():
    """The schema string is part of the canonical form — pin it
    so a typo doesn't silently change every state_root in the world."""
    assert SCHEMA_V1 == "nexus.sync.batch.v1"
    batch = build_anchor_batch(user_id="u", events=[])
    assert batch.schema == "nexus.sync.batch.v1"


# ── Public API surface ───────────────────────────────────────────────


def test_top_level_imports_are_exposed_on_nexus_core():
    """The anchor builder is part of the SDK's public surface so
    server / nexus runtime callers don't have to reach into a deep
    submodule path."""
    assert nexus_core.AnchorBatch is AnchorBatch
    assert nexus_core.build_anchor_batch is build_anchor_batch
    assert nexus_core.ANCHOR_SCHEMA_V1 == "nexus.sync.batch.v1"


# ── Determinism (the contract DPM relies on) ─────────────────────────


def test_canonicalize_is_idempotent():
    """Canonicalising twice MUST produce identical bytes — i.e. the
    transformation is a pure function of the logical document."""
    obj = {"b": 2, "a": [1, 2, 3], "c": {"y": 1, "x": 2}}
    once = canonicalize(obj)
    twice = canonicalize(obj)
    assert once == twice


def test_canonicalize_sorts_nested_keys():
    """JCS requires recursive key sorting; our impl gets this via
    ``json.dumps(sort_keys=True)``."""
    obj = {"z": {"b": 1, "a": 2}, "a": 1}
    expected = b'{"a":1,"z":{"a":2,"b":1}}'
    assert canonicalize(obj) == expected


def test_state_root_bytes_and_hex_are_consistent():
    """``state_root_bytes`` is just the raw SHA-256; the hex form
    must round-trip via fromhex."""
    batch = build_anchor_batch(user_id="u", events=[])
    raw = batch.state_root_bytes()
    hex_no_prefix = batch.state_root_hex(prefix=False)
    assert bytes.fromhex(hex_no_prefix) == raw
    assert batch.state_root_hex().startswith("0x")
    assert len(raw) == 32


# ── Merkle root (optional, only when keccak available) ───────────────


def test_merkle_root_empty_returns_zero_digest():
    """No chunks → zero digest (the "no Merkle proof committed"
    sentinel per BEP §3.2)."""
    batch = build_anchor_batch(user_id="u", events=[])
    assert batch.merkle_root_hex(chunk_hashes=None) == "0x" + "00" * 32
    assert batch.merkle_root_hex(chunk_hashes=[]) == "0x" + "00" * 32
