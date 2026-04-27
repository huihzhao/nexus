"""
LocalBackend — file-based storage for development.

Zero configuration, no blockchain, no network. All data is stored
as files in a local directory. Anchors go to a JSON "chain" file.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

from ..core.backend import StorageBackend

logger = logging.getLogger("nexus_core.backend.local")


class LocalBackend(StorageBackend):
    """
    File-based storage backend for local development.

    Directory layout:
        {base_dir}/
        ├── data/           # JSON payloads and binary blobs
        │   └── agents/
        │       └── {agent_id}/
        │           ├── sessions/
        │           ├── memory/
        │           └── artifacts/
        └── chain/          # Simulated on-chain state
            └── anchors.json
    """

    def __init__(self, base_dir: str = ".nexus_state"):
        self._base_dir = Path(base_dir)
        self._data_dir = self._base_dir / "data"
        self._chain_dir = self._base_dir / "chain"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._chain_dir.mkdir(parents=True, exist_ok=True)

        self._anchors_file = self._chain_dir / "anchors.json"
        if not self._anchors_file.exists():
            self._write_atomic(self._anchors_file, {"anchors": {}})

        logger.info("LocalBackend initialized at %s", self._base_dir)

    # ── JSON ────────────────────────────────────────────────────────

    async def store_json(self, path: str, data: dict) -> str:
        raw = self.json_bytes(data)
        content_hash = self.content_hash(raw)

        file_path = self._data_dir / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_atomic(file_path, data)

        logger.debug("Stored JSON %d bytes -> %s", len(raw), path)
        return content_hash

    async def load_json(self, path: str) -> Optional[dict]:
        file_path = self._data_dir / path
        if not file_path.exists():
            return None
        with open(file_path, "r") as f:
            return json.load(f)

    # ── Blobs ───────────────────────────────────────────────────────

    async def store_blob(self, path: str, data: bytes) -> str:
        content_hash = self.content_hash(data)

        file_path = self._data_dir / path
        file_path.parent.mkdir(parents=True, exist_ok=True)

        tmp = file_path.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            f.write(data)
        tmp.rename(file_path)

        logger.debug("Stored blob %d bytes -> %s", len(data), path)
        return content_hash

    async def load_blob(self, path: str) -> Optional[bytes]:
        file_path = self._data_dir / path
        if not file_path.exists():
            return None
        with open(file_path, "rb") as f:
            return f.read()

    # ── Anchoring ───────────────────────────────────────────────────

    async def anchor(self, agent_id: str, content_hash: str, namespace: str = "state") -> None:
        store = self._read_anchors()
        key = f"{agent_id}:{namespace}"
        store["anchors"][key] = {
            "content_hash": content_hash,
            "updated_at": time.time(),
        }
        self._write_atomic(self._anchors_file, store)
        logger.debug("Anchored %s:%s -> %s", agent_id, namespace, content_hash[:16])

    async def resolve(self, agent_id: str, namespace: str = "state") -> Optional[str]:
        store = self._read_anchors()
        key = f"{agent_id}:{namespace}"
        entry = store["anchors"].get(key)
        if entry is None:
            return None
        return entry["content_hash"]

    # ── Listing ─────────────────────────────────────────────────────

    async def list_paths(self, prefix: str) -> list[str]:
        base = self._data_dir / prefix
        if not base.exists():
            return []
        results = []
        for p in sorted(base.rglob("*")):
            if p.is_file() and not p.name.endswith(".tmp"):
                rel = str(p.relative_to(self._data_dir))
                results.append(rel)
        return results

    # ── Deletion ────────────────────────────────────────────────────

    async def delete(self, path: str) -> bool:
        file_path = self._data_dir / path
        if file_path.exists():
            file_path.unlink()
            return True
        return False

    # ── Helpers ─────────────────────────────────────────────────────

    def _read_anchors(self) -> dict:
        with open(self._anchors_file, "r") as f:
            return json.load(f)

    @staticmethod
    def _write_atomic(path: Path, data: dict) -> None:
        """Atomic write via temp file + rename."""
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, default=str)
        tmp.rename(path)
