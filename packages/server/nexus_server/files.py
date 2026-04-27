"""File upload endpoint for the thin desktop client.

The desktop used to read attachments locally, base64-encode them, and
embed them in the chat request. That's logic that doesn't belong on a
thin client — moved to server (Round 2-B):

    POST /api/v1/files/upload   (multipart/form-data, field "file")
        → { "file_id": "...", "name": "...", "size": N, "mime": "..." }

The server stores the bytes under the user's data dir; the chat
endpoint accepts ``attachment_ids`` referring to these.

Storage today is a per-user filesystem cache. When Round 2-D collapses
to twin's ChainBackend, files will go straight to the user's
Greenfield bucket via ``twin.tools.file_reader.store(...)``.
"""

from __future__ import annotations

import logging
import mimetypes
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from nexus_server.auth import get_current_user
from nexus_server.config import get_config
from nexus_server.database import get_db_connection

logger = logging.getLogger(__name__)
config = get_config()

router = APIRouter(prefix="/api/v1/files", tags=["files"])


# Hard cap mirrors llm_gateway's MAX_ATTACHMENT_BYTES_TOTAL — a single
# file shouldn't be bigger than the chat-time per-call cap.
MAX_FILE_BYTES = 100 * 1024 * 1024


def _files_dir() -> Path:
    """Where uploads live before twin/Greenfield consumes them."""
    base = Path(getattr(config, "UPLOAD_DIR",
                        Path.home() / ".nexus_server" / "uploads"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def _ensure_uploads_table() -> None:
    """Lazy table create — keeps database.py focused on core schema."""
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS uploads (
                file_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                mime TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                disk_path TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_uploads_user "
            "ON uploads(user_id, created_at DESC)"
        )
        conn.commit()


class UploadResponse(BaseModel):
    file_id: str
    name: str
    mime: str
    size_bytes: int


@router.post("/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    current_user: str = Depends(get_current_user),
) -> UploadResponse:
    """Receive a multipart upload and stash it under the user's data dir."""
    _ensure_uploads_table()

    # Stream-read with a hard size cap. Naïve approach — fine for files
    # up to MAX_FILE_BYTES; switch to chunked tee + cap if we grow this.
    raw = await file.read()
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {MAX_FILE_BYTES // (1024*1024)} MB limit",
        )

    name = file.filename or "upload"
    # Trust client-provided mime when given, else guess from extension.
    mime = file.content_type or (mimetypes.guess_type(name)[0] or
                                 "application/octet-stream")
    file_id = uuid.uuid4().hex
    user_dir = _files_dir() / current_user
    user_dir.mkdir(parents=True, exist_ok=True)
    disk_path = user_dir / f"{file_id}-{_safe_name(name)}"
    disk_path.write_bytes(raw)

    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO uploads
            (file_id, user_id, name, mime, size_bytes, disk_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (file_id, current_user, name, mime, len(raw),
             str(disk_path), now_iso),
        )
        conn.commit()

    logger.info(
        "Uploaded file %s (%s, %d bytes) for user %s",
        name, mime, len(raw), current_user,
    )
    return UploadResponse(
        file_id=file_id, name=name, mime=mime, size_bytes=len(raw),
    )


def _safe_name(name: str) -> str:
    """Strip path traversal + bad chars from filename for disk storage."""
    bad = '/\\:*?"<>|'
    return "".join("_" if c in bad else c for c in name)[:128]


# ── Internal helpers used by llm_gateway when resolving attachment_ids ──


def resolve_files(user_id: str, file_ids: list[str]) -> list[dict]:
    """Look up uploaded files by id (scoped to user) and return their
    on-disk content + metadata for the chat handler / distiller. The
    caller — typically llm_gateway when an Attachment.file_id is set —
    is responsible for reading bytes from ``disk_path``.
    """
    if not file_ids:
        return []
    placeholders = ",".join("?" * len(file_ids))
    with get_db_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT file_id, name, mime, size_bytes, disk_path
            FROM uploads
            WHERE user_id = ? AND file_id IN ({placeholders})
            """,
            (user_id, *file_ids),
        ).fetchall()
    return [
        {
            "file_id": r[0],
            "name": r[1],
            "mime": r[2],
            "size_bytes": int(r[3]),
            "disk_path": r[4],
        }
        for r in rows
    ]


def read_file_bytes(disk_path: str) -> Optional[bytes]:
    p = Path(disk_path)
    if not p.exists():
        return None
    return p.read_bytes()
