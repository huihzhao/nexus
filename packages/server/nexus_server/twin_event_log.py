"""Read-only views over each user's per-twin EventLog SQLite.

Background — S5 of the post-Phase-D server cleanup
==================================================
Up to S4 the server kept its own table (``sync_events``) that mirrored
the desktop's local event log. Once chat moved through Nexus's
DigitalTwin (S1), twin's own SDK ``EventLog`` became the single
authoritative event store — server-side ``sync_events`` was reduced to
a passive mirror written by ``twin_manager._build_on_event``. S5
finishes the job: the agent_state HTTP endpoints stop reading the
mirror and read directly from each user's twin EventLog SQLite file.

Why this isn't reading via ``DigitalTwin.create``
-------------------------------------------------
Instantiating a twin is heavy (LLM client init, ChainBackend bring-up,
session restore from Greenfield). The ``/agent/state`` snapshot — and
the polled ``/agent/timeline`` / ``/agent/memories`` requests behind
the desktop sidebar — must be fast and shouldn't trigger any of that.
SDK's ``EventLog`` is plain SQLite under the hood (one ``events`` table,
WAL mode, well-defined schema), so we open the per-user DB read-only
with stdlib ``sqlite3`` and run direct queries. No twin start-up cost,
no risk of mutating state mid-read.

File layout
-----------
``twin_manager`` builds each twin with ::

    base_dir = TWIN_BASE_DIR / user_id
    agent_id = f"user-{user_id[:8]}"

SDK's EventLog stores its DB at
``{base_dir}/event_log/{agent_id}.db``, so for user ``abc1234…`` we end
up at ``~/.nexus_server/twins/abc1234…/event_log/user-abc12345.db``.

EventLog schema (single ``events`` table)
-----------------------------------------
``idx`` INTEGER PRIMARY KEY AUTOINCREMENT — used here as the ``sync_id``
the desktop expects on the wire (within-user monotonic).

``timestamp`` REAL — unix seconds, converted to ISO-8601 on output for
parity with the legacy ``sync_events.server_received_at`` shape.

``event_type`` TEXT, ``content`` TEXT, ``metadata`` TEXT (JSON),
``agent_id`` TEXT, ``session_id`` TEXT.

Falsey behaviour
----------------
A user who has never chatted has no ``events.db`` on disk. Every helper
in this module treats "file missing" as "no events" and returns the
empty answer for that read shape (empty list / zero count). That's the
correct behaviour: such a user genuinely has nothing to show, and the
sidebar already renders the empty state for it.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Path resolution ───────────────────────────────────────────────────


def _twin_base_dir() -> Path:
    """Where TwinManager places per-user twin data dirs.

    Read from NEXUS_TWIN_BASE_DIR if set (matches twin_manager's env
    contract), else fall back to ``~/.nexus_server/twins`` — same default
    as ``twin_manager.TWIN_BASE_DIR``.
    """
    return Path(
        os.environ.get(
            "NEXUS_TWIN_BASE_DIR",
            os.path.expanduser("~/.nexus_server/twins"),
        )
    )


def _agent_id_for(user_id: str) -> str:
    """Mirror twin_manager._create_twin's agent_id derivation so the
    db file path lines up. Keep these in lockstep — if you change one,
    change the other (or hoist into a shared constant)."""
    return f"user-{user_id[:8]}"


def _db_path(user_id: str) -> Path:
    return (
        _twin_base_dir() / user_id / "event_log" / f"{_agent_id_for(user_id)}.db"
    )


def _open_readonly(user_id: str) -> Optional[sqlite3.Connection]:
    """Open a user's EventLog SQLite read-only. ``None`` on miss.

    URI mode + ``mode=ro`` so a stray write would error rather than
    silently mutate the agent's source of truth.
    """
    p = _db_path(user_id)
    if not p.exists():
        return None
    try:
        # Path → URI: needs forward slashes and proper escaping. ``Path.as_uri``
        # produces ``file:///abs/path``; SQLite expects just the path part
        # plus ``?mode=ro``.
        uri = f"file:{p}?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=2.0)
    except sqlite3.Error as e:
        logger.warning("twin_event_log: open failed for %s: %s", user_id, e)
        return None


def _ts_to_iso(ts: float | int | None) -> str:
    if ts is None:
        return ""
    try:
        return (
            datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
        )
    except Exception:
        return ""


def _safe_json(s: str | None) -> dict:
    if not s:
        return {}
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


# ── Counts ────────────────────────────────────────────────────────────


def count_by_type(user_id: str, event_types: list[str]) -> int:
    """Number of events whose ``event_type`` is in the given list."""
    if not event_types:
        return 0
    conn = _open_readonly(user_id)
    if conn is None:
        return 0
    try:
        placeholders = ",".join("?" * len(event_types))
        row = conn.execute(
            f"SELECT COUNT(*) FROM events WHERE event_type IN ({placeholders})",
            event_types,
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error as e:
        logger.warning("count_by_type failed for %s: %s", user_id, e)
        return 0
    finally:
        conn.close()


def memory_compact_count(user_id: str) -> int:
    """Compatibility helper: how many ``memory_compact`` events the
    user's twin has produced. Replaces the old
    ``memory_service.memory_compact_count`` (which read sync_events)."""
    return count_by_type(user_id, ["memory_compact"])


# ── Memories ──────────────────────────────────────────────────────────


def list_memory_compacts(user_id: str, limit: int = 50) -> list[dict]:
    """Return memory_compact events newest-first, shaped for the desktop's
    MemoryEntry model.

    Matches the contract that ``agent_state.MemoryEntry`` expects so the
    pivot to twin's event_log is invisible to the desktop client.
    """
    conn = _open_readonly(user_id)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            """
            SELECT idx, content, metadata, timestamp
            FROM events
            WHERE event_type = 'memory_compact'
            ORDER BY idx DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except sqlite3.Error as e:
        logger.warning("list_memory_compacts failed for %s: %s", user_id, e)
        rows = []
    finally:
        conn.close()

    out: list[dict] = []
    for idx, content, meta_json, ts in rows:
        meta = _safe_json(meta_json)
        projected = meta.get("projected_from") or [None, None]
        if not isinstance(projected, list):
            projected = [None, None]
        out.append({
            "sync_id": int(idx),
            "content": content or "",
            "first_sync_id": projected[0] if len(projected) >= 1 else None,
            "last_sync_id": projected[1] if len(projected) >= 2 else None,
            "event_count": int(meta.get("event_count", 0) or 0),
            "char_count": int(
                meta.get("char_count", len(content or "")) or 0
            ),
            "created_at": _ts_to_iso(ts),
        })
    return out


# ── Chat history ──────────────────────────────────────────────────────


def list_messages(
    user_id: str, limit: int, before_idx: Optional[int] = None
) -> tuple[list[dict], int]:
    """Recent chat turns for the desktop's history pane.

    Returns ``(messages_oldest_first, total_count)``. ``before_idx`` is
    a pagination cursor (mirrors the legacy ``before_sync_id`` query
    param on /agent/messages). Each message is shaped like
    ``ChatMessageView`` so the existing endpoint Pydantic model
    serialises unchanged.
    """
    conn = _open_readonly(user_id)
    if conn is None:
        return [], 0
    try:
        where = "event_type IN ('user_message', 'assistant_response')"
        params: list = []
        if before_idx is not None:
            where += " AND idx < ?"
            params.append(int(before_idx))
        params.append(limit)
        rows = conn.execute(
            f"""
            SELECT idx, event_type, content, timestamp
            FROM events
            WHERE {where}
            ORDER BY idx DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        total = int(conn.execute(
            "SELECT COUNT(*) FROM events "
            "WHERE event_type IN ('user_message', 'assistant_response')"
        ).fetchone()[0])
    except sqlite3.Error as e:
        logger.warning("list_messages failed for %s: %s", user_id, e)
        return [], 0
    finally:
        conn.close()

    # DESC fetch above so the LIMIT picks the *newest* N; flip back to
    # oldest-at-top for the desktop renderer.
    rows = list(reversed(rows))
    msgs = [
        {
            "role": "user" if r[1] == "user_message" else "assistant",
            "content": r[2] or "",
            "timestamp": _ts_to_iso(r[3]),
            "sync_id": int(r[0]),
        }
        for r in rows
    ]
    return msgs, total


# ── Timeline (raw, server merges with anchors) ────────────────────────


def list_timeline_events(user_id: str, limit: int) -> list[dict]:
    """Return raw event rows for the timeline endpoint to render.

    The server merges these with sync_anchors and converts them to
    ``TimelineItem`` shape. Keeping the merge there (rather than baking
    anchors into this helper) preserves layer separation: this module
    is a thin reader over twin's event_log; anchor lifecycle is a
    legacy concern owned by sync_anchor.
    """
    conn = _open_readonly(user_id)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            """
            SELECT idx, event_type, content, metadata, timestamp
            FROM events
            ORDER BY idx DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except sqlite3.Error as e:
        logger.warning("list_timeline_events failed for %s: %s", user_id, e)
        rows = []
    finally:
        conn.close()

    return [
        {
            "sync_id": int(idx),
            "event_type": et,
            "content": content or "",
            "metadata": _safe_json(meta_json),
            "timestamp": _ts_to_iso(ts),
        }
        for (idx, et, content, meta_json, ts) in rows
    ]


# ── Test helpers ──────────────────────────────────────────────────────
#
# Tests used to build state by inserting into ``sync_events`` directly.
# After S5 the canonical store is twin's per-user event_log SQLite, so
# tests need a small writer that mirrors what twin's append() does
# without spinning up a DigitalTwin. This is intentionally NOT exposed
# via ``__all__`` and lives under a ``_test_`` prefix — production code
# should never call it.


def _test_append_event(
    user_id: str,
    event_type: str,
    content: str,
    metadata: Optional[dict] = None,
    session_id: str = "",
    timestamp: Optional[float] = None,
) -> int:
    """Append one row to a user's twin EventLog — test-only.

    Creates the directory + table layout on first call so a test can
    seed events for a freshly-registered user that never opened a
    twin. Returns the row's ``idx`` (== sync_id on the wire).
    """
    p = _db_path(user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                idx INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                event_type TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                agent_id TEXT NOT NULL,
                session_id TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)"
        )
        ts = timestamp if timestamp is not None else datetime.now(
            timezone.utc
        ).timestamp()
        cur = conn.execute(
            """
            INSERT INTO events
            (timestamp, event_type, content, metadata, agent_id, session_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ts,
                event_type,
                content,
                json.dumps(metadata or {}),
                _agent_id_for(user_id),
                session_id,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()
