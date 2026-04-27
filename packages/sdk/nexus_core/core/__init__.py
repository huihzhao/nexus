"""
Rune Protocol — Core abstractions.

This package defines the foundational interfaces and data models
that all other layers build upon:

  - models.py    — Framework-agnostic data models (Checkpoint, MemoryEntry, Artifact)
  - backend.py   — StorageBackend ABC (Strategy pattern for local/chain/mock)
  - providers.py — Provider ABCs (SessionProvider, MemoryProvider, etc.)
  - flush.py     — FlushPolicy, FlushBuffer, WriteAheadLog
"""

from .models import Checkpoint, MemoryEntry, MemoryCompact, Artifact
from .backend import StorageBackend
from .providers import (
    SessionProvider,
    MemoryProvider,
    ArtifactProvider,
    TaskProvider,
    AgentRuntime,
)
from .flush import FlushPolicy, FlushBuffer, WriteAheadLog

__all__ = [
    "Checkpoint",
    "MemoryEntry",
    "MemoryCompact",
    "Artifact",
    "StorageBackend",
    "SessionProvider",
    "MemoryProvider",
    "ArtifactProvider",
    "TaskProvider",
    "AgentRuntime",
    "FlushPolicy",
    "FlushBuffer",
    "WriteAheadLog",
]
