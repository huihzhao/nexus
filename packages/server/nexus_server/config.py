"""Configuration management for Rune Server.

Loads all server settings from environment variables with sensible defaults.
Organized by functional area:
  - Server basics (host, port, environment)
  - Security (JWT, WebAuthn, CORS)
  - LLM providers (keys and defaults)
  - Blockchain (RPC, contracts)
  - Rate limiting
  - Database
"""

import os
from typing import Optional


class ServerConfig:
    """Server configuration from environment variables."""

    # Server basics
    SERVER_HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")
    SERVER_PORT: int = int(os.getenv("SERVER_PORT", "8001"))
    SERVER_SECRET: str = os.getenv("SERVER_SECRET", "dev-secret-key")
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # CORS
    CORS_ALLOW_ORIGINS: str = os.getenv(
        "CORS_ALLOW_ORIGINS",
        "http://localhost:3000,http://localhost:5173",
    )

    # JWT / Auth
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_EXPIRATION_HOURS: int = int(
        os.getenv("JWT_EXPIRATION_HOURS", "24")
    )

    # WebAuthn
    WEBAUTHN_RP_ID: str = os.getenv("WEBAUTHN_RP_ID", "localhost")
    WEBAUTHN_RP_NAME: str = os.getenv("WEBAUTHN_RP_NAME", "Rune Protocol")
    WEBAUTHN_ORIGIN: str = os.getenv("WEBAUTHN_ORIGIN", "http://localhost:3000")

    # LLM Configuration
    DEFAULT_LLM_PROVIDER: str = os.getenv(
        "DEFAULT_LLM_PROVIDER", "anthropic"
    )
    DEFAULT_LLM_MODEL: str = os.getenv(
        "DEFAULT_LLM_MODEL", "claude-3-sonnet-20240229"
    )

    # LLM API Keys
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")
    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
    ANTHROPIC_API_KEY: Optional[str] = os.getenv("ANTHROPIC_API_KEY")

    # Tool API Keys (for server-side tool execution)
    TAVILY_API_KEY: Optional[str] = os.getenv("TAVILY_API_KEY")
    JINA_API_KEY: Optional[str] = os.getenv("JINA_API_KEY")

    # Rate Limiting
    RATE_LIMIT_LLM_REQUESTS_PER_MINUTE: int = int(
        os.getenv("RATE_LIMIT_LLM_REQUESTS_PER_MINUTE", "60")
    )
    RATE_LIMIT_OTHER_REQUESTS_PER_MINUTE: int = int(
        os.getenv("RATE_LIMIT_OTHER_REQUESTS_PER_MINUTE", "120")
    )

    # ── Chain / Blockchain ─────────────────────────────────────────
    # Server-owned custodial signing key. Used to sponsor on-chain ops
    # for Web2 users who don't carry their own wallet. NOT the same as
    # SDK's RUNE_PRIVATE_KEY (which is per-agent / standalone use).
    SERVER_PRIVATE_KEY: Optional[str] = os.getenv("SERVER_PRIVATE_KEY")

    # Legacy / explicit overrides — if the operator wants to point chain
    # ops at something different than what RUNE_*_RPC says.
    CHAIN_RPC_URL: Optional[str] = os.getenv("CHAIN_RPC_URL")
    CHAIN_AGENT_CONTRACT_ADDRESS: Optional[str] = os.getenv(
        "CHAIN_AGENT_CONTRACT_ADDRESS"
    )

    # Network selection: "bsc-testnet" or "bsc-mainnet". Mirrors SDK's
    # RUNE_NETWORK.
    RUNE_NETWORK: str = os.getenv("RUNE_NETWORK", "bsc-testnet")

    # Network-level config: shared with SDK via dotenv fallback (see
    # main._load_dotenv). These don't carry secrets.
    RUNE_TESTNET_RPC: Optional[str] = os.getenv(
        "RUNE_TESTNET_RPC",
        "https://data-seed-prebsc-1-s1.bnbchain.org:8545",
    )
    RUNE_TESTNET_AGENT_STATE_ADDRESS: Optional[str] = os.getenv(
        "RUNE_TESTNET_AGENT_STATE_ADDRESS"
    )
    RUNE_TESTNET_TASK_MANAGER_ADDRESS: Optional[str] = os.getenv(
        "RUNE_TESTNET_TASK_MANAGER_ADDRESS"
    )
    RUNE_TESTNET_IDENTITY_REGISTRY: Optional[str] = os.getenv(
        "RUNE_TESTNET_IDENTITY_REGISTRY"
    )
    RUNE_MAINNET_RPC: Optional[str] = os.getenv(
        "RUNE_MAINNET_RPC",
        "https://bsc-dataseed1.bnbchain.org",
    )
    RUNE_MAINNET_IDENTITY_REGISTRY: Optional[str] = os.getenv(
        "RUNE_MAINNET_IDENTITY_REGISTRY"
    )

    @property
    def network_short(self) -> str:
        """Short-form network name ("testnet" / "mainnet") that the SDK
        and twin expect.

        Hoisted here so every module agrees on the mapping. Previously
        each of ``twin_manager``, ``chain_proxy``, and ``sync_anchor``
        rolled its own ``"mainnet" in network`` check — easy to drift,
        easy to typo. Centralised so a single ``RUNE_NETWORK`` validation
        point covers everyone.
        """
        return "mainnet" if "mainnet" in (self.RUNE_NETWORK or "").lower() else "testnet"

    @property
    def chain_active_rpc(self) -> Optional[str]:
        """The RPC to use for the active network. CHAIN_RPC_URL wins if set."""
        if self.CHAIN_RPC_URL:
            return self.CHAIN_RPC_URL
        return (
            self.RUNE_MAINNET_RPC
            if self.network_short == "mainnet"
            else self.RUNE_TESTNET_RPC
        )

    @property
    def chain_is_configured(self) -> bool:
        """True iff we can attempt real on-chain calls (private key + RPC)."""
        return bool(self.SERVER_PRIVATE_KEY and self.chain_active_rpc)

    # ── Twin (Nexus DigitalTwin) ────────────────────────────────────
    # When 1, /api/v1/llm/chat is served by a per-user DigitalTwin
    # instead of the direct LLM gateway. Default 1 because the user
    # explicitly committed to Phase D ("we haven't launched, data can
    # be wiped"). Tests flip this to "0" via RUNE_USE_TWIN env to
    # exercise the legacy path.
    USE_TWIN: bool = os.getenv("RUNE_USE_TWIN", "1") == "1"
    TWIN_BASE_DIR: str = os.getenv(
        "RUNE_TWIN_BASE_DIR",
        os.path.expanduser("~/.nexus_server/twins"),
    )
    TWIN_IDLE_SECONDS: int = int(os.getenv("RUNE_TWIN_IDLE_SECONDS", "1800"))

    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "sqlite:///./nexus_server.db"
    )

    def validate(self) -> None:
        """Validate configuration on startup.

        Fails hard on misconfigurations that would silently corrupt
        production behaviour; warns on missing optionals.
        """
        if self.ENVIRONMENT == "production":
            assert self.SERVER_SECRET != "dev-secret-key", (
                "SERVER_SECRET must be set in production"
            )
            assert self.WEBAUTHN_RP_ID != "localhost", (
                "WEBAUTHN_RP_ID must be set to real domain in production"
            )

        # Validate RUNE_NETWORK loudly — a typo (e.g. "bsc_mainnet" with
        # underscore, or "mainet") used to silently fall back to testnet
        # via the ``"mainnet" in network`` substring check. That meant
        # production traffic could end up anchoring to the wrong chain
        # without any warning. We now whitelist the canonical values.
        valid_networks = {"bsc-testnet", "bsc-mainnet"}
        if self.RUNE_NETWORK not in valid_networks:
            raise ValueError(
                f"RUNE_NETWORK must be one of {sorted(valid_networks)}, "
                f"got {self.RUNE_NETWORK!r}. (Common mistakes: 'bsc_testnet' "
                f"with underscore, or 'mainet' typo — both fail.)"
            )

        if not self.GEMINI_API_KEY and \
           not self.OPENAI_API_KEY and \
           not self.ANTHROPIC_API_KEY:
            import warnings
            warnings.warn(
                "No LLM API keys configured. LLM endpoints will fail.",
                RuntimeWarning,
            )


def get_config() -> ServerConfig:
    """Get singleton configuration instance."""
    return ServerConfig()
