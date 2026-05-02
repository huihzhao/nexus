# nexus-server

> The multi-tenant FastAPI front for Nexus. The server doesn't run
> agent intelligence itself — it owns the lifecycle of one
> `nexus.DigitalTwin` per logged-in user, and exposes thin HTTP views
> over each twin's per-user EventLog SQLite. The desktop client (and
> any other UI) reads exclusively from these views.

For the cross-cutting story (immortal-agent design, DPM, falsifiable
evolution, on-chain identity) read the root [`README.md`](../../README.md)
and [`ARCHITECTURE.md`](../../ARCHITECTURE.md). This file is the
package quickstart and HTTP-route reference.

---

## What the server is responsible for

| Concern | Module | Routes |
|---|---|---|
| Auth (passkey + JWT) | `nexus_server.auth/` | `/api/v1/auth/*`, `/passkey` |
| Chat / attachments | `nexus_server.llm_gateway` (+ `attachment_distiller`, `files`) | `POST /api/v1/llm/chat`, `POST /api/v1/files/upload` |
| Chain (ERC-8004) | `nexus_server.chain_proxy` | `/api/v1/chain/me`, `/api/v1/chain/agent/{id}` |
| Read views over twin state | `nexus_server.agent_state` | `/api/v1/agent/{state,timeline,messages,memories}` |
| **Phase J: typed memory namespaces** | `nexus_server.agent_state` | `GET /api/v1/agent/memory/namespaces` |
| **Phase O: evolution timeline** | `nexus_server.agent_state` | `GET /api/v1/agent/evolution/verdicts` |
| **Phase O.6: manual approve / revert** | `nexus_server.agent_state` | `POST /api/v1/agent/evolution/{edit_id}/{approve,revert}` |
| Per-user twin lifecycle | `nexus_server.twin_manager` | – (idle reaper, chain bootstrap, chain-activity log handler) |

The server is intentionally simple: nothing in `nexus_server` knows
how chat works inside a turn, how anchoring computes a state root,
or how a verdict decides to roll back a persona. Those live in the
framework / SDK and are reached via `await twin.chat(...)`,
`twin.event_log`, etc.

Phase B (HISTORY.md) retired the standalone `sync_hub` event-sync
router and the `sync_events` mirror table — the desktop is a thin
client and the twin's own EventLog is the source of truth.

---

## Routes you'll care about

```
# Identity / chain
GET  /api/v1/chain/me                       Current user's ERC-8004 status
GET  /api/v1/chain/agent/{id}               Look up another agent

# Chat
POST /api/v1/llm/chat                       (routes to twin.chat — server is dumb)
POST /api/v1/files/upload                   multipart, streams to per-user dir

# Agent state — sidebar header / timeline / messages / memories
GET  /api/v1/agent/state                    {chain_agent_id, on_chain, counts, last_anchor}
GET  /api/v1/agent/timeline?limit=60        unified activity stream
GET  /api/v1/agent/messages?limit=200       chat history (server-authoritative)
GET  /api/v1/agent/memories?limit=50        legacy memory_compact projections

# Phase J.8 — typed memory namespaces
GET  /api/v1/agent/memory/namespaces?include_items=true&items_limit=50
     Returns NamespacesResponse: 5 NamespaceSummary rows (item_count,
     current_version, version_count) + optional bulk items keyed by
     namespace name (episodes / facts / skills / persona / knowledge).

# Phase O.5 — falsifiable-evolution timeline
GET  /api/v1/agent/evolution/verdicts?limit=100
     Returns EvolutionTimelineResponse: counts (proposals / verdicts /
     reverts), the events list (newest-first), and `pending` — the
     edit_ids with a proposal but no verdict yet.

# Phase O.6 — manual approve / revert
POST /api/v1/agent/evolution/{edit_id}/revert
     Forces decision="reverted", calls store.rollback(rollback_pointer),
     emits both evolution_verdict and evolution_revert events.
     Idempotent — re-calling on a settled edit returns the prior decision.

POST /api/v1/agent/evolution/{edit_id}/approve
     Forces decision="kept", emits an evolution_verdict event with
     trigger="manual" + approver=current_user.

# Anchors
GET  /api/v1/sync/anchors?limit=20          newest-first anchor lifecycle
```

All read endpoints fail open: they catch storage errors per row /
namespace and return what they can, so a single corrupt store can't
take down the whole panel.

---

## Install

```bash
pip install -e ".[dev]"
```

Optional LLM providers beyond the default Gemini:

```bash
pip install -e ".[llm-extra]"   # openai + anthropic
```

---

## Configuration

Settings come from environment variables (loaded from `./.env`,
`packages/server/.env`, then `packages/sdk/.env` — first to set a
key wins; later files only fill blanks).

```env
# Server basics
SERVER_HOST=0.0.0.0
SERVER_PORT=8001
SERVER_SECRET=your-jwt-signing-secret
ENVIRONMENT=development

# LLM
GEMINI_API_KEY=AIza...
DEFAULT_LLM_PROVIDER=gemini

# Database
DATABASE_URL=sqlite:///./nexus_server.db

# Twin (per-user EventLog SQLite)
NEXUS_USE_TWIN=1
NEXUS_TWIN_BASE_DIR=~/.nexus_server/twins   # default
NEXUS_TWIN_IDLE_SECONDS=1800

# WebAuthn
WEBAUTHN_RP_ID=localhost
WEBAUTHN_ORIGIN=http://localhost:8001

# Chain (custodial mode — server signs on behalf of the user)
SERVER_PRIVATE_KEY=0x...
NEXUS_TESTNET_RPC=https://data-seed-prebsc-1-s1.binance.org:8545
# Plus contract addresses pulled from packages/sdk/.env
```

---

## Run

```bash
python -m nexus_server.main --reload
# or via the entry-point script:
nexus-server --reload
```

The app starts on `0.0.0.0:8001` by default. Health check at
`GET /health`.

---

## Test

```bash
pytest tests/                    # 76 tests
pytest tests/ -k namespaces      # Phase J.8 endpoint
pytest tests/ -k evolution       # Phase O.5 timeline + Phase O.6 manual decisions
pytest --cov=nexus_server tests/ # coverage
```

The test suite spans: passkey + JWT lifecycle, attachment streaming
limits, twin routing (real and fake `_test_override`), chain bootstrap
+ idle reaper, namespace endpoint round-trip, evolution timeline,
manual approve / revert (with PersonaStore actually rolled back).

---

## Layout

```
nexus_server/
├── __init__.py
├── main.py                 FastAPI app assembly, lifespan, env loading
├── config.py               Settings dataclass
├── database.py             SQLite init + connection helpers
├── middleware.py           rate limiting, shared utilities
│
├── auth/                   passkey + JWT
│   ├── routes.py           /api/v1/auth/*
│   └── passkey_page.py     HTML/JS for passkey ceremonies
│
├── llm_gateway.py          /api/v1/llm/chat — routes to twin.chat
├── attachment_distiller.py thin shim → nexus_core.distiller
├── files.py                /api/v1/files/upload
│
├── chain_proxy.py          /api/v1/chain/* (ERC-8004 reads)
├── sync_anchor.py          legacy enqueue_anchor + list view (read-only)
│
├── twin_manager.py         per-user DigitalTwin lifecycle, idle reaper,
│                           bootstrap_chain_identity, chain-activity log
├── twin_event_log.py       read-only views over twin EventLog SQLite
├── agent_state.py          /api/v1/agent/{state,timeline,messages,memories,
│                                          memory/namespaces,
│                                          evolution/verdicts,
│                                          evolution/{edit}/{approve,revert}}
├── user_profile.py         /api/v1/profile/*
│
├── chat/  chain/  twins/  views/   Phase C navigation packages (facade
│                                   __init__.py only — canonical code at
│                                   top-level modules)
└── tests/                  76 cases
```

---

## Migration notes (if you came from older docs)

- `bnbchain_agent` → `nexus_core` (Phase D rename of the SDK package)
- `rune_twin` → `nexus` (Phase D rename of the framework package)
- `rune_server` → `nexus_server` (Phase D rename of this package)
- `nexus.{tools,skills,mcp}` thin re-exports → tombstones; import
  from `nexus_core.*` directly (Phase E)
- Logger namespace `rune.*` → `nexus_core.*` (Phase F)
- Greenfield bucket prefix `rune-agent-{token_id}` →
  `nexus-agent-{token_id}` (Phase F)
- Env var prefix `RUNE_*` → `NEXUS_*` (Phase G)
- `sync_hub.py`, `sync_events` table, anchor retry daemon — deleted
  (Phase B). `from nexus_server.sync_hub import …` raises ImportError
  on purpose.
- `RuneSessionService` / `RuneMemoryService` / `RuneArtifactService`
  → `NexusSessionService` / `NexusMemoryService` / `NexusArtifactService`
  in the ADK adapter (post-Phase H cleanup)
