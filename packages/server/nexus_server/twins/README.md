# `twins/` — per-user DigitalTwin lifecycle

What's in here:

| File | Purpose |
|---|---|
| `manager.py` | Facade for `TwinManager` — lazy create per user, idle eviction, `_resolve_chain_kwargs` decision logic, `_ChainActivityLogHandler` (Bug 3 visibility), `bootstrap_chain_identity` (chain registration). Real code at `rune_server.twin_manager`. |
| `event_views.py` | Facade for read-only views over each user's twin EventLog SQLite. Used by `views/routes.py` to serve `/agent/{messages,memories,timeline}` without instantiating a twin. Real code at `rune_server.twin_event_log`. |
| `__init__.py` | Re-exports `manager`, `event_views`, plus the most-used helpers (`get_twin`, `bootstrap_chain_identity`, `install_chain_activity_handler`, `start_reaper`, `shutdown_all`). |

What the new dev needs to know:

- **One twin per logged-in user**, kept in an in-process registry. Idle 30 min → evicted (close + drop reference). Next chat cold-starts a new one. Bug 2 fix means re-creation is cheap.
- Chain mode vs local mode is decided by `_resolve_chain_kwargs(user_id)`: it reads `users.chain_agent_id` and runs `bootstrap_chain_identity` if the user hasn't been registered yet. See [`docs/concepts/modes.md`](../../../../docs/concepts/modes.md) for the full state machine.
- The `_ChainActivityLogHandler` subscribes to `nexus_core.backend.chain` and `nexus_core.greenfield` Python loggers and writes `twin_chain_events` rows so the desktop sidebar can show "0 → N anchored".
- `event_views.py` opens twin's SQLite read-only via `sqlite3.connect("file:...?mode=ro")`. No twin instantiation per HTTP request.

Phase D split plan:

```
twins/
├── manager.py        ← TwinManager class + get_twin/close_user/reaper
├── chain_log.py      ← _ChainActivityLogHandler + install/uninstall
└── event_views.py    ← read-only EventLog access
```

Plus `bootstrap_chain_identity` extracted out to `chain/bootstrap.py` (it's
chain-side concern, not twin-lifecycle).
