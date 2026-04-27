"""
MockBackend — pure in-memory storage for unit tests.

Zero I/O, zero side effects, deterministic behavior.
"""

from __future__ import annotations

from typing import Optional

from ..core.backend import StorageBackend


class MockBackend(StorageBackend):
    """
    In-memory storage backend for unit tests.

    All data lives in Python dicts — nothing touches the filesystem
    or network. Perfect for fast, isolated tests.
    """

    def __init__(self):
        self._json_store: dict[str, dict] = {}
        self._blob_store: dict[str, bytes] = {}
        self._anchors: dict[str, dict[str, str]] = {}  # agent_id -> {namespace: hash}

    async def store_json(self, path: str, data: dict) -> str:
        raw = self.json_bytes(data)
        content_hash = self.content_hash(raw)
        self._json_store[path] = data
        return content_hash

    async def load_json(self, path: str) -> Optional[dict]:
        return self._json_store.get(path)

    async def store_blob(self, path: str, data: bytes) -> str:
        content_hash = self.content_hash(data)
        self._blob_store[path] = data
        return content_hash

    async def load_blob(self, path: str) -> Optional[bytes]:
        return self._blob_store.get(path)

    async def anchor(self, agent_id: str, content_hash: str, namespace: str = "state") -> None:
        if agent_id not in self._anchors:
            self._anchors[agent_id] = {}
        self._anchors[agent_id][namespace] = content_hash

    async def resolve(self, agent_id: str, namespace: str = "state") -> Optional[str]:
        return self._anchors.get(agent_id, {}).get(namespace)

    async def list_paths(self, prefix: str) -> list[str]:
        paths = []
        for path in self._json_store:
            if path.startswith(prefix):
                paths.append(path)
        for path in self._blob_store:
            if path.startswith(prefix):
                paths.append(path)
        return sorted(set(paths))

    async def delete(self, path: str) -> bool:
        deleted = False
        if path in self._json_store:
            del self._json_store[path]
            deleted = True
        if path in self._blob_store:
            del self._blob_store[path]
            deleted = True
        return deleted

    def reset(self) -> None:
        """Clear all stored data (useful between tests)."""
        self._json_store.clear()
        self._blob_store.clear()
        self._anchors.clear()
