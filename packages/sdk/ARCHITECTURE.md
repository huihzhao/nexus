# Rune Protocol SDK — Architecture

## Project Structure

```
bnbchain_agent/
  memory/             # DPM: EventLog (SQLite+FTS5) + CuratedMemory
    event_log.py      #   Append-only event log with full-text search
    curated.py        #   Hermes-style MEMORY.md + USER.md
  contracts/          # ABC: Agent Behavioral Contracts
    spec.py           #   YAML contract definition + user rules
    engine.py         #   Runtime enforcement (pre/post check)
    drift.py          #   Behavioral drift score (compliance + distributional)
  tools/              # Tool framework
    base.py           #   BaseTool, ToolResult, ToolRegistry
    web_search.py     #   WebSearchTool (Tavily)
    url_reader.py     #   URLReaderTool (Jina)
  mcp/                # MCP client (Model Context Protocol)
    client.py         #   MCPClient (stdio), MCPManager, MCPServerConfig
  skills/             # Skill management
    manager.py        #   Install from GitHub, LobeHub Skills, LobeHub MCP
  core/               # Abstract interfaces
    providers.py      #   StorageBackend, RuneProvider ABCs
    models.py         #   Checkpoint, MemoryEntry, Artifact, Social models
    backend.py        #   StorageBackend base class
    flush.py          #   FlushPolicy, WriteAheadLog
  backends/           # Storage implementations
    local.py          #   File-based, no chain
    chain.py          #   BSC + Greenfield + WAL + daemon
    mock.py           #   In-memory for tests
  providers/          # Domain-specific data managers
    session.py        #   SessionProviderImpl (with backend load)
    memory.py         #   MemoryProviderImpl (dirty tracking, bulk_add)
    artifact.py       #   ArtifactProviderImpl (rollback)
    task.py           #   TaskProviderImpl
    impression.py     #   ImpressionProviderImpl (social)
  adapters/           # Framework integrations
    adk.py            #   Google ADK
    langgraph.py      #   LangGraph
    crewai.py         #   CrewAI
    a2a.py            #   A2A Protocol
    a2a_task_store.py #   A2A TaskStore
    registry.py       #   AdapterRegistry
  social/             # Social protocol primitives
    gossip.py, graph.py, profile.py
  utils/              # Shared utilities
    json_parse.py     #   robust_json_parse (LLM output repair)
    dotenv.py         #   .env file loader
    agent_id.py       #   Agent ID to uint256 conversion
  builder.py          # Rune.builder() entry point
  state.py            # StateManager
  chain.py            # RuneChainClient (BSC contracts)
  greenfield.py       # GreenfieldClient (storage + persistent daemon)
  keystore.py         # RuneKeystore (encrypted wallet)
```

## Layered Architecture

```
┌───────────────────────────────────────────────────────┐
│  Contracts (ABC enforcement, drift detection)         │
├───────────────────────────────────────────────────────┤
│  Skills / MCP / Tools (capabilities)                  │
├───────────────────────────────────────────────────────┤
│  Memory (EventLog + CuratedMemory)                    │
├───────────────────────────────────────────────────────┤
│  Adapters (ADK, LangGraph, CrewAI, A2A)               │
├───────────────────────────────────────────────────────┤
│  Providers (Session, Memory, Artifact, Task)           │
├───────────────────────────────────────────────────────┤
│  Backends (Local, Chain, Mock)                         │
├───────────────────────────────────────────────────────┤
│  BNB Chain (BSC + Greenfield)                          │
└───────────────────────────────────────────────────────┘
```

Each layer depends only on the layer below. No circular dependencies.

## Memory Architecture (DPM)

Based on "Stateless Decision Memory for Enterprise AI Agents" (arXiv:2604.20158).

```
Conversation event → EventLog.append() [SQLite, instant]
                          │
                          ▼
Decision time → Projection π(E, T, B) [one LLM call]
                          │
                          ▼
                   Memory view M (FACTS + CONTEXT + USER_PROFILE)
                          │
                          ▼
                   Injected into system prompt
```

EventLog is the single source of truth. Events are never edited, summarized, or deleted. The projection is a pure function over the log — same log + same model = same output.

Enterprise properties: deterministic replay, auditable rationale (2 LLM calls vs 83-97), multi-tenant isolation, stateless scale.

### Auto-Compact (EventLogCompactor)

When the event log grows beyond 30K chars, the compactor triggers a background projection and writes the result back to the EventLog as a `memory_compact` event. This event syncs to Greenfield like any other, ensuring compact summaries are on-chain.

```python
from bnbchain_agent.memory import EventLogCompactor

compactor = EventLogCompactor(event_log, curated_memory, projection_fn=my_llm)

if compactor.should_compact(turn_count=20):
    await compactor.compact(session_id="session_abc")
    # 1. Projection → appended to EventLog as memory_compact event
    # 2. CuratedMemory (MEMORY.md / USER.md) updated as derived view
```

## Greenfield Data Structure

### Layout B — One bucket per agent (CANONICAL)

```
nexus-agent-{erc8004_token_id}/                 ← Bucket per ERC-8004 NFT
  sync/
    {content_hash}.json                        ← One JSON per anchor batch
  sessions/...
  memory/...
  contracts/...
  curated_memory/...
```

This is the canonical layout for any SDK consumer that has an
ERC-8004 token id — Rune Server, Nexus production deployments,
multi-tenant SaaS. Use the helper:

```python
from bnbchain_agent import bucket_for_agent

bucket = bucket_for_agent(token_id)            # → "nexus-agent-864"
backend = ChainBackend(private_key=..., greenfield_bucket=bucket)
```

Rationale:
- **Aligns storage ownership with NFT ownership.** Each ERC-8004 agent
  is an ERC-721 token. The bucket lives or dies with the token; a
  future "transfer agent" workflow maps to a single resource.
- **Quota / billing per agent.** Multi-tenant SaaS can attach quota,
  retention, and billing to a single Greenfield resource.
- **Blast radius isolation.** A malformed write or compromised agent
  can only affect its own bucket.
- **Naming is well-formed.** ERC-8004 tokenIds are uint256; the bucket
  name `nexus-agent-{N}` stays under Greenfield's 63-char ceiling for
  any realistic N and avoids the IP-address-shape rule.

### Layout A — Single shared bucket (LEGACY)

```
nexus-agent-state/
  agents/{agent_id}/
    sessions/...
    memory/...
    ...
```

`GreenfieldClient` and `ChainBackend` still accept the legacy single
bucket name `nexus-agent-state` for backward compatibility, but emit a
`DeprecationWarning` at construction time. Standalone Nexus dev
sessions that haven't acquired an ERC-8004 token id may still rely on
this — they should pass `bucket_name=...` explicitly to silence the
warning, or migrate to Layout B once they register on chain.

EventLog events (including `memory_compact`) are the canonical data. Everything else is a derived view. On-chain verification: `SHA-256(greenfield_data) == bsc_state_root`.

## Contract Architecture (ABC)

Based on "Agent Behavioral Contracts" (arXiv:2602.22302).

Contract C = (P, I_hard, I_soft, G_hard, G_soft, R):

```
User message
    │
    ▼
Pre-check (Hard Governance) ──── Blocked? → Return error
    │
    ▼
LLM generates response
    │
    ▼
Post-check (Invariants) ──── Hard violation? → Recovery
    │                         Soft violation? → Track + recover in k steps
    ▼
Update Drift Score D(t) = w_c × compliance + w_d × distributional
    │
    ├── D(t) < θ₁ → normal
    ├── θ₁ < D(t) < θ₂ → warning
    └── D(t) > θ₂ → intervention
```

User-defined rules (from conversation) are persisted as soft constraints. Cannot override hard constraints.

## Greenfield Storage

```
Agent calls store_json(path, data)
    │
    ├── 1. Write to local cache (instant)
    ├── 2. Append to WAL (crash-safe)
    └── 3. Fire async Greenfield PUT via persistent Node.js daemon
              │
              └── Daemon: SDK init once → reuse for all PUTs (no cold start)
```

WAL ensures cancelled writes are replayed on next startup. Daemon eliminates the 2-3s Node.js cold start per operation.

## Two-Chain Model

**BSC**: Small tamper-proof commitments (32-byte SHA-256 hashes). ERC-8004/8183 identity registry.

**Greenfield**: Actual data (sessions, memories, artifacts). Content-addressed: hash(payload) = on-chain pointer.

Verification: `SHA-256(greenfield_data) == bsc_state_root`
