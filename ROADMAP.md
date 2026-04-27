# Roadmap

What's next, in approximate order. Items in **Now** are actively being
worked on; **Next** is queued; **Later** is shape-known but not
prioritised.

## Now

### Reorg Phase A — docs (this commit)

`README.md`, `ARCHITECTURE.md`, `HISTORY.md`, `docs/concepts/*.md`,
`docs/how-to/*.md`. Zero code change. Done.

### Reorg Phase B — delete dead code

- `git rm -rf packages/desktop/RuneDesktop.Sync/`
- Delete `nexus_server/sync_hub.py` + `/sync/push` `/sync/pull`
  endpoints (desktop is thin client now, no callers).
- Delete `nexus_server/sync_anchor.py` retry daemon (already opt-in,
  no production users).
- Delete the `sync_events` table + `_build_on_event` mirror writes
  (S5 made `/agent/*` endpoints read from twin's own EventLog; the
  mirror is no longer consulted on the read path).
- Delete `nexus_server/memory_service.py` placeholder.

### Reorg Phase C — server internal grouping

Reorganise `nexus_server/*.py` flat layout into domain folders:
`auth/`, `twins/`, `chat/`, `chain/`, `views/`. Each with a one-page
README. Tests split by domain too.

## Next

### Phase D — Python module rename

Code rename to match doc names:

- `bnbchain_agent` → `nexus_core`
- `rune_twin` → `nexus`
- `rune_server` → `nexus_server`

Plus pyproject.toml package names, .env / conftest env vars, all
import strings. Single mechanical sweep + full test pass.

### Phase E — SDK internal grouping

- Submodule reorg of `nexus_core/`: `chain/`, `greenfield/`, `memory/`,
  `contracts/`, `runtime/`, `llm/`, `tools/`, `distill/`. README per
  submodule.
- Drop `nexus/{tools,skills,mcp}/` re-export wrappers — let nexus
  code import from `nexus_core.*` directly.

### Phase F — public class renames

Decide the class API split that's been deferred:

- `Rune` builder → top-level functions (`nexus_core.testnet()`) or
  `Provider.testnet()`
- `RuneProvider` → `AgentRuntime` or kept as `Provider`
- `RuneChainClient` → `BSCClient`

Also logger namespace `rune.*` → `nexus_core.*`.

### Bucket prefix migration

`rune-agent-{token_id}` → `nexus-agent-{token_id}`. New buckets created
under the new prefix. Pre-existing testnet buckets are abandoned (test
phase, no real user data).

## Later

### Planning support — the missing capability

Today the agent reacts. It doesn't plan. Add:

- `nexus_core.planning` — `Plan` / `PlanStep` data model,
  `EventLogPlanStore` (DPM-aligned: plans are events).
- `nexus.planning` — `Planner` (LLM decompose / re-plan) +
  `PlanExecutor` (run steps via tools, persist progress).
- `twin.chat` integration: detect planning intent → decompose →
  return "I've broken this into N steps" + run in background.
- `/agent/plans` server endpoint + desktop Plans panel.

Full design: see [planning thread] (TBD — write up once Phase A–F
land).

### Non-custodial chain mode

Currently chain mode is custodial: server signs with
`SERVER_PRIVATE_KEY`. A future non-custodial mode would have users
sign in with their own wallet (MetaMask / WalletConnect) and pass
their address through.

Required changes:

- Auth flow: passkey → wallet signature
- ChainBackend: `private_key` becomes per-twin (from session) instead
  of server-wide
- Cost model: each twin pays its own gas

Out of scope until basic product is shipping.

### OpenAPI-driven view types

Server's Pydantic view types (`ChatMessageView`, `MemoryEntry`,
`AgentStateSnapshot`, `FileUploadResponse`, …) are duplicated in the
desktop's C# code. Hand-maintained. When server adds a field, desktop
silently doesn't see it.

Generate C# DTO from server OpenAPI schema (via
`datamodel-code-generator` or similar). Keeps types in lockstep, no
silent drift.

### Test taxonomy cleanup

`test_server_regression.py` is 65 tests in one file (>2000 lines).
Split by domain matching the Phase C server reorg:
`test_auth.py`, `test_twins.py`, `test_chat.py`, `test_chain.py`,
`test_views.py`. Plus `tests/integration/` for end-to-end SDK + Nexus
+ Server.

### Repo rename

`rune-protocol` → `nexus` (or `nexus-protocol`). Wait until product
strategy commits to dropping the "Rune" brand externally. Today the
brand is internal-facing only.

## Done (selected)

See [`HISTORY.md`](HISTORY.md) for the full chronology. Highlights:

- **S1–S6** — server-side cleanup. Each step retired a piece of the
  server's parallel intelligence layer in favour of routing through
  Nexus's `DigitalTwin`. The result: server is a pure HTTP frontend,
  Nexus is the single agent runtime.
- **Round 2-A/B/C** — desktop became a thin client. Deleted
  `LocalEventLog`, `RuneEngine`, JWT decoder for user-id scoping, the
  per-user data directory, the `_build_system_prompt` /
  `_build_context_messages` logic. `MainViewModel` is ~140 lines
  total now.
- **Bug 1/2/3** — post-S6 stability fixes around bucket auto-create,
  duplicate ERC-8004 registration, and UI visibility into chain
  failures.
- **Distiller move to SDK** — `attachment_distiller`'s reusable
  pipeline lives in `nexus_core.distiller`; server keeps a thin shim
  for the `record_distilled_event` persistence half.
