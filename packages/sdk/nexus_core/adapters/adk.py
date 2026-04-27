"""
Rune Protocol — Google ADK Adapter.

Thin wrappers that bridge ADK's native interfaces to Rune providers.
Each class does ONLY two things: type conversion + delegation.
No persistence logic lives here.

    RuneSessionService   — ADK BaseSessionService  → rune.sessions
    RuneMemoryService    — ADK BaseMemoryService   → rune.memory
    RuneArtifactService  — ADK BaseArtifactService → rune.artifacts

Usage:
    from nexus_core import Rune
    from nexus_core.adapters.adk import RuneSessionService, RuneMemoryService, RuneArtifactService

    rune = Rune.local()

    runner = Runner(
        agent=my_agent,
        session_service=RuneSessionService(rune.sessions),
        memory_service=RuneMemoryService(rune.memory),
        artifact_service=RuneArtifactService(rune.artifacts),
    )
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from ..core.models import Checkpoint, MemoryEntry
from ..core.providers import RuneSessionProvider, RuneMemoryProvider, RuneArtifactProvider

# ADK imports are optional — only needed when actually using this adapter
try:
    from google.adk.sessions import BaseSessionService, GetSessionConfig, ListSessionsResponse
    from google.adk.memory import BaseMemoryService
    from google.adk.artifacts import BaseArtifactService
    from google.adk.events import Event
    from google.adk.sessions import Session
    from google.genai import types

    _ADK_AVAILABLE = True
except ImportError:
    _ADK_AVAILABLE = False
    BaseSessionService = object
    BaseMemoryService = object
    BaseArtifactService = object

from .registry import AdapterRegistry


# ═══════════════════════════════════════════════════════════════════════
# Session Adapter
# ═══════════════════════════════════════════════════════════════════════


class RuneSessionService(BaseSessionService):
    """
    Adapter: ADK BaseSessionService → RuneSessionProvider.

    Converts ADK Session/Event objects ↔ Rune Checkpoints,
    then delegates all persistence to the provider.
    """

    def __init__(self, session_provider: RuneSessionProvider):
        self._provider = session_provider
        self._sessions: dict[str, Any] = {}

    async def create_session(
        self, *, app_name: str, user_id: str,
        state: Optional[dict] = None, session_id: Optional[str] = None,
    ) -> Any:
        sid = session_id or str(uuid.uuid4())

        if _ADK_AVAILABLE:
            session = Session(
                app_name=app_name, user_id=user_id,
                id=sid, state=state or {},
            )
        else:
            session = {
                "app_name": app_name, "user_id": user_id,
                "id": sid, "state": state or {}, "events": [],
            }

        self._sessions[sid] = session

        agent_id = f"{app_name}:{user_id}"
        checkpoint = Checkpoint(
            thread_id=sid, agent_id=agent_id, state=state or {},
            metadata={"app_name": app_name, "user_id": user_id, "framework": "adk"},
        )
        await self._provider.save_checkpoint(checkpoint)
        return session

    async def get_session(
        self, *, app_name: str, user_id: str,
        session_id: str, config: Optional[Any] = None,
    ) -> Optional[Any]:
        if session_id in self._sessions:
            return self._sessions[session_id]

        agent_id = f"{app_name}:{user_id}"
        cp = await self._provider.load_checkpoint(agent_id, session_id)
        if cp is None:
            return None

        if _ADK_AVAILABLE:
            session = Session(
                app_name=app_name, user_id=user_id,
                id=session_id, state=cp.state,
            )
        else:
            session = {
                "app_name": app_name, "user_id": user_id,
                "id": session_id, "state": cp.state, "events": [],
            }

        self._sessions[session_id] = session
        return session

    async def append_event(self, session: Any, event: Any) -> Any:
        """
        Append an event to the session and persist updated state.

        This is an ADK-specific convenience method. It applies the event's
        state_delta to the session state, appends the event, and saves
        a new checkpoint.
        """
        # Extract session ID and agent ID
        if _ADK_AVAILABLE and hasattr(session, 'id'):
            sid = session.id
            app_name = session.app_name
            user_id = session.user_id
            # Apply state delta
            if hasattr(event, 'actions') and event.actions and hasattr(event.actions, 'state_delta'):
                if event.actions.state_delta:
                    session.state.update(event.actions.state_delta)
            if not hasattr(session, 'events') or session.events is None:
                session.events = []
            session.events.append(event)
        else:
            sid = session.get("id", "")
            app_name = session.get("app_name", "")
            user_id = session.get("user_id", "")
            # Apply state delta
            if hasattr(event, 'actions') and event.actions and hasattr(event.actions, 'state_delta'):
                if event.actions.state_delta:
                    session.get("state", {}).update(event.actions.state_delta)
            session.setdefault("events", []).append(event)

        # Save updated state as checkpoint
        agent_id = f"{app_name}:{user_id}"
        state = session.state if hasattr(session, 'state') else session.get("state", {})
        cp = Checkpoint(
            thread_id=sid, agent_id=agent_id, state=dict(state),
            metadata={"app_name": app_name, "user_id": user_id, "framework": "adk"},
        )
        await self._provider.save_checkpoint(cp)
        return event

    async def delete_session(
        self, *, app_name: str, user_id: str, session_id: str,
    ) -> None:
        self._sessions.pop(session_id, None)
        agent_id = f"{app_name}:{user_id}"
        await self._provider.delete_checkpoint(agent_id, session_id)

    async def list_sessions(
        self, *, app_name: str, user_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Any:
        results = []
        for session in self._sessions.values():
            if _ADK_AVAILABLE:
                if session.app_name == app_name:
                    if user_id is None or session.user_id == user_id:
                        results.append(session)
            else:
                if session.get("app_name") == app_name:
                    if user_id is None or session.get("user_id") == user_id:
                        results.append(session)
        if limit:
            results = results[:limit]
        if _ADK_AVAILABLE:
            return ListSessionsResponse(sessions=results)
        return results


# ═══════════════════════════════════════════════════════════════════════
# Memory Adapter
# ═══════════════════════════════════════════════════════════════════════


class RuneMemoryService(BaseMemoryService):
    """
    Adapter: ADK BaseMemoryService → RuneMemoryProvider.
    """

    def __init__(self, memory_provider: RuneMemoryProvider):
        self._provider = memory_provider

    async def add_session_to_memory(self, session: Any) -> None:
        if _ADK_AVAILABLE and hasattr(session, 'events'):
            agent_id = f"{session.app_name}:{session.user_id}"
            for event in (session.events or []):
                text = self._event_to_text(event)
                if text:
                    await self._provider.add(text, agent_id=agent_id, user_id=session.user_id)
        elif isinstance(session, dict):
            agent_id = f"{session.get('app_name', '')}:{session.get('user_id', '')}"
            user_id = session.get("user_id", "")
            for event in session.get("events", []):
                if isinstance(event, dict):
                    text = event.get("text", str(event))
                else:
                    text = self._event_to_text(event)
                if text:
                    await self._provider.add(text, agent_id=agent_id, user_id=user_id)

    async def search_memory(self, *, app_name: str, user_id: str, query: str) -> Any:
        agent_id = f"{app_name}:{user_id}"
        results = await self._provider.search(query, agent_id=agent_id, user_id=user_id)
        if _ADK_AVAILABLE:
            from google.adk.memory import SearchMemoryResponse, MemoryResult
            return SearchMemoryResponse(
                memories=[MemoryResult(content=r.content, score=r.score) for r in results],
            )
        return results

    @staticmethod
    def _event_to_text(event: Any) -> str:
        # Try content.parts first (standard ADK events with LLM responses)
        if hasattr(event, 'content') and event.content:
            parts = []
            if hasattr(event.content, 'parts'):
                for part in event.content.parts:
                    if hasattr(part, 'text') and part.text:
                        parts.append(part.text)
            if parts:
                return " ".join(parts)

        # Fall back to state_delta values (events with tool results / state updates)
        if hasattr(event, 'actions') and event.actions:
            delta = getattr(event.actions, 'state_delta', None)
            if delta:
                texts = [str(v) for k, v in delta.items()
                         if isinstance(v, str) and len(v) > 10]
                if texts:
                    return " | ".join(texts)

        return ""


# ═══════════════════════════════════════════════════════════════════════
# Artifact Adapter
# ═══════════════════════════════════════════════════════════════════════


class RuneArtifactService(BaseArtifactService):
    """
    Adapter: ADK BaseArtifactService → RuneArtifactProvider.
    """

    def __init__(self, artifact_provider: RuneArtifactProvider):
        self._provider = artifact_provider

    async def save_artifact(
        self, artifact: Any, *, app_name: str, user_id: str,
        session_id: str = "", metadata: Optional[dict] = None,
    ) -> int:
        data, content_type = self._to_bytes(artifact)
        agent_id = f"{app_name}:{user_id}"
        filename = metadata.get("filename", "artifact") if metadata else "artifact"
        return await self._provider.save(
            filename=filename, data=data, agent_id=agent_id,
            session_id=session_id, content_type=content_type or "", metadata=metadata,
        )

    async def load_artifact(
        self, artifact_id: str, *, app_name: str, user_id: str,
        session_id: str = "", version: Optional[int] = None,
    ) -> Optional[Any]:
        agent_id = f"{app_name}:{user_id}"
        artifact = await self._provider.load(
            filename=artifact_id, agent_id=agent_id,
            session_id=session_id, version=version,
        )
        if artifact is None:
            return None
        if _ADK_AVAILABLE:
            return self._from_bytes(artifact.data, artifact.content_type)
        return artifact.data

    @staticmethod
    def _to_bytes(artifact: Any) -> tuple[bytes, Optional[str]]:
        if isinstance(artifact, bytes):
            return artifact, None
        # Check for ADK Part-like objects (works even if ADK import failed)
        if hasattr(artifact, 'inline_data') and artifact.inline_data is not None:
            return artifact.inline_data.data, getattr(artifact.inline_data, 'mime_type', None)
        if isinstance(artifact, str):
            return artifact.encode("utf-8"), "text/plain"
        import json
        return json.dumps(artifact, default=str).encode("utf-8"), "application/json"

    @staticmethod
    def _from_bytes(data: bytes, content_type: str = "") -> Any:
        try:
            from google.genai import types as _types
            return _types.Part(
                inline_data=_types.Blob(data=data, mime_type=content_type or "application/octet-stream"),
            )
        except ImportError:
            return data


# Register
AdapterRegistry.register("adk", RuneSessionService)
