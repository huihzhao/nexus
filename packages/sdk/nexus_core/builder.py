"""
Rune Protocol — Builder + Static Factories.

This is the main entry point for users. Provides:

  - Quick factories:  Rune.local(), Rune.testnet(), Rune.mainnet()
  - Builder pattern:  Rune.builder().backend(...).flush_policy(...).build()

Design patterns:
  - Builder:          RuneBuilder for complex configuration
  - Static Factory:   Rune.local() / Rune.testnet() for 80% use case
  - Facade:           Returns RuneProvider (clean 4-provider surface)

Usage:
    # Zero config (most common)
    rune = Rune.local()

    # Testnet
    rune = Rune.testnet(private_key="0x...")

    # Custom config
    rune = (
        Rune.builder()
        .backend(LocalBackend(base_dir="/tmp/my-agent"))
        .flush_policy(FlushPolicy.sync_every())
        .build()
    )
"""

from __future__ import annotations

from typing import Optional

from .core.backend import StorageBackend
from .core.flush import FlushPolicy
from .core.providers import RuneProvider
from .providers.session import SessionProviderImpl
from .providers.memory import MemoryProviderImpl
from .providers.artifact import ArtifactProviderImpl
from .providers.task import TaskProviderImpl
from .providers.impression import ImpressionProviderImpl


class Rune:
    """
    Main entry point — static factories + builder access.

    Not instantiated directly. Use the static methods:

        rune = Rune.local()
        rune = Rune.testnet(private_key="0x...")
        rune = Rune.builder().backend(...).build()
    """

    # ── Quick Factories (cover 80% of use cases) ────────────────────

    @staticmethod
    def local(base_dir: str = ".rune_state") -> RuneProvider:
        """
        Create a local-mode provider. Zero config, no blockchain.

        Perfect for development, testing, and demos.
        All data stored as files in base_dir.

        Args:
            base_dir: Directory for local storage (default: .rune_state)

        Returns:
            RuneProvider with all four providers backed by local files.
        """
        return Rune.builder().local_backend(base_dir).build()

    @staticmethod
    def testnet(private_key: str, **kwargs) -> RuneProvider:
        """
        Create a testnet-mode provider. BSC testnet + Greenfield testnet.

        Requires:
          - BNB testnet tokens (from faucet)
          - Deployed contracts (AgentStateExtension + TaskStateManager)

        Args:
            private_key: BSC wallet private key (0x-prefixed hex).
            **kwargs: Additional ChainBackend options.

        Returns:
            RuneProvider backed by BSC testnet + Greenfield.
        """
        return Rune.builder().chain_backend(private_key, network="testnet", **kwargs).build()

    @staticmethod
    def mainnet(private_key: str, **kwargs) -> RuneProvider:
        """
        Create a mainnet-mode provider. BSC mainnet + Greenfield mainnet.

        Args:
            private_key: BSC wallet private key.
            **kwargs: Additional ChainBackend options.

        Returns:
            RuneProvider backed by BSC mainnet + Greenfield.
        """
        return Rune.builder().chain_backend(private_key, network="mainnet", **kwargs).build()

    # ── Builder Access ──────────────────────────────────────────────

    @staticmethod
    def builder() -> "RuneBuilder":
        """
        Start building a custom provider configuration.

        Returns:
            RuneBuilder for fluent configuration.
        """
        return RuneBuilder()


class RuneBuilder:
    """
    Fluent builder for RuneProvider.

    Allows fine-grained control over backend, flush policy, and runtime ID.

    Usage:
        rune = (
            Rune.builder()
            .backend(my_custom_backend)
            .flush_policy(FlushPolicy.aggressive())
            .runtime_id("prod-runtime-1")
            .build()
        )
    """

    def __init__(self):
        self._backend: Optional[StorageBackend] = None
        self._flush_policy: FlushPolicy = FlushPolicy.balanced()
        self._runtime_id: Optional[str] = None

    def backend(self, backend: StorageBackend) -> "RuneBuilder":
        """Set a custom storage backend."""
        self._backend = backend
        return self

    def local_backend(self, base_dir: str = ".rune_state") -> "RuneBuilder":
        """Use LocalBackend (file-based, zero config)."""
        from .backends.local import LocalBackend
        self._backend = LocalBackend(base_dir=base_dir)
        return self

    def chain_backend(self, private_key: str, network: str = "testnet", **kwargs) -> "RuneBuilder":
        """Use ChainBackend (BSC + Greenfield)."""
        from .backends.chain import ChainBackend
        self._backend = ChainBackend(private_key=private_key, network=network, **kwargs)
        return self

    def mock_backend(self) -> "RuneBuilder":
        """Use MockBackend (in-memory, for tests)."""
        from .backends.mock import MockBackend
        self._backend = MockBackend()
        return self

    def flush_policy(self, policy: FlushPolicy) -> "RuneBuilder":
        """Set the flush policy for write batching."""
        self._flush_policy = policy
        return self

    def runtime_id(self, rid: str) -> "RuneBuilder":
        """Set the runtime identifier (for multi-runtime scenarios)."""
        self._runtime_id = rid
        return self

    def build(self) -> RuneProvider:
        """
        Build the RuneProvider with all configured options.

        If no backend was set, defaults to LocalBackend.

        Returns:
            Fully configured RuneProvider.
        """
        if self._backend is None:
            from .backends.local import LocalBackend
            self._backend = LocalBackend()

        return RuneProvider(
            sessions=SessionProviderImpl(
                self._backend,
                runtime_id=self._runtime_id,
            ),
            memory=MemoryProviderImpl(
                self._backend,
                runtime_id=self._runtime_id,
            ),
            artifacts=ArtifactProviderImpl(self._backend),
            tasks=TaskProviderImpl(self._backend),
            impressions=ImpressionProviderImpl(self._backend),
            backend=self._backend,
        )
