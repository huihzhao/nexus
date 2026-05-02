"""File upload endpoint + three-layer file resolver.

    POST /api/v1/files/upload   (multipart/form-data, field "file")
        → { "file_id": "...", "name": "...", "size": N, "mime": "..." }

The desktop streams attachments here; chat then references them by
``file_id`` rather than re-encoding base64 in every request.

Storage model — three layers, no in-memory state:

  Layer 1 (canonical, immutable):
      Greenfield bytes at ``files/<file_id>/<safe_name>`` inside
      the user's per-agent bucket, written via twin's ChainBackend
      on upload. The matching ``file_uploaded`` event lands in
      twin.event_log and participates in the next BSC state-root
      anchor — making the upload survive server replacement /
      database wipes / migration.

  Layer 2 (server cache, regenerable):
      ``uploads`` SQLite row (file_id, sha256, gnfd_path,
      extracted_text) + bytes on local disk. Fast path; rebuilt
      from Layer 1 if lost.

  Layer 3 (tool surface, stateless):
      ``ReadUploadedFileTool`` calls ``resolve_file_text`` below.
      No in-memory state on the tool side — the previous
      ``store()`` / ``store_path()`` API was removed once every
      production caller switched to this resolver path. See
      ``packages/sdk/nexus_core/tools/file_reader.py`` for the
      tool contract; ``test_file_reader_resolver.py`` guards that
      no in-memory fallback creeps back in.
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
    """Lazy table create — keeps database.py focused on core schema.

    Schema evolution (live three-layer file storage):
      v1: file_id, user_id, name, mime, size_bytes, disk_path, created_at
      v2: + sha256, gnfd_path, extracted_text
          - sha256: content hash, also the Greenfield key suffix
          - gnfd_path: gnfd://<bucket>/files/<file_id>/<name> — the
            Greenfield object path the ChainBackend wrote to. Set on
            successful Greenfield mirror; ``""`` until then. The disk
            copy under disk_path remains the fast path — gnfd_path is
            the recovery path after disk loss / cross-server hop.
          - extracted_text: cached plain-text projection so
            read_uploaded_file doesn't have to re-decode PDFs / DOCX
            on every cross-turn read. Lazy: filled on first read,
            persists for the file's lifetime.
    """
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
                created_at TIMESTAMP NOT NULL,
                sha256 TEXT NOT NULL DEFAULT '',
                gnfd_path TEXT NOT NULL DEFAULT '',
                extracted_text TEXT NOT NULL DEFAULT ''
            )
            """
        )
        # Migration for existing v1 rows: try ALTER TABLE; ignore
        # "duplicate column" failures so re-running on a v2 db is a
        # no-op. We don't drop the legacy DEFAULTs above (they keep
        # CREATE TABLE on a fresh db one statement).
        for col_def in (
            "sha256 TEXT NOT NULL DEFAULT ''",
            "gnfd_path TEXT NOT NULL DEFAULT ''",
            "extracted_text TEXT NOT NULL DEFAULT ''",
        ):
            try:
                conn.execute(f"ALTER TABLE uploads ADD COLUMN {col_def}")
            except Exception:
                pass  # column already exists
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_uploads_user "
            "ON uploads(user_id, created_at DESC)"
        )
        # Lookup-by-name path used by read_uploaded_file.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_uploads_user_name "
            "ON uploads(user_id, name)"
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
    """Receive a multipart upload, persist it across all three layers
    of the file-storage model, and return its addressable file_id.

    Layer 1 (canonical, immutable): bytes mirrored to the user's
    Greenfield bucket via twin's ChainBackend; an ``file_uploaded``
    event lands in twin.event_log so the metadata
    (file_id + sha256 + gnfd_path + name + mime + size) participates
    in the next state-root anchor on BSC. After a server crash /
    migration the file is recoverable from this layer alone.

    Layer 2 (fast cache, regenerable): bytes also kept on local disk
    under ``UPLOAD_DIR/<user>/`` and indexed by the ``uploads`` SQLite
    table. ``read_uploaded_file`` always tries this layer first.

    Layer 3 (tool surface, stateless): the SDK
    ``ReadUploadedFileTool`` queries Layer 2 by user_id + name; on
    miss falls back to Layer 1 via Greenfield + the EventLog. This
    means twin instance lifecycle (idle eviction, cold restart) no
    longer affects file recall — the previous in-memory ``_file_reader``
    cache was the source of the cross-turn file-not-found bug.
    """
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

    # Compute content hash up front — used as the integrity field on the
    # EventLog metadata + the Greenfield object's content-addressable
    # part of the gnfd_path. Cheap (sha256 on at most 100 MB).
    import hashlib
    sha256 = hashlib.sha256(raw).hexdigest()

    user_dir = _files_dir() / current_user
    user_dir.mkdir(parents=True, exist_ok=True)
    disk_path = user_dir / f"{file_id}-{_safe_name(name)}"
    disk_path.write_bytes(raw)

    # ── Layer 1 mirror: Greenfield + EventLog → BSC anchor ────────
    # Best-effort. If the user's twin or chain backend isn't ready
    # (fresh signup pre-registration, local-mode dev), we still
    # accept the upload — Layer 2 (disk + SQL) is enough for chat
    # to work. ``gnfd_path`` stays empty until a future re-mirror
    # opportunity.
    gnfd_path = ""
    try:
        from nexus_server.twin_manager import get_twin
        twin = await get_twin(current_user)
        # Path convention: files/<file_id>/<safe_name>. Bucket is
        # injected by ChainBackend so a per-agent bucket layout
        # (nexus-agent-{token_id}) Just Works.
        gnfd_path = f"files/{file_id}/{_safe_name(name)}"
        backend = getattr(twin, "rune", None)
        backend = getattr(backend, "_backend", None) if backend else None
        if backend is not None and hasattr(backend, "store_blob"):
            try:
                await backend.store_blob(gnfd_path, raw)
            except Exception as e:  # noqa: BLE001
                # store_blob is async write-behind; failures here are
                # rare and only mean the local cache succeeded.
                logger.warning(
                    "Greenfield mirror failed for %s: %s — disk + SQL "
                    "still serve chat, file recovery limited.",
                    name, e,
                )
                gnfd_path = ""
        # Emit the file_uploaded event regardless — Layer 1 anchor
        # records the metadata even if the Greenfield blob write
        # didn't land. The recovery path can re-fetch from disk OR
        # re-upload to Greenfield on next reference.
        try:
            twin.event_log.append(
                "file_uploaded",
                f"📎 uploaded {name}",
                metadata={
                    "file_id": file_id,
                    "name": name,
                    "mime": mime,
                    "size_bytes": len(raw),
                    "sha256": sha256,
                    "gnfd_path": gnfd_path,
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "event_log append for file_uploaded failed: %s", e,
            )
    except Exception as e:  # noqa: BLE001
        # Twin not ready (e.g. unauthenticated test path): we still
        # write to disk + SQL so the next chat works.
        logger.debug("twin/chain unavailable for %s: %s", name, e)

    # ── Layer 2: SQL + disk index ─────────────────────────────────
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO uploads
            (file_id, user_id, name, mime, size_bytes, disk_path,
             created_at, sha256, gnfd_path, extracted_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '')
            """,
            (file_id, current_user, name, mime, len(raw),
             str(disk_path), now_iso, sha256, gnfd_path),
        )
        conn.commit()

    logger.info(
        "Uploaded file %s (%s, %d bytes, sha256=%s, gnfd=%s) for user %s",
        name, mime, len(raw), sha256[:12],
        gnfd_path or "(skipped)", current_user,
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


# ── Layer 3 surface used by ReadUploadedFileTool ─────────────────────


async def resolve_file_text(
    user_id: str, name: str,
) -> Optional[tuple[str, str]]:
    """Three-layer fallback resolution for ``read_uploaded_file``.

    Returns ``(filename, full_text)`` on hit; ``None`` if the file
    isn't reachable through any layer.

    Lookup strategy:
      1. **SQL cache** — ``uploads.extracted_text`` is the hot path
         (already-decoded plain text, ready to slice).
      2. **Disk → extract** — bytes still on local disk under
         ``UPLOAD_DIR``. Run the SDK distiller's text extractor and
         write the result back to ``extracted_text`` so future
         turns are O(1) again.
      3. **Greenfield → extract** — disk gone (server migration,
         crash + missing volume). Pull bytes from the
         agent's Greenfield bucket via ChainBackend.load_blob; if
         that hits, repopulate the disk copy AND extracted_text so
         we degrade gracefully back to layer 2 for subsequent
         reads.
      4. **EventLog recovery** — last resort. If the SQL row is
         gone too (e.g. the SQLite was reset but EventLog +
         Greenfield remain), scan recent ``file_uploaded`` events
         for one whose ``metadata.name`` matches; that gives us
         file_id + gnfd_path and we re-fetch.

    All layers are best-effort and isolated — a Greenfield outage
    can't make the SQL fast path stop working.
    """
    _ensure_uploads_table()

    # Match by file_id (exact) OR name (substring tolerated). The tool
    # supports partial-name matching for ergonomics, but we resolve
    # the canonical row via SQL ORDER BY most-recent-first to avoid
    # ambiguity when the user uploaded the same name twice.
    row = None
    with get_db_connection() as conn:
        # Exact name first.
        rs = conn.execute(
            """
            SELECT file_id, name, mime, size_bytes, disk_path,
                   sha256, gnfd_path, extracted_text
            FROM uploads
            WHERE user_id = ? AND name = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (user_id, name),
        ).fetchone()
        if rs is not None:
            row = rs
        else:
            # Substring fallback (matches tool's _find_file behaviour).
            like = f"%{name}%"
            rs = conn.execute(
                """
                SELECT file_id, name, mime, size_bytes, disk_path,
                       sha256, gnfd_path, extracted_text
                FROM uploads
                WHERE user_id = ? AND name LIKE ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (user_id, like),
            ).fetchone()
            if rs is not None:
                row = rs

    # Layer 4 (recovery via EventLog) — handled separately below if
    # row is still None.
    if row is None:
        return await _recover_via_event_log(user_id, name)

    file_id = row[0]
    real_name = row[1]
    mime = row[2]
    disk_path = row[4]
    gnfd_path = row[6] or ""
    cached_text = row[7] or ""

    # Layer 1 hit: cached extracted_text.
    if cached_text:
        return real_name, cached_text

    # Layer 2: bytes on disk → extract → cache.
    text = await _extract_from_disk(disk_path, real_name, mime)
    if text:
        _save_extracted_text(file_id, text)
        return real_name, text

    # Layer 3: pull from Greenfield → re-hydrate disk + cache.
    if gnfd_path:
        text = await _extract_from_greenfield(
            user_id, gnfd_path, real_name, mime, disk_path,
        )
        if text:
            _save_extracted_text(file_id, text)
            return real_name, text

    return None


def _save_extracted_text(file_id: str, text: str) -> None:
    """Persist extracted text back into ``uploads.extracted_text`` so
    the next read for the same file is a SQL hit. We cap at a sane
    upper bound (1 MB) — anything larger is an LLM-context-busting
    document we shouldn't be inlining anyway."""
    capped = text if len(text) <= 1_000_000 else text[:1_000_000]
    try:
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE uploads SET extracted_text = ? WHERE file_id = ?",
                (capped, file_id),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.debug("save_extracted_text(%s) failed: %s", file_id, e)


async def _extract_from_disk(
    disk_path: str, name: str, mime: str,
) -> Optional[str]:
    p = Path(disk_path)
    if not p.exists():
        return None
    try:
        raw = p.read_bytes()
    except Exception as e:  # noqa: BLE001
        logger.debug("disk read failed for %s: %s", disk_path, e)
        return None
    return _bytes_to_text(raw, name, mime)


async def _extract_from_greenfield(
    user_id: str, gnfd_path: str, name: str, mime: str,
    restore_to_disk_path: str,
) -> Optional[str]:
    try:
        from nexus_server.twin_manager import get_twin
        twin = await get_twin(user_id)
        backend = getattr(twin, "rune", None)
        backend = getattr(backend, "_backend", None) if backend else None
        if backend is None or not hasattr(backend, "load_blob"):
            return None
        raw = await backend.load_blob(gnfd_path)
        if not raw:
            return None
    except Exception as e:  # noqa: BLE001
        logger.debug("greenfield load_blob(%s) failed: %s", gnfd_path, e)
        return None

    # Best-effort: rehydrate the local disk copy so layer 2 picks it
    # up next time. A failure here doesn't block the read.
    try:
        p = Path(restore_to_disk_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
    except Exception:
        pass

    return _bytes_to_text(raw, name, mime)


def _bytes_to_text(
    raw: bytes, name: str, mime: str,
) -> Optional[str]:
    """Run the SDK distiller's text extractor on raw bytes."""
    try:
        from nexus_core.distiller import extract_text
        import base64 as _b64
        b64 = _b64.b64encode(raw).decode("ascii")
        text, _src = extract_text(name, mime, None, b64)
        return text or None
    except Exception as e:  # noqa: BLE001
        logger.debug("extract_text(%s) failed: %s", name, e)
        return None


async def _recover_via_event_log(
    user_id: str, name: str,
) -> Optional[tuple[str, str]]:
    """Layer 4 recovery: find a ``file_uploaded`` event whose
    metadata.name matches and rebuild the SQL row from it. Useful
    when the server migrated to a fresh SQLite but the user's
    EventLog + Greenfield bucket carry over.
    """
    try:
        from nexus_server.twin_manager import get_twin
        twin = await get_twin(user_id)
        events = list(twin.event_log.recent(limit=200))
    except Exception:
        return None

    # Newest matching upload first.
    match = None
    for e in reversed(events):
        if getattr(e, "event_type", "") != "file_uploaded":
            continue
        md = getattr(e, "metadata", None) or {}
        if md.get("name") == name or (
            isinstance(md.get("name"), str) and name.lower() in md["name"].lower()
        ):
            match = md
            break
    if match is None or not match.get("gnfd_path"):
        return None

    gnfd_path = match["gnfd_path"]
    file_id = match.get("file_id") or uuid.uuid4().hex
    real_name = match.get("name") or name
    mime = match.get("mime") or "application/octet-stream"

    # Re-fetch from Greenfield, repopulate disk + SQL.
    user_dir = _files_dir() / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    disk_path = user_dir / f"{file_id}-{_safe_name(real_name)}"
    text = await _extract_from_greenfield(
        user_id, gnfd_path, real_name, mime, str(disk_path),
    )
    if not text:
        return None

    # Best-effort SQL rehydrate so subsequent reads hit Layer 1.
    try:
        with get_db_connection() as conn:
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT OR REPLACE INTO uploads
                (file_id, user_id, name, mime, size_bytes, disk_path,
                 created_at, sha256, gnfd_path, extracted_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (file_id, user_id, real_name, mime,
                 int(match.get("size_bytes") or 0), str(disk_path),
                 now_iso, match.get("sha256") or "", gnfd_path, text),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.debug("recovery rehydrate failed: %s", e)

    return real_name, text


def list_user_files(user_id: str) -> dict[str, int]:
    """Return ``{filename: total_chars_or_size_bytes}`` for the
    ``read_uploaded_file()`` listing surface. Prefers
    ``len(extracted_text)`` when cached, falls back to
    ``size_bytes`` so the LLM still sees the file even if we
    haven't decoded it yet."""
    _ensure_uploads_table()
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT name, size_bytes, extracted_text
            FROM uploads WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()
    out: dict[str, int] = {}
    for r in rows:
        # Replacing duplicate-name entries with the latest is fine —
        # the tool's listing surface is informational.
        out[r[0]] = len(r[2]) if r[2] else int(r[1])
    return out
