# `views/` — read-only HTTP endpoints

What's in here:

| File | Purpose |
|---|---|
| `routes.py` | Facade. Real code at `rune_server.agent_state`. Hosts two routers: `router` (prefix `/api/v1/agent`) and `sync_router` (prefix `/api/v1/sync` for the legacy `/sync/anchors` view). |
| `__init__.py` | Re-exports both routers. |

Endpoints exposed:

| Endpoint | Source |
|---|---|
| `GET /api/v1/agent/state` | `users.chain_agent_id` + `sync_anchors` counts + `twin_chain_events` counts + last chain event |
| `GET /api/v1/agent/timeline` | twin's EventLog (per-user SQLite) ⊎ `sync_anchors` ⊎ `twin_chain_events` |
| `GET /api/v1/agent/messages` | twin's EventLog (filtered to `user_message` / `assistant_response`) |
| `GET /api/v1/agent/memories` | twin's EventLog (filtered to `memory_compact`) |
| `GET /api/v1/sync/anchors` | `sync_anchors` table (legacy read view) |

What the new dev needs to know:

- These are **all read-only**. No state mutations happen here. The
  underlying data is owned by twin (its EventLog SQLite, populated by
  the chat flow) and by the server's own tables (`sync_anchors`,
  `twin_chain_events`).
- The pivot from "read sync_events table" to "read twin's per-user
  EventLog SQLite read-only" happened in S5. There's a tiny module
  (``twins/event_views`` / ``rune_server.twin_event_log``) that opens
  the per-user DB with `sqlite3.connect("file:...?mode=ro", uri=True)`
  and runs typed queries.
- Adding a new view endpoint means: define the response Pydantic
  model + route in `agent_state.py` (eventually here as `routes.py`),
  + reading helper in `twin_event_log.py`. Nothing else.

Phase D split plan: per-endpoint files (`state.py`, `timeline.py`,
`messages.py`, `memories.py`) once we add Planning's `plans.py`.
Until then `routes.py` holds them all.
