# Nexus

A self-evolving AI agent platform anchored on BNB Chain. Each user gets a
private, persistent agent (a **Digital Twin**) whose memory, identity, and
behaviour are auditable on-chain.

## Five-minute tour

A user installs the desktop app, signs in with passkey, and starts chatting.
Behind the scenes:

1. **Server** spins up a Digital Twin for that user (lazy, per-user).
2. **Twin** runs a 9-step chat flow: contract pre-check → append to event
   log → project relevant memory → call LLM → contract post-check → drift
   score → append response → background self-evolution.
3. **SDK** writes every event to BNB Greenfield (durable storage) and
   anchors a content hash to BSC (verifiability) — per agent, in the
   agent's own bucket `nexus-agent-{token_id}`.
4. **Desktop** is a thin client. It holds nothing on disk except the JWT;
   chat history, memories, anchor status all read from server.

The "self-evolving" part is real: every N turns the twin compacts its event
log into a curated memory snapshot, occasionally rewrites its persona
based on what it's learned, and grows its skill registry. All of this is
persisted on Greenfield + anchored on BSC, so the agent's growth is
verifiable.

## Repository layout

```
packages/
  sdk/nexus_core/         ← Infrastructure: chain, storage, primitives.
                            Knows nothing about "agent" or "user".
  nexus/nexus/            ← Agent framework: DigitalTwin + 9-step chat +
                            self-evolution. Built on top of nexus_core.
  server/nexus_server/    ← FastAPI multi-tenant HTTP frontend. One
                            DigitalTwin per logged-in user.
  desktop/                ← Avalonia C# thin client. UI only.
docs/
  concepts/               ← Mental models a new dev needs.
  how-to/                 ← Step-by-step recipes.
ARCHITECTURE.md           ← How the layers fit together.
HISTORY.md                ← How we got to the current design.
```

> **Naming note**. Doc names (`nexus_core`, `nexus`, `nexus_server`)
> are the target. The Python packages on disk are still
> `bnbchain_agent`, `rune_twin`, and `rune_server` — code rename is a
> separate Phase D step. Imports today still read `from bnbchain_agent
> import …` etc.; this lag is documented in [`HISTORY.md`](HISTORY.md).

## Where to read first

| You want to… | Read this |
|---|---|
| Understand the system at all | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Know why the design is what it is | [`HISTORY.md`](HISTORY.md) |
| Trace what happens when a user sends a message | [`docs/concepts/data-flow.md`](docs/concepts/data-flow.md) |
| Understand the memory model | [`docs/concepts/dpm.md`](docs/concepts/dpm.md) |
| Understand the safety / contract model | [`docs/concepts/abc.md`](docs/concepts/abc.md) |
| Understand on-chain identity | [`docs/concepts/identity.md`](docs/concepts/identity.md) |
| Understand chain mode vs local mode | [`docs/concepts/modes.md`](docs/concepts/modes.md) |
| Add a new tool the agent can call | [`docs/how-to/add-a-tool.md`](docs/how-to/add-a-tool.md) |
| Add a new behaviour rule | [`docs/how-to/add-a-contract-rule.md`](docs/how-to/add-a-contract-rule.md) |
| Build the desktop locally | [`packages/desktop/README.md`](packages/desktop/README.md) |
| Run the server locally | [`packages/server/README.md`](packages/server/README.md) |

## Quickstart

```bash
# Server
cd packages/server
uv sync
cp .env.example .env  # fill in GEMINI_API_KEY at minimum
uv run rune-server

# Desktop (separate terminal, requires .NET 8+)
cd packages/desktop
dotnet run --project RuneDesktop.UI
```

For a fully on-chain setup (BSC + Greenfield), see
[`docs/concepts/modes.md`](docs/concepts/modes.md) and
[`packages/server/README.md`](packages/server/README.md).

## Key concepts in one paragraph each

**DPM (Deterministic Projection Memory).** The agent's memory is an
append-only event log. Every chat turn appends events; "memories" are not
a separate store but a *projection* of the log — a function that reads
recent events and returns a curated summary. The agent never "forgets" a
fact in the log; it just chooses what to project for the current turn.
[Full doc.](docs/concepts/dpm.md)

**ABC (Agent Behaviour Contract).** Every agent ships with a YAML
contract listing rules (compliance, distributional, system invariants).
A `ContractEngine` runs pre-check on user input and post-check on
LLM output; violations are logged and may abort the turn. A `DriftScore`
tracks how often the agent deviates from its contract over time, giving
operators a single compliance metric.
[Full doc.](docs/concepts/abc.md)

**On-chain identity.** Every agent owns an ERC-8004 token on BSC. The
token id is the agent's permanent identifier; it owns a Greenfield bucket
named `nexus-agent-{token_id}` where its event log + memory snapshots
live, and the BSC `AgentStateExtension` contract anchors a content-hash
state root for that bucket. Third parties can independently verify the
agent's growth by replaying the bucket and checking the on-chain hash.
[Full doc.](docs/concepts/identity.md)

## Status

Test phase. APIs may break, schemas may break, on-chain contracts are on
BSC testnet only. See [`ROADMAP.md`](ROADMAP.md) for what's next.
