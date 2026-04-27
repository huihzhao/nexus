"""Removed in Phase E.

``nexus.skills`` was a thin re-export of :mod:`nexus_core.skills` —
it never carried Nexus-specific code, just two name aliases. Phase E
of the reorg deletes such re-export shims so the import graph
matches the dependency story (Nexus depends on Core, not the other
way around).

Migrate any caller from ``from nexus.skills import ...`` to
``from nexus_core.skills import ...``.

The file remains as a tombstone because the workspace shell can't
delete files; the ImportError below makes the migration breakage
loud rather than silently importing stale code.
"""

raise ImportError(
    "nexus.skills was removed in Phase E of the reorg. "
    "Use ``nexus_core.skills`` instead."
)
