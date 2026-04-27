"""
Rune Protocol — Concrete Provider Implementations.

Each provider implements the corresponding ABC from core.providers,
using a StorageBackend for actual persistence.

    SessionProviderImpl  — checkpoint save/load with parent linking
    MemoryProviderImpl   — semantic memory with local search engine
    ArtifactProviderImpl — versioned file storage with manifests
    TaskProviderImpl     — A2A task lifecycle
"""

from .session import SessionProviderImpl
from .memory import MemoryProviderImpl
from .artifact import ArtifactProviderImpl
from .task import TaskProviderImpl
from .impression import ImpressionProviderImpl

__all__ = [
    "SessionProviderImpl",
    "MemoryProviderImpl",
    "ArtifactProviderImpl",
    "TaskProviderImpl",
    "ImpressionProviderImpl",
]
