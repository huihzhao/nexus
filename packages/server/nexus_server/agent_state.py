"""User-facing agent state endpoints.

These are the shape that powers the desktop's redesigned sidebar:

  GET /api/v1/agent/state      → quick snapshot (counts + latest anchor)
  GET /api/v1/agent/timeline   → unified event stream for the activity panel
  GET /api/v1/agent/memories   → list of MemoryService-extracted memories

Each endpoint is read-only and authenticated; all heavy lifting (memory
extraction, anchoring, etc.) happens elsewhere — these are just views.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from nexus_server.auth import get_current_user
from nexus_server.database import get_db_connection
from nexus_server.sync_anchor import list_anchors_for_user
from nexus_server import twin_event_log

# Back-compat re-exports (S3 → S5 evolution): these used to live here
# as functions reading sync_events. After S5 they delegate to
# twin_event_log, which reads each user's per-twin SQLite directly.
# Kept as module-level bindings so any straggler callers still work.
list_memory_compacts = twin_event_log.list_memory_compacts
memory_compact_count = twin_event_log.memory_compact_count

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

# Secondary router for the legacy /api/v1/sync/anchors path. Phase B
# deleted ``sync_hub.py`` (which used to host /sync/push, /sync/pull,
# AND /sync/anchors); the first two are gone for good but /sync/anchors
# is still useful as a read-only view of historical anchor lifecycle.
# The path stays /api/v1/sync/anchors so existing desktop clients
# don't need to change.
sync_router = APIRouter(prefix="/api/v1/sync", tags=["sync"])


# ───────────────────────────────────────────────────────────────────────────
# Models
# ───────────────────────────────────────────────────────────────────────────


class MemoryEntry(BaseModel):
    """One memory_compact event surfaced as a "memory" snapshot.

    Aligns with SDK ARCHITECTURE.md — there's no separate memory store,
    every memory is a derived view of EventLog's memory_compact entries.
    The desktop renders these in the sidebar's Memories panel.
    """
    sync_id: int
    content: str
    first_sync_id: Optional[int]
    last_sync_id: Optional[int]
    event_count: int
    char_count: int
    created_at: str


class MemoriesResponse(BaseModel):
    memories: list[MemoryEntry]
    total: int


class ChatMessageView(BaseModel):
    """A single chat message as rendered by the desktop. Server is the
    single source of truth — desktop never persists messages locally
    after the thin-client refactor (Round 2)."""
    role: str           # "user" | "assistant"
    content: str
    timestamp: str      # ISO-8601 (server_received_at)
    sync_id: int


class ChatMessagesResponse(BaseModel):
    messages: list[ChatMessageView]
    total: int


class TimelineItem(BaseModel):
    """One row of the activity stream.

    ``kind`` distinguishes events the UI renders differently:
      - chat.user / chat.assistant  → conversation turns
      - file.attached / file.distilled → attachment lifecycle
      - memory.extracted            → MemoryService output
      - anchor.{status}             → sync_anchor lifecycle
    """
    kind: str
    timestamp: str
    summary: str
    sync_id: Optional[int] = None
    anchor_id: Optional[int] = None
    metadata: dict = {}


class TimelineResponse(BaseModel):
    items: list[TimelineItem]


class AgentStateSnapshot(BaseModel):
    """Quick state read for the sidebar header / counters.

    The anchor counters are the **union** of two sources:
      - legacy ``sync_anchors`` rows (pre-S4 history; never grows for
        new users after S4 retired the /sync/push enqueue path)
      - new ``twin_chain_events`` rows (post-S4, written by the chain
        activity log handler in :mod:`twin_manager` whenever twin's
        ChainBackend commits a BSC anchor or attempts a Greenfield PUT)

    For chat-mode users today the meaningful signal lives in
    twin_chain_events; sync_anchors is only relevant for users whose
    accounts pre-date S4.
    """
    user_id: str
    chain_agent_id: Optional[int]
    chain_register_tx: Optional[str]
    network: str
    on_chain: bool
    memory_count: int
    anchored_count: int
    pending_anchor_count: int
    failed_anchor_count: int
    total_anchor_count: int
    last_anchor: Optional[dict] = None
    # Last chain activity (success OR failure) — surfaced so the
    # desktop top bar can show "Last write: 3s ago, ok" or
    # "Last write: 12s ago, failed: bucket missing" without a separate
    # round-trip.
    last_chain_event: Optional[dict] = None
    server_time: str


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────


_EVENT_KIND_MAP = {
    "user_message": "chat.user",
    "assistant_response": "chat.assistant",
    "attachment_added": "file.attached",
    "attachment_distilled": "file.distilled",
    "memory_compact": "memory.compact",
}


def _truncate(s: str, n: int = 280) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[:n] + "…"


def _build_timeline(user_id: str, limit: int) -> list[TimelineItem]:
    """Merge twin event_log events + sync_anchors into a single
    chronological feed.

    S5: events come from the user's twin EventLog SQLite (read via
    ``twin_event_log.list_timeline_events``) instead of the legacy
    server-side sync_events mirror. Anchors continue to come from
    ``sync_anchors`` because that table is still the only place the
    legacy anchor lifecycle lives — its eventual replacement (chain-mode
    twin's ChainBackend) doesn't expose a per-user anchor history yet.

    We over-fetch (limit×2) from each source then merge and trim, so a
    burst of one kind doesn't starve the other. Newest first.
    """
    over = max(limit * 2, 50)
    items: list[TimelineItem] = []

    evt_rows = twin_event_log.list_timeline_events(user_id, over)

    with get_db_connection() as conn:
        # Anchor lifecycle (use updated_at since it captures status transitions)
        anch_rows = conn.execute(
            """
            SELECT anchor_id, content_hash, bsc_tx_hash, status,
                   first_sync_id, last_sync_id, event_count, retry_count,
                   updated_at
            FROM sync_anchors
            WHERE user_id = ?
            ORDER BY anchor_id DESC
            LIMIT ?
            """,
            (user_id, over),
        ).fetchall()

    for evt in evt_rows:
        sync_id = evt["sync_id"]
        et = evt["event_type"]
        content = evt["content"]
        meta = evt["metadata"]
        ts = evt["timestamp"]
        kind = _EVENT_KIND_MAP.get(et, f"event.{et}")
        if kind == "chat.user":
            summary = _truncate(content, 200)
        elif kind == "chat.assistant":
            summary = _truncate(content, 200)
        elif kind == "file.attached":
            summary = f"📎 {meta.get('name', 'file')} ({meta.get('size_bytes', 0)} bytes)"
        elif kind == "file.distilled":
            summary = f"💎 distilled {meta.get('name', 'file')} → {meta.get('summary_chars', 0)} chars"
        elif kind == "memory.compact":
            n_events = meta.get("event_count", "?")
            summary = f"🧠 Memory snapshot ({n_events} events) — {_truncate(content, 160)}"
        else:
            summary = _truncate(content, 200)
        items.append(TimelineItem(
            kind=kind,
            timestamp=ts,
            summary=summary,
            sync_id=sync_id,
            metadata=meta,
        ))

    # Twin chain events (post-S4 — captured by the logging handler in
    # twin_manager). Surface every entry so the user can see both
    # successful BSC anchors AND Greenfield PUT failures right in the
    # activity feed instead of having to dig through server logs.
    with get_db_connection() as conn:
        twin_rows = conn.execute(
            """
            SELECT event_id, kind, status, summary, tx_hash, content_hash,
                   object_path, error, duration_ms, created_at
            FROM twin_chain_events
            WHERE user_id = ?
            ORDER BY event_id DESC
            LIMIT ?
            """,
            (user_id, over),
        ).fetchall()
    for (eid, kind, status, summary, txh, chash, opath, err,
         dur_ms, ts) in twin_rows:
        if kind == "bsc_anchor" and status == "ok":
            kind_str = "anchor.committed"
            display = (
                f"🔗 BSC anchor committed — tx {txh[:10]+'…' if txh else '?'}"
            )
        elif kind == "bsc_anchor" and status == "failed":
            kind_str = "anchor.failed"
            display = f"✕ BSC anchor failed: {(err or '')[:120]}"
        elif kind == "greenfield_put" and status == "ok":
            kind_str = "greenfield.put_ok"
            display = f"💾 Greenfield PUT {opath or ''}"
        elif kind == "greenfield_put" and status == "failed":
            kind_str = "greenfield.put_failed"
            display = f"✕ Greenfield PUT failed: {(err or '')[:120]}"
        else:
            kind_str = f"chain.{kind}.{status}"
            display = summary or f"{kind} {status}"
        items.append(TimelineItem(
            kind=kind_str,
            timestamp=ts,
            summary=display,
            metadata={
                "tx_hash": txh,
                "content_hash": chash,
                "object_path": opath,
                "error": err,
                "duration_ms": dur_ms,
                "twin_event_id": eid,
            },
        ))

    for (aid, chash, txh, status, first_id, last_id, n, retry, ts) in anch_rows:
        if status == "anchored":
            summary = (
                f"🔗 anchored {n} event(s) — tx {txh[:10]+'…' if txh else '(no tx)'}"
            )
        elif status == "pending":
            summary = f"⏳ anchoring {n} event(s) (hash {chash[:8]}…)"
        elif status == "failed":
            summary = f"↻ anchor retry #{retry} (hash {chash[:8]}…)"
        elif status == "failed_permanent":
            summary = f"✕ anchor failed permanently (hash {chash[:8]}…)"
        elif status == "awaiting_registration":
            summary = f"⌛ anchor waiting for chain registration ({n} event(s))"
        elif status == "stored_only":
            summary = f"◐ stored locally — chain disabled ({n} event(s))"
        else:
            summary = f"anchor {status}: {chash[:8]}…"
        items.append(TimelineItem(
            kind=f"anchor.{status}",
            timestamp=ts,
            summary=summary,
            anchor_id=aid,
            metadata={
                "content_hash": chash,
                "bsc_tx_hash": txh,
                "first_sync_id": first_id,
                "last_sync_id": last_id,
                "event_count": n,
                "retry_count": retry,
            },
        ))

    items.sort(key=lambda i: i.timestamp, reverse=True)
    return items[:limit]


def _anchor_status_counts(user_id: str) -> dict[str, int]:
    """Legacy sync_anchors counters (pre-S4 history only)."""
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM sync_anchors WHERE user_id = ? "
            "GROUP BY status",
            (user_id,),
        ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def _twin_chain_event_counts(user_id: str) -> dict[str, int]:
    """Bug 3: counts of chain writes captured from twin's ChainBackend.

    Returns ``{"bsc_anchor_ok", "bsc_anchor_failed", "greenfield_ok",
    "greenfield_failed"}`` keys, each an int. Empty/zero for users
    whose twin hasn't written anything yet.
    """
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT kind, status, COUNT(*) FROM twin_chain_events "
            "WHERE user_id = ? GROUP BY kind, status",
            (user_id,),
        ).fetchall()
    out: dict[str, int] = {}
    for kind, status, n in rows:
        out[f"{kind}_{status}"] = int(n)
    return out


def _last_twin_chain_event(user_id: str, *, status: Optional[str] = None) -> Optional[dict]:
    """Newest twin_chain_events row, optionally filtered by status."""
    where = "user_id = ?"
    params: list = [user_id]
    if status is not None:
        where += " AND status = ?"
        params.append(status)
    with get_db_connection() as conn:
        row = conn.execute(
            f"""
            SELECT event_id, kind, status, summary, tx_hash, content_hash,
                   object_path, error, duration_ms, created_at
            FROM twin_chain_events
            WHERE {where}
            ORDER BY event_id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
    if row is None:
        return None
    return {
        "event_id": int(row[0]),
        "kind": row[1],
        "status": row[2],
        "summary": row[3] or "",
        "tx_hash": row[4],
        "content_hash": row[5],
        "object_path": row[6],
        "error": row[7],
        "duration_ms": row[8],
        "created_at": row[9],
    }


# ───────────────────────────────────────────────────────────────────────────
# Routes
# ───────────────────────────────────────────────────────────────────────────


@router.get("/state", response_model=AgentStateSnapshot)
async def get_agent_state(
    current_user: str = Depends(get_current_user),
) -> AgentStateSnapshot:
    """One-shot read for the sidebar."""
    from datetime import datetime, timezone
    from nexus_server.config import get_config
    config = get_config()

    with get_db_connection() as conn:
        urow = conn.execute(
            "SELECT chain_agent_id, chain_register_tx FROM users WHERE id = ?",
            (current_user,),
        ).fetchone()
    chain_agent_id = urow[0] if urow else None
    register_tx = urow[1] if urow else None

    legacy = _anchor_status_counts(current_user)
    twin_counts = _twin_chain_event_counts(current_user)

    # Sum legacy + new sources so the UI sees one unified counter
    # regardless of which path produced the anchor. After S4 the legacy
    # bucket only carries pre-existing history; new chat traffic flows
    # through twin_chain_events.
    anchored = (
        legacy.get("anchored", 0)
        + twin_counts.get("bsc_anchor_ok", 0)
    )
    # No "pending" semantic in twin_chain_events (chain writes are
    # synchronous w.r.t. the BSC tx receipt). Pending only reflects
    # legacy rows.
    pending = (
        legacy.get("pending", 0)
        + legacy.get("awaiting_registration", 0)
    )
    failed = (
        legacy.get("failed", 0)
        + legacy.get("failed_permanent", 0)
        + twin_counts.get("bsc_anchor_failed", 0)
        + twin_counts.get("greenfield_put_failed", 0)
    )
    total = (
        sum(legacy.values())
        + sum(twin_counts.values())
    )

    anchors = list_anchors_for_user(current_user, limit=1)
    last_anchor = anchors[0] if anchors else None
    last_chain_event = _last_twin_chain_event(current_user)

    return AgentStateSnapshot(
        user_id=current_user,
        chain_agent_id=chain_agent_id,
        chain_register_tx=register_tx,
        network=config.NEXUS_NETWORK,
        on_chain=chain_agent_id is not None,
        memory_count=memory_compact_count(current_user),
        anchored_count=anchored,
        pending_anchor_count=pending,
        failed_anchor_count=failed,
        total_anchor_count=total,
        last_anchor=last_anchor,
        last_chain_event=last_chain_event,
        server_time=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/timeline", response_model=TimelineResponse)
async def get_timeline(
    limit: int = 60,
    current_user: str = Depends(get_current_user),
) -> TimelineResponse:
    """Newest-first activity stream."""
    if limit <= 0 or limit > 200:
        limit = 60
    return TimelineResponse(items=_build_timeline(current_user, limit))


@router.get("/messages", response_model=ChatMessagesResponse)
async def get_messages(
    limit: int = 200,
    before_sync_id: Optional[int] = None,
    current_user: str = Depends(get_current_user),
) -> ChatMessagesResponse:
    """Server-authoritative chat history.

    Replaces the desktop's old LocalEventLog (Round 2 thin-client
    refactor): the desktop pulls history from here on every login and
    renders messages from this stream alone.

    S5: the source of truth pivoted from server-side sync_events (a
    mirror) to twin's per-user EventLog SQLite (the original).
    ``twin_event_log.list_messages`` opens that file read-only so this
    endpoint never has to instantiate a DigitalTwin to serve a state
    read.

    Returns oldest first within the requested window. ``before_sync_id``
    is the pagination cursor — it maps to the EventLog's ``idx``.
    """
    if limit <= 0 or limit > 500:
        limit = 200

    raw_messages, total = twin_event_log.list_messages(
        current_user, limit, before_idx=before_sync_id,
    )
    messages = [
        ChatMessageView(
            role=m["role"],
            content=m["content"],
            timestamp=m["timestamp"],
            sync_id=m["sync_id"],
        )
        for m in raw_messages
    ]
    return ChatMessagesResponse(messages=messages, total=total)


@router.get("/memories", response_model=MemoriesResponse)
async def get_memories(
    limit: int = 50,
    current_user: str = Depends(get_current_user),
) -> MemoriesResponse:
    """List MemoryService-extracted memories, newest first."""
    if limit <= 0 or limit > 200:
        limit = 50
    rows = list_memory_compacts(current_user, limit=limit)
    return MemoriesResponse(
        memories=[MemoryEntry(**r) for r in rows],
        total=memory_compact_count(current_user),
    )


# ── /api/v1/sync/anchors — read-only legacy view (Phase B migration) ─


@sync_router.get("/anchors")
async def get_sync_anchors(
    limit: int = 20,
    current_user: str = Depends(get_current_user),
) -> dict:
    """Newest-first anchor lifecycle rows (pre-S4 history).

    The /sync/push enqueue path is gone (Phase B); chat-mode twin's
    ChainBackend writes BSC anchors directly without touching this
    table. So in production this list only grows for users that
    pre-date S4. Kept as a back-compat read view for the desktop
    sidebar's anchor count badge.
    """
    if limit <= 0 or limit > 200:
        limit = 20
    rows = list_anchors_for_user(current_user, limit=limit)
    return {"anchors": rows}
