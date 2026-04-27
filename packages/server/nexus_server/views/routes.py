"""Removed — never had a caller (Phase C placeholder).

Use ``from nexus_server.views import router, sync_router`` (the
package __init__.py re-exports them) or
``from nexus_server.agent_state import …`` directly.
"""

raise ImportError(
    "nexus_server.views.routes was a Phase C placeholder with no "
    "callers — removed during dead-code cleanup. "
    "Use ``from nexus_server.agent_state import router, sync_router``."
)
