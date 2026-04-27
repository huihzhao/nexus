"""ReadUploadedFileTool — read uploaded file content by section.

Supports two storage backends:
  1. Small files (<100KB text): stored in memory for fast access
  2. Large files: text extracted to a .txt sidecar on disk, read on demand

Flow:
  1. User uploads file via web demo
  2. Backend calls store() or store_path() to register the file
  3. A short preview goes into _messages
  4. Agent calls read_uploaded_file(filename, offset, limit) to read sections
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from .base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

# Max text to keep in memory per file (100KB)
_MEM_THRESHOLD = 100_000


class ReadUploadedFileTool(BaseTool):
    """Read content from an uploaded file, with offset/limit for large files."""

    def __init__(self, cache_dir: str | Path = "/tmp/rune_file_cache"):
        self._files: dict[str, str] = {}       # filename -> text (small files)
        self._disk_files: dict[str, Path] = {}  # filename -> path to .txt cache
        self._file_sizes: dict[str, int] = {}   # filename -> total char count
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

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

    def store(self, filename: str, content: str) -> None:
        """Store file content. Small files stay in memory, large go to disk."""
        total = len(content)
        self._file_sizes[filename] = total

        if total <= _MEM_THRESHOLD:
            self._files[filename] = content
            logger.info("Stored file in memory: %s (%d chars)", filename, total)
        else:
            # Write to disk cache
            cache_path = self._cache_dir / f"{filename}.txt"
            cache_path.write_text(content, encoding="utf-8")
            self._disk_files[filename] = cache_path
            logger.info("Stored file on disk: %s (%d chars) -> %s", filename, total, cache_path)

    def store_path(self, filename: str, text_path: Path, total_chars: int) -> None:
        """Register a pre-extracted text file on disk (for very large files)."""
        self._disk_files[filename] = text_path
        self._file_sizes[filename] = total_chars
        logger.info("Registered disk file: %s (%d chars) at %s", filename, total_chars, text_path)

    def list_files(self) -> dict[str, int]:
        """Return {filename: char_count} for all stored files."""
        return dict(self._file_sizes)

    def _read_content(self, filename: str, offset: int, limit: int) -> tuple[str, int]:
        """Read a chunk from a file. Returns (chunk, total_chars)."""
        # In-memory first
        if filename in self._files:
            content = self._files[filename]
            total = len(content)
            chunk = content[offset : offset + limit]
            return chunk, total

        # Disk-based
        if filename in self._disk_files:
            path = self._disk_files[filename]
            total = self._file_sizes.get(filename, 0)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(offset)
                    chunk = f.read(limit)
                return chunk, total
            except Exception as e:
                return f"(read error: {e})", total

        return "", 0

    def _search_content(self, filename: str, keyword: str, context: int = 500) -> str:
        """Search for a keyword in a file, return match with context."""
        # Read full content (in-memory) or scan disk
        if filename in self._files:
            content = self._files[filename]
        elif filename in self._disk_files:
            try:
                content = self._disk_files[filename].read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return f"(search error: {e})"
        else:
            return "(file not found)"

        idx = content.lower().find(keyword.lower())
        if idx == -1:
            return f"Keyword '{keyword}' not found in {filename}."

        # Return match with surrounding context
        start = max(0, idx - context)
        end = min(len(content), idx + len(keyword) + context)
        snippet = content[start:end]

        return (
            f"Found '{keyword}' at position {idx}:\n"
            f"[...chars {start}-{end} of {len(content)} total...]\n\n"
            f"{snippet}"
        )

    def _find_file(self, filename: str) -> Optional[str]:
        """Find a file by exact or partial match. Returns resolved name or None."""
        all_names = set(self._files.keys()) | set(self._disk_files.keys())

        if filename in all_names:
            return filename

        # Partial match
        matches = [n for n in all_names if filename.lower() in n.lower()]
        return matches[0] if matches else None

    async def execute(
        self, filename: str = "", offset: int = 0, limit: int = 2000,
        search: str = "", **kwargs
    ) -> ToolResult:
        if not filename:
            # List available files
            if not self._file_sizes:
                return ToolResult(output="No uploaded files available.")
            listing = "\n".join(
                f"- {name} ({chars:,} chars)"
                for name, chars in self._file_sizes.items()
            )
            return ToolResult(output=f"Available uploaded files:\n{listing}")

        resolved = self._find_file(filename)
        if not resolved:
            available = ", ".join(self._file_sizes.keys()) if self._file_sizes else "(none)"
            return ToolResult(
                success=False,
                error=f"File '{filename}' not found. Available: {available}",
            )
        filename = resolved

        # Search mode
        if search:
            result = self._search_content(filename, search)
            return ToolResult(output=result)

        # Read mode
        total = self._file_sizes.get(filename, 0)
        limit = min(limit, 8000)  # Hard cap at 8K per read
        offset = max(0, min(offset, total))

        chunk, total = self._read_content(filename, offset, limit)

        remaining = total - offset - len(chunk)
        header = f"[File: {filename} | Total: {total:,} chars | Showing: {offset:,}-{offset + len(chunk):,}]"
        if remaining > 0:
            header += f"\n[{remaining:,} more chars — use offset={offset + len(chunk)} to continue]"
            header += f"\n[Tip: use search='keyword' to find specific content]"

        return ToolResult(output=f"{header}\n\n{chunk}")
