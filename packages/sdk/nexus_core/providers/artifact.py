"""
ArtifactProviderImpl — concrete ArtifactProvider backed by StorageBackend.

Domain logic:
  - Version management (saving same filename creates new version)
  - Content-addressed storage (SHA-256 hash of data)
  - Manifest tracking per agent/session

Storage layout:
    agents/{agent_id}/artifacts/{session_id}/{filename}.v{N}  — data blobs
    agents/{agent_id}/artifacts/{session_id}/manifest.json     — version manifest
"""

from __future__ import annotations

import hashlib
import time
from typing import Optional

from ..core.backend import StorageBackend
from ..core.models import Artifact
from ..core.providers import ArtifactProvider


class ArtifactProviderImpl(ArtifactProvider):
    """
    Concrete artifact provider with versioning.

    Each save of the same filename creates a new version.
    Artifacts are content-addressed and immutable once written.
    """

    def __init__(self, backend: StorageBackend):
        self._backend = backend
        # In-memory manifest: manifest_key -> {filename: [version_data]}
        self._manifests: dict[str, dict] = {}

    @staticmethod
    def _safe(value: str) -> str:
        """Sanitize a path component to prevent directory traversal."""
        return value.replace("/", "__").replace("\\", "__").replace("..", "__")

    def _manifest_key(self, agent_id: str, session_id: str = "") -> str:
        return f"{agent_id}:{session_id or 'default'}"

    def _manifest_path(self, agent_id: str, session_id: str = "") -> str:
        sid = self._safe(session_id) if session_id else "default"
        return f"agents/{self._safe(agent_id)}/artifacts/{sid}/manifest.json"

    def _blob_path(self, agent_id: str, session_id: str, filename: str, version: int) -> str:
        sid = self._safe(session_id) if session_id else "default"
        return f"agents/{self._safe(agent_id)}/artifacts/{sid}/{self._safe(filename)}.v{version}"

    def _get_manifest(self, key: str) -> dict:
        if key not in self._manifests:
            self._manifests[key] = {}
        return self._manifests[key]

    async def save(
        self,
        filename: str,
        data: bytes,
        agent_id: str,
        session_id: str = "",
        content_type: str = "",
        metadata: Optional[dict] = None,
    ) -> int:
        key = self._manifest_key(agent_id, session_id)
        manifest = self._get_manifest(key)

        if filename not in manifest:
            manifest[filename] = []

        version = len(manifest[filename]) + 1
        content_hash = hashlib.sha256(data).hexdigest()

        # Store blob
        blob_path = self._blob_path(agent_id, session_id, filename, version)
        await self._backend.store_blob(blob_path, data)

        # Update manifest
        manifest[filename].append({
            "version": version,
            "content_hash": content_hash,
            "content_type": content_type,
            "metadata": metadata or {},
            "created_at": time.time(),
            "size": len(data),
        })

        # Persist manifest
        manifest_path = self._manifest_path(agent_id, session_id)
        await self._backend.store_json(manifest_path, manifest)

        return version

    async def load(
        self,
        filename: str,
        agent_id: str,
        session_id: str = "",
        version: Optional[int] = None,
    ) -> Optional[Artifact]:
        key = self._manifest_key(agent_id, session_id)
        manifest = self._get_manifest(key)

        # If manifest is empty in cache, try loading from backend (cold start)
        if not manifest:
            manifest_path = self._manifest_path(agent_id, session_id)
            loaded = await self._backend.load_json(manifest_path)
            if loaded and isinstance(loaded, dict):
                self._manifests[key] = loaded
                manifest = loaded

        if filename not in manifest or not manifest[filename]:
            return None

        versions = manifest[filename]
        if version is not None:
            matches = [v for v in versions if v["version"] == version]
            if not matches:
                return None
            ver_data = matches[0]
        else:
            ver_data = versions[-1]  # latest

        # Load blob
        blob_path = self._blob_path(agent_id, session_id, filename, ver_data["version"])
        data = await self._backend.load_blob(blob_path)
        if data is None:
            return None

        # Verify content integrity
        computed_hash = hashlib.sha256(data).hexdigest()
        expected_hash = ver_data.get("content_hash", "")
        if expected_hash and computed_hash != expected_hash:
            import logging
            logging.getLogger("nexus_core.artifact").warning(
                "Content hash mismatch for %s v%d: expected %s, got %s — returning None",
                filename, ver_data["version"], expected_hash[:16], computed_hash[:16],
            )
            return None

        return Artifact(
            filename=filename,
            data=data,
            version=ver_data["version"],
            content_type=ver_data.get("content_type", ""),
            agent_id=agent_id,
            session_id=session_id,
            metadata=ver_data.get("metadata", {}),
            content_hash=ver_data["content_hash"],
            created_at=ver_data.get("created_at", 0.0),
        )

    async def list_artifacts(
        self,
        agent_id: str,
        session_id: str = "",
    ) -> list[str]:
        key = self._manifest_key(agent_id, session_id)
        manifest = self._get_manifest(key)
        return list(manifest.keys())

    async def list_versions(
        self,
        filename: str,
        agent_id: str,
        session_id: str = "",
    ) -> list[int]:
        key = self._manifest_key(agent_id, session_id)
        manifest = self._get_manifest(key)
        if filename not in manifest:
            return []
        return [v["version"] for v in manifest[filename]]
