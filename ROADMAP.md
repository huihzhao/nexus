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

### Phase E — SDK internal grouping (in progress)

Done:
- Dropped `nexus/{tools,skills,mcp}/` re-export wrappers. The
  submodules are tombstones that raise ImportError pointing at
  ``nexus_core.*``; ``nexus.tools`` survives only to host
  :class:`ExtendedToolRegistry`, the genuinely Nexus-specific bit.

Still to do (heavier refactor, deferred):
- Submodule reorg of `nexus_core/`: split monolithic ``chain.py`` /
  ``greenfield.py`` / ``state.py`` into ``chain/``, ``greenfield/``,
  ``memory/``, ``contracts/``, ``runtime/``, ``llm/``, ``tools/``,
  ``distill/``. README per submodule. Holds until the class-rename
  decisions in Phase F land — splitting + renaming together is
  cheaper than two passes.

### Phase F — public class renames + namespace migration

Done:
- Logger namespace `rune.*` → `nexus_core.*`. All ``getLogger("rune.X")``
  call sites were updated.
- Greenfield bucket prefix `rune-agent-{token_id}` →
  `nexus-agent-{token_id}`. The legacy shared default
  ``rune-agent-state`` was also renamed in keystore.py / state.py /
  cli.py / web_demo.py / scripts; pre-existing testnet buckets
  abandoned (test phase, no real user data).

Class renames are now done in Phase H — see below.

### Phase G — env var + cache + chain-schema rename (done)

- Env vars `RUNE_*` → `NEXUS_*`. Migrated everywhere:
  `NEXUS_NETWORK`, `NEXUS_TESTNET_RPC` / `NEXUS_MAINNET_RPC`,
  `NEXUS_TESTNET_AGENT_STATE_ADDRESS` /
  `NEXUS_TESTNET_TASK_MANAGER_ADDRESS` /
  `NEXUS_TESTNET_IDENTITY_REGISTRY` (and mainnet equivalents),
  `NEXUS_PRIVATE_KEY`, `NEXUS_GREENFIELD_BUCKET` /
  `NEXUS_GREENFIELD_KEY` / `NEXUS_GREENFIELD_NETWORK`,
  `NEXUS_USE_TWIN`, `NEXUS_TWIN_BASE_DIR`,
  `NEXUS_TWIN_IDLE_SECONDS`, `NEXUS_DISABLE_TWIN_REAPER`,
  `NEXUS_CACHE_DIR`, `NEXUS_MAX_ATTACHMENT_BYTES`,
  `NEXUS_MAX_INLINE_TEXT_BYTES`. Test fixtures
  ``TEST_RUNE_*`` → ``TEST_NEXUS_*``. Twin manager's dynamic
  ``getattr(config, f"RUNE_{net_prefix}_…")`` lookups also updated.
- Cache directory ``.rune_twin_demo`` → ``.nexus_demo``. Pre-existing
  local caches abandoned per user instruction.
- Chain anchor schema id ``"rune.sync.batch.v1"`` →
  ``"nexus.sync.batch.v1"``. Pre-existing testnet anchors
  abandoned per user instruction (we can re-anchor from scratch).

### Phase H — public class renames (done)

The static-factory class ``Rune`` was retired in favour of
module-level functions. The 80% surface is now::

    import nexus_core
    rt = nexus_core.local()                       # was Rune.local()
    rt = nexus_core.testnet(private_key="0x...")  # was Rune.testnet(...)
    rt = nexus_core.mainnet(private_key="0x...")  # was Rune.mainnet(...)
    rt = nexus_core.builder().mock_backend().build()

Class rename map:

| Was | Is now |
| --- | --- |
| ``Rune`` (static-factory class) | dropped — use top-level functions |
| ``RuneBuilder`` | ``Builder`` |
| ``RuneProvider`` (the 5-provider facade) | ``AgentRuntime`` |
| ``RuneSessionProvider`` / ``RuneMemoryProvider`` / ``RuneArtifactProvider`` / ``RuneTaskProvider`` / ``RuneImpressionProvider`` | drop ``Rune`` prefix — ``SessionProvider`` etc. |
| ``RuneChainClient`` (BSC web3 wrapper) | ``BSCClient`` |
| ``RuneKeystore`` | ``Keystore`` |
| A2A's ``AgentRuntime`` (separate concept — A2A process container) | ``A2ARuntime`` (matches existing ``A2AAgentConfig`` convention; resolves the naming clash with the new SDK ``AgentRuntime``) |

Pre-existing class names had no production users, so the rename
is a clean break — no compatibility aliases.

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
