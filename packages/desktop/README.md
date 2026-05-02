# Nexus Desktop

> A cross-platform thin client for the Nexus DigitalTwin platform.
> The desktop holds *no* persistent state of its own (post-Round-2
> refactor — see HISTORY.md): every panel reads from the server's
> view-shape APIs, and the server is the source of truth for chat
> history, memories, anchors, and evolution timeline.

For the platform-level story (immortal agents, DPM, falsifiable
self-evolution, on-chain identity) read the root
[`README.md`](../../README.md). This file is the desktop
package-level reference.

---

## What the desktop renders

```
┌────────────────────────────────────────────────────────────────┐
│  Top bar:  N  Nexus               status …  [user pill] ⏏     │
│                                                                │
│  Sidebar           Chat                  Cognition column     │
│   ─────────         ───                   ─────────            │
│   • twin name       user message          NOW (live thinking)  │
│   • ERC-8004 badge  assistant reply       JUST HAPPENED        │
│   • on-chain ctrs   …                     ON CHAIN feed        │
│                                           AUDIT TRAIL          │
│   • [📋 Progress]                         EVOLUTION PRESSURE   │
│   • [🗂 Workdir]                          ↳ gauges + 24h hist  │
│   ─────────                               ↳ recent verdicts    │
│   • [Browse memories]   slide-over →                           │
│   • [Brain panel]       slide-over →                           │
│   • [Evolution timeline] slide-over →                          │
│   • [Browse anchors]    slide-over →                           │
│                                                                │
│  Activity stream (auto-refreshing)                             │
└────────────────────────────────────────────────────────────────┘
```

The slide-over panels read from server view-shape endpoints and refresh
on demand:

- **Brain panel** (Phase D 续 / #159) — replaces the old Memory
  namespaces dump with a learning-progress + chain-status view. Five
  sections answer *"is my agent learning, and is what it learned
  safely on chain?"*:
  1. **Brain at a Glance** — five namespace cards (persona /
     knowledge / skills / facts / episodes), each showing total
     count, today's delta, and a 3-dot chain status indicator
     (● local · ● mirrored to Greenfield · ● anchored on BSC).
  2. **Learning Timeline** — last 7 days as bars (auto-normalised
     against the week's max so the pyramid shape jumps out).
  3. **Data Flow** — the chat → facts → skills → knowledge →
     persona pipeline with each evolver's `live` / `ready ⏳` /
     `just fired` / `N/threshold` status and a mini progress bar.
  4. **Just Learned** — newest-first feed of recent additions across
     the five namespaces, each tagged with kind (FACT / SKILL /
     PERSONA …) and chain dots.
  5. **Chain Health** — bottom card: WAL queue size, daemon state,
     Greenfield + BSC readiness — answers "why isn't anything
     anchoring yet?" at a glance.

  Backed by `GET /api/v1/agent/chain_status` + `GET
  /api/v1/agent/learning_summary?window=7d` (parallel-fetched).

- **Evolution timeline** (Phase O.5 + O.6) — every persona / memory /
  skill edit shows up as a proposal row, settles into a verdict
  (`kept` / `kept_with_warning` / `reverted`), and pending proposals
  expose **Approve** / **Revert** buttons that drive
  `POST /api/v1/agent/evolution/{edit_id}/{approve,revert}`.

- **Browse memories** — historic `memory_compact` snapshots
  (current brain + superseded history).

- **Browse anchors** — BSC anchor history pulled from `/sync/anchors`.

The always-visible **Cognition** column on the right runs four 2 s
streams (NOW / JUST HAPPENED / ON CHAIN / AUDIT TRAIL) plus the
**Evolution Pressure** dashboard and **Recent Verdicts** feed (Phase
D 续 / #159) — gauges show what's about to evolve, the verdicts feed
shows what was decided and why ("KEPT · regression: 30% · drift: +0.15").

---

## Architecture

```
RuneDesktop/
├── RuneDesktop.UI         Avalonia views + view models (MVVM)
├── RuneDesktop.Core       ApiClient + service abstractions
└── RuneDesktop.UI.Tests   (planned)
```

`RuneDesktop.Sync` from earlier rounds is gone — see HISTORY for the
Round 2 thin-client refactor that retired the local event log + bidi
sync engine in favour of server-authoritative reads.

### RuneDesktop.Core
- `Services/ApiClient.cs` — typed HTTP client (auth, retries, multipart
  uploads, all view-shape endpoints)
- `Services/ChainModels.cs` — DTOs for chain / memory / namespace /
  evolution payloads
- `Models/` — `EventEntry`, `ChatMessage`, `AgentProfile`, etc.

### RuneDesktop.UI
- `Views/ChatView.axaml` — main split: sidebar + chat + cognition +
  slide-over panel
- `Views/HistogramHeightConverter.cs` / `TimelineHeightConverter.cs` —
  ratio→pixel height converters for the Pressure histogram (20 px) and
  Brain timeline (60 px)
- `ViewModels/ChatViewModel.cs` — chat state + slash-command surface
- `ViewModels/DetailPanelViewModels.cs` — slide-over modes (Memories /
  Anchors / **Brain** / Evolution / Progress / Workdir / Thinking) +
  per-row VMs
- `ViewModels/BrainPanelViewModel.cs` — five-section Brain panel
  (Glance / Timeline / DataFlow / JustLearned / Health) with parallel
  fetch of chain_status + learning_summary
- `ViewModels/PressureDashboardViewModel.cs` — gauges + 24 h histogram
  + recent verdicts feed
- `ViewModels/CognitionPanelViewModel.cs` — owns the four live streams
  + Pressure dashboard
- `ViewModels/ActivityStreamViewModel.cs` — top-of-sidebar feed
- `App.axaml.cs` — DI, login boot, JWT lifecycle

---

## Technology stack

- **Framework**: .NET 8+ (.NET 10 in current `obj/` artifacts)
- **UI**: Avalonia (cross-platform — Windows / macOS / Linux)
- **MVVM**: CommunityToolkit.Mvvm with `[ObservableProperty]` /
  `[RelayCommand]` source generators
- **HTTP**: System.Net.Http with retry helper
- **Serialisation**: System.Text.Json with `[JsonPropertyName]`
- **Auth**: WebAuthn passkeys (server-driven ceremony shown via embedded
  WebView)

---

## Building & running

```bash
# Server first (in another terminal)
cd ../server
uv run nexus-server

# Desktop
cd packages/desktop
./scripts/build-icon.sh      # one-time: produces .ico / .icns / .png from SVG
dotnet restore
dotnet run --project RuneDesktop.UI
```

The desktop expects the server at `http://localhost:8001` by default
(configurable in `App.axaml.cs` settings boot).

### App icon

The dock / taskbar / Start-menu icon is derived from
`RuneDesktop.UI/Assets/nexus-logo.svg`. Run
`scripts/build-icon.sh` once to bake out the platform-specific raster
files (`nexus-icon.ico` for Windows, `nexus-icon.icns` for macOS,
`nexus-icon.png` for generic Linux). The csproj wires the .ico into
the assembly; on macOS you additionally need to copy `nexus-icon.icns`
into the bundle's `Contents/Resources/` and reference it from the
generated `Info.plist` (`CFBundleIconFile = nexus-icon`). The
in-app window icon (top-left of the title bar) loads the SVG directly
via `Avalonia.Svg.Skia`, so it shows up even before you've run the
build script.

---

## What changed since the older version of this README

- **Brain panel replaces Memory namespaces.** (Phase D 续 / #159) The
  raw "5 typed-namespace dumps" view has been retired in favour of a
  learning-progress + chain-status view: namespace counts + 7-day
  bars + data-flow pyramid + just-learned feed + chain-health card.
  Every item carries a 3-dot indicator (● local · ● mirrored ·
  ● anchored) so users can see at a glance which writes are durable.
- **Pressure dashboard verdicts feed.** (Phase D 续 / #159) The
  always-visible Pressure dashboard now also shows recent kept /
  reverted verdicts with the reasoning blurb ("regression: 30% ·
  drift: +0.15") so falsifiable evolution is observable without
  having to open the slide-over.
- **Local sync engine retired.** Round 2 moved chat history + event
  ownership to the server. The desktop no longer ships
  `RuneDesktop.Sync` or `LocalEventLog`; authentication state is the
  only thing it persists locally.
- **Evolution moderation surface.** Pending proposals expose
  Approve / Revert buttons that hit the server's manual decision
  endpoints. The store rollback happens server-side; the desktop
  refreshes the timeline.
- **Brand cleanup.** Stale `RuneEngine` / `RuneSession*` / `RuneMemory*`
  service names are gone (post-Phase-H rename).

---

## Configuration

There's no client-side config file. The server URL, JWT, and
WebAuthn ceremony URL come from settings the user enters at login.
Token storage uses the OS-native credential vault via
`Avalonia.Storage` helpers.

---

## License

Apache 2.0.
