"""Removed in Phase E.

``nexus.mcp`` was a thin re-export of :mod:`nexus_core.mcp` — never
carried Nexus-specific code. Phase E drops such re-export shims so
the import graph matches the dependency story (Nexus depends on
Core, not the reverse).

Migrate ``from nexus.mcp import ...`` → ``from nexus_core.mcp
import ...``.

Tombstone because the workspace shell can't delete files; the
ImportError makes a stale caller fail loudly with a clear message.
"""

raise ImportError(
    "nexus.mcp was removed in Phase E of the reorg. "
    "Use ``nexus_core.mcp`` instead."
)
