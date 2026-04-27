"""Removed — never had a caller (Phase C placeholder).

Use ``from nexus_server.chain import legacy_anchors`` (the package
__init__.py aliases :mod:`nexus_server.sync_anchor` under that
name) or ``from nexus_server.sync_anchor import …`` directly.

Phase B trimmed sync_anchor to a read-only view —
``enqueue_anchor`` + ``list_anchors_for_user``. The retry daemon is
gone; the twin's ChainBackend owns anchoring now.
"""

raise ImportError(
    "nexus_server.chain.legacy_anchors was a Phase C placeholder "
    "with no callers — removed during dead-code cleanup. "
    "Use ``from nexus_server.sync_anchor import enqueue_anchor, "
    "list_anchors_for_user``."
)
