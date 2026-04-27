"""[DELETED — Phase B]

``nexus_server.sync_hub`` exposed ``POST /api/v1/sync/push`` and
``GET /api/v1/sync/pull`` endpoints. Both retired in stages:

- S4 stopped /sync/push from creating new anchor rows (twin's
  ChainBackend took over anchoring).
- Round 2-A made the desktop a thin client — it pulls history from
  ``GET /api/v1/agent/messages`` and stops calling /sync/push entirely.
- Round 2-C completed the desktop refactor.

After Round 2-C nothing in production calls /sync/push or /sync/pull.
This module is a tombstone; importing it raises ``ImportError`` so a
stale reference fails loudly instead of silently registering dead
routes. The associated ``sync_events`` table is dropped in Phase B as
well — see :mod:`nexus_server.database`.

If you found this file via a stale ``app.include_router(sync_hub.router)``
in main.py, that line is gone. Update your code path.
"""

raise ImportError(
    "nexus_server.sync_hub was removed in Phase B. /sync/push and "
    "/sync/pull endpoints retired post-Round-2. Reads of chat history "
    "go through GET /api/v1/agent/messages now."
)
