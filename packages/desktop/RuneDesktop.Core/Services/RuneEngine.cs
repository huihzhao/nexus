// [DELETED — Round 2-C thin-client refactor]
//
// RuneEngine used to wrap LocalEventLog and own conversation curation
// (BuildSystemPrompt, BuildContextMessages, MaxRecentEventsBeforeCuration).
// All of that moved server-side: the Nexus DigitalTwin runs the same
// flow (CuratedMemory + ProjectionMemory + EventLogCompactor) per-user
// and ships canonical messages back via GET /api/v1/agent/messages.
//
// New chat path: ChatViewModel calls ApiClient.SendChatAsync directly.
// New history path: ChatViewModel.InitializeAsync calls
// ApiClient.GetMessagesAsync. There is no local engine, no local event
// log, no per-user data dir, no fire-and-forget /sync/push.
//
// This file is kept as an empty placeholder because the build system
// can't tolerate dangling .cs entries in the .csproj. Add nothing here.
namespace RuneDesktop.Core.Services;

// Empty — RuneEngine type removed. If a stray reference survives the
// refactor it will fail to compile, which is the point.
