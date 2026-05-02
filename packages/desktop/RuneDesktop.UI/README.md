# RuneDesktop.UI

Avalonia (.NET 10) view + view-model layer for the Nexus desktop
client. The companion **RuneDesktop.Core** project owns transport
(ApiClient + DTOs); this project owns rendering and user interaction.

For the platform story (immortal agents, DPM, falsifiable
self-evolution, on-chain identity) read the root
[`README.md`](../../../README.md). For the desktop package overview
(server contract, slide-over panel inventory, what's new) read
[`../README.md`](../README.md). This file is the UI-package
implementation reference.

---

## Layout (Phase D 续 / #159)

```
ChatView.axaml
├── Sidebar           Sessions, ERC-8004 status, panel buttons,
│                     activity stream
├── Chat              Messages + composer + attachments
├── Cognition column  NOW · JUST HAPPENED · ON CHAIN · AUDIT TRAIL ·
│                     EVOLUTION PRESSURE (gauges + 24 h hist +
│                     recent verdicts)
└── Slide-over        One of: Memories · Anchors · Brain ·
                      Evolution · Progress · Workdir · Thinking
```

Both side columns are user-resizable via GridSplitters and
collapsible (Width="Auto" on the column tracks so the chat fills the
freed space).

---

## ViewModel guide

Files under `ViewModels/`:

| File | Owns |
|---|---|
| `MainViewModel.cs` | Auth + view switching (Login → Chat) |
| `LoginViewModel.cs` | Passkey ceremony + JWT capture |
| `ChatViewModel.cs` | Chat state, slash-commands, panel-open commands |
| `ChatMessageViewModel.cs` | Per-message rendering (markdown, attachments, copy) |
| `CognitionPanelViewModel.cs` | The 2 s polled live streams + Pressure dashboard |
| `PressureDashboardViewModel.cs` | Gauges, 24 h histogram, recent-verdicts feed |
| `BrainPanelViewModel.cs` | Brain panel — Glance / Timeline / DataFlow / JustLearned / Health |
| `DetailPanelViewModels.cs` | Slide-over mode dispatcher + per-row VMs |
| `ActivityStreamViewModel.cs` | Top-of-sidebar feed |
| `SessionListViewModel.cs` | Session drawer + create/rename/delete |

`BrainPanelViewModel` parallel-fetches
`GET /api/v1/agent/chain_status` and
`GET /api/v1/agent/learning_summary?window=7d`, then calls
`ApplyChain` to populate the 5 namespace cards' chain-status dots and
`ApplyLearning` to backfill counts/deltas from the timeline last row
(the chain endpoint is shape-only; counts come from the learning
summary).

---

## Resources & converters

- `App.axaml` — colour palette (gold #F0B90B + dark surfaces),
  typography (Inter), `[StaticResource]` brushes used throughout the
  XAML
- `Views/HistogramHeightConverter.cs` — `[0, 1]` → 20 px (Pressure
  24 h histogram)
- `Views/TimelineHeightConverter.cs` — `[0, 1]` → 60 px (Brain panel
  7-day timeline)

Both converters clamp non-zero ratios to a 2-3 px minimum so a single
data point isn't visually invisible next to a saturated peer.

---

## Build & run

Prerequisites: .NET 10 SDK (the project also builds against 8 + 9 if
you remove the `<TargetFramework>` line, but the commit pins net10.0).

```bash
cd packages/desktop
dotnet restore
dotnet run --project RuneDesktop.UI
```

The desktop expects the server at `http://localhost:8001` by default
— editable in the login screen's settings dialog.

For the icon-baking step (one-time), see the package-level
[`../README.md#building--running`](../README.md#building--running).

---

## MVVM conventions

- Source-generated properties via `CommunityToolkit.Mvvm`
  (`[ObservableProperty]`, `[RelayCommand]`).
- `ApiClient` is the single transport seam — view-models never touch
  HTTP directly.
- Empty-state booleans (`MemoriesEmpty`, `IsJustLearnedEmpty` …) are
  exposed as properties rather than computed in XAML, so binding
  diagnostics catch typos.
- Refresh logic is idempotent: every panel can be reloaded without
  side effects (last-write-wins on collections).
- All async work is `Task`-returning; the view layer never blocks.

---

## Testing

ViewModel-level unit tests are not yet wired up; the SDK + framework +
server tests cover the contracts the desktop talks to. When adding
new bindings, prefer:

1. Express the binding via a property on the VM (not via converter
   chains).
2. Add a tiny test that exercises that property's logic.
3. Verify by running the desktop locally — Avalonia binding errors
   surface in stdout when `Logger=Trace` is enabled in `Program.cs`.

---

## License

Apache 2.0 — see the repo root.
