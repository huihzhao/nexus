"""
Nexus — Concrete Provider Implementations.

Each provider implements the corresponding ABC from core.providers,
using a StorageBackend for actual persistence.

    SessionProviderImpl  — checkpoint save/load with parent linking
    ArtifactProviderImpl — versioned file storage with manifests
    TaskProviderImpl     — A2A task lifecycle
    ImpressionProviderImpl — peer-to-peer attestation

Phase D 续 #2: ``MemoryProviderImpl`` was deleted. Use the typed
Phase J namespace stores (``FactsStore`` / etc.) from
``nexus_core.memory`` instead.
"""

from .session import SessionProviderImpl
from .artifact import ArtifactProviderImpl
from .task import TaskProviderImpl
from .impression import ImpressionProviderImpl

__all__ = [
    "SessionProviderImpl",
    "ArtifactProviderImpl",
    "TaskProviderImpl",
    "ImpressionProviderImpl",
]
