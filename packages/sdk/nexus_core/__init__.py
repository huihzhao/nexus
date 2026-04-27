"""
Rune Protocol SDK — Persistent, Verifiable Agent Infrastructure on BNBChain.

"Runtime Is Temporary, Identity Is Eternal."

Quick start:
    from nexus_core import Rune

    rune = Rune.local()                          # Zero config
    rune = Rune.testnet(private_key="0x...")      # BSC testnet
    rune = Rune.builder().mock_backend().build()  # Unit tests

Architecture:

  Entry Point:
    - Rune:               Static factories + builder access
    - RuneBuilder:        Fluent builder for custom configurations

  Core Abstractions:
    - StorageBackend:     Strategy pattern — pluggable persistence
    - RuneProvider:       Facade — bundles 5 providers
    - RuneSessionProvider, RuneMemoryProvider, RuneArtifactProvider, RuneTaskProvider
    - RuneImpressionProvider (Social Protocol)

  Social Protocol:
    - social.gossip:      Async/sync agent-to-agent gossip
    - social.profile:     Agent profile generation + discovery
    - social.graph:       Social graph queries + propagation

  Backends (Strategy implementations):
    - LocalBackend:       File-based (dev/demo)
    - ChainBackend:       BSC + Greenfield (production)
    - MockBackend:        In-memory (unit tests)

  Framework Adapters:
    - adapters.adk:       Google ADK
    - adapters.langgraph: LangGraph
    - adapters.crewai:    CrewAI
"""

__version__ = "0.5.0"

# ── Entry point + builder ──────────────────────────────────────────────
from .builder import Rune, RuneBuilder

# ── Core abstractions ──────────────────────────────────────────────────
from .core.backend import StorageBackend
from .core.providers import (
    RuneProvider,
    RuneSessionProvider,
    RuneMemoryProvider,
    RuneArtifactProvider,
    RuneTaskProvider,
    RuneImpressionProvider,
)
from .core.models import (
    Checkpoint, MemoryEntry, MemoryCompact, Artifact,
    Impression, ImpressionDimensions, ImpressionSummary, NetworkStats,
    GossipMessage, GossipSession, AgentProfile,
)
from .core.flush import FlushPolicy, FlushBuffer, WriteAheadLog
from .utils import robust_json_parse, load_dotenv
from .tools import BaseTool, ToolResult, ToolCall, ToolRegistry, WebSearchTool, URLReaderTool
from .llm import LLMClient, LLMProvider
from .mcp import MCPClient, MCPServerConfig, MCPManager
from .skills import SkillManager

# ── Backends ───────────────────────────────────────────────────────────
from .backends.mock import MockBackend
from .backends.local import LocalBackend

try:
    from .backends.chain import ChainBackend
except ImportError:
    ChainBackend = None  # web3 not installed

# ── Provider implementations ───────────────────────────────────────────
from .providers import (
    SessionProviderImpl,
    MemoryProviderImpl,
    ArtifactProviderImpl,
    TaskProviderImpl,
    ImpressionProviderImpl,
)

# ── Adapter registry ──────────────────────────────────────────────────
from .adapters.registry import AdapterRegistry

# ── Social Protocol ───────────────────────────────────────────────────
from .social.gossip import GossipProtocol
from .social.profile import ProfileManager
from .social.graph import SocialGraph

# ── Infrastructure (used by ChainBackend) ──────────────────────────────
from .state import StateManager, ERC8004Identity, AgentStateRecord
from .greenfield import GreenfieldClient

# ── Generic LLM utilities ──────────────────────────────────────────────
# Reusable file-distillation pipeline (formerly server-only).
from .distiller import (
    distill,
    extract_text,
    DISTILL_INPUT_CHAR_BUDGET,
    DISTILL_OUTPUT_CHAR_BUDGET,
    DISTILL_SYSTEM_PROMPT,
)

try:
    from .chain import RuneChainClient
except ImportError:
    RuneChainClient = None

# ── A2A (Agent-to-Agent) ──────────────────────────────────────────────
# a2a_task_store + a2a depend on the optional a2a-sdk package (declared
# in pyproject as the ``a2a`` extra). Importing them unconditionally
# made the WHOLE SDK unimportable when a2a-sdk wasn't installed —
# chain.py / greenfield.py couldn't even load. Treat the a2a layer as
# best-effort: callers that actually need it import directly from the
# adapter module and get a clean ImportError; everyone else still gets
# a working ``nexus_core`` package.
try:
    from .adapters.a2a_task_store import BNBChainTaskStore
    from .adapters.a2a import StatelessA2AAgent, AgentRuntime, A2AAgentConfig
    _A2A_AVAILABLE = True
except Exception as _a2a_err:  # noqa: BLE001 — optional integration
    BNBChainTaskStore = None
    StatelessA2AAgent = None
    AgentRuntime = None
    A2AAgentConfig = None
    _A2A_AVAILABLE = False
    import logging as _logging
    _logging.getLogger("nexus_core").info(
        "A2A integration unavailable (%s) — install with [a2a] extra to enable.",
        _a2a_err,
    )

# ── Framework-specific services ────────────────────────────────────────
# session/artifact lean on google-adk (optional ``adk`` extra). Same
# softening pattern as A2A: don't make the whole SDK uninportable just
# because the operator hasn't pulled in the ADK integration.
try:
    from .session import BNBChainSessionService
    from .artifact import BNBChainArtifactService
    _ADK_AVAILABLE = True
except Exception as _adk_err:  # noqa: BLE001 — optional integration
    BNBChainSessionService = None
    BNBChainArtifactService = None
    _ADK_AVAILABLE = False
    import logging as _logging
    _logging.getLogger("nexus_core").info(
        "Google-ADK integration unavailable (%s) — install with [adk] extra to enable.",
        _adk_err,
    )

from .memory import EventLog, Event, CuratedMemory, EventLogCompactor
from .contracts import ContractEngine, ContractSpec, CheckResult, DriftScore, Rule

try:
    from .keystore import RuneKeystore
except ImportError:
    RuneKeystore = None

__all__ = [
    # Primary API
    "Rune",
    "RuneBuilder",
    "StorageBackend",
    "RuneProvider",
    "RuneSessionProvider",
    "RuneMemoryProvider",
    "RuneArtifactProvider",
    "RuneTaskProvider",
    "Checkpoint",
    "MemoryEntry",
    "MemoryCompact",
    "Artifact",
    "FlushPolicy",
    "FlushBuffer",
    "WriteAheadLog",
    "MockBackend",
    "LocalBackend",
    "ChainBackend",
    "SessionProviderImpl",
    "MemoryProviderImpl",
    "ArtifactProviderImpl",
    "TaskProviderImpl",
    "ImpressionProviderImpl",
    "AdapterRegistry",
    # Social Protocol
    "RuneImpressionProvider",
    "Impression",
    "ImpressionDimensions",
    "ImpressionSummary",
    "NetworkStats",
    "GossipMessage",
    "GossipSession",
    "AgentProfile",
    "GossipProtocol",
    "ProfileManager",
    "SocialGraph",
    # Infrastructure
    "StateManager",
    "ERC8004Identity",
    "AgentStateRecord",
    "RuneChainClient",
    "GreenfieldClient",
    # A2A
    "BNBChainTaskStore",
    "StatelessA2AAgent",
    "AgentRuntime",
    "A2AAgentConfig",
    # Framework services
    "BNBChainSessionService",
    "BNBChainArtifactService",
    "RuneKeystore",
    # Tools & MCP
    "BaseTool",
    "ToolResult",
    "ToolCall",
    "ToolRegistry",
    "LLMClient",
    "LLMProvider",
    "MCPClient",
    "MCPServerConfig",
    "MCPManager",
    # Skills
    "SkillManager",
    # Built-in tools
    "WebSearchTool",
    "URLReaderTool",
    # Memory (DPM)
    "EventLog",
    "Event",
    "CuratedMemory",
    "EventLogCompactor",
    # Contracts (ABC)
    "ContractEngine",
    "ContractSpec",
    "CheckResult",
    "DriftScore",
    "Rule",
    # Utilities
    "robust_json_parse",
    "load_dotenv",
]
