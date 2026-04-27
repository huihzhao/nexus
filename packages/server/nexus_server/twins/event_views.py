"""Removed — never had a caller (Phase C placeholder).

Use ``from nexus_server.twins import event_views`` (the package
__init__.py aliases :mod:`nexus_server.twin_event_log` under that
name) or ``from nexus_server.twin_event_log import …`` directly.
"""

raise ImportError(
    "nexus_server.twins.event_views was a Phase C placeholder with "
    "no callers — removed during dead-code cleanup. "
    "Use ``from nexus_server.twin_event_log import …`` instead."
)
