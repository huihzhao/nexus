# RuneDesktop.Sync — Excluded from `RuneDesktop.sln`

This project is **NOT** part of the active solution build. The source files
are preserved here for reference because the offline-first / multi-device
sync design they sketched out is still useful, but the implementation as
written no longer compiles against the current SDK surface.

## Why it was excluded

`SyncEngine.cs` references at least 11 APIs that no longer exist:

| What the code calls | What actually exists today |
|---|---|
| `_eventLog.GetEvents()` | `GetUnsyncedEventsAsync()` / `GetRecentEventsAsync()` |
| `_eventLog.AppendEvent(entry)` | `AppendAsync(entry)` |
| `_eventLog.MarkSynced(Guid, long)` | `MarkSyncedAsync(List<long>, long)` |
| `_apiClient.GetAsync(url, token)` | (no such public method) |
| `_apiClient.PostAsync(url, content, token)` | (no such public method) |
| `EventEntry.Id = Guid.NewGuid()` | `Id` is `long`, init-only |
| `EventEntry.Type` | `EventType` |
| `EventEntry.Timestamp` | `CreatedAt` |
| `EventEntry.Data` | `Content` |
| `_syncState = await SyncState.LoadAsync(...)` | `_syncState` is `readonly` |
| (a couple more) | … |

Rather than rewrite a class nobody currently consumes, we excluded the
project from `RuneDesktop.sln`.

## Where the live sync code lives now

`RuneDesktop.Core/Services/RuneEngine.cs` — the `SyncRecentEventsAsync`
method runs after every `ChatAsync` turn:

  1. `_eventLog.GetUnsyncedEventsAsync()` — pick up everything not yet
     pushed.
  2. `_apiClient.PushEventsAsync(events)` — POST `/api/v1/sync/push`.
  3. `_eventLog.MarkSyncedAsync(ids, syncId)` — record server's
     watermark.

That's enough for the current single-device product flow. The richer
features `SyncEngine` was reaching for (timer-driven background sync,
device-id tracking, offline queueing with online-status callbacks,
server-pushed pull) belong here when we revisit multi-device + offline
support.

## How to bring it back

1. Open `RuneDesktop.sln`, re-add the project entry and its build configs.
2. Rewrite `SyncEngine.cs` against the current `EventEntry` /
   `LocalEventLog` / `ApiClient` shapes (see the table above).
3. Decide what `SyncEngine` does that `RuneEngine.SyncRecentEventsAsync`
   doesn't — and add tests for those features.
