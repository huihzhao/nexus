// [DELETED — Round 2-A thin-client refactor]
//
// LocalEventLog was the desktop's per-user SQLite event log
// (~/AppData/RuneProtocol/users/{uid}/events.db). Pre-refactor every
// chat turn was appended here and a fire-and-forget /sync/push pushed
// rows to the server. After the thin-client refactor:
//
//   * Server's twin EventLog is the single source of truth for every
//     event (user_message / assistant_response / memory_compact / etc.)
//   * Desktop pulls history from GET /api/v1/agent/messages on login.
//   * Files go through POST /api/v1/files/upload, not inline base64.
//   * No local SQLite, no per-user data dir, no /sync/push.
//
// The build system can't easily drop a .cs file, so this is kept as
// an empty placeholder — if a stray reference survives the refactor
// it will fail to compile, which is the point.
namespace RuneDesktop.Core.Storage;

// Empty — LocalEventLog type removed. RuneDesktop.Sync still references
// the old type by name, but that project is excluded from the .sln
// (task #31) and isn't part of the build.
