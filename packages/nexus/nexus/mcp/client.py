"""Removed in Phase E — duplicate of :mod:`nexus_core.mcp.client`.

The two files were byte-identical; Phase E keeps the SDK copy as
the single source of truth.

Tombstone because the workspace shell can't delete files.
"""

raise ImportError(
    "nexus.mcp.client was removed in Phase E. "
    "Use ``nexus_core.mcp.client`` (or ``nexus_core.mcp``) instead."
)
