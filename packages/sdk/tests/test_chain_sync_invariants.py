"""Local ↔ chain sync invariants + data-loss recovery.

These tests validate the contract that the chain backend must keep:

  Invariant 1: every successful caller-visible write is durably
  recorded somewhere — either persisted to Greenfield, or pinned in
  the WAL for replay on the next process start.

  Invariant 2: WAL replay is **lossless and idempotent** — events
  cancelled by a crashed shutdown re-fire cleanly the next time.

  Invariant 3: the content hash of a payload is path-independent
  and write-path-independent — the same bytes always produce the
  same SHA-256 (so anchor manifests are reproducible by third
  parties).

  Invariant 4: reads-your-writes — once ``store_json`` returns,
  ``load_json`` for that path returns the same data without
  needing Greenfield to be reachable.

  Invariant 5: shutdown after a Greenfield outage preserves the
  un-flushed writes in the WAL for the next start to retry. The
  shutdown path itself MUST NOT silently drop them.

  Invariant 6: state-root for a manifest is reproducible — a
  third party with the canonical bytes can recompute the same
  hash that was anchored on BSC.

Each test instantiates a real ``ChainBackend`` and swaps in a
``StubGreenfield`` so we can fail / succeed puts deterministically
without needing a live Greenfield SP. The BSC client side stays
``None`` (chain anchoring is a separate concern, exercised by
``test_anchor.py``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from typing import Optional

import pytest

from nexus_core.backends.chain import ChainBackend
from nexus_core.core.flush import WriteAheadLog


# ── Stub Greenfield ──────────────────────────────────────────────────


class StubGreenfield:
    """Pluggable Greenfield client double.

    Tracks every put that has been issued, lets the test choose
    which ones succeed and which fail (raising / returning False).
    Exposes the put history so tests can assert on ordering +
    re-issue counts.
    """

    def __init__(self):
        self.puts: list[tuple[str, bytes, str]] = []  # (object_path, data, hash)
        self.fail_paths: set[str] = set()
        self.fail_all = False
        self.objects: dict[str, bytes] = {}  # successful gets read from here

    async def put(self, data: bytes, object_path: Optional[str] = None) -> str:
        chash = hashlib.sha256(data).hexdigest()
        self.puts.append((object_path or "", bytes(data), chash))
        if self.fail_all or (object_path and object_path in self.fail_paths):
            raise RuntimeError(f"simulated SP failure for {object_path}")
        if object_path:
            self.objects[object_path] = bytes(data)
        return chash

    async def get(self, content_hash: str, object_path: Optional[str] = None) -> Optional[bytes]:
        if object_path and object_path in self.objects:
            return self.objects[object_path]
        return None

    async def close(self):
        pass


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def chain_dir(tmp_path, monkeypatch):
    """Per-test cache + WAL directory. Pinned via NEXUS_CACHE_DIR so
    ChainBackend's __init__ honours it."""
    cache = tmp_path / "cache"
    monkeypatch.setenv("NEXUS_CACHE_DIR", str(cache))
    return cache


@pytest.fixture
def backend(chain_dir, monkeypatch):
    """A real ChainBackend with the Greenfield client swapped for a stub.

    The bucket name is required; the stub doesn't actually use it
    but the class refuses to construct without one.
    """
    # Avoid the constructor reaching out to BSC — leave RPC / contract
    # addresses unset so _chain_client stays None.
    for var in (
        "NEXUS_TESTNET_RPC", "NEXUS_BSC_RPC",
        "NEXUS_TESTNET_AGENT_STATE_ADDRESS", "NEXUS_AGENT_STATE_ADDRESS",
    ):
        monkeypatch.delenv(var, raising=False)

    be = ChainBackend(
        private_key="0x" + "a" * 64,        # dummy — never used because BSC client is None
        network="testnet",
        greenfield_bucket="nexus-agent-test",
    )
    stub = StubGreenfield()
    be._greenfield = stub
    return be


# ── Invariant 1 — successful write goes durable through cache + WAL ──


@pytest.mark.asyncio
async def test_store_json_writes_local_cache_synchronously(backend, chain_dir):
    """``store_json`` must populate the local cache before returning,
    so a crash *immediately* after the call still preserves the data.
    Cache lookup is the only path that doesn't depend on Greenfield."""
    chash = await backend.store_json("memory/index.json", {"a": 1, "b": "two"})
    assert chash, "content hash should be returned"

    # Drain background writes so the test isn't racey.
    await asyncio.gather(*list(backend._pending_tasks), return_exceptions=True)

    # Cache file exists with the canonical JSON bytes.
    cache_path = backend._cache_path("memory/index.json")
    assert cache_path.exists()
    raw = cache_path.read_bytes()
    assert json.loads(raw.decode()) == {"a": 1, "b": "two"}


@pytest.mark.asyncio
async def test_wal_records_every_write(backend, chain_dir):
    """Every store_json appends a WAL entry BEFORE firing the
    write-behind. WAL is the safety net if the process dies before
    the Greenfield put completes.

    Phase Q audit fix #2: on PUT success the entry is removed from
    the WAL so it stays bounded during long sessions. This test
    therefore checks the pre-completion shape (WAL has both entries
    with hash+size+body) and the post-success shape (WAL empties as
    PUTs land), both of which are part of the new contract.
    """
    await backend.store_json("memory/a.json", {"v": 1})
    await backend.store_json("memory/b.json", {"v": 2})

    # Pre-completion: both entries are recorded with hash + size
    # (and the new inline body for small writes — audit fix #3).
    entries_pre = backend._wal.read_all()
    paths_pre = sorted(e["path"] for e in entries_pre)
    assert paths_pre == ["memory/a.json", "memory/b.json"]
    for e in entries_pre:
        assert e.get("hash"), "WAL entry must carry content hash"
        assert e.get("size", 0) > 0
        # Audit fix #3: small writes embed body so cache eviction
        # can't break replay.
        assert e.get("body_b64"), "small writes must inline body for replay safety"

    # Drain the in-flight PUTs, then verify successful writes have
    # been removed from the WAL — keeping it bounded.
    await asyncio.gather(*list(backend._pending_tasks), return_exceptions=True)
    paths_post = {e["path"] for e in backend._wal.read_all()}
    assert paths_post == set(), (
        "Successful Greenfield PUTs must be removed from WAL on completion. "
        f"Still in WAL: {paths_post}"
    )


# ── Invariant 2 — WAL replay is lossless + idempotent ────────────────


@pytest.mark.asyncio
async def test_wal_replay_re_fires_pending_writes_on_new_instance(chain_dir, monkeypatch):
    """Crash recovery: after killing a backend mid-write, a fresh
    ChainBackend pointed at the same cache dir reads the WAL and
    re-issues the puts."""
    for var in ("NEXUS_TESTNET_RPC", "NEXUS_BSC_RPC"):
        monkeypatch.delenv(var, raising=False)

    # Round 1: write something but never let the put complete.
    be1 = ChainBackend(
        private_key="0x" + "a" * 64, network="testnet",
        greenfield_bucket="nexus-agent-test",
    )
    stub1 = StubGreenfield()
    stub1.fail_all = True
    be1._greenfield = stub1
    await be1.store_json("session/checkpoint.json", {"turn": 7})
    await asyncio.gather(*list(be1._pending_tasks), return_exceptions=True)

    # WAL still holds the entry, cache file is on disk, but the SP
    # never accepted the write (stub raised). Simulate crash by just
    # dropping the reference — don't call close().
    wal_after_crash = WriteAheadLog(
        str(chain_dir / "_wal"), agent_id="chain",
    ).read_all()
    assert any(e["path"] == "session/checkpoint.json" for e in wal_after_crash)

    # Round 2: spin up a fresh backend, this time with a healthy stub.
    be2 = ChainBackend(
        private_key="0x" + "a" * 64, network="testnet",
        greenfield_bucket="nexus-agent-test",
    )
    stub2 = StubGreenfield()
    be2._greenfield = stub2

    replayed = await be2.replay_wal()
    await asyncio.gather(*list(be2._pending_tasks), return_exceptions=True)

    assert replayed >= 1
    # The replay did fire a put for the original path against the
    # second-round (healthy) Greenfield.
    assert any(p == "session/checkpoint.json" for (p, _, _) in stub2.puts)
    # WAL is cleared after replay so the next start doesn't repeat.
    assert be2._wal.read_all() == []


@pytest.mark.asyncio
async def test_wal_replay_is_idempotent(backend):
    """Calling replay_wal twice on the same instance is a no-op the
    second time — must not duplicate writes or re-read the (now
    truncated) WAL file."""
    await backend.store_json("memory/x.json", {"v": 1})
    await asyncio.gather(*list(backend._pending_tasks), return_exceptions=True)
    backend._wal_replay_done = False  # force first replay path
    n1 = await backend.replay_wal()
    n2 = await backend.replay_wal()
    assert n2 == 0, "second replay must not re-issue any writes"


# ── Invariant 3 — content hash is deterministic ──────────────────────


@pytest.mark.asyncio
async def test_content_hash_is_deterministic_across_paths(backend):
    """Same JSON payload at two different paths → same content_hash.
    The hash commits to the bytes, not the path; this is what makes
    cross-bucket dedup + manifest verifiability work."""
    h1 = await backend.store_json("memory/a.json", {"k": [1, 2, 3]})
    h2 = await backend.store_json("memory/b.json", {"k": [1, 2, 3]})
    await asyncio.gather(*list(backend._pending_tasks), return_exceptions=True)
    assert h1 == h2 == hashlib.sha256(
        backend.json_bytes({"k": [1, 2, 3]})
    ).hexdigest()


@pytest.mark.asyncio
async def test_content_hash_unaffected_by_put_failure(backend):
    """Greenfield outage during the write must NOT corrupt the
    returned content hash — the hash is computed locally from the
    bytes, before any network round-trip."""
    backend._greenfield.fail_all = True
    h = await backend.store_json("memory/a.json", {"k": "v"})
    await asyncio.gather(*list(backend._pending_tasks), return_exceptions=True)
    assert h == hashlib.sha256(
        backend.json_bytes({"k": "v"})
    ).hexdigest()


# ── Invariant 4 — read-your-writes via local cache ───────────────────


@pytest.mark.asyncio
async def test_load_json_returns_just_written_value_without_greenfield(backend):
    """``load_json`` must serve from local cache when the path was
    written this session — even if Greenfield is currently unreachable."""
    backend._greenfield.fail_all = True  # simulate full SP outage
    await backend.store_json("artifacts/persona.json", {"version": 3})
    await asyncio.gather(*list(backend._pending_tasks), return_exceptions=True)

    # All puts failed — but cache holds the data, so reads succeed.
    got = await backend.load_json("artifacts/persona.json")
    assert got == {"version": 3}


@pytest.mark.asyncio
async def test_load_json_unknown_path_negative_caches(backend):
    """A miss on an unknown path is recorded in the negative cache
    so subsequent loads don't retry Greenfield within the TTL.
    Required for cold-start performance and for correctness of the
    'has this agent ever written x?' check."""
    got = await backend.load_json("memory/never-written.json")
    assert got is None
    # Negative cache hit on second call.
    assert backend._neg_cache_hit("memory/never-written.json")


@pytest.mark.asyncio
async def test_load_json_returns_none_when_outage_and_no_cache(backend):
    """Cache miss + Greenfield outage → ``None``, not an exception.

    Calling code (twin._initialize, evolution loaders, etc.) treats
    None as 'not yet exists'. If we let the underlying SP error
    propagate, every cold-start read after an outage would crash the
    agent during boot."""
    backend._greenfield.fail_all = True
    got = await backend.load_json("memory/never-existed.json")
    assert got is None  # graceful, no exception
    assert backend._neg_cache_hit("memory/never-existed.json")


@pytest.mark.asyncio
async def test_load_json_falls_back_to_greenfield_on_cache_miss(backend):
    """When the local cache is cold (e.g. fresh process, prior data
    only on Greenfield), reads fall through to the SP and re-populate
    the cache. This is the cold-start happy path."""
    # Seed Greenfield directly (simulates a write from a prior process
    # whose cache has since been cleared / not yet populated).
    seeded_bytes = json.dumps({"hello": "from greenfield"}).encode()
    backend._greenfield.objects["memory/seeded.json"] = seeded_bytes

    # Local cache is cold for this path.
    assert not backend._cache_path("memory/seeded.json").exists()

    got = await backend.load_json("memory/seeded.json")
    assert got == {"hello": "from greenfield"}
    # Cache repopulated for next read.
    assert backend._cache_path("memory/seeded.json").exists()


@pytest.mark.asyncio
async def test_write_after_outage_clears_neg_cache(backend):
    """A path that was 'not found' during an outage must NOT stay
    poisoned in the negative cache once the agent writes to it. If
    we forgot to clear neg-cache on write, ``store_json`` followed
    by ``load_json`` would return None even though the data is right
    there in the local cache."""
    # Outage: read a non-existent path → neg-cache poisoned.
    backend._greenfield.fail_all = True
    miss = await backend.load_json("memory/about-to-be-written.json")
    assert miss is None
    assert backend._neg_cache_hit("memory/about-to-be-written.json")

    # Now the agent writes that path. The write-behind still fails
    # (Greenfield still down) but the local cache + neg-cache update
    # MUST happen synchronously so subsequent reads can serve.
    await backend.store_json(
        "memory/about-to-be-written.json", {"now": "exists"},
    )
    assert not backend._neg_cache_hit("memory/about-to-be-written.json")
    got = await backend.load_json("memory/about-to-be-written.json")
    assert got == {"now": "exists"}


@pytest.mark.asyncio
async def test_partial_outage_only_some_paths_fail(backend):
    """Mixed outage: SP rejects writes to one path but accepts
    others. The in-flight writes must each be evaluated independently
    — a single failing path shouldn't WAL-poison or block other
    successful writes."""
    backend._greenfield.fail_paths = {"memory/cursed.json"}

    h_ok = await backend.store_json("memory/healthy.json", {"v": 1})
    h_bad = await backend.store_json("memory/cursed.json", {"v": 2})
    await asyncio.gather(*list(backend._pending_tasks), return_exceptions=True)

    # Both calls returned valid hashes (they're computed locally before
    # the put even runs — the cursed path's stub failure can't corrupt
    # the contract).
    assert h_ok and h_bad and h_ok != h_bad

    # Both reads serve from local cache regardless of outage state.
    assert await backend.load_json("memory/healthy.json") == {"v": 1}
    assert await backend.load_json("memory/cursed.json") == {"v": 2}

    # Greenfield stub recorded both put attempts — only the healthy
    # one made it into the SP's object store.
    paths_attempted = {p for (p, _, _) in backend._greenfield.puts}
    assert paths_attempted == {"memory/healthy.json", "memory/cursed.json"}
    paths_landed = set(backend._greenfield.objects.keys())
    assert paths_landed == {"memory/healthy.json"}

    # WAL keeps the FAILED entry only (audit fix #2: successful PUTs
    # are removed from the WAL on completion to keep it bounded;
    # the cursed entry stays so a next-start replay re-fires it).
    wal_paths = {e["path"] for e in backend._wal.read_all()}
    assert wal_paths == {"memory/cursed.json"}, (
        f"Expected only the failed cursed.json to remain in WAL; got {wal_paths}"
    )

    # Audit fix #4: failure was recorded on the backend so sync_status
    # can surface it to the desktop.
    assert backend.write_failure_count >= 1
    last = backend.last_write_error
    assert last is not None and last.get("path") == "memory/cursed.json"


# ── Invariant 5 — shutdown preserves un-flushed writes ───────────────


@pytest.mark.asyncio
async def test_close_during_outage_keeps_pending_writes_in_wal(chain_dir, monkeypatch):
    """If Greenfield is down during shutdown, WAL must NOT be
    truncated — the next process start needs those entries to retry."""
    for var in ("NEXUS_TESTNET_RPC", "NEXUS_BSC_RPC"):
        monkeypatch.delenv(var, raising=False)
    be = ChainBackend(
        private_key="0x" + "a" * 64, network="testnet",
        greenfield_bucket="nexus-agent-test",
    )
    stub = StubGreenfield()

    # Hang every put forever — close() will hit grace_period timeout
    # and cancel them.
    async def slow_put(*a, **k):
        await asyncio.sleep(60)
    stub.put = slow_put
    be._greenfield = stub

    await be.store_json("memory/a.json", {"v": 1})
    await be.store_json("memory/b.json", {"v": 2})
    await be.close(grace_period=0.1)

    # Pending writes still in WAL for the next start to replay.
    leftover = WriteAheadLog(
        str(chain_dir / "_wal"), agent_id="chain",
    ).read_all()
    paths = sorted(e["path"] for e in leftover)
    assert paths == ["memory/a.json", "memory/b.json"]


@pytest.mark.asyncio
async def test_close_after_successful_writes_clears_wal(backend):
    """Happy path: every put completed within the grace window →
    WAL truncated on close → next start has nothing to replay.

    Note: ``close()`` is the canonical place that drains pending
    writes, so this test does NOT pre-drain — that reflects the
    real shutdown ordering. The truncate branch fires only when
    we entered close() with pending tasks AND all of them finished
    inside the grace window."""
    await backend.store_json("memory/a.json", {"v": 1})
    await backend.store_json("memory/b.json", {"v": 2})

    # Don't drain manually — let close() do the wait. Both puts
    # are against the (fast) stub so they complete well within the
    # 2s grace window.
    await backend.close(grace_period=2.0)
    assert backend._wal.read_all() == []


# ── Invariant 6 — third-party state-root reproducibility ─────────────


def test_state_root_is_reproducible_from_canonical_bytes():
    """Anyone with the canonical manifest bytes can recompute the
    same SHA-256 we anchor on BSC. This is the verifiability
    contract third parties rely on to audit an agent's growth."""
    from nexus_core.anchor import (
        build_anchor_batch, SCHEMA_V1, ZERO_DIGEST_HEX,
    )

    events = [
        {"client_created_at": "2026-05-01T12:00:00Z",
         "event_type": "user_message", "content": "hi",
         "session_id": "s1", "sync_id": 1,
         "server_received_at": "2026-05-01T12:00:00Z"},
        {"client_created_at": "2026-05-01T12:00:01Z",
         "event_type": "assistant_response", "content": "hello",
         "session_id": "s1", "sync_id": 2,
         "server_received_at": "2026-05-01T12:00:01Z"},
    ]
    batch_a = build_anchor_batch(
        user_id="user-abc",
        prev_root="0x" + "0" * 64,
        events=events,
    )
    bytes_a = batch_a.canonicalize()
    root_a = "0x" + hashlib.sha256(bytes_a).hexdigest()

    # Third party rebuild: same events, same prev root, same agent →
    # byte-for-byte identical canonical form, identical SHA-256.
    batch_b = build_anchor_batch(
        user_id="user-abc",
        prev_root="0x" + "0" * 64,
        events=list(events),
    )
    bytes_b = batch_b.canonicalize()
    root_b = "0x" + hashlib.sha256(bytes_b).hexdigest()

    assert bytes_a == bytes_b, "canonical encoding must be byte-stable"
    assert root_a == root_b, "state root must be deterministic"
    assert batch_a.schema == SCHEMA_V1
    # Empty manifest sanity: never collides with the zero digest.
    assert root_a.replace("0x", "") != ZERO_DIGEST_HEX


def test_state_root_changes_with_a_single_byte_diff():
    """Tampering: any change to even one event byte must produce a
    different state root. This is the property third-party auditors
    rely on to detect manipulation of the agent's history."""
    from nexus_core.anchor import (
        build_anchor_batch, canonicalize as canonicalize_manifest,
    )
    base = [
        {"client_created_at": "2026-05-01T12:00:00Z",
         "event_type": "user_message", "content": "hello world",
         "session_id": "s1", "sync_id": 1,
         "server_received_at": "2026-05-01T12:00:00Z"},
    ]
    tampered = [dict(base[0])]
    tampered[0]["content"] = "hello worle"  # one-byte change

    root_base = hashlib.sha256(
        build_anchor_batch(
            user_id="x", prev_root="0x" + "0" * 64, events=base,
        ).canonicalize()
    ).hexdigest()
    root_tampered = hashlib.sha256(
        build_anchor_batch(
            user_id="x", prev_root="0x" + "0" * 64, events=tampered,
        ).canonicalize()
    ).hexdigest()
    assert root_base != root_tampered


# ── Invariant 7 — content hash chain stops a forked history ──────────


def test_prev_state_root_chains_into_current_hash():
    """Each anchor commits to the previous state-root, so two forks
    that share an event prefix but differ on prev_root produce
    distinct current roots. Chain backend uses this to detect
    runtime hand-off / fork attempts."""
    from nexus_core.anchor import (
        build_anchor_batch, canonicalize as canonicalize_manifest,
    )
    events = [
        {"client_created_at": "2026-05-01T12:00:00Z",
         "event_type": "user_message", "content": "same event",
         "session_id": "s1", "sync_id": 1,
         "server_received_at": "2026-05-01T12:00:00Z"},
    ]
    root_a = hashlib.sha256(
        build_anchor_batch(
            user_id="x",
            prev_root="0x" + "1" * 64,
            events=events,
        ).canonicalize()
    ).hexdigest()
    root_b = hashlib.sha256(
        build_anchor_batch(
            user_id="x",
            prev_root="0x" + "2" * 64,
            events=events,
        ).canonicalize()
    ).hexdigest()
    assert root_a != root_b
