"""Database utilities and initialization.

Handles SQLite connection management and schema setup.
"""

import logging
import sqlite3
from contextlib import contextmanager
from typing import Generator

from nexus_server.config import get_config

logger = logging.getLogger(__name__)
config = get_config()


@contextmanager
def get_db_connection() -> Generator[sqlite3.Connection, None, None]:
    """Get a database connection context manager.

    Yields:
        SQLite connection with row factory enabled.
    """
    db_path = config.DATABASE_URL.replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Initialize SQLite database with required tables."""
    db_path = config.DATABASE_URL.replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Users table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            passkey_credential TEXT,
            jwt_secret TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            chain_agent_id INTEGER,
            chain_register_tx TEXT
        )
        """
    )

    # Idempotent migration: add chain_agent_id / chain_register_tx columns
    # if the table predates them (CREATE TABLE IF NOT EXISTS won't add new
    # columns on its own).
    cursor.execute("PRAGMA table_info(users)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    if "chain_agent_id" not in existing_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN chain_agent_id INTEGER")
    if "chain_register_tx" not in existing_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN chain_register_tx TEXT")

    # Phase B: ``sync_events`` table dropped.
    #
    # Pre-S5 the server mirrored every twin emit here so legacy
    # /agent/timeline + /agent/memories endpoints could read events
    # without poking into twin's per-user EventLog. After S5 those
    # endpoints opened twin's EventLog directly via ``twin_event_log``,
    # and the mirror became write-only — no production read path
    # consulted it. Phase B drops the table along with its three
    # writers (twin_manager._mirror_to_sync_events,
    # attachment_distiller.record_distilled_event, and the deleted
    # sync_hub /sync/push handler). If a stale instance still has the
    # table from a pre-Phase-B boot, it sits there harmless — nothing
    # writes to or reads from it.

    # Rate limiting table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS rate_limits (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            request_count INTEGER NOT NULL,
            window_start TIMESTAMP NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    # NOTE: an earlier short-lived design had a separate `memories` table
    # for per-row insights (Nexus MemoryEvolver style). We walked that
    # back to align with SDK ARCHITECTURE.md's DPM principle: EventLog
    # is the single source of truth, every "memory" is a derived view of
    # `memory_compact` events in sync_events. The CREATE TABLE for
    # memories is intentionally absent here — its sister code in
    # memory_service.py reads memory_compact events from sync_events
    # directly.

    # Sync anchors table — one row per /sync/push batch that we attempt
    # to push to Greenfield + anchor on BSC. Status is the source of
    # truth for "did the durable copy land yet".
    #
    # Status values:
    #   'pending'              — created, work hasn't started
    #   'stored_only'          — Greenfield write succeeded, BSC anchor
    #                            skipped (no chain config or no agent id)
    #   'anchored'             — Greenfield + BSC both succeeded
    #   'failed'               — terminal failure (see error column)
    #   'awaiting_registration' — user has no chain_agent_id yet
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_anchors (
            anchor_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            first_sync_id INTEGER NOT NULL,
            last_sync_id INTEGER NOT NULL,
            event_count INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            greenfield_path TEXT,
            bsc_tx_hash TEXT,
            status TEXT NOT NULL,
            error TEXT,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_sync_anchors_user "
        "ON sync_anchors(user_id, anchor_id DESC)"
    )

    # retry_count: how many times the daemon has tried to push this row
    # past 'failed'/'awaiting_registration' into a terminal good state.
    # Idempotent migration so we don't crash on existing DBs.
    cursor.execute("PRAGMA table_info(sync_anchors)")
    sync_anchor_cols = {row[1] for row in cursor.fetchall()}
    if "retry_count" not in sync_anchor_cols:
        cursor.execute(
            "ALTER TABLE sync_anchors ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"
        )
    if "next_retry_at" not in sync_anchor_cols:
        cursor.execute(
            "ALTER TABLE sync_anchors ADD COLUMN next_retry_at TIMESTAMP"
        )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_sync_anchors_retry "
        "ON sync_anchors(status, next_retry_at)"
    )

    # ── twin_chain_events (Bug 3 visibility, post-S4) ────────────────
    # After S4 the legacy /sync/push → enqueue_anchor path stopped firing
    # for chat traffic, so ``sync_anchors`` no longer accumulates rows
    # for normal chat-mode users. The desktop sidebar's anchor counters
    # were therefore stuck at 0/0/0 even when twin's ChainBackend was
    # successfully writing BSC anchors and Greenfield objects in the
    # background. Without a row anywhere, "Greenfield put failed" only
    # surfaced in server stderr — invisible to the operator.
    #
    # This table is the new mirror: a logging.Handler in twin_manager
    # subscribes to the SDK's ``rune.backend.chain`` and
    # ``rune.greenfield`` loggers and writes one row per chain write
    # attempt. ``status`` is intentionally only ``ok`` / ``failed`` —
    # twin's ChainBackend is synchronous w.r.t. the BSC tx, so there
    # is no "pending" state to track here (unlike legacy sync_anchors).
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS twin_chain_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            summary TEXT,
            tx_hash TEXT,
            content_hash TEXT,
            object_path TEXT,
            error TEXT,
            duration_ms INTEGER,
            created_at TIMESTAMP NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_twin_chain_events_user "
        "ON twin_chain_events(user_id, event_id DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_twin_chain_events_status "
        "ON twin_chain_events(user_id, status)"
    )

    conn.commit()
    conn.close()
    logger.info("Database initialized")
