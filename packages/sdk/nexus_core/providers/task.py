"""
TaskProviderImpl — concrete RuneTaskProvider backed by StorageBackend.

Domain logic:
  - Task creation with agent association
  - State hash computation and versioning
  - Status lifecycle (pending → running → completed/failed)

Storage layout:
    agents/{agent_id}/tasks/{task_id}.json
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Optional

from ..core.backend import StorageBackend
from ..core.providers import RuneTaskProvider


class TaskProviderImpl(RuneTaskProvider):
    """
    Concrete task lifecycle provider.

    Tracks A2A task state with version numbers for optimistic concurrency.
    """

    def __init__(self, backend: StorageBackend):
        self._backend = backend
        # In-memory cache: task_id -> task_record
        self._tasks: dict[str, dict] = {}

    def _path(self, agent_id: str, task_id: str) -> str:
        return f"agents/{agent_id}/tasks/{task_id}.json"

    async def create_task(
        self,
        task_id: str,
        agent_id: str,
        metadata: Optional[dict] = None,
    ) -> dict:
        now = time.time()
        record = {
            "task_id": task_id,
            "agent_id": agent_id,
            "status": "pending",
            "version": 0,
            "state_hash": "",
            "metadata": metadata or {},
            "created_at": now,
            "updated_at": now,
        }

        self._tasks[task_id] = record

        path = self._path(agent_id, task_id)
        await self._backend.store_json(path, record)

        return {
            "task_id": record["task_id"],
            "agent_id": record["agent_id"],
            "status": record["status"],
            "version": record["version"],
        }

    async def update_task(
        self,
        task_id: str,
        state: dict,
        status: str = "running",
    ) -> dict:
        record = self._tasks.get(task_id)
        if record is None:
            raise KeyError(f"Task {task_id} not found")

        # Compute state hash
        state_json = json.dumps(state, default=str, sort_keys=True).encode()
        state_hash = hashlib.sha256(state_json).hexdigest()

        record["state_hash"] = state_hash
        record["status"] = status
        record["version"] += 1
        record["updated_at"] = time.time()

        path = self._path(record["agent_id"], task_id)
        await self._backend.store_json(path, record)

        return {
            "task_id": record["task_id"],
            "status": record["status"],
            "version": record["version"],
            "state_hash": record["state_hash"],
        }

    async def get_task(self, task_id: str) -> Optional[dict]:
        record = self._tasks.get(task_id)
        if record is None:
            return None
        return {
            "task_id": record["task_id"],
            "agent_id": record["agent_id"],
            "status": record["status"],
            "version": record["version"],
            "state_hash": record["state_hash"],
        }
