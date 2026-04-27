"""
ChainBackend — BSC + Greenfield storage for production.

Stores data on BNB Greenfield (decentralized object storage) and
anchors content hashes on BSC (BNB Smart Chain) for verifiability.

Performance architecture:
  - Local file cache for instant reads (no Greenfield roundtrip on startup)
  - Write-behind: writes go to local cache immediately, Greenfield sync
    happens asynchronously in a background thread so chat is never blocked
  - On shutdown, pending writes are flushed

Requires: web3, eth_account (pip install web3)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional

from ..core.backend import StorageBackend

logger = logging.getLogger("nexus_core.backend.chain")


class ChainBackend(StorageBackend):
    """
    Production storage backend: BSC + Greenfield.

    - JSON/blob data → Greenfield (decentralized object storage)
    - Content hashes  → BSC (on-chain anchoring for verifiability)
    """

    def __init__(
        self,
        private_key: str,
        network: str = "testnet",
        # Per-agent bucket is canonical now. ``nexus-agent-state`` shared
        # bucket is legacy — see SDK ARCHITECTURE.md (Layout A vs B).
        # Use nexus_core.bucket_for_agent(token_id) to compute.
        # ``None`` here delegates the deprecation warning to GreenfieldClient.
        greenfield_bucket: Optional[str] = None,
        rpc_url: Optional[str] = None,
        agent_state_address: Optional[str] = None,
        task_manager_address: Optional[str] = None,
        identity_registry_address: Optional[str] = None,
    ):
        import os
        from ..greenfield import GreenfieldClient

        self._network = network
        self._private_key = private_key

        # Resolve config from env if not provided
        net_prefix = "MAINNET" if "mainnet" in network else "TESTNET"
        self._rpc_url = (
            rpc_url
            or os.environ.get(f"NEXUS_{net_prefix}_RPC")
            or os.environ.get("NEXUS_BSC_RPC")
        )
        self._agent_state_address = (
            agent_state_address
            or os.environ.get(f"NEXUS_{net_prefix}_AGENT_STATE_ADDRESS")
            or os.environ.get("NEXUS_AGENT_STATE_ADDRESS")
        )
        self._identity_registry_address = (
            identity_registry_address
            or os.environ.get(f"NEXUS_{net_prefix}_IDENTITY_REGISTRY_ADDRESS")
            or os.environ.get(f"NEXUS_{net_prefix}_IDENTITY_REGISTRY")
            or os.environ.get("NEXUS_IDENTITY_REGISTRY_ADDRESS")
            or os.environ.get("NEXUS_IDENTITY_REGISTRY")
        )
        self._task_manager_address = (
            task_manager_address
            or os.environ.get(f"NEXUS_{net_prefix}_TASK_MANAGER_ADDRESS")
            or os.environ.get("NEXUS_TASK_MANAGER_ADDRESS")
        )

        # Initialize Greenfield client. Per-agent bucket is mandatory in
        # chain mode — see SDK ARCHITECTURE.md. Caller must compute via
        # ``nexus_core.bucket_for_agent(token_id)``.
        if not greenfield_bucket:
            raise ValueError(
                "ChainBackend: greenfield_bucket is required. "
                "Use nexus_core.bucket_for_agent(token_id) to compute "
                "the canonical per-agent bucket name."
            )
        try:
            self._greenfield = GreenfieldClient(
                private_key=private_key,
                bucket_name=greenfield_bucket,
                network=network,
            )
        except ImportError:
            logger.warning("Greenfield SDK not available, using local fallback")
            self._greenfield = GreenfieldClient(local_dir=".nexus_state/data")

        # Initialize chain client (optional — not all operations need it)
        self._chain_client = None
        if self._rpc_url and self._agent_state_address:
            try:
                from ..chain import BSCClient
                self._chain_client = BSCClient(
                    rpc_url=self._rpc_url,
                    private_key=private_key,
                    agent_state_address=self._agent_state_address,
                    task_manager_address=self._task_manager_address,
                    identity_registry_address=self._identity_registry_address,
                    network=network,
                )
            except ImportError:
                logger.warning("web3 not installed, chain anchoring disabled")

        # Local fallback for anchor operations when chain client unavailable
        self._local_anchors: dict[str, dict[str, str]] = {}

        # Track agents that failed on-chain: agent_id -> (skip_until_ts, backoff_seconds)
        self._anchor_skip_until: dict[str, float] = {}
        self._anchor_backoff: dict[str, float] = {}  # agent_id -> current backoff seconds

        # Map string agent_id → actual on-chain agentId (may differ for ERC-8004 register())
        self._agent_id_map: dict[str, int] = {}

        # ── Local cache (write-through) ──────────────────────────────
        # Avoids slow Greenfield reads on startup by caching data locally.
        # Every write goes to both local cache AND Greenfield.
        # Reads check local first, only hit Greenfield on cache miss.
        import os
        cache_base = os.environ.get("NEXUS_CACHE_DIR", ".rune_cache")
        self._cache_dir = Path(cache_base)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        # Track background tasks for graceful shutdown
        self._pending_tasks: set[asyncio.Task] = set()

        # In-memory negative cache: paths confirmed "not found" on Greenfield.
        # Entries expire after _NEG_CACHE_TTL seconds so new data can be discovered.
        self._neg_cache: dict[str, float] = {}  # path -> expiry timestamp
        self._NEG_CACHE_TTL: float = 600.0  # 10 minutes

        # WAL for crash recovery — ensures cancelled writes are retried on next startup
        from ..core.flush import WriteAheadLog
        wal_dir = str(self._cache_dir / "_wal")
        self._wal = WriteAheadLog(wal_dir, agent_id="chain")
        self._wal_replay_done = False

        logger.info("ChainBackend initialized: network=%s, cache=%s", network, self._cache_dir)

    # ── Local cache helpers ───────────────────────────────────────────

    def _cache_path(self, path: str) -> Path:
        """Convert a storage path to a local cache file path."""
        safe = path.replace("/", "__").replace("\\", "__")
        return self._cache_dir / safe

    def _cache_write(self, path: str, data: bytes) -> None:
        """Write data to local cache (best-effort, never raises)."""
        try:
            self._cache_path(path).write_bytes(data)
        except OSError as e:
            # Covers disk full (ENOSPC), permission denied, etc.
            logger.warning("Cache write failed for %s: %s", path, e)
        except Exception as e:
            logger.debug("Cache write failed for %s: %s", path, e)

    def _cache_read(self, path: str) -> Optional[bytes]:
        """Read data from local cache. Returns None on miss."""
        try:
            cp = self._cache_path(path)
            if cp.exists():
                return cp.read_bytes()
        except Exception as e:
            logger.debug("Cache read failed for %s: %s", path, e)
        return None

    # ── Background task management ─────────────────────────────────

    def _fire_and_forget(self, coro, label: str = "background") -> None:
        """Launch an async coroutine as a tracked fire-and-forget task.

        Tasks are tracked so close() can gracefully cancel them on shutdown.
        CancelledError is swallowed so shutdown never crashes.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("No event loop for %s, skipping", label)
            return

        async def _wrapped():
            try:
                await coro
            except asyncio.CancelledError:
                logger.debug("[%s] Task cancelled (shutdown)", label)
            except Exception as e:
                logger.warning("[%s] Task failed: %s", label, e)

        task = loop.create_task(_wrapped())
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    # ── WAL replay on startup ──────────────────────────────────────

    _wal_replay_lock = None  # asyncio.Lock, created on first use

    async def replay_wal(self) -> int:
        """Replay WAL entries that were cancelled during previous shutdown.

        Reads each WAL entry, checks if the data exists in local cache,
        and re-fires Greenfield writes for any that weren't completed.
        Returns the number of replayed writes.

        Thread-safe: only one replay runs even with concurrent callers.
        """
        if self._wal_replay_done:
            return 0

        if self._wal_replay_lock is None:
            self._wal_replay_lock = asyncio.Lock()

        async with self._wal_replay_lock:
            if self._wal_replay_done:
                return 0  # another caller already did it
            self._wal_replay_done = True

            entries = self._wal.read_all()
            if not entries:
                return 0

            # Prioritize: replay only the most critical writes immediately
            # (session + index files first, individual memories can sync later)
            priority = [e for e in entries if "session" in e.get("path", "") or "index" in e.get("path", "")]
            deferred = [e for e in entries if e not in priority]

            # Replay priority writes + max 5 deferred writes now; rest will sync in background
            to_replay = priority + deferred[:5]
            if len(deferred) > 5:
                logger.info("WAL replay: deferring %d low-priority write(s) to background",
                            len(deferred) - 5)

            logger.info("WAL replay: %d pending write(s), replaying %d now",
                         len(entries), len(to_replay))
            replayed = 0

            for entry in to_replay:
                path = entry.get("path", "")
                if not path:
                    continue

                data = self._cache_read(path)
                if data is None:
                    logger.warning("WAL replay: no cached data for %s — skipping", path)
                    continue

                content_hash = entry.get("hash", self.content_hash(data))
                logger.info("WAL replay: re-syncing %s (%d bytes)", path, len(data))

                async def _do_replay_put(p=path, d=data, h=content_hash):
                    t0 = time.time()
                    await self._greenfield.put(d, object_path=p)
                    logger.info("[WAL-REPLAY][Greenfield] PUT %s (%d bytes) %.2fs",
                                p, len(d), time.time() - t0)

                self._fire_and_forget(_do_replay_put(), label=f"WAL-replay:{path}")
                replayed += 1

            # Clear WAL after all replays are fired
            # Fire remaining deferred writes in background
            for entry in deferred[5:]:
                path = entry.get("path", "")
                if not path:
                    continue
                data = self._cache_read(path)
                if data is None:
                    continue
                content_hash = entry.get("hash", self.content_hash(data))

                async def _do_deferred_put(p=path, d=data, h=content_hash):
                    t0 = time.time()
                    await self._greenfield.put(d, object_path=p)
                    logger.info("[WAL-DEFERRED][Greenfield] PUT %s (%d bytes) %.2fs",
                                p, len(d), time.time() - t0)

                self._fire_and_forget(_do_deferred_put(), label=f"WAL-deferred:{path}")
                replayed += 1

            self._wal.truncate()
            logger.info("WAL replay: fired %d write(s), WAL cleared", replayed)
            return replayed

    # ── Write-behind: async Greenfield sync ──────────────────────────

    def _greenfield_write_behind(self, path: str, data: bytes, content_hash: str) -> None:
        """Fire-and-forget Greenfield write with WAL protection.

        1. Append to WAL (instant, durable)
        2. Fire async Greenfield PUT
        3. On success → remove from WAL
        4. On cancel/fail → WAL entry survives, replayed on next startup
        """
        # Record in WAL before firing async write
        wal_entry = {"path": path, "hash": content_hash, "size": len(data)}
        try:
            self._wal.append(wal_entry)
        except OSError:
            logger.warning("WAL append failed for %s — write is NOT crash-safe", path)

        async def _do_put():
            t0 = time.time()
            await self._greenfield.put(data, object_path=path)
            elapsed = time.time() - t0
            logger.info(
                "[WRITE][Greenfield] PUT %s (%d bytes, hash=%s) %.2fs",
                path, len(data), content_hash[:16], elapsed,
            )

        self._fire_and_forget(_do_put(), label=f"Greenfield-PUT:{path}")

    # ── JSON ────────────────────────────────────────────────────────

    async def store_json(self, path: str, data: dict) -> str:
        # Replay WAL on first write (catches cancelled writes from previous session)
        if not self._wal_replay_done:
            await self.replay_wal()

        raw = self.json_bytes(data)
        content_hash = self.content_hash(raw)

        # 1. Write to local cache (instant) + clear negative cache
        self._cache_write(path, raw)
        self._neg_cache.pop(path, None)

        # 2. Sync to Greenfield in background (non-blocking, WAL-protected)
        self._greenfield_write_behind(path, raw, content_hash)

        return content_hash

    def _neg_cache_hit(self, path: str) -> bool:
        """Check negative cache with TTL expiry."""
        expiry = self._neg_cache.get(path)
        if expiry is None:
            return False
        if time.time() > expiry:
            del self._neg_cache[path]
            return False
        return True

    async def load_json(self, path: str) -> Optional[dict]:
        # Check in-memory negative cache (instant, with TTL)
        if self._neg_cache_hit(path):
            logger.debug("[READ][NegCache] %s (known not found)", path)
            return None

        # Check local file cache (instant)
        cached = self._cache_read(path)
        if cached is not None:
            logger.debug("[READ][Cache] HIT %s (%d bytes)", path, len(cached))
            return json.loads(cached.decode("utf-8"))

        # Cache miss → Greenfield
        t0 = time.time()
        data = await self._greenfield.get("", object_path=path)
        elapsed = time.time() - t0
        if data is None:
            logger.info("[READ][Greenfield] GET %s → not found (%.2fs)", path, elapsed)
            # Negative cache: don't retry this path during this session
            self._neg_cache[path] = time.time() + self._NEG_CACHE_TTL
            return None
        logger.info("[READ][Greenfield] GET %s (%d bytes) %.2fs", path, len(data), elapsed)

        # Populate file cache for next startup
        self._cache_write(path, data)

        return json.loads(data.decode("utf-8"))

    # ── Blobs ───────────────────────────────────────────────────────

    async def store_blob(self, path: str, data: bytes) -> str:
        content_hash = self.content_hash(data)

        # 1. Write to local cache (instant) + clear negative cache
        self._cache_write(path, data)
        self._neg_cache.pop(path, None)

        # 2. Sync to Greenfield in background (non-blocking)
        self._greenfield_write_behind(path, data, content_hash)

        return content_hash

    async def load_blob(self, path: str) -> Optional[bytes]:
        # Check in-memory negative cache (instant, with TTL)
        if self._neg_cache_hit(path):
            logger.debug("[READ][NegCache] blob %s (known not found)", path)
            return None

        # Check local file cache (instant)
        cached = self._cache_read(path)
        if cached is not None:
            logger.debug("[READ][Cache] HIT blob %s (%d bytes)", path, len(cached))
            return cached

        # Cache miss → Greenfield
        t0 = time.time()
        data = await self._greenfield.get("", object_path=path)
        elapsed = time.time() - t0
        if data is not None:
            logger.info("[READ][Greenfield] GET blob %s (%d bytes) %.2fs", path, len(data), elapsed)
            self._cache_write(path, data)
        else:
            logger.info("[READ][Greenfield] GET blob %s → not found (%.2fs)", path, elapsed)
            self._neg_cache[path] = time.time() + self._NEG_CACHE_TTL
        return data

    # ── Anchoring ───────────────────────────────────────────────────

    @staticmethod
    def _agent_id_to_int(agent_id: str) -> int:
        """Convert a string agent_id to a deterministic uint256 for on-chain calls."""
        from ..utils import agent_id_to_int
        return agent_id_to_int(agent_id)

    async def anchor(self, agent_id: str, content_hash: str, namespace: str = "state") -> None:
        # Always store locally (instant, never blocks)
        self._local_anchors.setdefault(agent_id, {})[namespace] = content_hash

        # Skip on-chain anchor if this agent recently failed (exponential backoff)
        skip_until = self._anchor_skip_until.get(agent_id, 0)
        if skip_until > time.time():
            logger.debug("[BSC] Skipping anchor for %s (cooldown until %.0fs from now)", agent_id, skip_until - time.time())
            return
        elif agent_id in self._anchor_skip_until:
            del self._anchor_skip_until[agent_id]  # Cooldown expired, retry
            # Reset backoff on successful retry window
            self._anchor_backoff.pop(agent_id, None)

        if self._chain_client and namespace == "state":
            # Fire-and-forget: BSC chain calls run in background, never block chat
            self._fire_and_forget(
                self._anchor_on_chain(agent_id, content_hash),
                label=f"BSC-Anchor:{agent_id}",
            )

    def _next_backoff(self, agent_id: str) -> float:
        """Return next backoff delay and double it (capped at 300s)."""
        current = self._anchor_backoff.get(agent_id, 15)  # start at 15s
        self._anchor_backoff[agent_id] = min(current * 2, 300)
        return current

    async def _anchor_on_chain(self, agent_id: str, content_hash: str) -> None:
        """Background task: register + anchor state root on BSC. Never blocks chat."""
        try:
            root_bytes = bytes.fromhex(content_hash)
        except ValueError as e:
            logger.error("[BSC] Invalid content_hash format for %s: %s", agent_id, e)
            return
        if len(root_bytes) < 32:
            root_bytes = root_bytes.ljust(32, b"\x00")

        # Use cached on-chain ID if available (avoids re-registration on every anchor)
        if agent_id in self._agent_id_map:
            numeric_id = self._agent_id_map[agent_id]
            logger.debug("[BSC] Using cached on-chain ID %s for %s", numeric_id, agent_id)
        else:
            # First anchor for this agent — register in ERC-8004
            numeric_id = self._agent_id_to_int(agent_id)
            try:
                t0 = time.time()
                success, actual_id = self._chain_client.ensure_agent_registered(
                    numeric_id, agent_name=agent_id,
                )
                reg_elapsed = time.time() - t0
                if not success:
                    self._anchor_skip_until[agent_id] = time.time() + self._next_backoff(agent_id)
                    logger.warning(
                        "[WRITE][ERC-8004] Agent %s registration failed (%.2fs) — local fallback",
                        agent_id, reg_elapsed,
                    )
                    return
                numeric_id = actual_id
                self._agent_id_map[agent_id] = actual_id
                logger.info(
                    "[WRITE][ERC-8004] Agent %s → on-chain ID %s (%.2fs)",
                    agent_id, actual_id, reg_elapsed,
                )
            except Exception as e:
                self._anchor_skip_until[agent_id] = time.time() + 300
                logger.warning("[WRITE][ERC-8004] Registration check failed for %s: %s", agent_id, e)
                return

        try:
            t0 = time.time()
            tx_hash = self._chain_client.update_state_root(
                numeric_id, root_bytes, "0x" + "0" * 40,
            )
            anchor_elapsed = time.time() - t0
            logger.info(
                "[WRITE][BSC] Anchor OK: agent=%s hash=%s tx=%s (%.2fs)",
                agent_id, content_hash[:16], tx_hash[:16] if tx_hash else "?", anchor_elapsed,
            )
        except Exception as e:
            self._anchor_skip_until[agent_id] = time.time() + 300
            logger.warning(
                "[WRITE][BSC] Anchor failed for %s — local fallback. Reason: %s",
                agent_id, e,
            )

    async def resolve(self, agent_id: str, namespace: str = "state") -> Optional[str]:
        # Check local cache first (instant) — avoids blocking on BSC RPC during startup
        local = self._local_anchors.get(agent_id, {}).get(namespace)
        if local is not None:
            logger.info("[READ][Cache] Resolve agent=%s hash=%s (local)", agent_id, local[:16])
            return local

        # Try chain with timeout so startup is never blocked
        if self._chain_client and namespace == "state":
            numeric_id = self._agent_id_map.get(agent_id, self._agent_id_to_int(agent_id))
            try:
                t0 = time.time()
                root = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, self._chain_client.resolve_state_root, numeric_id,
                    ),
                    timeout=10.0,
                )
                elapsed = time.time() - t0
                if root is not None:
                    root_hex = root.hex()
                    logger.info("[READ][BSC] Resolve agent=%s hash=%s (%.2fs)", agent_id, root_hex[:16], elapsed)
                    # Cache locally for next time
                    self._local_anchors.setdefault(agent_id, {})[namespace] = root_hex
                    return root_hex
                else:
                    logger.info("[READ][BSC] Resolve agent=%s → not found (%.2fs)", agent_id, elapsed)
            except asyncio.TimeoutError:
                logger.warning("[READ][BSC] Resolve timed out for %s (10s) — using local fallback", agent_id)
            except Exception as e:
                logger.warning(
                    "[READ][BSC] Resolve failed for %s (falling back to local): %s",
                    agent_id, e,
                )

        return local

    # ── Listing ─────────────────────────────────────────────────────

    async def list_paths(self, prefix: str) -> list[str]:
        t0 = time.time()
        objects = await self._greenfield.list_objects(prefix)
        elapsed = time.time() - t0
        paths = [obj.get("key", "") for obj in objects]
        logger.info("[READ][Greenfield] LIST %s → %d objects (%.2fs)", prefix, len(paths), elapsed)
        return paths

    # ── Lifecycle ───────────────────────────────────────────────────

    async def close(self, grace_period: float = 10.0) -> None:
        """Graceful shutdown: wait for pending writes, then close Greenfield.

        Writes that complete successfully are removed from WAL.
        Writes that are cancelled remain in WAL for replay on next startup.

        Args:
            grace_period: Max seconds to wait for pending Greenfield writes
                to finish before cancelling them. Default 10s.
        """
        if self._pending_tasks:
            n = len(self._pending_tasks)
            logger.info(
                "Waiting up to %.0fs for %d pending Greenfield write(s)...",
                grace_period, n,
            )
            done, pending = await asyncio.wait(
                self._pending_tasks, timeout=grace_period,
            )
            if done:
                logger.info("%d background write(s) completed successfully", len(done))
            if pending:
                logger.warning(
                    "%d write(s) still pending after %.0fs — cancelling (WAL will retry on next start)",
                    len(pending), grace_period,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

            # Only truncate WAL if ALL writes completed (nothing cancelled)
            if not pending:
                self._wal.truncate()
                logger.info("All writes completed — WAL cleared")
            else:
                logger.info("WAL preserved: %d cancelled write(s) will retry on next startup",
                            len(pending))

            self._pending_tasks.clear()

        self._wal.close()

        try:
            await self._greenfield.close()
        except Exception as e:
            logger.debug("Greenfield close error (ignored): %s", e)
