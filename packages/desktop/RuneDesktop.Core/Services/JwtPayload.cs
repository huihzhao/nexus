// [DELETED — Round 2-C thin-client refactor]
//
// JwtPayload.ExtractUserId was a non-verifying base64-decode of the
// JWT body that the desktop used to read its user_id claim — needed
// only to scope LocalEventLog to a per-user SQLite path
// (~/AppData/RuneProtocol/users/{user_id}/events.db).
//
// After the thin-client refactor, the desktop holds no per-user state
// on disk; everything reads from server with the bearer token, and the
// server enforces JWT verification properly. There is no remaining
// reason for a non-verifying decoder on the client.
//
// Empty placeholder so the .csproj keeps compiling. A stray reference
// will fail to compile, which is the point.
namespace RuneDesktop.Core.Services;

// Empty — JwtPayload type removed.
