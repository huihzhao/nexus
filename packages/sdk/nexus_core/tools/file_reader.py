"""ReadUploadedFileTool — read uploaded file content by section.

Storage model: **stateless tool over a persistent file store**.

The tool itself holds no canonical file content. When called, it
delegates to a resolver function that implements the actual lookup
against whatever persistent store the host owns. The host wires
resolver + lister at twin construction time (server: SQL + disk +
Greenfield three-layer store; future hosts: anything implementing
the same two callables).

This replaces an earlier in-memory ``store(filename, content)``
API that broke across twin idle eviction, server restart, and
session boundaries — see ARCHITECTURE.md "三层存储模型". The
in-memory mode was kept around briefly during the migration, then
removed once every production caller switched to the resolver
path: keeping a non-persistent fallback would re-open the
class of bug the migration was meant to close.
"""

from __future__ import annotations

import inspect
import logging
from typing import Awaitable, Callable, Optional, Tuple

from .base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


# Resolver contract: ``resolve(filename) -> (real_name, text)`` or
# ``None`` when the file isn't reachable. May be sync or async — the
# tool awaits accordingly.
ResolveFn = Callable[[str], Optional[Tuple[str, str]]]
AsyncResolveFn = Callable[[str], Awaitable[Optional[Tuple[str, str]]]]

# Listing contract: ``list_files() -> {filename: char_count}``. Used
# both for the empty-filename "what files are there?" surface AND for
# the LLM-self-correction list when the resolver misses.
ListFn = Callable[[], dict]
AsyncListFn = Callable[[], Awaitable[dict]]


class ReadUploadedFileTool(BaseTool):
    """Read content from an uploaded file, with offset/limit for large files.

    The tool is intentionally stateless: every ``execute()`` call
    delegates to the injected resolver, so cross-turn / cross-eviction
    / cross-restart reads all see the same canonical store. Without a
    resolver wired in, the tool reports "no files available" — that's
    the correct answer when the host hasn't bound a backing store
    rather than silently degrading to a per-instance cache that loses
    data on the next twin restart.

    Wiring:
      * Server (production): ``nexus_server.twin_manager._create_twin``
        binds ``resolver=`` to ``files.resolve_file_text(user_id, …)``
        and ``lister=`` to ``files.list_user_files(user_id)`` after
        twin construction.
      * Tests: pass the callables to the constructor directly.
    """

    def __init__(
        self,
        *,
        resolver: Optional[ResolveFn | AsyncResolveFn] = None,
        lister: Optional[ListFn | AsyncListFn] = None,
    ):
        # Mutable so the host can attach the resolver after twin
        # construction (the existing ``_create_twin`` pattern). Once
        # bound, never reassigned during a tool call.
        self._resolver = resolver
        self._lister = lister

    @property
    def name(self) -> str:
        return "read_uploaded_file"

    @property
    def description(self) -> str:
        return (
            "Read content from a file the user has uploaded. "
            "Use this to read large files that were only partially previewed. "
            "Specify offset (character position) and limit (max chars to return). "
            "Call with just the filename to get file info and first 2000 chars. "
            "Call with no filename to list all uploaded files."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name of the uploaded file to read (omit to list files)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Character offset to start reading from (default: 0)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max characters to return (default: 2000, max: 8000)",
                },
                "search": {
                    "type": "string",
                    "description": "Search for a keyword in the file. Returns the first match with surrounding context.",
                },
            },
            "required": [],
        }

    # ── Internal: resolver / lister shims ─────────────────────────

    async def _resolve(self, filename: str) -> Optional[Tuple[str, str]]:
        """Run the injected resolver, awaiting if it's async. Returns
        ``(real_name, text)`` on hit, ``None`` otherwise."""
        if self._resolver is None:
            return None
        try:
            r = self._resolver(filename)
            if inspect.isawaitable(r):
                r = await r
            return r  # type: ignore[return-value]
        except Exception as e:  # noqa: BLE001
            logger.debug("resolver(%s) failed: %s", filename, e)
            return None

    async def _list(self) -> dict[str, int]:
        if self._lister is None:
            return {}
        try:
            r = self._lister()
            if inspect.isawaitable(r):
                r = await r
            return r if isinstance(r, dict) else {}
        except Exception as e:  # noqa: BLE001
            logger.debug("lister failed: %s", e)
            return {}

    # ── Pure-text helpers (no instance state) ────────────────────

    @staticmethod
    def _slice(content: str, offset: int, limit: int) -> tuple[str, int]:
        """Slice ``content`` to (chunk, total_chars). Bounds-checked."""
        total = len(content)
        offset = max(0, min(offset, total))
        return content[offset : offset + limit], total

    @staticmethod
    def _search_in_text(
        content: str, filename: str, keyword: str, context: int = 500,
    ) -> str:
        """Find ``keyword`` in ``content`` and surround the hit with
        ``context`` chars on each side. Returns a human-readable
        message — no match is communicated as text, not via an
        exception, so the LLM can read the result and adjust."""
        idx = content.lower().find(keyword.lower())
        if idx == -1:
            return f"Keyword '{keyword}' not found in {filename}."
        start = max(0, idx - context)
        end = min(len(content), idx + len(keyword) + context)
        snippet = content[start:end]
        return (
            f"Found '{keyword}' at position {idx}:\n"
            f"[...chars {start}-{end} of {len(content)} total...]\n\n"
            f"{snippet}"
        )

    # ── Public surface ────────────────────────────────────────────

    async def execute(
        self, filename: str = "", offset: int = 0, limit: int = 2000,
        search: str = "", **kwargs
    ) -> ToolResult:
        # ── No filename → list available files ────────────────────
        if not filename:
            listing_map = await self._list()
            if not listing_map:
                return ToolResult(output="No uploaded files available.")
            listing = "\n".join(
                f"- {n} ({c:,} chars)" for n, c in listing_map.items()
            )
            return ToolResult(output=f"Available uploaded files:\n{listing}")

        # ── Look up content via the resolver ─────────────────────
        hit = await self._resolve(filename)
        if hit is None:
            # Tell the LLM what IS available so it can self-correct
            # on the next call instead of giving up. The
            # availability list is what made the cross-turn flow
            # debuggable from chat — without it, "file not found"
            # was a dead-end.
            listing_map = await self._list()
            available = ", ".join(listing_map.keys()) if listing_map else "(none)"
            return ToolResult(
                success=False,
                error=f"File '{filename}' not found. Available: {available}",
            )

        real_name, content = hit
        if search:
            return ToolResult(
                output=self._search_in_text(content, real_name, search),
            )

        limit = min(limit, 8000)  # Hard cap at 8K per read
        chunk, total = self._slice(content, offset, limit)
        remaining = total - offset - len(chunk)
        header = (
            f"[File: {real_name} | Total: {total:,} chars | "
            f"Showing: {offset:,}-{offset + len(chunk):,}]"
        )
        if remaining > 0:
            header += (
                f"\n[{remaining:,} more chars — "
                f"use offset={offset + len(chunk)} to continue]"
            )
            header += f"\n[Tip: use search='keyword' to find specific content]"
        return ToolResult(output=f"{header}\n\n{chunk}")
