"""Removed — never had a caller.

Phase C left this as a tiny ``from nexus_server.llm_gateway import *``
shim inside ``chat/`` with a "Phase D will move it here for real"
promise. Phase D renamed the package (``rune_server`` →
``nexus_server``) but did NOT split the top-level monoliths into
per-domain files; that split is deferred. With no callers this
file just duplicated what ``chat/__init__.py`` already re-exports.

Tombstoned via ``raise ImportError`` — workspace shell can't
delete files.
"""

raise ImportError(
    "nexus_server.chat.routes was a Phase C placeholder with no "
    "callers — removed during dead-code cleanup. The chat router "
    "lives at ``nexus_server.llm_gateway.router``; importing it "
    "via the package facade still works: "
    "``from nexus_server.chat import router``."
)
