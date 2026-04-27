"""Background anchoring of synced events to Greenfield + BSC.

After every successful ``/api/v1/sync/push``, the handler calls
:func:`enqueue_anchor` which:

  1. Inserts a ``sync_anchors`` row in ``status='pending'`` so the work
     is durable across crashes / restarts.
  2. Schedules an asyncio task that:
       a. Builds a JSON payload of the just-pushed events.
       b. PUTs it to Greenfield (returns SHA-256 content hash).
       c. Calls :py:meth:`BSCClient.update_state_root` to anchor the
          hash into the user's ERC-8004 AgentStateExtension.
       d. Updates the ``sync_anchors`` row with final status.

Design notes:

* The task is fire-and-forget — the client's ``/sync/push`` returns
  immediately on the SQLite write. The anchor row is the way the client
  later learns whether the durable copy actually landed.

* All chain calls happen via :func:`asyncio.to_thread` because the SDK's
  web3 + HTTP calls are synchronous; running them inline would block the
  event loop.

* If the user has no ``chain_agent_id`` yet (never registered via
  ``chain_proxy.register-agent``), we record ``awaiting_registration``
  and DO NOT skip the Greenfield write — the durable copy still goes up;
  the BSC anchor can be replayed later when registration completes.

* Test override hook: set ``_chain_backend_test_override`` to a fake with
  the methods ``put_json(obj, path) -> sha`` and
  ``anchor(agent_id, content_hash, runtime) -> tx_hash``. The real path
  is the same shape so tests need only stub the boundary, not web3.
"""

import asyncio
import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from nexus_server.config import get_config
from nexus_server.database import get_db_connection

logger = logging.getLogger(__name__)
config = get_config()


# ───────────────────────────────────────────────────────────────────────────
# Per-agent backends.
#
# Architectural note (2026-04 correction):
#
# Each ERC-8004 agent gets its OWN Greenfield bucket: ``nexus-agent-{tokenId}``.
# That mirrors NFT ownership semantics — the bucket lives or dies with the
# agent, and a future export/transfer of agent ownership maps to a single
# resource. The shared "rune-server-sync" bucket the first iteration used
# was wrong: it conflated all users into one Greenfield namespace.
#
# Implementation:
#   - One BSCClient process-wide (BSC client doesn't care which agent;
#     same signer signs every tx).
#   - One GreenfieldClient PER chain_agent_id, cached. Each instance shares
#     the same Node daemon (class-level _daemon_proc in GreenfieldClient).
#     Bucket auto-create on first put is already handled by GreenfieldClient.
#   - AnchorBackend.anchor first calls setActiveRuntime if needed, THEN
#     updateStateRoot — the AgentStateExtension contract requires this
#     pairing (the runtime parameter to updateStateRoot is checked against
#     the stored activeRuntime, which starts at address(0) for new agents).
#
# Tests inject a fake via _chain_backend_test_override that returns a single
# AnchorBackend regardless of agent id; that's fine for unit-level tests.
# ───────────────────────────────────────────────────────────────────────────


_chain_backend_test_override = None  # set by tests
_greenfield_by_agent: dict[int, Any] = {}
_chain_client = None  # singleton for BSC
_init_lock = threading.Lock()


def _bucket_for_agent(chain_agent_id: int) -> str:
    """Greenfield bucket name for an ERC-8004 agent.

    Delegates to :func:`nexus_core.utils.agent_id.bucket_for_agent`
    so SDK / Nexus / server all agree on the convention. SDK falls back
    gracefully if the import fails (older SDK builds without the helper).
    """
    try:
        from nexus_core.utils.agent_id import bucket_for_agent
        return bucket_for_agent(chain_agent_id)
    except Exception:
        return f"nexus-agent-{chain_agent_id}"


class AnchorBackend:
    """Thin protocol that sync_anchor needs from the chain layer."""

    async def put_json(self, payload: Any, path: str) -> str:
        """Store JSON payload, return whatever object path Greenfield used."""
        raise NotImplementedError

    def anchor(
        self, agent_id_int: int, content_hash_hex: str, runtime: str
    ) -> str:
        """Submit BSC tx anchoring content_hash for agent_id. Returns tx hash."""
        raise NotImplementedError


class _RealAnchorBackend(AnchorBackend):
    def __init__(self, greenfield, chain_client, runtime_address: str):
        self._gf = greenfield
        self._chain = chain_client
        self._runtime = runtime_address
        self._bucket_ready = False  # set after first ensure_bucket() succeeds

    async def put_json(self, payload: Any, path: str) -> str:
        # GreenfieldClient.put doesn't auto-create the bucket — it assumes
        # the bucket is there and falls back to local cache if not. For our
        # per-agent buckets that don't exist yet on a fresh deploy, we
        # explicitly call ensure_bucket() once and cache the result.
        if not self._bucket_ready:
            try:
                ok = await self._gf.ensure_bucket()
                self._bucket_ready = bool(ok)
                if not ok:
                    logger.warning(
                        "ensure_bucket returned False for %s — put may fail",
                        getattr(self._gf, "_bucket_name", "?"),
                    )
            except Exception as e:
                logger.warning("ensure_bucket raised: %s", e)
        return await self._gf.put_json(payload, object_path=path)

    def anchor(
        self, agent_id_int: int, content_hash_hex: str, runtime: str
    ) -> str:
        state_root = bytes.fromhex(content_hash_hex.replace("0x", ""))
        runtime_addr = runtime or self._runtime
        # Pre-flight: if the agent's stored activeRuntime is 0 or != ours,
        # call setActiveRuntime first so updateStateRoot's runtime-match
        # check passes. AgentStateExtension's storage layout (per ABI):
        #   agents(agentId) -> (bytes32 stateRoot, address activeRuntime, uint256 updatedAt)
        try:
            from web3 import Web3
            current_runtime = "0x" + "0" * 40
            try:
                _root, current_runtime, _ts = (
                    self._chain.agent_state.functions.agents(agent_id_int).call()
                )
            except Exception as e:
                logger.debug(
                    "agents(%d) read failed (will assume default): %s",
                    agent_id_int, e,
                )

            need_set = (
                current_runtime is None
                or int(current_runtime, 16) == 0
                or Web3.to_checksum_address(current_runtime)
                != Web3.to_checksum_address(runtime_addr)
            )
            if need_set:
                logger.info(
                    "Anchor: setActiveRuntime(%d, %s) — current=%s",
                    agent_id_int, runtime_addr, current_runtime,
                )
                self._chain.set_active_runtime(agent_id_int, runtime_addr)
        except Exception as e:
            # Not fatal: updateStateRoot may still succeed if we guessed
            # wrong about the contract's preconditions.
            logger.warning(
                "Anchor: pre-flight setActiveRuntime check failed: %s", e
            )

        return self._chain.update_state_root(
            agent_id=agent_id_int,
            state_root=state_root,
            runtime=runtime_addr,
        )


def _get_chain_client_singleton():
    """Build (or return) the process-wide BSCClient.

    BSC interactions don't depend on agent — same signer, same contracts.
    """
    global _chain_client
    if _chain_client is not None:
        return _chain_client

    if not config.chain_is_configured:
        return None

    try:
        from nexus_core.chain import BSCClient
    except Exception as e:
        logger.warning("SDK chain client unavailable: %s", e)
        return None

    pk = config.SERVER_PRIVATE_KEY or ""
    if pk and not pk.startswith("0x"):
        pk = "0x" + pk
    is_mainnet = "mainnet" in config.NEXUS_NETWORK
    try:
        _chain_client = BSCClient(
            rpc_url=config.chain_active_rpc,
            private_key=pk,
            identity_registry_address=(
                config.NEXUS_MAINNET_IDENTITY_REGISTRY
                if is_mainnet
                else config.NEXUS_TESTNET_IDENTITY_REGISTRY
            ),
            agent_state_address=(
                None if is_mainnet
                else config.NEXUS_TESTNET_AGENT_STATE_ADDRESS
            ),
            task_manager_address=(
                None if is_mainnet
                else config.NEXUS_TESTNET_TASK_MANAGER_ADDRESS
            ),
            network="bsc_mainnet" if is_mainnet else "bsc_testnet",
        )
    except Exception as e:
        logger.warning("Failed to init BSCClient: %s", e)
        _chain_client = None
        return None
    return _chain_client


def _get_backend_for_agent(chain_agent_id: int) -> Optional[AnchorBackend]:
    """Return an AnchorBackend whose Greenfield bucket is per-agent.

    Cached per chain_agent_id so we share the SDK's Node daemon and don't
    repeatedly probe the chain for bucket existence. Tests bypass via
    ``_chain_backend_test_override``.
    """
    if _chain_backend_test_override is not None:
        return _chain_backend_test_override

    chain = _get_chain_client_singleton()
    if chain is None:
        return None

    if chain_agent_id in _greenfield_by_agent:
        return _RealAnchorBackend(
            _greenfield_by_agent[chain_agent_id], chain, chain.address or ""
        )

    with _init_lock:
        if chain_agent_id in _greenfield_by_agent:
            gf = _greenfield_by_agent[chain_agent_id]
        else:
            try:
                from nexus_core.greenfield import GreenfieldClient
            except Exception as e:
                logger.warning("SDK Greenfield unavailable: %s", e)
                return None

            pk = config.SERVER_PRIVATE_KEY or ""
            if pk and not pk.startswith("0x"):
                pk = "0x" + pk
            is_mainnet = "mainnet" in config.NEXUS_NETWORK
            try:
                gf = GreenfieldClient(
                    private_key=pk,
                    bucket_name=_bucket_for_agent(chain_agent_id),
                    network="mainnet" if is_mainnet else "testnet",
                )
            except Exception as e:
                logger.warning(
                    "Failed to build GreenfieldClient for agent %d: %s",
                    chain_agent_id, e,
                )
                return None
            _greenfield_by_agent[chain_agent_id] = gf

    return _RealAnchorBackend(gf, chain, chain.address or "")


# ───────────────────────────────────────────────────────────────────────────
# DB helpers
# ───────────────────────────────────────────────────────────────────────────


def _insert_pending_anchor(
    user_id: str,
    sync_ids: list[int],
    content_hash: str,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO sync_anchors
                (user_id, first_sync_id, last_sync_id, event_count,
                 content_hash, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                user_id,
                min(sync_ids),
                max(sync_ids),
                len(sync_ids),
                content_hash,
                now,
                now,
            ),
        )
        anchor_id = cursor.lastrowid
        conn.commit()
    return anchor_id


def _update_anchor(
    anchor_id: int,
    *,
    status: str,
    greenfield_path: Optional[str] = None,
    bsc_tx_hash: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE sync_anchors
            SET status = ?,
                greenfield_path = COALESCE(?, greenfield_path),
                bsc_tx_hash = COALESCE(?, bsc_tx_hash),
                error = COALESCE(?, error),
                updated_at = ?
            WHERE anchor_id = ?
            """,
            (status, greenfield_path, bsc_tx_hash, error, now, anchor_id),
        )
        conn.commit()


def _fetch_chain_agent_id(user_id: str) -> Optional[int]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT chain_agent_id FROM users WHERE id = ?", (user_id,)
        )
        row = cursor.fetchone()
    return row[0] if row and row[0] is not None else None


# ───────────────────────────────────────────────────────────────────────────
# Public API
# ───────────────────────────────────────────────────────────────────────────


def compute_content_hash(payload_bytes: bytes) -> str:
    """SHA-256 hex of the serialized payload — also the Greenfield content key."""
    return hashlib.sha256(payload_bytes).hexdigest()


def serialize_batch(user_id: str, sync_ids: list[int], events: list[dict]) -> bytes:
    """Build the canonical JSON the anchor should hash + store.

    Sorted keys + no whitespace + NO non-deterministic fields (no
    server clock) so the hash is reproducible by an external auditor:
    given the same events, anyone re-running ``serialize_batch`` →
    ``compute_content_hash`` must arrive at the same SHA-256 the
    server posted on-chain. The "when did we anchor it?" timestamp
    lives in the ``sync_anchors`` row's ``created_at`` column instead.
    """
    payload = {
        "schema": "nexus.sync.batch.v1",
        "user_id": user_id,
        "sync_ids": sorted(sync_ids),
        "events": events,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


async def _run_anchor_job(
    anchor_id: int,
    user_id: str,
    payload_bytes: bytes,
    content_hash: str,
) -> None:
    """The actual background work.

    Order matters now that bucket is per-agent:
      1. Resolve chain_agent_id. Without it, we don't know which bucket
         to write to — defer Greenfield until registration completes.
      2. Build the per-agent backend (cached after first hit).
      3. Greenfield put using object path scoped under the agent.
      4. BSC anchor (the backend itself handles the setActiveRuntime
         pre-flight before updateStateRoot).
    """
    # 0. If a test override is in effect we don't need an agent id at all
    #    — tests pass any int.
    if _chain_backend_test_override is None:
        if not config.chain_is_configured:
            logger.info("Anchor %d: chain not configured, stored_only.", anchor_id)
            _update_anchor(anchor_id, status="stored_only")
            return

    # 1. Need chain_agent_id for both bucket name AND BSC anchor.
    chain_agent_id = _fetch_chain_agent_id(user_id)
    if chain_agent_id is None:
        # Greenfield bucket name is `nexus-agent-{tokenId}`, so we can't
        # even pick a bucket without registration. Mark and let the daemon
        # come back to it once /chain/register-agent succeeds.
        _update_anchor(
            anchor_id,
            status="awaiting_registration",
            error="user has no chain_agent_id; "
            "call /api/v1/chain/register-agent first",
        )
        return

    # 2. Per-agent backend (one bucket per ERC-8004 token id).
    backend = _get_backend_for_agent(int(chain_agent_id))
    if backend is None:
        logger.info(
            "Anchor %d: backend unavailable for agent %d, stored_only.",
            anchor_id, chain_agent_id,
        )
        _update_anchor(anchor_id, status="stored_only")
        return

    # The Greenfield object path stays nested under the agent for clarity
    # even though the bucket is already per-agent — makes manual browsing
    # via DCellar easier.
    object_path = f"sync/{content_hash}.json"

    # 3. Greenfield write
    try:
        gf_path = await backend.put_json(json.loads(payload_bytes), object_path)
        _update_anchor(
            anchor_id, status="pending",
            greenfield_path=(
                f"nexus-agent-{chain_agent_id}/{gf_path or object_path}"
            ),
        )
    except Exception as e:
        logger.error("Anchor %d: Greenfield put failed: %s", anchor_id, e)
        _update_anchor(anchor_id, status="failed", error=f"greenfield: {e}")
        return

    # 4. BSC anchor (backend handles setActiveRuntime pre-flight)
    try:
        tx_hash = await asyncio.to_thread(
            backend.anchor, int(chain_agent_id), content_hash, ""
        )
        _update_anchor(
            anchor_id, status="anchored", bsc_tx_hash=tx_hash
        )
    except Exception as e:
        logger.error("Anchor %d: BSC anchor failed: %s", anchor_id, e)
        _update_anchor(
            anchor_id, status="failed", error=f"bsc: {e}"
        )


def enqueue_anchor(user_id: str, sync_ids: list[int], events: list[dict]) -> int:
    """Synchronously create the sync_anchors row and schedule the work.

    Returns the anchor_id so callers can include it in their response if
    they want.
    """
    if not sync_ids:
        return 0

    payload_bytes = serialize_batch(user_id, sync_ids, events)
    content_hash = compute_content_hash(payload_bytes)
    anchor_id = _insert_pending_anchor(user_id, sync_ids, content_hash)

    # Fire-and-forget. asyncio.create_task requires a running event loop;
    # FastAPI handlers always run on one.
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            _run_anchor_job(anchor_id, user_id, payload_bytes, content_hash)
        )
    except RuntimeError:
        # No event loop (e.g. called from a sync test). Run inline.
        asyncio.run(
            _run_anchor_job(anchor_id, user_id, payload_bytes, content_hash)
        )

    return anchor_id


def list_anchors_for_user(user_id: str, limit: int = 50) -> list[dict]:
    """Return the user's most recent sync_anchors (most recent first)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT anchor_id, first_sync_id, last_sync_id, event_count,
                   content_hash, greenfield_path, bsc_tx_hash, status,
                   error, created_at, updated_at, retry_count
            FROM sync_anchors
            WHERE user_id = ?
            ORDER BY anchor_id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        rows = cursor.fetchall()
    return [
        {
            "anchor_id": r[0],
            "first_sync_id": r[1],
            "last_sync_id": r[2],
            "event_count": r[3],
            "content_hash": r[4],
            "greenfield_path": r[5],
            "bsc_tx_hash": r[6],
            "status": r[7],
            "error": r[8],
            "created_at": r[9],
            "updated_at": r[10],
            "retry_count": r[11] or 0,
        }
        for r in rows
    ]


# ───────────────────────────────────────────────────────────────────────────
# Retry daemon (deleted in Phase B)
# ───────────────────────────────────────────────────────────────────────────
#
# A periodic daemon used to claim 'failed' / 'awaiting_registration' rows
# and retry them with exponential backoff. After S4 retired the
# /sync/push enqueue path, this daemon ran on top of an empty queue —
# no new rows were ever created via the legacy pipeline, only via tests.
# The daemon was made opt-in (NEXUS_ENABLE_RETRY_DAEMON=1) in S4 and
# never re-enabled in production. Phase B removes the daemon entirely
# along with its retry-state columns (next_retry_at, retry_count are
# still in the schema for back-compat reads but never updated).
#
# If you have orphan 'failed' rows from a pre-S4 deployment that you
# need to drain, write a one-shot script that calls
# ``_run_anchor_job`` (the per-row anchor coroutine) for each row.
# Don't restore the daemon.
