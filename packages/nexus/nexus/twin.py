"""
Rune Nexus — Self-evolving AI avatar powered by Rune Protocol SDK.

Startup flow:
  1. If chain mode (private_key provided):
     a. Connect to BSC via nexus_core.testnet() / nexus_core.mainnet()
     b. Register ERC-8004 identity (one-time, auto-detected)
     c. All data persists to BSC + Greenfield
  2. If local mode (no private_key):
     a. Use nexus_core.local()
     b. All data persists to local files
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import nexus_core
from nexus_core import AgentRuntime, Checkpoint, LLMClient, LLMProvider

from .config import TwinConfig
from .evolution.engine import EvolutionEngine
from .tools.base import ExtendedToolRegistry

logger = logging.getLogger(__name__)


class DigitalTwin:
    """
    Rune Nexus: A self-evolving digital avatar with persistent memory on BNB Chain.

    Usage:
        # Local mode (dev)
        twin = await DigitalTwin.create("my-twin", llm_api_key="AIza...")

        # Chain mode (production — registers ERC-8004 on first startup)
        twin = await DigitalTwin.create(
            "my-twin",
            llm_api_key="AIza...",
            private_key="0x...",
            network="testnet",
        )

        response = await twin.chat("Hello, remember I like sushi")
        await twin.close()
    """

    def __init__(self, config: TwinConfig, rune: AgentRuntime, llm: LLMClient):
        self.config = config
        self.rune = rune
        self.llm = llm

        self._thread_id: str = ""
        self._messages: list[dict] = []
        self._turn_count: int = 0

        # ERC-8004 identity (set after on-chain registration)
        self._erc8004_agent_id: Optional[int] = None
        self._chain_client = None

        # Tool registry — tools are registered after creation via register_tool()
        self.tools = ExtendedToolRegistry()
        self._file_reader = None  # Set by _register_default_tools

        # Skill manager — external skills (Binance Skills Hub compatible)
        from nexus_core.skills import SkillManager
        self.skills = SkillManager(base_dir=config.base_dir)

        # DPM: Event log (append-only, SDK layer) + Projection (Nexus layer)
        from nexus_core.memory import EventLog, CuratedMemory, EventLogCompactor
        self.event_log = EventLog(base_dir=config.base_dir, agent_id=config.agent_id)
        self.curated_memory = CuratedMemory(base_dir=config.base_dir)
        # Compactor initialized after LLM (needs projection_fn)
        self._compactor = None

        # ABC: Contract enforcement engine
        from nexus_core.contracts import ContractEngine, ContractSpec, DriftScore, Rule
        contract_path = Path(config.base_dir) / "contracts" / "system.yaml"
        user_rules_path = Path(config.base_dir) / "contracts" / "user_rules.json"
        self._contract_spec = ContractSpec.from_yaml(contract_path) if contract_path.exists() else ContractSpec()
        self._contract_spec.load_user_rules(user_rules_path)
        self.contract = ContractEngine(self._contract_spec, event_log=self.event_log)
        self.drift = DriftScore(
            compliance_weight=self._contract_spec.compliance_weight,
            distributional_weight=self._contract_spec.distributional_weight,
            warning_threshold=self._contract_spec.warning_threshold,
            intervention_threshold=self._contract_spec.intervention_threshold,
            observation_window=self._contract_spec.observation_window,
        )
        self._user_rules_path = user_rules_path

        # Projection initialized after LLM is ready (in _initialize)
        self._projection = None

        # Event callback for on-chain activity notifications
        # Signature: on_event(event_type: str, detail: dict) -> None
        self.on_event: Optional[Callable[[str, dict], None]] = None

        self.evolution = EvolutionEngine(
            rune=rune,
            agent_id=config.agent_id,
            llm_fn=llm.complete,
            default_persona=config.base_persona,
            agent_name=config.name,
        )
        self._initialized = False
        self._bg_tasks: set[asyncio.Task] = set()

    @classmethod
    async def create(
        cls,
        name: str = "Twin",
        owner: str = "",
        agent_id: str = "digital-twin",
        llm_provider: str = "gemini",
        llm_api_key: str = "",
        llm_model: str = "",
        base_dir: str = ".nexus",
        # ── Tools ──
        enable_tools: bool = True,
        tavily_api_key: str = "",
        jina_api_key: str = "",
        # ── Chain mode ──
        private_key: str = "",
        network: str = "testnet",
        rpc_url: str = "",
        agent_state_address: str = "",
        task_manager_address: str = "",
        identity_registry_address: str = "",
        # Required in chain mode. Use ``nexus_core.bucket_for_agent``.
        # No shared-bucket default — per-agent isolation is mandatory.
        greenfield_bucket: str = "",
        # When the caller has already registered the agent on chain (e.g.
        # server's TwinManager runs ``bootstrap_chain_identity`` before
        # creating the twin), pass the assigned ERC-8004 token id here.
        # The twin will pre-populate its identity cache file with this
        # value and skip its own background ``_register_identity`` task,
        # avoiding the double-registration race that mints a second token
        # and leaves the bucket name out-of-sync with the on-chain state.
        cached_agent_id: Optional[int] = None,
        cached_wallet: str = "",
    ) -> "DigitalTwin":
        provider = LLMProvider(llm_provider)
        use_chain = bool(private_key)
        if use_chain and not greenfield_bucket:
            raise ValueError(
                "DigitalTwin.create: chain mode requires greenfield_bucket. "
                "Compute via nexus_core.bucket_for_agent(token_id)."
            )

        config = TwinConfig(
            agent_id=agent_id,
            name=name,
            owner=owner,
            llm_provider=provider,
            llm_api_key=llm_api_key,
            llm_model=llm_model or "",
            base_dir=base_dir,
            use_chain=use_chain,
            private_key=private_key,
            network=network,
            rpc_url=rpc_url,
            agent_state_address=agent_state_address,
            task_manager_address=task_manager_address,
            identity_registry_address=identity_registry_address,
            greenfield_bucket=greenfield_bucket,
        )

        # ── Create Rune provider ──
        if use_chain:
            chain_kwargs = {}
            if rpc_url:
                chain_kwargs["rpc_url"] = rpc_url
            if agent_state_address:
                chain_kwargs["agent_state_address"] = agent_state_address
            if task_manager_address:
                chain_kwargs["task_manager_address"] = task_manager_address
            if identity_registry_address:
                chain_kwargs["identity_registry_address"] = identity_registry_address
            if greenfield_bucket:
                chain_kwargs["greenfield_bucket"] = greenfield_bucket

            if "mainnet" in network:
                rune = nexus_core.mainnet(private_key=private_key, **chain_kwargs)
            else:
                rune = nexus_core.testnet(private_key=private_key, **chain_kwargs)
            logger.info("Chain mode: BSC %s + Greenfield", network)
        else:
            rune = nexus_core.local(base_dir=base_dir)
            logger.info("Local mode: data stored in %s", base_dir)

        llm = LLMClient(
            provider=config.llm_provider,
            api_key=config.llm_api_key,
            model=config.llm_model,
        )

        twin = cls(config=config, rune=rune, llm=llm)

        # ── Pre-seed identity cache if caller already registered ──────
        # When TwinManager's bootstrap_chain_identity has already minted
        # the ERC-8004 token, write it into the twin's local cache file
        # BEFORE _initialize() runs. _initialize then loads the cache
        # synchronously, sets _erc8004_agent_id, and skips firing the
        # _register_identity background task. Net effect: zero extra
        # on-chain registration, single source of truth for the bucket
        # name (token_id from server's DB).
        if use_chain and cached_agent_id is not None:
            try:
                twin._save_identity_cache(int(cached_agent_id), cached_wallet or "")
                logger.info(
                    "Pre-seeded ERC-8004 identity cache: agentId=%s (skip self-register)",
                    cached_agent_id,
                )
            except Exception as e:
                # Non-fatal: if cache write fails, twin will fall back to
                # its own registration path. Worst case: a duplicate
                # registration tx — which is exactly the bug we're trying
                # to avoid, but the system still works.
                logger.warning("Pre-seed identity cache failed: %s", e)

        # ── Register default tools ──
        if enable_tools:
            twin._register_default_tools(
                tavily_api_key=tavily_api_key,
                jina_api_key=jina_api_key,
            )
            # Tell SkillEvolver to never learn skills that share names with
            # registered tools — prevents the name collision bug where the
            # LLM role-plays tool use instead of making actual function calls.
            twin.evolution.skills._blocked_names = set(twin.tools.tool_names)

        await twin._initialize()
        return twin

    async def _initialize(self):
        if self._initialized:
            return

        # ── Step 1: Identity — use cache or fire-and-forget chain registration ──
        if self.config.use_chain and self.config.private_key:
            cached = self._load_identity_cache()
            if cached:
                # Instant: load from local cache, no chain call
                self._erc8004_agent_id = cached["erc8004_id"]
                self._emit("identity_found", {
                    "agent_id": self.config.agent_id,
                    "erc8004_id": cached["erc8004_id"],
                    "network": self.config.network,
                    "wallet": cached.get("wallet", ""),
                    "source": "cache",
                })
                logger.info(
                    "ERC-8004 identity loaded from cache: agentId=%s",
                    cached["erc8004_id"],
                )
                # Create chain_client in background (non-blocking)
                self._bg_task("chain-client-init", self._init_chain_client_async())
            else:
                # No cache: fire-and-forget registration in background
                self._emit("identity_check", {
                    "agent_id": self.config.agent_id,
                    "network": self.config.network,
                    "source": "background",
                })
                self._bg_task("identity-register", self._register_identity())

        # ── Step 2: Initialize evolution engine (persona + skills + knowledge + memory) ──
        # Memory preloading is included in initialize() to avoid cold-start timeouts
        # during the first chat() call. Greenfield reads can take 3-10s on cold start,
        # so we give generous time here (10s). If it still times out, memories will
        # lazy-load on next access (slightly delayed first response).
        try:
            await asyncio.wait_for(self.evolution.initialize(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.info("Evolution init timed out (10s) — loading in background with defaults")
            self._bg_task("evolution-init", self.evolution.initialize())

        # ── Step 2b: Clean up any learned skills that conflict with tools ──
        # This handles the case where a previous session learned "web_search" etc.
        # as a text skill before the tool was registered.
        if self.tools:
            for tool_name in self.tools.tool_names:
                if tool_name in self.evolution.skills._skills_cache:
                    logger.info(
                        "Removing conflicting learned skill '%s' (shadows registered tool)",
                        tool_name,
                    )
                    del self.evolution.skills._skills_cache[tool_name]
                    self.evolution.skills._dirty = True

        # ── Step 3: Restore last session ──
        # Try loading from cache (instant). If Greenfield is slow, start fresh
        # and recover old session knowledge in background.
        session_restored = False
        try:
            checkpoints = await asyncio.wait_for(
                self.rune.sessions.list_checkpoints(
                    agent_id=self.config.agent_id, limit=1,
                ),
                timeout=10.0,  # generous: daemon startup + Greenfield read
            )
        except asyncio.TimeoutError:
            logger.info("Session restore timed out (10s) — starting fresh, recovering in background")
            checkpoints = []
            # Background: load old session and extract memories from it
            # (don't overwrite current messages — just mine the knowledge)
            self._bg_task("session-recovery", self._recover_old_session())

        if checkpoints:
            last = checkpoints[0]
            self._thread_id = last.thread_id
            self._messages = last.state.get("messages", [])
            self._turn_count = last.state.get("turn_count", 0)
            session_restored = True
            logger.info(
                "Resumed session %s (%d messages)",
                self._thread_id, len(self._messages),
            )

        if not session_restored:
            self._thread_id = f"session_{uuid.uuid4().hex[:8]}"
            self._messages = []
            self._turn_count = 0

        # Initialize DPM projection + compactor
        from .evolution.projection import ProjectionMemory
        from nexus_core.memory import EventLogCompactor
        self._projection = ProjectionMemory(self.event_log, self.llm.complete)
        self._compactor = EventLogCompactor(
            self.event_log, self.curated_memory,
            projection_fn=self._projection.project,
        )

        self._initialized = True

    # ── Tool Management ──────────────────────────────────────────

    def _register_default_tools(
        self, tavily_api_key: str = "", jina_api_key: str = "",
    ) -> None:
        """Register the default tool set (web search + URL reader).

        Tools are only registered if their dependencies (httpx) are available.
        Missing dependencies are logged but don't prevent startup.
        """
        try:
            from nexus_core.tools.web_search import WebSearchTool
            from nexus_core.tools.url_reader import URLReaderTool

            tavily_key = tavily_api_key or os.environ.get("TAVILY_API_KEY", "")
            jina_key = jina_api_key or os.environ.get("JINA_API_KEY", "")

            self.tools.register(WebSearchTool(api_key=tavily_key))
            self.tools.register(URLReaderTool(api_key=jina_key))

            # File generator — agent can create files for user download
            from nexus_core.tools import FileGeneratorTool
            output_dir = Path(self.config.base_dir).parent / "outputs"
            output_dir.mkdir(parents=True, exist_ok=True)
            self.tools.register(FileGeneratorTool(output_dir=output_dir))

            # File reader — agent reads uploaded file content on demand
            from nexus_core.tools import ReadUploadedFileTool
            self._file_reader = ReadUploadedFileTool()
            self.tools.register(self._file_reader)

            logger.info(
                "Registered %d tools: %s",
                len(self.tools), ", ".join(self.tools.tool_names),
            )
        except Exception as e:
            logger.debug("Default tool registration skipped: %s", e)

    def register_tool(self, tool) -> None:
        """Register a custom tool.

        Args:
            tool: A BaseTool instance to register. Must have name, description,
                  parameters, and execute() method.

        Example:
            from nexus_core.tools import BaseTool, ToolResult

            class MyTool(BaseTool):
                name = "my_tool"
                description = "Does something custom"
                parameters = {"type": "object", "properties": {}, "required": []}

                async def execute(self, **kwargs):
                    return ToolResult(output="done")

            twin.register_tool(MyTool())
        """
        self.tools.register(tool)

    # ── Background task helpers ──────────────────────────────────

    def _bg_task(self, label: str, coro) -> None:
        """Fire-and-forget a coroutine as a tracked background task.

        The coroutine is wrapped in a safety wrapper that catches CancelledError
        and exceptions. If no event loop is available, the coroutine is explicitly
        closed to avoid 'was never awaited' warnings.
        """
        async def _safe():
            try:
                await coro
            except asyncio.CancelledError:
                logger.debug("[%s] cancelled (shutdown)", label)
            except Exception as e:
                logger.warning("[%s] failed: %s", label, e)

        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(_safe())
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
        except RuntimeError:
            # No event loop — explicitly close the coroutine to prevent
            # "coroutine was never awaited" warnings from Python GC
            coro.close()
            logger.debug("No event loop for background task %s", label)

    async def _init_chain_client_async(self) -> None:
        """Initialize chain_client in background (non-blocking startup)."""
        try:
            from nexus_core.chain import BSCClient
            cfg = self.config
            net_prefix = "MAINNET" if "mainnet" in cfg.network else "TESTNET"
            rpc_url = cfg.rpc_url or os.environ.get(f"NEXUS_{net_prefix}_RPC", "")
            agent_state_addr = cfg.agent_state_address or os.environ.get(f"NEXUS_{net_prefix}_AGENT_STATE_ADDRESS", "")
            identity_registry_addr = (
                cfg.identity_registry_address
                or os.environ.get(f"NEXUS_{net_prefix}_IDENTITY_REGISTRY", "")
                or os.environ.get(f"NEXUS_{net_prefix}_IDENTITY_REGISTRY_ADDRESS", "")
            )
            task_manager_addr = cfg.task_manager_address or os.environ.get(f"NEXUS_{net_prefix}_TASK_MANAGER_ADDRESS", "")

            if rpc_url:
                self._chain_client = BSCClient(
                    rpc_url=rpc_url,
                    private_key=cfg.private_key,
                    agent_state_address=agent_state_addr or None,
                    task_manager_address=task_manager_addr or None,
                    identity_registry_address=identity_registry_addr or None,
                    network=f"bsc_{cfg.network}",
                )
        except Exception as e:
            logger.debug("Background chain client init failed: %s", e)

    # ── ERC-8004 Registration ───────────────────────────────────

    # ── ERC-8004 Identity Cache ──────────────────────────────────

    def _identity_cache_path(self) -> Path:
        """Local file that caches the ERC-8004 agent ID after first registration."""
        cache_dir = Path(os.environ.get("NEXUS_CACHE_DIR", ".rune_cache"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        safe_id = self.config.agent_id.replace("/", "_").replace("\\", "_")
        return cache_dir / f"identity_{safe_id}_{self.config.network}.json"

    def _load_identity_cache(self) -> Optional[dict]:
        """Load cached identity from local file. Returns None on miss."""
        try:
            path = self._identity_cache_path()
            if path.exists():
                data = json.loads(path.read_text())
                if data.get("agent_id") == self.config.agent_id:
                    return data
        except Exception as e:
            logger.debug("Identity cache read failed: %s", e)
        return None

    def _save_identity_cache(self, erc8004_id: int, wallet: str) -> None:
        """Save identity to local cache after successful registration/verification."""
        try:
            path = self._identity_cache_path()
            path.write_text(json.dumps({
                "agent_id": self.config.agent_id,
                "erc8004_id": erc8004_id,
                "network": self.config.network,
                "wallet": wallet,
            }))
        except Exception as e:
            logger.debug("Identity cache write failed: %s", e)

    async def _register_identity(self):
        """
        Background task: register ERC-8004 identity on BSC.

        Called as fire-and-forget from _initialize() when no local cache exists.
        On success, caches the identity locally so next startup is instant.
        """
        try:
            from nexus_core.chain import BSCClient
        except ImportError:
            logger.warning("web3 not installed — skipping ERC-8004 registration")
            return

        cfg = self.config
        net_prefix = "MAINNET" if "mainnet" in cfg.network else "TESTNET"

        rpc_url = cfg.rpc_url or os.environ.get(f"NEXUS_{net_prefix}_RPC", "")
        agent_state_addr = cfg.agent_state_address or os.environ.get(f"NEXUS_{net_prefix}_AGENT_STATE_ADDRESS", "")
        identity_registry_addr = (
            cfg.identity_registry_address
            or os.environ.get(f"NEXUS_{net_prefix}_IDENTITY_REGISTRY", "")
            or os.environ.get(f"NEXUS_{net_prefix}_IDENTITY_REGISTRY_ADDRESS", "")
        )
        task_manager_addr = cfg.task_manager_address or os.environ.get(f"NEXUS_{net_prefix}_TASK_MANAGER_ADDRESS", "")

        if not rpc_url or not identity_registry_addr:
            logger.warning("No BSC RPC or Identity Registry configured — skipping registration")
            return

        try:
            chain_client = BSCClient(
                rpc_url=rpc_url,
                private_key=cfg.private_key,
                agent_state_address=agent_state_addr or None,
                task_manager_address=task_manager_addr or None,
                identity_registry_address=identity_registry_addr,
                network=f"bsc_{cfg.network}",
            )
            self._chain_client = chain_client

            agent_uri = f"rune-twin://{cfg.agent_id}"
            numeric_id = abs(hash(cfg.agent_id)) % (2**32)

            already_exists = chain_client.agent_exists(numeric_id)
            success, actual_id = chain_client.ensure_agent_registered(
                agent_id=numeric_id,
                agent_name=agent_uri,
            )

            if success:
                self._erc8004_agent_id = actual_id
                self._save_identity_cache(actual_id, chain_client.address)

                event = "identity_found" if already_exists else "identity_registered"
                self._emit(event, {
                    "agent_id": cfg.agent_id,
                    "erc8004_id": actual_id,
                    "network": cfg.network,
                    "wallet": chain_client.address,
                })
                logger.info(
                    "ERC-8004 identity %s: agentId=%s (name=%s)",
                    "found" if already_exists else "registered",
                    actual_id, cfg.agent_id,
                )
            else:
                self._emit("sync_error", {
                    "component": "ERC-8004",
                    "error": "Registration failed — run /sync to retry",
                })
                logger.warning("ERC-8004 registration failed for %s — local-only mode", cfg.agent_id)

        except Exception as e:
            self._emit("sync_error", {
                "component": "ERC-8004",
                "error": str(e),
                "hint": "Run /sync to retry",
            })
            logger.warning("ERC-8004 registration error (background): %s", e)

    # ── Background Session Recovery ─────────────────────────────

    async def _recover_old_session(self) -> None:
        """
        Background: load old session from Greenfield and extract memories.

        Does NOT overwrite current _messages — the user is already chatting
        in a new session. Instead, feeds old messages to the evolution engine
        so the knowledge isn't lost.

        Total timeout: 60s (if Greenfield is completely down, don't block forever).
        """
        try:
            # list_checkpoints is in-memory only, but the data might need
            # loading from backend — wrap everything in a generous timeout
            checkpoints = await asyncio.wait_for(
                self.rune.sessions.list_checkpoints(
                    agent_id=self.config.agent_id, limit=1,
                ),
                timeout=30.0,
            )
            if not checkpoints:
                logger.debug("No old session found for recovery")
                return

            old = checkpoints[0]
            old_messages = old.state.get("messages", [])
            if not old_messages:
                return

            logger.info(
                "Recovered old session %s (%d messages) — extracting memories",
                old.thread_id, len(old_messages),
            )

            # Feed old conversation to evolution engine for memory extraction
            try:
                await asyncio.wait_for(
                    self.evolution.after_conversation_turn(
                        old_messages,
                        max_memories=8,
                    ),
                    timeout=30.0,
                )
                self._emit("memory_stored", {
                    "count": len(old_messages),
                    "items": [f"Recovered from previous session ({old.thread_id})"],
                    "storage": "Greenfield + BSC" if self.config.use_chain else "local",
                })
            except asyncio.TimeoutError:
                logger.warning("Memory extraction from recovered session timed out")
            except Exception as e:
                logger.warning("Memory extraction from recovered session failed: %s", e)

        except asyncio.TimeoutError:
            logger.warning("Session recovery timed out (30s)")
        except Exception as e:
            logger.warning("Background session recovery failed: %s", e)

    # ── Event System ───────────────────────────────────────────

    def _emit(self, event_type: str, detail: dict = None):
        """Emit an on-chain activity event for CLI display."""
        if self.on_event:
            try:
                self.on_event(event_type, detail or {})
            except Exception:
                pass  # never let event callbacks break the main flow

    # ── Identity Context for LLM ───────────────────────────────

    def _build_identity_context(self) -> str:
        """Build on-chain identity context so the twin knows its own registration details.

        This is injected into the system prompt during chat() so the twin can
        answer questions like 'what's your agent ID?', 'what chain are you on?',
        'what's your wallet address?', etc.
        """
        parts = ["## Your On-Chain Identity"]
        parts.append(f"- Agent Name: {self.config.name}")
        parts.append(f"- Agent ID: {self.config.agent_id}")

        if self.config.use_chain:
            parts.append(f"- Network: BNB Chain ({self.config.network})")
            parts.append(f"- Storage: BNB Greenfield (bucket: {self.config.greenfield_bucket})")

            if self._erc8004_agent_id is not None:
                parts.append(f"- ERC-8004 Token ID: {self._erc8004_agent_id}")
                parts.append("- Registration Status: REGISTERED on-chain")
            else:
                parts.append("- ERC-8004 Token ID: Pending registration")

            if self._chain_client:
                parts.append(f"- Wallet Address: {self._chain_client.address}")

            # Include contract addresses if available
            cached = self._load_identity_cache()
            if cached and cached.get("wallet"):
                if not self._chain_client:
                    parts.append(f"- Wallet Address: {cached['wallet']}")

            cfg = self.config
            net_prefix = "MAINNET" if "mainnet" in cfg.network else "TESTNET"
            agent_state_addr = cfg.agent_state_address or os.environ.get(f"NEXUS_{net_prefix}_AGENT_STATE_ADDRESS", "")
            identity_registry_addr = (
                cfg.identity_registry_address
                or os.environ.get(f"NEXUS_{net_prefix}_IDENTITY_REGISTRY", "")
                or os.environ.get(f"NEXUS_{net_prefix}_IDENTITY_REGISTRY_ADDRESS", "")
            )
            if agent_state_addr:
                parts.append(f"- AgentState Contract: {agent_state_addr}")
            if identity_registry_addr:
                parts.append(f"- Identity Registry Contract: {identity_registry_addr}")
        else:
            parts.append("- Storage: Local (not connected to chain)")

        return "\n".join(parts)

    # ── Chat ─────────────────────────────────────────────────────

    async def chat(self, user_message: str) -> str:
        if not self._initialized:
            await self._initialize()

        cmd_result = await self._handle_command(user_message)
        if cmd_result is not None:
            return cmd_result

        # ABC: Pre-check (hard governance)
        pre = self.contract.pre_check(user_message)
        if pre.blocked:
            return f"[Contract violation] {pre.reason}"

        # DPM: Append user message to event log (instant, no LLM)
        self.event_log.append("user_message", user_message, session_id=self._thread_id)

        # DPM Smart Context Strategy (inspired by Claude Cowork compact):
        #
        # Trigger      │ Context source          │ Extra LLM calls
        # ─────────────┼─────────────────────────┼─────────────────
        # <10 events   │ _messages (full history) │ 0
        # 10-50 events │ CuratedMemory snapshot   │ 0
        # >50 events   │ CuratedMemory snapshot   │ 0
        # Recall ask   │ Projection (EventLog)    │ 1
        # Auto-compact │ Projection → CuratedMem  │ 1 (background)
        #
        # Auto-compact: when event log exceeds COMPACT_THRESHOLD chars,
        # do a background projection and update CuratedMemory.
        # Similar to Cowork's "compact_boundary" at token limits.
        COMPACT_THRESHOLD = 30000  # chars in event log before auto-compact
        COMPACT_INTERVAL = 20     # minimum turns between compacts

        event_count = self.event_log.count()
        evo_context = ""

        # Check if user is explicitly asking for recall
        recall_keywords = ["之前聊", "聊过什么", "记得", "remember", "recall", "previous", "earlier", "last time", "上次"]
        needs_recall = any(kw in user_message.lower() for kw in recall_keywords)

        if needs_recall and self._projection and event_count > 5:
            # Explicit recall — do projection (1 LLM call, synchronous)
            try:
                evo_context = await asyncio.wait_for(
                    self._projection.project(user_message, budget=2000),
                    timeout=8.0,
                )
            except asyncio.TimeoutError:
                logger.warning("Projection timed out (8s)")
                evo_context = ""
        elif event_count > 10:
            # Medium+ session — use curated memory snapshot (0ms)
            evo_context = self.curated_memory.get_prompt_context()

        # Auto-compact: delegate threshold check to SDK's EventLogCompactor
        if self._compactor and self._compactor.should_compact(self._turn_count):
            logger.info("Auto-compact triggered at turn %d", self._turn_count)
            self._bg_task("auto-compact", self._auto_compact())
        # else: short session — LLM sees full _messages history, no extra context needed

        registered_tool_names = set(self.tools.tool_names) if self.tools else set()

        persona = self.evolution.get_current_persona()
        system = persona

        # Inject current date/time
        from datetime import datetime
        system += f"\n\n## Current Date\nToday is {datetime.now().strftime('%B %d, %Y')} ({datetime.now().strftime('%A')})."

        # Inject capability awareness — tell the agent what it CAN do
        capabilities = [
            "You have access to web search, URL reading, and file generation tools via function calling.",
            "You can generate files (HTML, markdown, CSV, JSON) using the generate_file tool. "
            "For documents like reports or articles, generate a styled HTML file. "
            "The user can view and download generated files directly in the chat.",
            "You can install new skills from the LobeHub Skills Marketplace (100K+ skills) to gain new capabilities.",
            "You can install MCP servers from the LobeHub MCP Marketplace (27K+ servers) for tool integrations.",
            "When a user asks for a capability you don't have (e.g., 'generate PDF', 'search the web', 'edit images'), "
            "tell them you can search for and install a skill or MCP server that provides this capability.",
            "You remember everything from previous conversations via your event log.",
            "Files uploaded by the user are stored in your memory. For large files, use the read_uploaded_file tool to read specific sections. "
            "You can also search within files using read_uploaded_file(filename, search='keyword').",
        ]
        installed = self.skills.names
        if installed:
            capabilities.append(f"Currently installed skills: {', '.join(installed)}")
        system += "\n\n## Your Capabilities\n" + "\n".join(f"- {c}" for c in capabilities)

        # Inject on-chain identity so the twin knows its own registration details
        identity_ctx = self._build_identity_context()
        if identity_ctx:
            system += f"\n\n{identity_ctx}"

        # DPM: Inject projected memory (or curated fallback)
        if evo_context:
            system += f"\n\n## Memory (projected from event log)\n{evo_context[:3000]}"

        # Inject installed skill INDEX (names + descriptions only, not full instructions)
        skill_context = self.skills.get_prompt_context()
        if skill_context:
            system += skill_context

        # When tools are registered, add explicit instructions so the LLM
        # uses function calling instead of generating text about tools.
        active_tools = self.tools if self.tools else None
        if active_tools:
            tool_list = ", ".join(self.tools.tool_names)
            system += (
                f"\n\n## Tool Use Instructions\n"
                f"You have access to the following tools via function calling: {tool_list}.\n"
                f"When you need to search the web, read a URL, or use any tool, "
                f"you MUST invoke the tool function — do NOT generate text describing "
                f"what you would search for or pretend to have search results. "
                f"Call the tool and wait for its response."
            )

        self._messages.append({"role": "user", "content": user_message})

        response = await self.llm.chat(
            messages=self._messages[-20:],
            system=system,
            tools=active_tools,
        )

        self._messages.append({"role": "assistant", "content": response})
        self._turn_count += 1

        # ABC: Post-check (invariants + governance)
        post = self.contract.post_check(response)
        if post.hard_violation:
            # Hard violation — log and regenerate (simplified: append warning)
            self.event_log.append("contract_violation", f"Hard: {post.reason}", session_id=self._thread_id)
            response = f"{response}\n\n⚠️ [Contract notice: {post.reason}]"

        # Update drift score
        hard_score = post.details.get("hard_compliance", 1.0)
        soft_score = post.details.get("soft_compliance", 1.0)
        self.drift.update(hard_score, soft_score, "chat")

        # DPM: Append assistant response to event log (instant, no LLM)
        self.event_log.append("assistant_response", response, session_id=self._thread_id)

        # ── Post-response work runs in background — user sees response immediately ──
        # With DPM, this is much lighter: just session save + optional skill detection
        self._bg_task(
            f"post-turn-{self._turn_count}",
            self._post_response_work(),
        )

        return response

    async def _auto_compact(self) -> None:
        """Background: delegate to SDK's EventLogCompactor.

        Compact result is appended to EventLog (syncs to Greenfield)
        and updates local CuratedMemory (derived view).

        We surface the curated snapshot text in the emit's ``content``
        field so server-side mirrors (and any other on_event consumer)
        can render it as a "memory" entry without re-reading the SDK
        EventLog. ``memory_count`` / ``user_count`` are kept for
        backward compatibility.
        """
        if self._compactor:
            ok = await self._compactor.compact(session_id=self._thread_id)
            if ok:
                snapshot = ""
                try:
                    snapshot = self.curated_memory.get_prompt_context() or ""
                except Exception:
                    snapshot = ""
                self._emit("memory_compact", {
                    "memory_count": self.curated_memory.memory_count,
                    "user_count": self.curated_memory.user_count,
                    "content": snapshot,
                    "summary": snapshot,
                    "char_count": len(snapshot),
                })

    async def _post_response_work(self) -> None:
        """Background: memory extraction, session save, reflection. Never blocks chat.

        IMPORTANT: Memory extraction runs FIRST, session save runs AFTER.
        Previous ordering (save → extract) had a fatal race: if a crash occurred
        between save and extract, the session checkpoint was persisted but the
        memories from that turn were permanently lost. By extracting first, we
        ensure memories are stored before the session checkpoint references them.
        """
        storage = "Greenfield + BSC" if self.config.use_chain else "local"

        # ── 1. Extract and store memories FIRST ──
        self._emit("memory_extract", {"turn": self._turn_count})
        try:
            evo_result = await self.evolution.after_conversation_turn(
                self._messages,
                max_memories=self.config.max_memories_per_conversation,
            )
            if evo_result.get("actions"):
                for action in evo_result["actions"]:
                    if action["type"] == "memory_extraction":
                        self._emit("memory_stored", {
                            "count": action["count"],
                            "items": action["items"],
                            "storage": storage,
                        })
                    elif action["type"] == "skill_learning":
                        for skill_detail in action.get("details", []):
                            self._emit("skill_learned", {
                                "skill": skill_detail.get("skill_name", ""),
                                "lesson": skill_detail.get("lesson", ""),
                                "source": skill_detail.get("source", "conversation"),
                                "storage": storage,
                            })
            # Also update curated memory with extracted insights
            if evo_result.get("actions"):
                for action in evo_result["actions"]:
                    if action["type"] == "memory_extraction":
                        for item in action.get("items", []):
                            if isinstance(item, str):
                                cat = "memory"
                                content = item
                            elif isinstance(item, dict):
                                cat = item.get("category", "fact")
                                content = item.get("content", str(item))
                            else:
                                continue
                            if cat in ("preference", "style", "relationship"):
                                self.curated_memory.add_user_info(content)
                            else:
                                self.curated_memory.add_memory(content)
        except Exception as e:
            logger.warning("Background memory extraction failed: %s", e)

        # ── 2. Persist session checkpoint AFTER memories are stored ──
        self._emit("session_save", {
            "thread_id": self._thread_id,
            "turn": self._turn_count,
            "messages": len(self._messages),
            "storage": storage,
        })
        try:
            await self._save_session()
        except Exception as e:
            logger.warning("Background session save failed: %s", e)

        # ── Self-reflection & persona evolution ──
        if (
            self.config.persona_evolution_enabled
            and self._turn_count > 0
            and self._turn_count % self.config.reflection_after_every_n_turns == 0
        ):
            self._emit("persona_reflect", {
                "turn": self._turn_count,
                "trigger": f"every {self.config.reflection_after_every_n_turns} turns",
            })
            try:
                reflection = await self.evolution.trigger_reflection()
                pe = reflection.get("persona_evolution", {})
                if pe.get("version"):
                    self._emit("persona_evolved", {
                        "version": pe["version"],
                        "changes": pe.get("changes", ""),
                        "confidence": pe.get("confidence", 0),
                        "storage": storage,
                    })
            except Exception as e:
                logger.warning("Background reflection failed: %s", e)

    async def _handle_command(self, message: str) -> Optional[str]:
        msg = message.strip().lower()
        if msg == "/stats":
            return await self._format_stats()
        elif msg == "/memories":
            return await self._format_memories()
        elif msg == "/skills":
            return await self._format_skills()
        elif msg == "/history":
            return await self._format_evolution_history()
        elif msg == "/evolve":
            result = await self.evolution.trigger_reflection()
            return f"Self-reflection complete:\n{json.dumps(result, indent=2, ensure_ascii=False)}"
        elif msg == "/identity":
            return self._format_identity()
        elif msg == "/new":
            return await self.new_session()
        elif msg == "/social":
            return await self._format_social_map()
        elif msg == "/impressions":
            return await self._format_impressions()
        elif msg.startswith("/discover"):
            parts = message.strip().split(maxsplit=1)
            interest = parts[1] if len(parts) > 1 else None
            return await self._format_discover(interest)
        elif msg.startswith("/gossip"):
            parts = message.strip().split(maxsplit=1)
            target = parts[1] if len(parts) > 1 else None
            if not target:
                return "Usage: /gossip <agent_id> [topic]"
            # Parse "agent_id topic" or just "agent_id"
            gossip_parts = target.split(maxsplit=1)
            agent = gossip_parts[0]
            topic = gossip_parts[1] if len(gossip_parts) > 1 else ""
            return await self._start_gossip_command(agent, topic)
        elif msg == "/sync":
            return await self._sync_chain()
        elif msg == "/help":
            return (
                "Commands:\n"
                "  /stats       — Show evolution statistics\n"
                "  /memories    — List all memories\n"
                "  /skills      — List learned skills\n"
                "  /history     — Show persona evolution history\n"
                "  /evolve      — Trigger manual self-reflection\n"
                "  /identity    — Show on-chain identity (ERC-8004)\n"
                "  /sync        — Force sync identity & state to chain\n"
                "  /social      — View your social graph summary\n"
                "  /impressions — View impressions you've formed\n"
                "  /discover    — Search for agents (optionally by interest)\n"
                "  /gossip      — Start gossip: /gossip <agent_id> [topic]\n"
                "  /new         — Start a new session\n"
                "  /help        — Show this help\n"
            )
        return None

    # ── Session Management ───────────────────────────────────────

    async def _save_session(self):
        cp = Checkpoint(
            thread_id=self._thread_id,
            agent_id=self.config.agent_id,
            state={
                "messages": self._messages[-50:],
                "turn_count": self._turn_count,
            },
            metadata={
                "twin_name": self.config.name,
                "persona_version": self.evolution.persona._version,
                "erc8004_agent_id": self._erc8004_agent_id,
                "storage_mode": "chain" if self.config.use_chain else "local",
            },
        )
        await self.rune.sessions.save_checkpoint(cp)

    async def new_session(self) -> str:
        self._thread_id = f"session_{uuid.uuid4().hex[:8]}"
        self._messages = []
        return f"New session started: {self._thread_id}. Memories and skills carry over."

    # ── Task Delegation ──────────────────────────────────────────

    async def create_task(
        self, description: str, task_type: str = "general", assignee: str = "",
    ) -> str:
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        await self.rune.tasks.create_task(
            task_id=task_id,
            agent_id=self.config.agent_id,
            metadata={
                "description": description,
                "task_type": task_type,
                "assignee": assignee or self.config.agent_id,
                "status": "created",
            },
        )
        return task_id

    async def complete_task(
        self, task_id: str, outcome: str = "success",
        strategy: str = "", feedback: str = "",
    ) -> dict:
        task = await self.rune.tasks.get_task(task_id)
        if not task:
            return {"error": f"Task {task_id} not found"}

        meta = task.get("metadata", {})
        await self.rune.tasks.update_task(
            task_id=task_id,
            state={"outcome": outcome, "feedback": feedback},
            status="completed",
        )
        learning = await self.evolution.skills.record_task_outcome(
            task_type=meta.get("task_type", "general"),
            description=meta.get("description", ""),
            strategy=strategy,
            outcome=outcome,
            feedback=feedback,
        )
        if learning:
            storage = "Greenfield + BSC" if self.config.use_chain else "local"
            self._emit("skill_learned", {
                "skill": learning.get("skill_name", "unknown"),
                "lesson": learning.get("lesson", ""),
                "storage": storage,
            })
        return learning or {}

    # ── Formatting ───────────────────────────────────────────────

    def _format_identity(self) -> str:
        """Format on-chain identity information."""
        lines = [f"=== {self.config.name} On-Chain Identity ==="]

        if not self.config.use_chain:
            lines.append("Storage mode: LOCAL (no chain connection)")
            lines.append("Set NEXUS_PRIVATE_KEY in .env to enable chain mode.")
            return "\n".join(lines)

        lines.append(f"Storage mode: CHAIN ({self.config.network})")
        lines.append(f"Agent ID: {self.config.agent_id}")

        if self._erc8004_agent_id is not None:
            lines.append(f"ERC-8004 Token ID: {self._erc8004_agent_id}")
            lines.append("Identity Status: REGISTERED")
        else:
            lines.append("ERC-8004 Token ID: Not registered")
            lines.append("Identity Status: UNREGISTERED")

        if self._chain_client:
            lines.append(f"Wallet: {self._chain_client.address}")
            lines.append(f"BSC Network: {self.config.network}")
            lines.append(f"Greenfield Bucket: {self.config.greenfield_bucket}")

            # Show on-chain state if available
            if self._erc8004_agent_id is not None:
                try:
                    has_state = self._chain_client.has_state(self._erc8004_agent_id)
                    lines.append(f"On-chain state: {'YES' if has_state else 'NO (first run)'}")
                except Exception:
                    lines.append("On-chain state: Unable to query")

        return "\n".join(lines)

    async def _format_stats(self) -> str:
        stats = await self.evolution.get_full_stats()
        lines = [
            f"=== {self.config.name} Evolution Stats ===",
            f"Session: {self._thread_id}",
            f"Total turns: {stats['turn_count']}",
            f"Storage: {'CHAIN (' + self.config.network + ')' if self.config.use_chain else 'LOCAL'}",
        ]
        if self._erc8004_agent_id is not None:
            lines.append(f"ERC-8004 ID: {self._erc8004_agent_id}")
        lines.extend([
            f"",
            f"--- Memory ---",
            f"Total memories: {stats['memory']['total_memories']}",
            f"Categories: {json.dumps(stats['memory']['categories'])}",
            f"",
            f"--- Skills ---",
            f"Total skills: {stats['skills']['total_skills']}",
            f"Tasks completed: {stats['skills']['total_tasks_completed']}",
        ])
        for name, s in stats["skills"].get("skills", {}).items():
            lines.append(f"  {name}: {s['tasks']} tasks, {s['success_rate']:.0%} success")
        lines.extend([
            f"",
            f"--- Persona ---",
            f"Version: {stats['persona']['persona_version']}",
            f"Evolutions: {stats['persona']['total_evolutions']}",
        ])
        return "\n".join(lines)

    async def _format_memories(self) -> str:
        all_mem = await self.rune.memory.list_all(self.config.agent_id)
        if not all_mem:
            return "No memories yet. Chat with me to build my memory!"
        lines = [f"=== Memories ({len(all_mem)} total) ==="]
        for m in all_mem:
            cat = m.metadata.get("category", "?")
            imp = m.metadata.get("importance", 3)
            lines.append(f"  [{cat}] {'*' * imp} {m.content}")
        return "\n".join(lines)

    async def _format_skills(self) -> str:
        stats = await self.evolution.skills.get_stats()
        if stats["total_skills"] == 0:
            return "No skills learned yet. Complete tasks to build skills!"
        lines = [f"=== Skills ({stats['total_skills']} total) ==="]
        skills = await self.evolution.skills.load_skills()
        for name, s in skills.items():
            lines.append(f"\n  [{name}]")
            lines.append(f"    Tasks: {s.get('task_count', 0)} | Success: {s.get('success_count', 0)}")
            lines.append(f"    Strategy: {s.get('best_strategy', 'N/A')[:80]}")
        return "\n".join(lines)

    async def _format_evolution_history(self) -> str:
        history = await self.evolution.persona.get_evolution_history()
        if not history:
            return "No evolution history yet."
        lines = [f"=== Evolution History ==="]
        for h in history:
            lines.append(f"  v{h.get('version', '?')} [{h.get('notes', '')}] — {h.get('changes', 'N/A')}")
        return "\n".join(lines)

    # ── Social Protocol ──────────────────────────────────────────

    async def gossip(
        self,
        target_agent_id: str,
        topic: str = "",
        transport: str = "sync",
    ) -> dict:
        """
        Start and conduct a gossip session with another agent.

        Returns the session summary and impression formed.
        """
        social = self.evolution.social
        session = await social.start_gossip(target_agent_id, topic, transport)

        self._emit("gossip_started", {
            "target": target_agent_id,
            "topic": topic,
            "transport": transport,
            "session_id": session.session_id,
        })

        return {
            "session_id": session.session_id,
            "status": session.status,
            "target": target_agent_id,
            "topic": topic,
        }

    async def discover(
        self,
        interest: Optional[str] = None,
        capability: Optional[str] = None,
    ) -> list:
        """Find agents matching interests or capabilities."""
        interests = [interest] if interest else None
        capabilities = [capability] if capability else None
        return await self.evolution.social.discover_agents(
            interests=interests,
            capabilities=capabilities,
        )

    async def social_map(self) -> dict:
        """Get social graph summary."""
        return await self.evolution.social.get_social_map()

    # ── Social Formatting ─────────────────────────────────────────

    async def _format_social_map(self) -> str:
        smap = await self.social_map()
        if smap.get("status") == "no impression provider":
            return "Social protocol not available (no impression provider)."

        stats = smap.get("stats", {})
        lines = [
            f"=== {self.config.name} Social Map ===",
            f"Agents met: {stats.get('agents_met', 0)}",
            f"Gossip sessions: {stats.get('gossip_sessions', 0)}",
            f"Avg compatibility given: {stats.get('avg_compatibility_given', 0):.0%}",
            f"Avg compatibility received: {stats.get('avg_compatibility_received', 0):.0%}",
        ]

        matches = smap.get("top_matches", [])
        if matches:
            lines.append(f"\n--- Top Matches ---")
            for m in matches:
                lines.append(
                    f"  {m['agent']}: {m['score']:.0%} "
                    f"({m['gossip_count']} gossips, best: {m['top_dimension']})"
                )

        mutuals = smap.get("mutual_connections", [])
        if mutuals:
            lines.append(f"\n--- Mutual Connections ---")
            for m in mutuals:
                lines.append(
                    f"  {m['agent']}: "
                    f"you→them {m['my_score']:.0%}, "
                    f"them→you {m['their_score']:.0%}"
                )

        if not matches and not mutuals:
            lines.append("\nNo connections yet. Use /discover to find agents, /gossip to connect.")

        return "\n".join(lines)

    async def _format_impressions(self) -> str:
        if not self.rune.impressions:
            return "Social protocol not available."

        matches = await self.rune.impressions.get_top_matches(
            self.config.agent_id, top_k=20,
        )
        if not matches:
            return "No impressions formed yet. Gossip with agents to build impressions!"

        lines = [f"=== Impressions ({len(matches)} agents) ==="]
        for m in matches:
            again = "Y" if m.would_gossip_again else "N"
            lines.append(
                f"  {m.agent_id}: {m.latest_score:.0%} "
                f"| {m.gossip_count} gossips "
                f"| best: {m.top_dimension} "
                f"| again: {again}"
            )
        return "\n".join(lines)

    async def _format_discover(self, interest: Optional[str]) -> str:
        agents = await self.discover(interest=interest)
        if not agents:
            return f"No agents found{f' matching {interest!r}' if interest else ''}."

        lines = [f"=== Discovered Agents ==="]
        for a in agents:
            lines.append(
                f"  {a.agent_id}"
                f" | interests: {', '.join(a.interests[:3])}"
                f" | capabilities: {', '.join(a.capabilities[:2])}"
                f" | policy: {a.gossip_policy}"
            )
        lines.append(f"\nUse /gossip <agent_id> [topic] to start a conversation.")
        return "\n".join(lines)

    async def _start_gossip_command(self, target: str, topic: str) -> str:
        """Handle /gossip command from CLI."""
        result = await self.gossip(target, topic)
        return (
            f"Gossip session started!\n"
            f"  Session: {result['session_id']}\n"
            f"  Target: {result['target']}\n"
            f"  Topic: {result['topic'] or '(open)'}\n"
            f"  Status: {result['status']}\n"
            f"\nThe session is ready for message exchange."
        )

    # ── Chain Sync ───────────────────────────────────────────────

    async def _sync_chain(self) -> str:
        """Manual /sync command: force identity registration + state anchoring."""
        if not self.config.use_chain:
            return "Not in chain mode. Set NEXUS_PRIVATE_KEY to enable."

        lines = ["=== Chain Sync ==="]

        # 1. Identity registration
        if self._erc8004_agent_id is not None:
            lines.append(f"ERC-8004 identity: agentId={self._erc8004_agent_id} (already registered)")
        else:
            lines.append("Registering ERC-8004 identity on BSC...")
            try:
                await self._register_identity()
                if self._erc8004_agent_id is not None:
                    lines.append(f"  Registered: agentId={self._erc8004_agent_id}")
                else:
                    lines.append("  Registration failed — check logs for details")
            except Exception as e:
                lines.append(f"  Registration error: {e}")

        # 2. Save current session to chain
        lines.append("Syncing session to Greenfield + BSC...")
        try:
            await self._save_session()
            lines.append("  Session checkpoint saved")
        except Exception as e:
            lines.append(f"  Session save error: {e}")

        # 3. Flush memory index
        lines.append("Syncing memory index...")
        try:
            await self.rune.memory.flush(self.config.agent_id)
            lines.append("  Memory index flushed")
        except Exception as e:
            lines.append(f"  Memory flush error: {e}")

        lines.append("Sync complete. Background tasks may still be running.")
        return "\n".join(lines)

    # ── Lifecycle ────────────────────────────────────────────────

    async def close(self):
        # ── 1. Flush memories (persist access counts) + save session ──
        try:
            await self.rune.memory.flush(self.config.agent_id)
        except (asyncio.CancelledError, Exception) as e:
            logger.debug("Memory flush during shutdown: %s", e)
        try:
            await self._save_session()
        except (asyncio.CancelledError, Exception) as e:
            logger.debug("Session save during shutdown: %s", e)

        # ── 2. Wait for background tasks (memory extraction, session sync) ──
        # The _post_response_work tasks fire Greenfield writes inside them,
        # so we need to wait for those to complete first, then let
        # ChainBackend.close() drain its own pending write queue.
        if hasattr(self, "_bg_tasks") and self._bg_tasks:
            n = len(self._bg_tasks)
            grace = 15.0  # generous: post-turn work + Greenfield PUT latency
            self._emit("shutdown_sync", {
                "pending": n,
                "grace_seconds": grace,
            })
            logger.info(
                "Graceful shutdown: waiting up to %.0fs for %d background task(s)...",
                grace, n,
            )
            try:
                done, pending = await asyncio.wait(
                    self._bg_tasks, timeout=grace,
                )
                if done:
                    logger.info("%d background task(s) completed", len(done))
                if pending:
                    logger.warning(
                        "%d task(s) still pending after %.0fs — cancelling",
                        len(pending), grace,
                    )
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
            except Exception:
                # Fallback: cancel everything
                for task in list(self._bg_tasks):
                    task.cancel()
                await asyncio.gather(*self._bg_tasks, return_exceptions=True)
            self._bg_tasks.clear()

        # ── 3. Close Rune provider (drains ChainBackend pending writes) ──
        # ChainBackend.close() has its own grace period for Greenfield writes
        # that were fired by the tasks we just waited for.
        try:
            await self.rune.close()
        except (asyncio.CancelledError, Exception) as e:
            logger.debug("Rune close during shutdown: %s", e)

        try:
            await self.llm.close()
        except Exception:
            pass

        # ── 4. Close MCP server connections ──
        if self.tools:
            try:
                await self.tools.close()
            except Exception as e:
                logger.debug("MCP close during shutdown: %s", e)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
