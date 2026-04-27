# nexus-server

Multi-tenant FastAPI frontend for the Nexus DigitalTwin. The server
*does not* run agent intelligence itself — it routes per-user
requests through `nexus.DigitalTwin` (built on `nexus_core`) and
exposes thin HTTP views over each twin's per-user EventLog SQLite.

For the big picture (DPM, ABC, three-layer split, identity flow)
read the root [`README.md`](../../README.md),
[`ARCHITECTURE.md`](../../ARCHITECTURE.md), and
[`HISTORY.md`](../../HISTORY.md). This file is the package-level
quickstart only.

## What the server does

| Concern | Module | Routes |
| --- | --- | --- |
| Auth (passkey + JWT) | `nexus_server.auth` | `/api/v1/auth/*`, `/passkey` |
| Chat / attachments | `nexus_server.llm_gateway` (+ `attachment_distiller`, `files`) | `POST /api/v1/llm/chat`, `POST /api/v1/files/upload` |
| Chain (ERC-8004) | `nexus_server.chain_proxy` | `/api/v1/chain/me`, `/api/v1/chain/agent/{id}` |
| Read views over twin state | `nexus_server.agent_state` | `/api/v1/agent/{state,timeline,memories,messages}`, `/api/v1/sync/anchors` |
| Per-user twin lifecycle (background) | `nexus_server.twin_manager` (idle reaper, chain bootstrap, chain-activity log handler) | – |

Phase B (HISTORY.md) retired the standalone `sync_hub` event-sync
router and the `sync_events` mirror table — the desktop is a thin
client now and the twin's own EventLog is authoritative.

Phase C added the `auth/`, `chat/`, `chain/`, `twins/`, `views/`
domain sub-packages as a navigation aid for new readers; the
canonical implementations still live at the top-level
`nexus_server.*` modules.

## Install

```bash
pip install -e ".[dev]"
```

Optional LLM providers beyond the default Gemini:

```bash
pip install -e ".[llm-extra]"   # openai + anthropic
```

## Configuration

Settings come from environment variables (loaded from
`./.env`, `packages/server/.env`, then `packages/sdk/.env` —
first to set a key wins; later files only fill blanks).

```bash
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

# Chain (custodial mode)
SERVER_PRIVATE_KEY=0x...
NEXUS_TESTNET_RPC=https://data-seed-prebsc-1-s1.binance.org:8545
# Plus contract addresses pulled from packages/sdk/.env
```

## Run

```bash
python -m nexus_server.main --reload
# or via the entry-point script:
nexus-server --reload
```

The app starts on `0.0.0.0:8001` by default. Health check at
`GET /health`.

## Test

```bash
pytest tests/                    # 64 tests, ~3s
pytest tests/ -k attachments     # filter by name
pytest --cov=nexus_server tests/ # coverage
```

## Layout

```
nexus_server/
├── __init__.py             package exports + module overview
├── main.py                 FastAPI app assembly, lifespan, env loading
├── config.py               Settings dataclass
├── database.py             SQLite init + connection helpers
├── middleware.py           Rate limiting, shared utilities
│
├── auth/                   passkey + JWT  (real package — Phase C)
│   ├── routes.py           /api/v1/auth/*
│   └── passkey_page.py     HTML/JS for passkey ceremonies
│
├── llm_gateway.py          /api/v1/llm/chat — routes to twin.chat
├── attachment_distiller.py thin shim → nexus_core.distiller
├── files.py                /api/v1/files/upload (per-user picker)
│
├── chain_proxy.py          /api/v1/chain/* (ERC-8004 reads)
├── sync_anchor.py          legacy enqueue_anchor + list view (read-only)
│
├── twin_manager.py         per-user DigitalTwin lifecycle, idle reaper,
│                           bootstrap_chain_identity, chain-activity log
├── twin_event_log.py       read-only views over twin EventLog SQLite
├── agent_state.py          /api/v1/agent/{state,timeline,…}
├── user_profile.py         /api/v1/profile/*
│
├── chat/  chain/  twins/  views/   Phase C navigation packages
                                    (facade __init__.py only —
                                    canonical code at top level)
└── tests/                  test_server_regression.py (64 cases)
```

## Where things changed

If you came from older docs and something doesn't compile:

* `bnbchain_agent` → `nexus_core` (Phase D rename of the SDK package).
* `rune_twin` → `nexus` (Phase D rename of the framework package).
* `rune_server` → `nexus_server` (Phase D rename of this package).
* `nexus.{tools,skills,mcp}` thin re-exports → tombstones; import
  from `nexus_core.*` directly (Phase E).
* Logger namespace `rune.*` → `nexus_core.*` (Phase F).
* Greenfield bucket prefix `rune-agent-{token_id}` →
  `nexus-agent-{token_id}` (Phase F).
* `sync_hub.py`, `sync_events` table, anchor retry daemon —
  deleted (Phase B). `from nexus_server.sync_hub import …` raises
  ImportError on purpose.
