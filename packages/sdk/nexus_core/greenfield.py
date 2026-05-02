"""
Greenfield Storage Client — decentralized object storage for Nexus.

Handles bulk data (sessions, artifacts, task payloads) that's too large
for on-chain storage. BSC stores SHA-256 content hashes as pointers;
this client stores/retrieves the actual data on BNB Greenfield.

Architecture:
  - Uses Greenfield SP (Storage Provider) REST API directly
  - No Go shared library or greenfield-python-sdk required
  - Auth: GNFD1-ECDSA (secp256k1 signing via eth_account from web3.py)
  - Content-hash addressed: SHA-256(data) = object key

Greenfield Testnet:
  - Chain ID: 5600
  - RPC: https://gnfd-testnet-fullnode-tendermint-us.bnbchain.org
  - Storage Provider: https://gnfd-testnet-sp1.bnbchain.org

Greenfield Mainnet:
  - Chain ID: 1017
  - RPC: https://greenfield-chain.bnbchain.org
  - Storage Provider: https://gnfd-sp1.bnbchain.org
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

logger = logging.getLogger("nexus_core.greenfield")

# ── Network Presets ──────────────────────────────────────────────────

GREENFIELD_NETWORKS = {
    "testnet": {
        "chain_id": "greenfield_5600-1",
        "chain_rpc": "https://gnfd-testnet-fullnode-tendermint-us.bnbchain.org",
        "sp_endpoint": "https://gnfd-testnet-sp1.bnbchain.org",
    },
    "mainnet": {
        "chain_id": "greenfield_1017-1",
        "chain_rpc": "https://greenfield-chain.bnbchain.org",
        "sp_endpoint": "https://gnfd-sp1.bnbchain.org",
    },
}


class GreenfieldClient:
    """
    Client for BNB Greenfield decentralized object storage.

    Uses the SP REST API directly — no Go shared library needed.
    Only requires web3.py (for eth_account ECDSA signing).

    Usage:
        # Real Greenfield (Phase 2)
        client = GreenfieldClient(
            private_key="0x...",
            bucket_name="nexus-agent-state",
            network="testnet",
        )

        # Local fallback (Phase 1)
        client = GreenfieldClient(local_dir=".nexus_state/data")

    Content-hash addressing:
        hash = await client.put(data)    # SHA-256 of data → hex string
        data = await client.get(hash)    # Retrieve by hash
    """

    def __init__(
        self,
        private_key: Optional[str] = None,
        bucket_name: Optional[str] = None,
        network: str = "testnet",
        sp_endpoint: Optional[str] = None,
        local_dir: Optional[str] = None,
    ):
        # Per-agent bucket is mandatory in chain mode — see SDK
        # ARCHITECTURE.md. Use ``nexus_core.bucket_for_agent(token_id)``
        # to compute. There is intentionally no shared-bucket fallback;
        # every agent owns its own Greenfield bucket. Local mode (no
        # private_key, local_dir set) skips Greenfield entirely so
        # bucket_name there is irrelevant.
        if private_key and not bucket_name:
            raise ValueError(
                "GreenfieldClient: bucket_name is required in chain mode. "
                "Use nexus_core.bucket_for_agent(token_id) to compute "
                "the canonical per-agent bucket name."
            )

        self._private_key = private_key
        self._bucket_name = bucket_name or ""
        self._network = network
        self._account = None
        self._address = None

        # ── Lazy bucket auto-create (post-S4 architectural fix) ─────────
        # Greenfield buckets used to be created upfront via the legacy
        # server-side ``sync_anchor._RealAnchorBackend.put_json`` path,
        # which called ``ensure_bucket()`` before its first write. After
        # that path was retired in S4, every chain-mode caller of this
        # client (twin's ChainBackend, future SDK consumers) had no
        # bucket-creation step on its critical path — the result was
        # silent "No such bucket: unknown request" failures on the very
        # first put for any newly-registered agent.
        #
        # We now run ``ensure_bucket()`` lazily on first put/get, gated
        # by ``_bucket_verified``. The lock prevents two concurrent puts
        # from racing through bucket creation. Failure during
        # ensure_bucket leaves the flag unset, so a transient RPC blip
        # gets retried on the next call instead of permanently disabling
        # writes for the lifetime of this client.
        self._bucket_verified: bool = False
        self._bucket_ensure_lock = asyncio.Lock()

        # Determine mode: real Greenfield or local fallback
        if local_dir and not private_key:
            self._mode = "local"
            self._local_dir = Path(local_dir)
            self._local_dir.mkdir(parents=True, exist_ok=True)
            self._sp_endpoint = None
            logger.info("Greenfield client: local mode at %s", self._local_dir)
        elif private_key:
            try:
                from eth_account import Account
                self._account = Account.from_key(private_key)
                self._address = self._account.address
                self._mode = "greenfield"

                # SP endpoint: explicit > network preset
                net = GREENFIELD_NETWORKS.get(network, GREENFIELD_NETWORKS["testnet"])
                self._sp_endpoint = (sp_endpoint or net["sp_endpoint"]).rstrip("/")
                self._chain_rpc = net["chain_rpc"].rstrip("/")
                self._chain_id = net["chain_id"]

                logger.info(
                    "Greenfield client: REST mode, account=%s, sp=%s",
                    self._address, self._sp_endpoint,
                )
            except ImportError:
                # web3/eth_account not installed
                fallback_dir = Path(".nexus_state") / "data"
                fallback_dir.mkdir(parents=True, exist_ok=True)
                self._mode = "local"
                self._local_dir = fallback_dir
                self._sp_endpoint = None
                print("web3/eth_account not installed, falling back to local storage")
                print("  Install with: pip install web3")
        else:
            raise ValueError(
                "GreenfieldClient requires either private_key (for Greenfield) "
                "or local_dir (for local fallback)"
            )

    # ── GNFD1-ECDSA Request Signing ─────────────────────────────────

    def _make_expiry_timestamp(self, seconds: int = 3600) -> str:
        """Create ISO 8601 expiry timestamp."""
        dt = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _sign_request(
        self,
        method: str,
        url: str,
        headers: dict,
    ) -> str:
        """
        GNFD1-ECDSA request signing.

        Canonical request format (similar to AWS SigV4):
            HTTP_METHOD\n
            CANONICAL_URI\n
            CANONICAL_QUERY\n
            CANONICAL_HEADERS\n
            SIGNED_HEADERS

        Then: personal_sign(canonical_request) → hex signature.
        Uses Ethereum personal_sign: keccak256("\\x19Ethereum Signed Message:\\n" + len + msg)
        """
        from urllib.parse import urlparse
        from eth_account.messages import encode_defunct

        parsed = urlparse(url)
        canonical_uri = parsed.path or "/"
        canonical_query = parsed.query or ""

        # Sort headers: include x-gnfd-* plus content-type, host
        signed_header_names = []
        canonical_header_lines = []

        header_map = {}
        for k, v in headers.items():
            lower_k = k.lower()
            if lower_k.startswith("x-gnfd-") or lower_k in ("content-type", "host"):
                header_map[lower_k] = v.strip()

        for k in sorted(header_map.keys()):
            signed_header_names.append(k)
            canonical_header_lines.append(f"{k}:{header_map[k]}")

        canonical_headers = "\n".join(canonical_header_lines)
        signed_headers = ";".join(signed_header_names)

        # Build canonical request
        canonical_request = (
            f"{method.upper()}\n"
            f"{canonical_uri}\n"
            f"{canonical_query}\n"
            f"{canonical_headers}\n"
            f"{signed_headers}"
        )

        # Sign using Ethereum personal_sign (encode_defunct + sign_message)
        signable = encode_defunct(text=canonical_request)
        signed = self._account.sign_message(signable)
        signature = signed.signature.hex()

        return signature

    def _auth_headers(
        self, method: str, url: str,
        extra_headers: dict = None, body: bytes = None,
    ) -> dict:
        """Build full request headers with GNFD1-ECDSA auth."""
        from urllib.parse import urlparse

        parsed = urlparse(url)
        headers = {
            "Host": parsed.netloc,
            "X-Gnfd-Expiry-Timestamp": self._make_expiry_timestamp(),
            "X-Gnfd-User-Address": self._address,
        }
        # Content hash header for integrity
        if body is not None:
            headers["X-Gnfd-Content-Sha256"] = hashlib.sha256(body).hexdigest()

        if extra_headers:
            headers.update(extra_headers)

        signature = self._sign_request(method, url, headers)
        headers["Authorization"] = f"GNFD1-ECDSA, Signature={signature}"
        return headers

    # ── HTTP helpers ─────────────────────────────────────────────────

    def _get_http_session(self):
        """Get or create a requests Session."""
        if not hasattr(self, "_http_session") or self._http_session is None:
            import requests
            self._http_session = requests.Session()
        return self._http_session

    def _sp_url(self, path: str) -> str:
        """Build full SP URL."""
        return f"{self._sp_endpoint}{path}"

    # ── Content Hash ─────────────────────────────────────────────────

    @staticmethod
    def content_hash(data: bytes) -> str:
        """Compute SHA-256 content hash."""
        return hashlib.sha256(data).hexdigest()

    # ── Put / Get ────────────────────────────────────────────────────

    async def put(self, data: bytes, object_path: Optional[str] = None) -> str:
        """
        Store bytes in Greenfield. Returns SHA-256 content hash.

        The content hash serves as both the object key and the
        on-chain pointer stored in AgentStateExtension / TaskStateManager.

        Args:
            data: Raw bytes to store.
            object_path: Optional structured path override.
                If provided, the object is stored at this path instead
                of the default ``rune/{hash}``. The content hash is still
                returned and stored on BSC for integrity verification.
                Example: ``rune/agents/42/tasks/abc123/7f3a...json``
        """
        chash = self.content_hash(data)

        if self._mode == "local":
            return self._put_local(data, chash, object_path=object_path)
        else:
            return await self._put_greenfield(data, chash, object_path=object_path)

    async def get(self, content_hash: str, object_path: Optional[str] = None) -> Optional[bytes]:
        """
        Retrieve bytes from Greenfield by content hash.

        Args:
            content_hash: The SHA-256 content hash (used for integrity check).
            object_path: Optional structured path. If provided, fetches from
                this path instead of ``rune/{content_hash}``.
        """
        if self._mode == "local":
            return self._get_local(content_hash, object_path=object_path)
        else:
            return await self._get_greenfield(content_hash, object_path=object_path)

    # ── JSON convenience ─────────────────────────────────────────────

    async def put_json(self, obj: Any, object_path: Optional[str] = None) -> str:
        """Serialize object to JSON, store in Greenfield, return hash."""
        data = json.dumps(obj, default=str, sort_keys=True).encode("utf-8")
        return await self.put(data, object_path=object_path)

    async def get_json(self, content_hash: str, object_path: Optional[str] = None) -> Optional[Any]:
        """Load from Greenfield by hash, deserialize JSON."""
        data = await self.get(content_hash, object_path=object_path)
        if data is None:
            return None
        return json.loads(data.decode("utf-8"))

    # ── Bucket management ────────────────────────────────────────────

    async def ensure_bucket(self) -> bool:
        """
        Check if the storage bucket exists on Greenfield.

        Uses the Greenfield chain LCD API (not SP) for reliable detection.

        Note: Bucket creation requires a blockchain transaction.
        If the bucket doesn't exist, this returns False with instructions.
        Create the bucket first using DCellar (https://dcellar.io)
        or the gnfd-cmd CLI tool.
        """
        if self._mode == "local":
            return True

        session = self._get_http_session()

        # Use chain LCD API to check bucket — this is authoritative
        # (SP endpoints return 200 even for non-existent buckets)
        chain_url = (
            f"{self._chain_rpc}/greenfield/storage/head_bucket/{self._bucket_name}"
        )
        try:
            resp = session.get(chain_url, timeout=15)
            logger.debug(
                "Bucket check (chain): HTTP %d, body=%s",
                resp.status_code, resp.text[:300],
            )
            if resp.status_code == 200:
                data = resp.json()
                bucket_info = data.get("bucket_info", {})
                if bucket_info.get("bucket_name") == self._bucket_name:
                    owner = bucket_info.get("owner", "unknown")
                    logger.info(
                        "Bucket '%s' exists on chain (owner=%s)",
                        self._bucket_name, owner,
                    )
                    return True
            # Bucket doesn't exist — try to create it automatically
            logger.info(
                "Bucket '%s' not found on chain — attempting auto-create...",
                self._bucket_name,
            )
            created = await self.create_bucket()
            if created:
                # Verify it's on chain now
                import time as _time
                _time.sleep(5)  # Wait for chain finality
                resp2 = session.get(chain_url, timeout=15)
                if resp2.status_code == 200:
                    data2 = resp2.json()
                    if data2.get("bucket_info", {}).get("bucket_name") == self._bucket_name:
                        logger.info("Bucket '%s' created and verified!", self._bucket_name)
                        return True
            logger.warning(
                "Bucket '%s' could not be created. Create it manually:\n"
                "  Option 1: DCellar web UI — https://dcellar.io\n"
                "  Option 2: node scripts/create_greenfield_bucket.mjs %s",
                self._bucket_name, self._bucket_name,
            )
            return False
        except Exception as e:
            logger.warning("Bucket check failed: %s", e)
            return False

    async def create_bucket(self) -> bool:
        """
        Create the bucket on Greenfield using the JS SDK (via subprocess).

        Requires: node, npm, @bnb-chain/greenfield-js-sdk installed.
        The JS SDK handles EIP-712 signing + SP approval + chain broadcast.
        """
        if self._mode == "local":
            return True

        import subprocess
        import os

        # Find the script (prefer .cjs for Node v22+ compatibility)
        script_candidates = [
            os.path.join(os.path.dirname(__file__), "..", "scripts", "create_greenfield_bucket.cjs"),
            os.path.join(os.getcwd(), "scripts", "create_greenfield_bucket.cjs"),
            os.path.join(os.path.dirname(__file__), "..", "scripts", "create_greenfield_bucket.mjs"),
            os.path.join(os.getcwd(), "scripts", "create_greenfield_bucket.mjs"),
        ]
        script_path = None
        for candidate in script_candidates:
            if os.path.exists(candidate):
                script_path = os.path.abspath(candidate)
                break

        if not script_path:
            logger.error(
                "create_greenfield_bucket.mjs not found. "
                "Run from the project root directory."
            )
            return False

        env = os.environ.copy()
        if self._private_key:
            env["NEXUS_PRIVATE_KEY"] = self._private_key
        env["NEXUS_GREENFIELD_BUCKET"] = self._bucket_name
        env["NEXUS_GREENFIELD_NETWORK"] = self._network

        logger.info("Creating bucket '%s' via JS SDK...", self._bucket_name)
        try:
            result = subprocess.run(
                ["node", script_path, self._bucket_name],
                capture_output=True, text=True,
                timeout=60, env=env,
            )
            print(result.stdout)
            if result.returncode != 0:
                print(result.stderr)
                return False
            return True
        except FileNotFoundError:
            logger.error("Node.js not found. Install Node.js to create buckets.")
            return False
        except subprocess.TimeoutExpired:
            logger.error("Bucket creation timed out")
            return False

    async def check_balance(self) -> dict:
        """
        Check the account's BNB balance on the Greenfield chain.

        Returns dict with:
          - address: account address
          - balance_bnb: BNB balance as string
          - balance_wei: raw balance in wei
          - has_funds: True if balance > 0
        """
        if self._mode == "local":
            return {
                "address": "local-mode",
                "balance_bnb": "N/A",
                "balance_wei": "0",
                "has_funds": True,
            }

        session = self._get_http_session()
        result = {
            "address": self._address,
            "balance_bnb": "0",
            "balance_wei": "0",
            "has_funds": False,
        }

        # Query via Greenfield chain LCD (Cosmos REST API)
        balance_url = (
            f"{self._chain_rpc}/cosmos/bank/v1beta1/balances/{self._address}"
        )
        try:
            resp = session.get(balance_url, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                balances = data.get("balances", [])
                for bal in balances:
                    if bal.get("denom") == "BNB":
                        wei = int(bal["amount"])
                        result["balance_wei"] = str(wei)
                        result["balance_bnb"] = f"{wei / 1e18:.6f}"
                        result["has_funds"] = wei > 0
                        break
                logger.info(
                    "Greenfield balance: %s BNB (%s)",
                    result["balance_bnb"], self._address,
                )
            else:
                logger.warning(
                    "Balance query failed HTTP %d: %s",
                    resp.status_code, resp.text[:200],
                )
        except Exception as e:
            logger.warning("Balance query failed: %s", e)

        return result

    # ── Local fallback implementation ────────────────────────────────

    def _put_local(self, data: bytes, chash: str, object_path: Optional[str] = None) -> str:
        """Store data as local file (Phase 1).

        If object_path is provided, also create a symlink / copy at the
        structured path so local browsing mirrors the Greenfield layout.
        """
        # Always store by content hash (canonical)
        canonical = self._local_dir / chash
        if not canonical.exists():
            tmp = canonical.with_suffix(".tmp")
            with open(tmp, "wb") as f:
                f.write(data)
            tmp.rename(canonical)
            logger.debug("Stored %d bytes locally -> %s", len(data), chash[:16])

        # Also store at structured path for browsability
        if object_path:
            structured = self._local_dir / object_path
            structured.parent.mkdir(parents=True, exist_ok=True)
            if not structured.exists():
                # Symlink to canonical file (saves disk space)
                try:
                    structured.symlink_to(canonical.resolve())
                except (OSError, NotImplementedError):
                    # Windows or cross-device: copy instead
                    import shutil
                    shutil.copy2(canonical, structured)
                logger.debug("Linked %s -> %s", object_path, chash[:16])

        return chash

    def _get_local(self, content_hash: str, object_path: Optional[str] = None) -> Optional[bytes]:
        """Load data from local file (Phase 1).

        Tries structured path first (if given), then falls back to
        canonical content-hash path.
        """
        # Try structured path first
        if object_path:
            structured = self._local_dir / object_path
            if structured.exists():
                with open(structured, "rb") as f:
                    data = f.read()
                logger.debug("Loaded %d bytes locally (structured) <- %s", len(data), object_path)
                return data

        # Canonical content-hash path
        canonical = self._local_dir / content_hash
        if not canonical.exists():
            return None
        with open(canonical, "rb") as f:
            data = f.read()
        logger.debug("Loaded %d bytes locally <- %s", len(data), content_hash[:16])
        return data

    # ── Real Greenfield implementation (via JS SDK helper) ─────────

    def _find_ops_script(self) -> Optional[str]:
        """Find the greenfield_ops.cjs helper script."""
        import os
        candidates = [
            os.path.join(os.path.dirname(__file__), "..", "scripts", "greenfield_ops.cjs"),
            os.path.join(os.getcwd(), "scripts", "greenfield_ops.cjs"),
        ]
        for c in candidates:
            if os.path.exists(c):
                return os.path.abspath(c)
        return None

    # ── Persistent daemon (module-level singleton) ──
    _daemon_proc = None      # subprocess.Popen
    _daemon_lock = None      # threading.Lock — guards spawn + every read/write
    _daemon_req_id = 0
    # Process-wide kill switch. Once set, ``_ensure_daemon`` short-
    # circuits and fresh writes go straight to local fallback. Set by
    # ``shutdown()`` during teardown so the post-Ctrl-C burst of
    # write-behind tasks doesn't keep respawning the daemon.
    _shutting_down = False

    def _find_daemon_script(self) -> Optional[str]:
        """Find the greenfield_daemon.cjs script."""
        candidates = [
            os.path.join(os.path.dirname(__file__), "..", "scripts", "greenfield_daemon.cjs"),
            os.path.join(os.getcwd(), "scripts", "greenfield_daemon.cjs"),
        ]
        for c in candidates:
            if os.path.exists(c):
                return os.path.abspath(c)
        return None

    def _ensure_daemon(self) -> bool:
        """Start the persistent Node.js daemon if not running.

        Race-safe: the spawn is performed under the daemon lock so
        two concurrent first-callers can't both spawn a process. Old
        code released the lock before checking, which is what
        produced "Starting Greenfield daemon" twice in a row in
        production logs.
        """
        import subprocess, threading

        if GreenfieldClient._daemon_lock is None:
            GreenfieldClient._daemon_lock = threading.Lock()

        # Refuse to spawn during shutdown — the burst of pending
        # write-behind tasks would otherwise keep restarting the
        # daemon while the parent process is mid-teardown.
        if GreenfieldClient._shutting_down:
            return False

        # Fast-path: already running, skip the lock.
        if (GreenfieldClient._daemon_proc is not None
                and GreenfieldClient._daemon_proc.poll() is None):
            return True

        with GreenfieldClient._daemon_lock:
            # Re-check under the lock. Another thread may have just
            # spawned the daemon while we were waiting.
            if GreenfieldClient._shutting_down:
                return False
            if (GreenfieldClient._daemon_proc is not None
                    and GreenfieldClient._daemon_proc.poll() is None):
                return True
            GreenfieldClient._daemon_proc = None  # died (or never started)

            script = self._find_daemon_script()
            if not script:
                return False

            env = os.environ.copy()
            if self._private_key:
                env["NEXUS_PRIVATE_KEY"] = self._private_key
            env["NEXUS_GREENFIELD_BUCKET"] = self._bucket_name
            env["NEXUS_GREENFIELD_NETWORK"] = self._network

            logger.info("Starting Greenfield daemon: %s", script)
            GreenfieldClient._daemon_proc = subprocess.Popen(
                ["node", script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                bufsize=0,
            )

            # Wait for "ready" — daemon may print Node.js banners or
            # warnings before the JSON ready line. Skip non-JSON lines
            # until we hit one we can parse, capping at 10 attempts so
            # a totally garbled startup doesn't hang forever.
            try:
                for _ in range(10):
                    ready_line = GreenfieldClient._daemon_proc.stdout.readline()
                    if not ready_line:
                        break  # EOF — daemon died during startup
                    text = ready_line.decode(errors="replace").strip()
                    if not text or not text.startswith("{"):
                        # Banner / warning / blank — keep reading.
                        logger.debug("Greenfield daemon stdout pre-ready: %s", text[:120])
                        continue
                    ready = json.loads(text)
                    if ready.get("ok"):
                        logger.info(
                            "Greenfield daemon ready: %d SPs, bucket=%s",
                            ready.get("sps", 0), ready.get("bucket", "?"),
                        )
                        return True
                    logger.error("Greenfield daemon init failed: %s", ready.get("error"))
                    break
            except Exception as e:
                logger.error("Greenfield daemon startup error: %s", e)

            try:
                GreenfieldClient._daemon_proc.kill()
            except Exception:
                pass
            GreenfieldClient._daemon_proc = None
            return False

    @classmethod
    def shutdown(cls) -> None:
        """Mark the singleton as shut down and kill the daemon.

        Safe to call from teardown paths — pending write-behind tasks
        that race past this call will see ``_shutting_down`` and fall
        back to local storage instead of trying to respawn the
        daemon. The WAL on the chain backend then ensures these
        writes are replayed on the next process start.
        """
        cls._shutting_down = True
        proc = cls._daemon_proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    proc.kill()
        except Exception:
            pass
        cls._daemon_proc = None

    def _run_js_op(self, op: str, object_name: str, hex_data: str = None, timeout: int = None) -> dict:
        """Send a command to the persistent daemon (or fall back to legacy subprocess).

        Thread-safe: uses a threading.Lock for stdin/stdout coordination.
        """
        # Try daemon first
        if self._ensure_daemon():
            return self._run_daemon_op(op, object_name, hex_data, timeout)

        # Fall back to legacy per-call subprocess
        return self._run_js_op_legacy(op, object_name, hex_data, timeout)

    def _run_daemon_op(self, op: str, object_name: str, hex_data: str = None, timeout: int = None) -> dict:
        """Send command to daemon via stdin, read response from stdout.

        Timeout policy: the JS daemon's axios timeout is 60s for puts
        (delegateUploadObject). We give ourselves a small grace window
        on top (75s) so an upload that finishes just past axios's
        deadline still makes it back. Old 120s default kept calls
        hanging long after the SP had given up.
        """
        if timeout is None:
            timeout = 15 if op in ("get", "head", "list") else 75

        with GreenfieldClient._daemon_lock:
            # Check daemon is still alive
            if GreenfieldClient._daemon_proc is None or GreenfieldClient._daemon_proc.poll() is not None:
                return {"ok": False, "error": "Daemon not running (shutdown)"}

            GreenfieldClient._daemon_req_id += 1
            req_id = GreenfieldClient._daemon_req_id

            cmd = {"id": req_id, "op": op, "object": object_name}
            if hex_data:
                cmd["hex"] = hex_data

            try:
                line = json.dumps(cmd) + "\n"
                GreenfieldClient._daemon_proc.stdin.write(line.encode())
                GreenfieldClient._daemon_proc.stdin.flush()

                # Read response with timeout. Skip non-JSON lines —
                # the daemon may emit warnings / banners on stdout
                # that aren't part of the protocol. Old code blindly
                # json.loads()'d the first line and broke on those
                # ("Expecting property name enclosed in double quotes").
                import threading
                result = [None]
                error = [None]

                def read_resp():
                    try:
                        for _ in range(20):  # cap on garbage tolerance
                            resp_line = GreenfieldClient._daemon_proc.stdout.readline()
                            if not resp_line:
                                error[0] = "EOF from daemon"
                                return
                            text = resp_line.decode(errors="replace").strip()
                            if not text:
                                continue
                            if not text.startswith("{"):
                                logger.debug(
                                    "Greenfield daemon non-JSON line: %s", text[:120],
                                )
                                continue
                            try:
                                result[0] = json.loads(text)
                                return
                            except json.JSONDecodeError as je:
                                logger.debug(
                                    "Greenfield daemon malformed JSON, skipping: %s (%s)",
                                    text[:120], je,
                                )
                                continue
                        error[0] = "no parseable response after 20 lines"
                    except Exception as e:
                        error[0] = str(e)

                t = threading.Thread(target=read_resp, daemon=True)
                t.start()
                t.join(timeout=timeout)

                if t.is_alive():
                    logger.warning(
                        "Daemon response timeout (%ds) for %s %s — killing daemon, "
                        "writes will retry via WAL on restart",
                        timeout, op, object_name,
                    )
                    try:
                        GreenfieldClient._daemon_proc.kill()
                    except Exception:
                        pass
                    GreenfieldClient._daemon_proc = None
                    return {"ok": False, "error": f"Daemon timeout ({timeout}s)"}

                if error[0]:
                    return {"ok": False, "error": error[0]}

                return result[0] or {"ok": False, "error": "No response from daemon"}

            except (BrokenPipeError, OSError) as e:
                logger.info("Daemon pipe broken, will restart on next call: %s", e)
                GreenfieldClient._daemon_proc = None
                return {"ok": False, "error": f"Daemon pipe error: {e}"}

    def _run_js_op_legacy(self, op: str, object_name: str, hex_data: str = None, timeout: int = None) -> dict:
        """Legacy: spawn a new Node.js process per call (fallback if daemon unavailable)."""
        import subprocess

        script = self._find_ops_script()
        if not script:
            return {"ok": False, "error": "greenfield_ops.cjs not found"}

        env = os.environ.copy()
        if self._private_key:
            env["NEXUS_PRIVATE_KEY"] = self._private_key
        env["NEXUS_GREENFIELD_BUCKET"] = self._bucket_name
        env["NEXUS_GREENFIELD_NETWORK"] = self._network

        cmd = ["node", script, op, object_name]
        if hex_data:
            cmd.append(hex_data)

        if timeout is None:
            timeout = 30 if op in ("get", "head") else 120

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout, env=env,
            )
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            if stdout:
                lines = stdout.split("\n")
                for line in reversed(lines):
                    line = line.strip()
                    if line.startswith("{"):
                        try:
                            return json.loads(line)
                        except json.JSONDecodeError:
                            continue
                return {"ok": False, "error": f"No valid JSON in JS output: {stdout[:200]}"}
            if result.returncode != 0:
                return {"ok": False, "error": stderr[:300] or f"exit code {result.returncode}"}
            return {"ok": False, "error": "No output from JS helper"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"JS helper timed out ({timeout}s)"}
        except FileNotFoundError:
            return {"ok": False, "error": "Node.js not found — install Node.js 18+"}

    async def _ensure_bucket_once(self) -> bool:
        """Lazy, idempotent bucket-existence-and-auto-create.

        Called at the top of every Greenfield read/write so a freshly-
        registered agent's first PUT auto-creates its bucket instead of
        failing with "No such bucket: unknown request". Once verified,
        subsequent calls are a single boolean check (no RPC).

        Returns True iff the bucket exists (or was just created); False
        if creation failed. The caller decides how to handle False —
        ``_put_greenfield`` falls back to local storage, ``_get_greenfield``
        returns None.
        """
        if self._bucket_verified:
            return True
        async with self._bucket_ensure_lock:
            if self._bucket_verified:
                return True
            try:
                ok = await self.ensure_bucket()
            except Exception as e:
                logger.warning(
                    "ensure_bucket raised for %s: %s", self._bucket_name, e,
                )
                return False
            if ok:
                self._bucket_verified = True
            return ok

    async def _put_greenfield(self, data: bytes, chash: str, object_path: Optional[str] = None) -> str:
        """
        Store data on Greenfield via JS SDK helper.

        The JS SDK handles CreateObject tx + SP upload.
        When object_path is provided, stores at BOTH the structured path
        (for browsability) AND the canonical ``rune/{hash}`` path (so reads
        by content_hash always work without needing to reconstruct the path).
        """
        canonical = f"rune/{chash}"

        # Lazily ensure bucket exists. If creation fails, fall back to
        # local storage rather than spamming "No such bucket" forever.
        if not await self._ensure_bucket_once():
            logger.warning(
                "Greenfield bucket %s unavailable — falling back to local for %s",
                self._bucket_name, object_path or canonical,
            )
            self._ensure_local_fallback()
            return self._put_local(data, chash, object_path=object_path)

        # Run blocking JS subprocess in a thread to keep event loop free
        result = await asyncio.to_thread(self._run_js_op, "put", canonical, data.hex())
        if not result.get("ok"):
            error = result.get("error", "unknown")
            # Distinguish "Greenfield is slow / unavailable" (transient,
            # we have a local fallback) from "fallback also blew up"
            # (genuinely critical). The vast majority of timeouts are
            # the SP being briefly slow under load; logging them at
            # WARNING with "failed" wording is misleading because the
            # operation is about to succeed via local fallback.
            level = (
                logging.INFO
                if "Timeout" in error or "ECONNABORTED" in error
                else logging.WARNING
            )
            logger.log(
                level,
                "Greenfield put slow/unavailable, using local fallback "
                "(path=%s, %d bytes): %s",
                object_path or canonical, len(data), error,
            )
            self._ensure_local_fallback()
            return self._put_local(data, chash, object_path=object_path)

        # Also store at structured path (for DCellar browsing)
        if object_path and object_path != canonical:
            result2 = await asyncio.to_thread(self._run_js_op, "put", object_path, data.hex())
            if not result2.get("ok"):
                logger.debug("Structured path write skipped: %s", result2.get("error", ""))

        logger.info("Stored %d bytes on Greenfield -> %s", len(data), object_path or canonical)
        return chash

    async def _get_greenfield(self, content_hash: str, object_path: Optional[str] = None) -> Optional[bytes]:
        """
        Retrieve data from Greenfield via JS SDK helper.

        Always tries the canonical ``rune/{hash}`` path first (guaranteed
        to exist if the object was written by this SDK). Falls back to the
        structured object_path, then to local storage.
        """
        # If bucket isn't verified yet, ensure it. A get on a brand-new
        # agent (bucket not yet created) is fine — just return None.
        if not await self._ensure_bucket_once():
            return None

        # 1. Try canonical path (always written)
        canonical = f"rune/{content_hash}"
        result = await asyncio.to_thread(self._run_js_op, "get", canonical)
        if result.get("ok") and result.get("data_hex"):
            data = bytes.fromhex(result["data_hex"])
            logger.info("Loaded %d bytes from Greenfield <- %s", len(data), canonical)
            return data

        # 2. Try structured path if different
        if object_path and object_path != canonical:
            result2 = await asyncio.to_thread(self._run_js_op, "get", object_path)
            if result2.get("ok") and result2.get("data_hex"):
                data = bytes.fromhex(result2["data_hex"])
                logger.info("Loaded %d bytes from Greenfield <- %s", len(data), object_path)
                return data

        error = result.get("error", "unknown")
        if error != "not_found":
            logger.warning("Greenfield get failed: %s", error)

        # 3. Try local fallback
        if hasattr(self, "_local_dir") and self._local_dir:
            return self._get_local(content_hash, object_path=object_path)

        return None

    # ── Helpers ──────────────────────────────────────────────────────

    def _ensure_local_fallback(self):
        """Set up local fallback directory if not already present."""
        if not hasattr(self, "_local_dir") or self._local_dir is None:
            self._local_dir = Path(".nexus_state") / "data"
            self._local_dir.mkdir(parents=True, exist_ok=True)

    # ── List / browse ─────────────────────────────────────────────────

    async def list_objects(self, prefix: str = "rune/") -> list[dict]:
        """
        List objects under a given prefix.

        Returns a list of dicts: [{"key": "rune/agents/42/...", "size": 1024}, ...]
        Supports both local and Greenfield modes.

        Args:
            prefix: Object key prefix to list (e.g. "rune/agents/42/").
        """
        if self._mode == "local":
            return self._list_local(prefix)
        else:
            return await self._list_greenfield(prefix)

    def _list_local(self, prefix: str) -> list[dict]:
        """List objects in local storage matching a prefix."""
        results = []
        base = self._local_dir / prefix
        if not base.exists():
            # Also try to match the prefix as a path fragment
            # (e.g. prefix="rune/agents/42" but local stores at "agents/42")
            stripped = prefix.lstrip("rune/") if prefix.startswith("rune/") else prefix
            base = self._local_dir / stripped
            if not base.exists():
                return results

        for p in sorted(base.rglob("*")):
            if p.is_file():
                rel = p.relative_to(self._local_dir)
                results.append({
                    "key": str(rel),
                    "size": p.stat().st_size,
                    "modified": p.stat().st_mtime,
                })
        return results

    async def _list_greenfield(self, prefix: str) -> list[dict]:
        """
        List objects on Greenfield SP matching a prefix.

        Uses the Greenfield chain LCD API (not SP) because the SP
        list-objects-v2 endpoint requires specific auth and may return
        non-XML responses. The chain API reliably returns JSON.
        """
        results = []

        # Strategy 1: Use the JS helper (most reliable — handles auth)
        list_result = await asyncio.to_thread(self._run_js_op, "list", prefix)
        if list_result.get("ok") and list_result.get("objects"):
            for obj in list_result["objects"]:
                results.append({
                    "key": obj.get("key", obj.get("object_name", "")),
                    "size": obj.get("size", 0),
                })
            return results

        # Strategy 2: Fall back to SP REST with XML parsing
        session = self._get_http_session()
        url = self._sp_url(
            f"/{self._bucket_name}?list-objects-v2&prefix={quote(prefix)}&max-keys=1000"
        )
        headers = self._auth_headers("GET", url)
        try:
            resp = session.get(url, headers=headers, timeout=15)
            if resp.status_code == 200 and resp.text.strip().startswith("<"):
                import xml.etree.ElementTree as ET
                root = ET.fromstring(resp.text)
                ns = ""
                if root.tag.startswith("{"):
                    ns = root.tag.split("}")[0] + "}"
                for contents in root.findall(f"{ns}Contents"):
                    key_elem = contents.find(f"{ns}Key")
                    size_elem = contents.find(f"{ns}Size")
                    if key_elem is not None:
                        results.append({
                            "key": key_elem.text,
                            "size": int(size_elem.text) if size_elem is not None else 0,
                        })
            elif resp.status_code == 200:
                # Non-XML response (JSON or empty) — try JSON
                try:
                    data = resp.json()
                    for obj in data.get("objects", data.get("GfSpListObjectsByBucketNameResponse", {}).get("Objects", [])):
                        obj_info = obj.get("object_info", obj) if isinstance(obj, dict) else {}
                        results.append({
                            "key": obj_info.get("object_name", ""),
                            "size": int(obj_info.get("payload_size", 0)),
                        })
                except (ValueError, KeyError):
                    logger.debug("List objects: non-XML/JSON response, skipping")
            else:
                logger.debug("List objects HTTP %d for prefix %s", resp.status_code, prefix)
        except Exception as e:
            logger.debug("List objects SP fallback failed: %s", e)

        return results

    # ── Stats / diagnostics ──────────────────────────────────────────

    async def object_count(self) -> int:
        """Count objects in storage (for diagnostics)."""
        if self._mode == "local":
            return len(list(self._local_dir.iterdir()))

        # Use SP list-objects endpoint
        url = self._sp_url(f"/{self._bucket_name}?object-list&prefix=rune/&max-keys=1000")
        headers = self._auth_headers("GET", url)

        session = self._get_http_session()
        try:
            resp = session.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                # Parse XML or JSON response for object count
                text = resp.text
                # SP returns XML with <Contents> elements
                count = text.count("<Key>")
                return count
            return -1
        except Exception:
            return -1

    async def close(self):
        """Clean up HTTP sessions and the shared daemon.

        Sets the singleton's ``_shutting_down`` flag first so any
        write-behind tasks racing past this call fall through to
        local fallback instead of trying to respawn the daemon.
        """
        # Stop accepting new daemon work + kill the running daemon.
        type(self).shutdown()

        if hasattr(self, "_http_session") and self._http_session:
            self._http_session.close()
            self._http_session = None

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def bucket_name(self) -> str:
        return self._bucket_name

    @property
    def sp_endpoint(self) -> Optional[str]:
        return self._sp_endpoint
