"""Lightweight .env file loader.

No external dependencies. Searches CWD and parent directories for a .env file
and loads key=value pairs into os.environ (without overriding existing vars).

Usage:
    from nexus_core.utils import load_dotenv

    load_dotenv()  # searches CWD → parent dirs
    load_dotenv("/path/to/.env")  # explicit path
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path | None = None) -> str | None:
    """Load a .env file into os.environ.

    Args:
        path: Explicit path to .env file. If None, searches CWD and up to
              5 parent directories.

    Returns:
        The path that was loaded, or None if no .env file was found.
    """
    if path is not None:
        p = Path(path)
        if p.is_file():
            _parse_env_file(p)
            return str(p)
        return None

    # Search CWD and parent directories
    search = Path.cwd()
    for _ in range(6):
        candidate = search / ".env"
        if candidate.is_file():
            _parse_env_file(candidate)
            return str(candidate)
        parent = search.parent
        if parent == search:
            break
        search = parent

    return None


def _parse_env_file(path: Path) -> None:
    """Parse a .env file and set environment variables."""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        # Don't override existing env vars
        if key and key not in os.environ:
            os.environ[key] = value
