"""[DELETED — do not re-introduce]

``nexus_server.memory_service`` is gone.

Why it existed: pre-S3 the server ran its own periodic memory compactor
that walked ``sync_events`` and projected recent events into a single
``memory_compact`` row. Twin's own ``EventLogCompactor`` (SDK) replaces
this entirely after S3.

Where the read helpers went: ``list_memory_compacts`` and
``memory_compact_count`` live in :mod:`nexus_server.agent_state` now,
which delegates the actual SQLite read to
:mod:`nexus_server.twin_event_log` (the per-user twin EventLog DB —
S5's read pivot).

If you found this file via a stale import, update the call site to
import from ``nexus_server.agent_state`` (for back-compat reads) or
``nexus_server.twin_event_log`` (for the canonical read API).
"""

raise ImportError(
    "nexus_server.memory_service was deleted post-S3. Use "
    "nexus_server.agent_state.{list_memory_compacts,memory_compact_count} "
    "or nexus_server.twin_event_log directly."
)
