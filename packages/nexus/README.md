# nexus — self-evolving DigitalTwin

The intelligence layer of the BNBChain agent platform. Nexus adds
LLM integration, self-evolution, and the `DigitalTwin` runtime on
top of the [`nexus_core`](../sdk/) SDK (DPM memory, ABC contracts,
chain backend, tools/skills/MCP primitives).

```python
from nexus import DigitalTwin

twin = await DigitalTwin.create(
    name="My Twin",
    llm_provider="gemini",
    llm_api_key="AIza...",
)

response = await twin.chat("What's the latest in cancer immunotherapy?")
```

The cross-cutting story (three-layer split, DPM, ABC, identity
flow) lives in the root [`README.md`](../../README.md) and
[`ARCHITECTURE.md`](../../ARCHITECTURE.md). This file is the
package-level reference.

---

## What's in nexus vs. nexus_core

| In `nexus` (this package) | In `nexus_core` (the SDK) |
| --- | --- |
| `DigitalTwin` (`twin.py`) — chat loop, evolution scheduling, MCP-aware tool registry | `EventLog`, `CuratedMemory`, `Compactor` (DPM primitives) |
| `TwinConfig`, `LLMProvider` (`config.py`) | `ContractEngine`, `DriftScore` (ABC primitives) |
| `ExtendedToolRegistry` (`tools/base.py`) — adds MCP support on top of the SDK's `ToolRegistry` | `BaseTool`, `ToolRegistry`, `WebSearchTool`, `URLReaderTool`, `FileGeneratorTool` |
| MemoryEvolver / SkillEvolver / PersonaEvolver / KnowledgeCompiler (under `evolution/`) | `MCPClient`, `MCPManager` |
| Web demo (`demo/web_demo.py`) | `SkillManager`, `InstalledSkill` |
| CLI (`nexus.cli:cli_main`) | `BSCClient`, `GreenfieldClient`, `ChainBackend` |

> Phase E note — `nexus.{tools,skills,mcp}` used to be thin
> re-export shims. They're now tombstones; import from
> `nexus_core.*` directly. The only Nexus-specific tools class
> still hosted here is `ExtendedToolRegistry`.

---

## Features

| Feature | How it works |
| --- | --- |
| DPM memory | Append-only EventLog + task-conditioned projection (1 LLM call, not N) |
| Self-evolution | MemoryEvolver, SkillEvolver, PersonaEvolver, KnowledgeCompiler |
| Behavioral contracts | Runtime enforcement with hard/soft constraints + DriftScore |
| Multi-LLM | Gemini, GPT, Claude — switch via `TwinConfig.llm_provider` |
| Tool use | Function calling with built-in tools + custom tools |
| MCP integration | Connect any MCP server (`twin.tools.register_mcp_server(...)`) |
| Skill marketplace | Install skills from LobeHub, Binance Skills Hub, or GitHub via `SkillManager` |
| On-chain identity | ERC-8004 registration + Greenfield state-root anchoring (delegated to `nexus_core.ChainBackend`) |
| Web demo | FastAPI + real-time sidebar + markdown/mermaid/KaTeX + file upload + passkey auth |

---

## Quick start

```bash
cd packages/nexus
echo "GEMINI_API_KEY=AIza..." > .env

# CLI
python -m nexus

# Web demo
pip install fastapi uvicorn webauthn
python demo/web_demo.py
# open http://localhost:8000
```

---

## Architecture (in package)

```
nexus/
├── twin.py              DigitalTwin — chat loop, evolution scheduling
├── config.py            TwinConfig, LLMProvider
├── llm.py               Multi-provider LLM facade
├── evolution/
│   ├── projection.py    DPM projection π(E, T, B)
│   ├── engine.py        EvolutionEngine orchestrator
│   ├── memory_evolver.py
│   ├── skill_evolver.py
│   ├── persona_evolver.py
│   └── knowledge_compiler.py
├── tools/
│   └── base.py          ExtendedToolRegistry (MCP-aware)
├── cli.py               python -m nexus entry point
└── demo/
    └── web_demo.py      FastAPI demo with passkey login
```

---

## How memory works (DPM)

```
User message → EventLog.append()                       [instant]
             → Projection [1 LLM call → FACTS + CONTEXT + USER_PROFILE]
             → LLM chat with projected memory
             → EventLog.append(response)               [instant]
```

No periodic summarisation. No mutable state. EventLog =
authoritative source of truth.

---

## Configuration

```env
TWIN_LLM_PROVIDER=gemini
GEMINI_API_KEY=AIza...

# Chain mode (optional — see nexus_core docs for the full chain
# config; the twin pulls these via TwinConfig).
NEXUS_PRIVATE_KEY=0x...
NEXUS_TESTNET_RPC=https://data-seed-prebsc-1-s1.binance.org:8545

# Web search backends (optional)
TAVILY_API_KEY=tvly...
JINA_API_KEY=jina_...
```

---

## License

Apache 2.0 — [BNB Chain](https://www.bnbchain.org/)
