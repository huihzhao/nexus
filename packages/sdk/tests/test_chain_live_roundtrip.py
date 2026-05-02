"""End-to-end live round-trip against BNB testnet + Greenfield.

These tests are NOT mocked — they run a real ChainBackend against a
real BSC RPC + a real Greenfield SP. Their job is to catch the kind
of bug that no in-process stub can catch:

  * SP routing — the daemon must talk to the bucket's primary SP, not
    a random "first available" one. We caught this in production after
    seeing every put fall back to local with "No such bucket".
  * Bucket creation race — bootstrap must finish before first put.
  * State-root contract correctness — a third party with the bucket
    bytes can recompute exactly what's on chain.

**Skipped by default.** Every test in this module gates on a full
set of env vars (private key + RPC + contract addresses). CI without
those env vars sees the whole module skipped, so this is opt-in for
hands-on / staging validation, not a blocker for the regular fast
test suite.

To run locally::

    export NEXUS_PRIVATE_KEY=0x...
    export NEXUS_TESTNET_RPC=https://data-seed-prebsc-1-s1.binance.org:8545
    export NEXUS_TESTNET_AGENT_STATE_ADDRESS=0x...
    export NEXUS_TESTNET_BUCKET=nexus-agent-XXX     # an existing test bucket
    export NEXUS_TESTNET_AGENT_ID=XXX               # ERC-8004 tokenId for that bucket
    pytest packages/sdk/tests/test_chain_live_roundtrip.py -v

Tests clean up everything they write (object delete + WAL truncate).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
import uuid

import pytest


_REQUIRED_ENV = (
    "NEXUS_PRIVATE_KEY",
    "NEXUS_TESTNET_RPC",
    "NEXUS_TESTNET_AGENT_STATE_ADDRESS",
    "NEXUS_TESTNET_BUCKET",
    "NEXUS_TESTNET_AGENT_ID",
)


def _missing() -> list[str]:
    return [k for k in _REQUIRED_ENV if not os.environ.get(k)]


pytestmark = pytest.mark.skipif(
    bool(_missing()),
    reason=(
        "live BSC + Greenfield round-trip requires "
        + ", ".join(_REQUIRED_ENV)
        + " in env (set "
        + ", ".join(_missing())
        + ")"
    ),
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """Per-test cache + WAL directory so live tests don't collide
    with each other or with the developer's running server."""
    p = tmp_path / "live_cache"
    monkeypatch.setenv("NEXUS_CACHE_DIR", str(p))
    return p


@pytest.fixture
def backend(cache_dir):
    """Real ChainBackend pointed at testnet + the env-supplied bucket.
    No stub — every put genuinely tries Greenfield, every anchor
    genuinely lands on BSC."""
    from nexus_core.backends.chain import ChainBackend

    be = ChainBackend(
        private_key=os.environ["NEXUS_PRIVATE_KEY"],
        network="testnet",
        greenfield_bucket=os.environ["NEXUS_TESTNET_BUCKET"],
    )
    yield be
    # Best-effort teardown — if a test forgot to clean up, the WAL
    # at least lets next-start retry rather than orphaning data.
    try:
        asyncio.get_event_loop().run_until_complete(be.close(grace_period=15))
    except Exception:
        pass


# ── Round-trip ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_get_roundtrip_against_real_greenfield(backend):
    """The minimal sync test: write a small JSON to a unique path,
    read it back from Greenfield, then delete the object so the
    bucket is left clean."""
    path = f"_test/roundtrip_{uuid.uuid4().hex[:8]}.json"
    payload = {"hello": "greenfield", "ts": int(time.time())}

    chash = await backend.store_json(path, payload)
    assert chash, "store_json must return a content hash"

    # Wait up to 30s for the background put to finish so the next
    # read genuinely hits Greenfield and not the local cache.
    for _ in range(30):
        if not backend._pending_tasks:
            break
        await asyncio.sleep(1)

    # Force the load to bypass local cache by deleting the cache file
    # — that way load_json must reach Greenfield, proving the put
    # actually landed.
    cache_path = backend._cache_path(path)
    if cache_path.exists():
        cache_path.unlink()
    backend._neg_cache.pop(path, None)

    fetched = await backend.load_json(path)
    assert fetched == payload, (
        "Round trip failed — local fallback masked an SP routing issue. "
        "Check daemon logs for 'No such bucket' or 'bucket-sp-fallback'."
    )

    # Cleanup. delete_object isn't on the public API; reach into the
    # Greenfield client directly. Best-effort — leftover test objects
    # in a dev bucket are not catastrophic.
    try:
        gf = backend._greenfield
        if hasattr(gf, "_run_js_op"):
            await asyncio.to_thread(gf._run_js_op, "delete", path)
    except Exception:
        pass


# ── State-root ↔ on-chain anchor round-trip ───────────────────────────


@pytest.mark.asyncio
async def test_state_root_anchor_round_trips_to_bsc(backend):
    """Compute a manifest's state-root locally, write it to BSC via
    the BSCClient, then read it back from the contract. Must match
    byte-for-byte. This is the verifiability contract third-party
    auditors rely on."""
    from nexus_core.anchor import build_anchor_batch

    if backend._chain_client is None:
        pytest.skip("BSC chain client not configured (no RPC / contract address)")

    agent_id = int(os.environ["NEXUS_TESTNET_AGENT_ID"])

    # Build a small manifest from synthetic events. prev_root pulled
    # live so the chain stays consistent with what we anchor next.
    prev = backend._chain_client.resolve_state_root(agent_id)
    prev_hex = "0x" + (prev.hex() if prev else "0" * 64)

    events = [
        {
            "client_created_at": "2026-05-01T00:00:00Z",
            "event_type": "user_message",
            "content": f"live test {uuid.uuid4().hex[:6]}",
            "session_id": "live-test",
            "sync_id": int(time.time()),
            "server_received_at": "2026-05-01T00:00:00Z",
        },
    ]
    batch = build_anchor_batch(
        user_id=f"live-{uuid.uuid4().hex[:8]}",
        prev_root=prev_hex,
        events=events,
    )
    canonical = batch.canonicalize()
    state_root = hashlib.sha256(canonical).digest()

    # Write to BSC. The wallet behind NEXUS_PRIVATE_KEY MUST be the
    # currently active runtime for this agent_id, otherwise the tx
    # reverts.
    tx_hash = await asyncio.to_thread(
        backend._chain_client.update_state_root,
        agent_id, state_root, backend._chain_client.address,
    )
    assert tx_hash, "BSC tx hash should be returned by update_state_root"

    # Wait for the new value to be visible on chain. Polling avoids
    # tying the test to RPC-specific block intervals.
    for _ in range(45):
        on_chain = backend._chain_client.resolve_state_root(agent_id)
        if on_chain and on_chain == state_root:
            break
        await asyncio.sleep(1)

    on_chain = backend._chain_client.resolve_state_root(agent_id)
    assert on_chain == state_root, (
        f"On-chain state_root {on_chain.hex() if on_chain else None!r} "
        f"does not match locally-computed {state_root.hex()!r} after 45s"
    )


# ── WAL crash-recovery against real Greenfield ────────────────────────


@pytest.mark.asyncio
async def test_wal_replay_uploads_real_bytes_to_greenfield(cache_dir):
    """Simulate a crash + restart with a real SP. Round 1 writes to
    the WAL but cancels before put completes. Round 2 fires up a
    fresh ChainBackend pointed at the same cache dir and verifies
    that ``replay_wal`` actually uploads the bytes to Greenfield."""
    from nexus_core.backends.chain import ChainBackend

    bucket = os.environ["NEXUS_TESTNET_BUCKET"]
    pk = os.environ["NEXUS_PRIVATE_KEY"]
    path = f"_test/wal_replay_{uuid.uuid4().hex[:8]}.json"

    # Round 1 — write, then immediately cancel pending tasks before
    # the SP put can finish.
    be1 = ChainBackend(private_key=pk, network="testnet", greenfield_bucket=bucket)
    await be1.store_json(path, {"crash_test": True, "round": 1})
    for t in list(be1._pending_tasks):
        t.cancel()
    await asyncio.gather(*be1._pending_tasks, return_exceptions=True)
    # WAL still has the entry.
    assert any(e["path"] == path for e in be1._wal.read_all())

    # Round 2 — fresh backend. WAL replay should upload it.
    be2 = ChainBackend(private_key=pk, network="testnet", greenfield_bucket=bucket)
    await be2.replay_wal()
    for _ in range(30):
        if not be2._pending_tasks:
            break
        await asyncio.sleep(1)

    # Bypass local cache and read from Greenfield to confirm the
    # replay actually landed bytes on the SP.
    (be2._cache_path(path)).unlink(missing_ok=True)
    be2._neg_cache.pop(path, None)
    got = await be2.load_json(path)
    assert got == {"crash_test": True, "round": 1}, (
        "WAL replay didn't upload to Greenfield — likely SP routing "
        "or auth problem. Check daemon stderr."
    )

    # Cleanup
    try:
        await asyncio.to_thread(be2._greenfield._run_js_op, "delete", path)
    except Exception:
        pass
    await be2.close(grace_period=10)
