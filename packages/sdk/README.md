# Rune Protocol SDK

**Self-evolving AI agents with on-chain memory on BNB Chain.**

Rune gives AI agents persistent memory, behavioral contracts, tool/skill ecosystems, and verifiable identity on [BNB Chain](https://www.bnbchain.org/). Any agent framework, any LLM — your agent logic stays the same.

```python
from bnbchain_agent import Rune, EventLog, CuratedMemory, ContractEngine

# Storage: local or on-chain
rune = Rune.local()                          # Zero config
rune = Rune.testnet(private_key="0x...")      # BSC + Greenfield

# DPM: Append-only event log + task-conditioned projection
event_log = EventLog(base_dir=".agent", agent_id="my-agent")
event_log.append("user_message", "Hello, remember I like sushi")

# Behavioral contracts: runtime enforcement
from bnbchain_agent.contracts import ContractSpec
spec = ContractSpec.from_yaml("contract.yaml")
engine = ContractEngine(spec, event_log=event_log)
```

---

## What's in the SDK

| Module | What it does |
|--------|-------------|
| **memory/** | DPM EventLog (SQLite + FTS5) + CuratedMemory + EventLogCompactor |
| **contracts/** | Agent Behavioral Contracts — preconditions, invariants, governance, drift detection |
| **tools/** | BaseTool + ToolRegistry + WebSearch + URLReader |
| **mcp/** | MCP client (stdio/HTTP) — connect any MCP server |
| **skills/** | Skill manager — install from GitHub, Binance Skills Hub, or LobeHub (100K+ skills) |
| **backends/** | Local (file), Chain (BSC + Greenfield), Mock (tests) |
| **providers/** | Session, Memory, Artifact, Task, Impression providers |
| **adapters/** | Google ADK, LangGraph, CrewAI, A2A Protocol |
| **social/** | Gossip protocol, social graph, agent profiles |
| **utils/** | JSON repair, dotenv, agent ID conversion |

---

## Quick Start

```bash
pip install -e .
python demo/run_all.py --mode local
```

---

## Architecture

```
┌──────────────────────────────────────────────────┐
│  Your Agent (any framework)                      │
├──────────────────────────────────────────────────┤
│  Contracts  │  Skills/MCP  │  Tools              │  ← SDK capabilities
├──────────────────────────────────────────────────┤
│  Memory (EventLog + CuratedMemory)               │  ← DPM architecture
├──────────────────────────────────────────────────┤
│  Providers (Session, Memory, Artifact, Task)      │  ← Domain logic
├──────────────────────────────────────────────────┤
│  Backends (Local, Chain, Mock)                    │  ← Storage strategy
├──────────────────────────────────────────────────┤
│  BNB Chain (BSC + Greenfield)                     │  ← Infrastructure
└──────────────────────────────────────────────────┘
```

---

## Memory: Deterministic Projection Memory (DPM)

Based on [arXiv:2604.20158](https://arxiv.org/abs/2604.20158). Instead of stateful summarization, we use:

1. **Append-only EventLog** — every interaction stored in SQLite with FTS5 search. Events are never edited or deleted.
2. **Task-conditioned projection** — at decision time, one LLM call extracts relevant context from the log.

Properties (by construction): deterministic replay, auditable rationale, multi-tenant isolation, stateless horizontal scale.

```python
from bnbchain_agent.memory import EventLog

log = EventLog(base_dir=".agent", agent_id="my-agent")
log.append("user_message", "I work in blockchain research")
log.append("assistant_response", "Great! I'll remember that.")

# Full-text search across all events
results = log.search("blockchain")

# Get trajectory for LLM projection
trajectory = log.get_trajectory(max_chars=50000)
```

---

## Behavioral Contracts

Based on [arXiv:2602.22302](https://arxiv.org/abs/2602.22302). Runtime enforcement of agent behavior:

```yaml
# contract.yaml
contract:
  invariants:
    hard:
      - check: no_pii_leak
      - check: no_hallucinated_tools
    soft:
      - check: language_match
        params: { target: "zh-CN" }
        recovery: regenerate
        recovery_window: 3
  governance:
    hard:
      - check: tool_whitelist
        allowed: [web_search, url_reader]
```

```python
from bnbchain_agent.contracts import ContractEngine, ContractSpec

spec = ContractSpec.from_yaml("contract.yaml")
engine = ContractEngine(spec)

# Pre-check before LLM call
result = engine.pre_check(user_message)
if result.blocked:
    return result.reason

# Post-check after LLM response
result = engine.post_check(response)
# Drift monitoring
drift = engine.drift.current()  # D(t) ∈ [0, 1]
```

---

## Skills & MCP

Install capabilities from Binance Skills Hub, LobeHub (100K+ skills, 27K+ MCP servers), or GitHub:

```python
from bnbchain_agent.skills import SkillManager

manager = SkillManager(base_dir=".agent")

# Search LobeHub
results = await manager.search_lobehub("pdf editor")
results = await manager.search_mcp("postgres")

# Install
skill = await manager.install("lobehub:lobehub-pdf-tools")
info = await manager.install_mcp("crystaldba-postgres-mcp", tool_registry=registry)
```

---

## Tools & MCP Servers

```python
from bnbchain_agent.tools import ToolRegistry, WebSearchTool
from bnbchain_agent.mcp import MCPServerConfig

registry = ToolRegistry()
registry.register(WebSearchTool(api_key="tvly-..."))

# Connect MCP server
await registry.register_mcp_server(MCPServerConfig(
    name="filesystem",
    transport="stdio",
    command="npx",
    args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
))
```

---

## On-Chain Identity

Agents register on BSC via ERC-8004/8183:

```python
rune = Rune.testnet(private_key="0x...")
# Auto-registers on first startup
# Identity, memory hashes, and artifacts stored on Greenfield
# Content hashes anchored to BSC for tamper-proof verification
```

---

## Framework Adapters

| Framework | Adapter | What it wraps |
|-----------|---------|--------------|
| Google ADK | `RuneSessionService` | Session + checkpoint persistence |
| LangGraph | `RuneCheckpointer` | Graph state persistence |
| CrewAI | `RuneCrewStorage` | Agent memory + task storage |
| A2A Protocol | `StatelessA2AAgent` | Agent-to-agent communication |

---

## License

Apache 2.0 — [BNB Chain](https://www.bnbchain.org/)
