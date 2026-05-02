"""Chain recovery — server with empty local data rebuilds from chain.

This is the "永生 (immortality) story": if a server's local SQLite +
filesystem is wiped (migration, crash + missing volume, fresh deploy
from a clean image), the agent's state must still be reachable —
because Greenfield holds the canonical bytes and BSC anchors the
state-roots.

Each test sets up TWO ChainBackend instances backed by the SAME
"Greenfield" (StubGreenfield) but DIFFERENT local cache directories.
Instance A writes data, then dies; instance B starts fresh (no local
data) and must successfully read everything via Greenfield's recovery
path.

Coverage:

  * **Artifacts** (persona / skills / knowledge legacy artifacts +
    file uploads) — written via ``store_blob`` + ``store_json``,
    re-read on a clean instance via ``load_json`` / ``load_blob``.
  * **EventLog rows** — currently only the local SQLite. This test
    documents the gap so when we wire the EventLog → Greenfield
    mirror in a future task, the assertion changes from "documented
    gap" to "fully recoverable".
  * **Manifest cold-load** — ArtifactProviderImpl lazy-loads its
    manifest dict from Greenfield on first ``load`` call. Verified
    here as the keystone of cross-instance recovery.

Notes on the stub: the Greenfield substrate is shared between A and
B by passing the SAME ``StubGreenfield.objects`` dict to both — they
share the underlying object store, exactly as two real ChainBackends
pointing at the same Greenfield bucket would.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from typing import Optional

import pytest

from nexus_core.backends.chain import ChainBackend
from nexus_core.providers.artifact import ArtifactProviderImpl


# ── Shared-substrate Greenfield stub ─────────────────────────────────


class _SharedGreenfield:
    """Like StubGreenfield but two instances can share a backing
    dict so they appear as ONE Greenfield bucket from the outside."""

    def __init__(self, shared_objects: Optional[dict] = None):
        self.objects = shared_objects if shared_objects is not None else {}

    async def put(self, data: bytes, object_path: Optional[str] = None) -> str:
        chash = hashlib.sha256(data).hexdigest()
        if object_path:
            self.objects[object_path] = bytes(data)
        return chash

    async def get(self, content_hash: str, object_path: Optional[str] = None) -> Optional[bytes]:
        if object_path and object_path in self.objects:
            return self.objects[object_path]
        return None

    async def close(self):
        pass


def _make_backend(cache_dir: Path, shared_substrate: dict, monkeypatch):
    """Construct a ChainBackend rooted at ``cache_dir`` whose
    Greenfield-stub points at ``shared_substrate``. BSC client stays
    None so we don't try to reach a network."""
    monkeypatch.setenv("NEXUS_CACHE_DIR", str(cache_dir))
    for var in (
        "NEXUS_TESTNET_RPC", "NEXUS_BSC_RPC",
        "NEXUS_TESTNET_AGENT_STATE_ADDRESS", "NEXUS_AGENT_STATE_ADDRESS",
    ):
        monkeypatch.delenv(var, raising=False)

    be = ChainBackend(
        private_key="0x" + "a" * 64,
        network="testnet",
        greenfield_bucket="nexus-agent-recovery-test",
    )
    be._greenfield = _SharedGreenfield(shared_objects=shared_substrate)
    return be


@pytest.fixture
def shared_substrate():
    """The single Greenfield bucket shared between A and B. Mutating
    it from one backend is visible to the other — same as if both
    were pointing at the same gnfd:// path in production."""
    return {}


# ── Test 1: store_json round trip across fresh instance ──────────────


@pytest.mark.asyncio
async def test_store_json_recoverable_from_fresh_instance(
    tmp_path, shared_substrate, monkeypatch,
):
    """Layer-1 (Greenfield) is the canonical store. After A writes
    a JSON document and dies, B (fresh local cache) reads the same
    document via Greenfield → cache populated as a side effect.
    This is the keystone of "agent state survives server replacement".
    """
    a = _make_backend(tmp_path / "a_cache", shared_substrate, monkeypatch)
    payload = {"persona": "warm and curious", "version": 3}
    await a.store_json("agents/agent-x/persona.json", payload)
    # Drain the write-behind so substrate has the bytes.
    await asyncio.gather(*list(a._pending_tasks), return_exceptions=True)

    # ── Death of A: clear its local cache + close its backend ──
    await a.close() if hasattr(a, "close") else None
    shutil.rmtree(tmp_path / "a_cache", ignore_errors=True)

    # ── B starts fresh: no local cache, points at same Greenfield ──
    b = _make_backend(tmp_path / "b_cache", shared_substrate, monkeypatch)
    # Sanity: B's local cache is empty, so this read MUST go to
    # Greenfield to succeed.
    assert not (tmp_path / "b_cache" / "agents").exists()

    restored = await b.load_json("agents/agent-x/persona.json")
    assert restored == payload, (
        "ChainBackend must restore data from Greenfield when local "
        "cache is empty — that's the recovery contract"
    )

    # B's read populated its local cache so the next read is local.
    cache_path = b._cache_path("agents/agent-x/persona.json")
    assert cache_path.exists()


# ── Test 2: blob round trip across fresh instance ────────────────────


@pytest.mark.asyncio
async def test_store_blob_recoverable_from_fresh_instance(
    tmp_path, shared_substrate, monkeypatch,
):
    """Same as test 1 but for raw bytes (file uploads, big content).
    The three-layer file store relies on this path: even if the
    server's uploads/ disk is gone, ``ChainBackend.load_blob`` fetches
    the bytes back from Greenfield."""
    a = _make_backend(tmp_path / "a_cache", shared_substrate, monkeypatch)
    raw = b"%PDF-1.4 dummy uploaded paper bytes"
    await a.store_blob("files/abc123/paper.pdf", raw)
    await asyncio.gather(*list(a._pending_tasks), return_exceptions=True)

    shutil.rmtree(tmp_path / "a_cache", ignore_errors=True)

    b = _make_backend(tmp_path / "b_cache", shared_substrate, monkeypatch)
    restored = await b.load_blob("files/abc123/paper.pdf")
    assert restored == raw, (
        "Files uploaded under instance A must be recoverable from "
        "instance B via the same Greenfield bucket — ensures that "
        "users don't lose attachments when the server VM is replaced."
    )


# ── Test 3: ArtifactProvider manifest cold-loads from Greenfield ─────


@pytest.mark.asyncio
async def test_artifact_provider_recovers_manifest_on_fresh_instance(
    tmp_path, shared_substrate, monkeypatch,
):
    """ArtifactProvider keeps an in-memory manifest cache. When the
    cache is empty (cold start), it must lazy-load from Greenfield —
    otherwise persona / skills / knowledge artifacts saved by A
    would be invisible to B.

    This is the path PersonaEvolver / SkillEvolver / KnowledgeCompiler
    use to rehydrate state on twin restart. If this test fails, the
    user effectively gets a "factory-reset" agent every time the
    server replaces its disk.
    """
    a = _make_backend(tmp_path / "a_cache", shared_substrate, monkeypatch)
    ap_a = ArtifactProviderImpl(backend=a)
    persona_bytes = json.dumps({
        "persona": "an experienced tour guide who specializes in Tokyo",
        "version": 2,
    }).encode("utf-8")

    version = await ap_a.save(
        filename="persona.json",
        data=persona_bytes,
        agent_id="agent-x",
        content_type="application/json",
    )
    assert version == 1
    await asyncio.gather(*list(a._pending_tasks), return_exceptions=True)

    # Wipe A's local cache.
    shutil.rmtree(tmp_path / "a_cache", ignore_errors=True)

    # B: brand new instance, brand new ArtifactProvider with EMPTY
    # in-memory manifest dict. Must hit Greenfield to find the file.
    b = _make_backend(tmp_path / "b_cache", shared_substrate, monkeypatch)
    ap_b = ArtifactProviderImpl(backend=b)
    assert ap_b._manifests == {}, (
        "fresh ArtifactProvider must start with empty manifest cache"
    )

    art = await ap_b.load(filename="persona.json", agent_id="agent-x")
    assert art is not None, (
        "ArtifactProvider on the fresh instance must lazy-load the "
        "manifest from Greenfield — without this, evolvers see no "
        "prior persona / skills / knowledge after a server restart"
    )
    restored = json.loads(art.data.decode("utf-8"))
    assert restored["persona"].startswith("an experienced tour guide")
    assert restored["version"] == 2


# ── Test 4: multiple-version artifact survives recovery ──────────────


@pytest.mark.asyncio
async def test_artifact_versions_all_recoverable(
    tmp_path, shared_substrate, monkeypatch,
):
    """Each persona evolution / skill update creates a new version.
    After A writes 3 versions and dies, B must see ALL THREE in the
    manifest — version history (the audit trail) must NOT be lost
    because that's how rollback works."""
    a = _make_backend(tmp_path / "a_cache", shared_substrate, monkeypatch)
    ap_a = ArtifactProviderImpl(backend=a)
    for i, persona in enumerate(["v1 baseline", "v2 warmer", "v3 expert"], 1):
        await ap_a.save(
            filename="persona.json",
            data=persona.encode(),
            agent_id="agent-x",
        )
    await asyncio.gather(*list(a._pending_tasks), return_exceptions=True)

    shutil.rmtree(tmp_path / "a_cache", ignore_errors=True)

    b = _make_backend(tmp_path / "b_cache", shared_substrate, monkeypatch)
    ap_b = ArtifactProviderImpl(backend=b)

    # Latest version still readable.
    latest = await ap_b.load("persona.json", agent_id="agent-x")
    assert latest is not None
    assert latest.data == b"v3 expert"

    # Older versions still readable too — audit / rollback target.
    v1 = await ap_b.load("persona.json", agent_id="agent-x", version=1)
    assert v1 is not None
    assert v1.data == b"v1 baseline"


# ── Test 5: per-agent isolation survives recovery ────────────────────


@pytest.mark.asyncio
async def test_recovery_scoped_per_agent(
    tmp_path, shared_substrate, monkeypatch,
):
    """Two agents writing through the SAME Greenfield substrate must
    not bleed into each other after a fresh instance loads them.
    Real-world parallel: two users on a shared server back-end."""
    a = _make_backend(tmp_path / "a_cache", shared_substrate, monkeypatch)
    ap_a = ArtifactProviderImpl(backend=a)
    await ap_a.save("persona.json", b"agent_one_data", agent_id="agent-1")
    await ap_a.save("persona.json", b"agent_two_data", agent_id="agent-2")
    await asyncio.gather(*list(a._pending_tasks), return_exceptions=True)

    shutil.rmtree(tmp_path / "a_cache", ignore_errors=True)

    b = _make_backend(tmp_path / "b_cache", shared_substrate, monkeypatch)
    ap_b = ArtifactProviderImpl(backend=b)
    one = await ap_b.load("persona.json", agent_id="agent-1")
    two = await ap_b.load("persona.json", agent_id="agent-2")
    assert one is not None and one.data == b"agent_one_data"
    assert two is not None and two.data == b"agent_two_data"


# ── Test 6: EventLog recovers from chain snapshot ────────────────────


@pytest.mark.asyncio
async def test_event_log_round_trip_via_chain_snapshot(
    tmp_path, shared_substrate, monkeypatch,
):
    """EventLog → Greenfield snapshot → EventLog (fresh) recovery.

    Closes the gap that pre-Phase-A2 made the EventLog SQLite a
    single point of failure: now ``snapshot_to`` dumps the full log
    to Greenfield, and ``recover_from`` reloads on a fresh instance.

    This is what makes "the agent's whole conversation history
    survives a server replacement" actually true.
    """
    from nexus_core.memory import EventLog

    backend_a = _make_backend(tmp_path / "a_cache", shared_substrate, monkeypatch)
    log_a = EventLog(base_dir=str(tmp_path / "a"), agent_id="agent-x")
    log_a.append("user_message", "hello", metadata={"turn": 1})
    log_a.append("assistant_response", "hi there!", metadata={"turn": 1})
    log_a.append("memory_compact", "compacted 2 events",
                 metadata={"event_count": 2})
    log_a.append("user_message", "what's the weather", metadata={"turn": 2})

    # Snapshot to chain.
    snap = await log_a.snapshot_to(backend_a)
    await asyncio.gather(
        *list(backend_a._pending_tasks), return_exceptions=True,
    )
    assert snap["event_count"] == 4
    assert snap["agent_id"] == "agent-x"

    # ── Death of A: drop everything local ──
    log_a.close()
    shutil.rmtree(tmp_path / "a", ignore_errors=True)
    shutil.rmtree(tmp_path / "a_cache", ignore_errors=True)

    # ── B starts fresh: empty EventLog at a new base_dir ──
    backend_b = _make_backend(tmp_path / "b_cache", shared_substrate, monkeypatch)
    log_b = EventLog(base_dir=str(tmp_path / "b"), agent_id="agent-x")
    assert log_b.count() == 0  # genuinely empty

    restored = await log_b.recover_from(backend_b)
    assert restored == 4, (
        "EventLog.recover_from must restore every event from the "
        "snapshot — that's how a brand-new server picks up an "
        "agent's full conversation history after a migration"
    )
    assert log_b.count() == 4

    # Order + content preserved. Indices preserved so cross-references
    # in metadata (e.g. evolution_proposal.evidence_event_ids) stay valid.
    rows = log_b.recent(limit=10)
    assert [r.event_type for r in rows] == [
        "user_message", "assistant_response", "memory_compact", "user_message",
    ]
    assert rows[0].content == "hello"
    assert rows[0].metadata == {"turn": 1}
    assert rows[3].content == "what's the weather"


@pytest.mark.asyncio
async def test_event_log_recover_is_no_op_on_non_empty(
    tmp_path, shared_substrate, monkeypatch,
):
    """Safety: recover_from on an EventLog that already has local
    rows must NOT interleave / duplicate / overwrite. The recovery
    primitive is for cold-start only."""
    from nexus_core.memory import EventLog

    backend = _make_backend(tmp_path / "cache", shared_substrate, monkeypatch)
    # Seed Greenfield with a snapshot.
    seed = EventLog(base_dir=str(tmp_path / "seed"), agent_id="x")
    seed.append("user_message", "from snapshot")
    await seed.snapshot_to(backend)
    seed.close()

    # Brand new local log gets some local writes BEFORE recover.
    log_b = EventLog(base_dir=str(tmp_path / "b"), agent_id="x")
    log_b.append("user_message", "local first")
    assert log_b.count() == 1

    restored = await log_b.recover_from(backend)
    assert restored == 0, (
        "recover_from must skip when local log is non-empty — "
        "interleaving snapshot rows with concurrent local writes "
        "would scramble the timeline"
    )
    assert log_b.count() == 1
    rows = log_b.recent(limit=10)
    assert rows[0].content == "local first"


@pytest.mark.asyncio
async def test_event_log_recover_no_snapshot_returns_zero(
    tmp_path, shared_substrate, monkeypatch,
):
    """A genuinely brand-new agent with no prior snapshot: recover
    cleanly returns 0 instead of raising."""
    from nexus_core.memory import EventLog

    backend = _make_backend(tmp_path / "cache", shared_substrate, monkeypatch)
    log = EventLog(base_dir=str(tmp_path / "fresh"), agent_id="never-seen-before")
    restored = await log.recover_from(backend)
    assert restored == 0
    assert log.count() == 0
