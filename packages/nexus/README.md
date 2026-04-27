# Rune Nexus — Self-Evolving Digital Twin

**The intelligence layer for Rune Protocol.** Nexus adds LLM integration, self-evolution, and a digital twin interface on top of the [BNBChain Agent SDK](../bnbchain-agent-sdk/).

```python
from rune_twin import DigitalTwin

twin = await DigitalTwin.create(
    name="My Twin",
    llm_provider="gemini",
    llm_api_key="AIza...",
)

response = await twin.chat("What's the latest in cancer immunotherapy?")
```

---

## Features

| Feature | How it works |
|---------|-------------|
| **DPM Memory** | Append-only event log + task-conditioned projection (1 LLM call, not N) |
| **Self-Evolution** | Memory extraction, skill learning, persona adaptation, knowledge compilation |
| **Behavioral Contracts** | Runtime enforcement with hard/soft constraints and drift detection |
| **Multi-LLM** | Gemini, GPT, Claude — switch with one config change |
| **Tool Use** | Function calling with WebSearch, URLReader, and custom tools |
| **MCP Integration** | Connect any MCP server, 27K+ available on LobeHub |
| **Skill Marketplace** | Install skills from LobeHub (100K+), Binance Skills Hub, or GitHub |
| **Passkey Auth** | WebAuthn login for the web demo |
| **On-Chain Identity** | ERC-8004/8183 registration, Greenfield storage, BSC anchoring |
| **Web Demo** | FastAPI + real-time sidebar + markdown/mermaid/KaTeX + file upload |

---

## Quick Start

```bash
cd rune-nexus
echo "GEMINI_API_KEY=AIza..." > .env

# CLI
python -m rune_twin

# Web demo
pip install fastapi uvicorn webauthn
python demo/web_demo.py
# Open http://localhost:8000
```

---

## Architecture

```
Rune Nexus (Intelligence Layer)
  twin.py              DigitalTwin — main agent class
  llm.py               Multi-provider LLM + function calling
  evolution/
    projection.py      DPM projection π(E, T, B)
    engine.py          EvolutionEngine orchestrator
    memory_evolver     Extract insights → CuratedMemory
    skill_evolver      Auto-detect skills from conversation
    persona_evolver    Adapt communication style
    knowledge_compiler Distill into reusable articles
  tools/
    base.py            ExtendedToolRegistry (MCP-aware)

BNBChain Agent SDK (Infrastructure Layer)
  memory/    EventLog + CuratedMemory
  contracts/ ABC enforcement + drift
  tools/     BaseTool, WebSearch, URLReader
  mcp/       MCPClient
  skills/    SkillManager (LobeHub, GitHub)
  backends/  Local, Chain, Mock
```

---

## How Memory Works (DPM)

```
User message → EventLog.append() [instant]
             → Projection [1 LLM call → FACTS + CONTEXT + USER_PROFILE]
             → LLM chat with projected memory
             → EventLog.append(response) [instant]
```

No summarization. No mutable state. Event log = source of truth.

---

## Web Demo

Passkey login → chat with markdown/mermaid/LaTeX rendering → file upload → skill installation → memory sidebar → on-chain activity feed → session persistence across page refreshes.

---

## Configuration

```
TWIN_LLM_PROVIDER=gemini
GEMINI_API_KEY=AIza...
RUNE_PRIVATE_KEY=0x...          # Optional: chain mode
TAVILY_API_KEY=tvly...          # Optional: web search
```

---

## License

Apache 2.0 — [BNB Chain](https://www.bnbchain.org/)
