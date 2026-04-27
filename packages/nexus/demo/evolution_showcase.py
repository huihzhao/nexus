#!/usr/bin/env python3
"""
Rune Nexus — Evolution System Showcase

Demonstrates all 4 sub-systems of the self-evolving digital twin:

  1. MemoryEvolver    — Extract insights from conversations, persist as memories
  2. SkillEvolver     — Learn skills from tasks AND conversations
  3. KnowledgeCompiler — Cluster memories into structured knowledge articles
  4. PersonaEvolver   — Evolve the twin's persona based on accumulated knowledge

No external API key needed — uses MockBackend + MockLLM.

Usage:
    python demo/evolution_showcase.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus_core import Rune

from nexus.config import TwinConfig
from nexus.twin import DigitalTwin
from nexus.evolution.engine import EvolutionEngine
from nexus.evolution.memory_evolver import MemoryEvolver
from nexus.evolution.skill_evolver import SkillEvolver
from nexus.evolution.knowledge_compiler import KnowledgeCompiler
from nexus.evolution.persona_evolver import PersonaEvolver


# ── Mock LLM (same as test suite — deterministic, no API calls) ────

class MockLLMClient:
    """Simulates LLM responses for the evolution pipeline."""

    def __init__(self):
        self.call_count = 0

    async def chat(self, messages, system="", temperature=0.7, max_tokens=2048):
        self.call_count += 1
        last = messages[-1]["content"] if messages else ""
        if "sushi" in last.lower():
            return "I remember you love sushi! Shall I suggest some restaurants?"
        elif "tokyo" in last.lower():
            return "Tokyo sounds amazing! I recall you're planning a March trip."
        elif "code" in last.lower() or "solidity" in last.lower():
            return "Based on your Solidity experience, I'd suggest using Hardhat for testing."
        return f"Got it — I'll remember that."

    async def complete(self, prompt, temperature=0.3):
        self.call_count += 1

        if "analyze this completed task" in prompt.lower():
            return json.dumps({
                "skill_name": "smart_contract_review",
                "lesson": "User prefers gas-optimized Solidity patterns",
                "strategy_update": "Always check for reentrancy and suggest gas optimizations",
                "confidence": 0.85,
                "tags": ["solidity", "code_review", "security"],
            })
        elif "identify any skills" in prompt.lower():
            if "sushi" in prompt.lower() or "food" in prompt.lower():
                return json.dumps({
                    "implicit_tasks": [{
                        "skill_name": "food_recommendation",
                        "description": "Discussed food preferences",
                        "strategy": "Remember specific cuisine preferences and dietary restrictions",
                        "lesson": "User has strong sushi preference, allergic to shellfish",
                        "confidence": 0.8,
                        "tags": ["food", "preference"],
                    }],
                    "topic_signals": [
                        {"topic": "japanese_cuisine", "evidence": "User mentioned sushi"},
                    ],
                })
            elif "tokyo" in prompt.lower() or "travel" in prompt.lower():
                return json.dumps({
                    "implicit_tasks": [{
                        "skill_name": "travel_planning",
                        "description": "Helped plan Tokyo trip",
                        "strategy": "Provide detailed itineraries with restaurant suggestions",
                        "lesson": "User prefers cultural experiences over tourist spots",
                        "confidence": 0.75,
                        "tags": ["travel", "japan"],
                    }],
                    "topic_signals": [
                        {"topic": "japan_travel", "evidence": "Planning Tokyo trip"},
                    ],
                })
            elif "solidity" in prompt.lower() or "code" in prompt.lower():
                return json.dumps({
                    "implicit_tasks": [{
                        "skill_name": "code_review",
                        "description": "Reviewed Solidity contract",
                        "strategy": "Focus on gas optimization and security patterns",
                        "lesson": "User values security-first approach",
                        "confidence": 0.9,
                        "tags": ["solidity", "blockchain", "review"],
                    }],
                    "topic_signals": [
                        {"topic": "blockchain_dev", "evidence": "Discussing Solidity"},
                    ],
                })
            return json.dumps({"implicit_tasks": [], "topic_signals": []})
        elif "analyze the following conversation" in prompt.lower():
            return json.dumps([
                {"content": "User loves sushi, especially salmon nigiri", "category": "preference", "importance": 5},
                {"content": "User planning Tokyo trip March 2026", "category": "fact", "importance": 4},
                {"content": "User works on Solidity smart contracts at BNB Chain", "category": "fact", "importance": 4},
                {"content": "User prefers gas-optimized code patterns", "category": "preference", "importance": 3},
            ])
        elif "selecting relevant memories" in prompt.lower():
            import re
            ids = re.findall(r'\[([a-f0-9-]{36})\]', prompt)
            return json.dumps(ids[:3])
        elif "group them into topic clusters" in prompt.lower():
            return json.dumps({
                "food_preferences": [0, 1, 2],
                "travel_plans": [3, 4],
                "blockchain_development": [5, 6, 7],
            })
        elif "synthesize these related memories" in prompt.lower():
            topic = "general"
            if "food" in prompt.lower():
                topic = "food_preferences"
                return json.dumps({
                    "title": "User Food Preferences",
                    "summary": "The user has strong preferences for Japanese cuisine.",
                    "content": "The user loves Japanese food, particularly sushi (salmon nigiri is a favorite) and ramen. They are allergic to shellfish — this is critical safety information. They enjoy spicy Sichuan dishes and dislike overly sweet desserts. Monthly dining budget is around $500.",
                    "key_facts": ["loves sushi", "allergic to shellfish", "likes spicy food"],
                    "tags": ["food", "japanese", "sushi", "preferences"],
                    "memory_count": 3,
                    "confidence": 0.9,
                })
            elif "travel" in prompt.lower():
                topic = "travel_plans"
                return json.dumps({
                    "title": "User Travel Plans & Preferences",
                    "summary": "The user is planning a Japan trip and prefers cultural experiences.",
                    "content": "The user is planning a Tokyo trip for March 2026. They previously visited Seoul and loved Gangnam district. They prefer window seats on flights and have Global Entry for US customs. They favor cultural experiences over standard tourist attractions.",
                    "key_facts": ["Tokyo trip March 2026", "prefers window seats", "has Global Entry"],
                    "tags": ["travel", "japan", "tokyo", "preferences"],
                    "memory_count": 2,
                    "confidence": 0.85,
                })
            elif "blockchain" in prompt.lower() or "dev" in prompt.lower():
                return json.dumps({
                    "title": "User Development Background",
                    "summary": "The user is a blockchain developer at BNB Chain.",
                    "content": "The user works at BNB Chain as a developer, primarily using Python and Solidity. They value security-first development and gas-optimized patterns. They are researching AI agent frameworks for a new project. They use VSCode with Vim keybindings.",
                    "key_facts": ["works at BNB Chain", "Python + Solidity", "security-first approach"],
                    "tags": ["blockchain", "solidity", "python", "bnbchain"],
                    "memory_count": 3,
                    "confidence": 0.9,
                })
            return json.dumps({
                "title": f"User {topic.replace('_', ' ').title()}",
                "summary": f"Summary of {topic}",
                "content": f"Compiled knowledge about {topic}.",
                "key_facts": [],
                "tags": [topic],
                "memory_count": 2,
                "confidence": 0.7,
            })
        elif "evolved persona" in prompt.lower():
            return json.dumps({
                "evolved_persona": (
                    "You are Nexus, a self-evolving digital twin. "
                    "You know your user is a blockchain developer at BNB Chain who works with "
                    "Python and Solidity. They love Japanese food (especially sushi — but "
                    "they're allergic to shellfish!). They're planning a Tokyo trip for "
                    "March 2026. They value security-first code and gas optimizations. "
                    "You adapt your communication style to be technical and precise."
                ),
                "changes_summary": "Integrated food preferences, travel plans, and dev background",
                "confidence": 0.88,
                "version_notes": "v1: food + travel + blockchain expertise",
            })
        return "[]"

    async def close(self):
        pass


# ── Helpers ──────────────────────────────────────────────────────

def header(title):
    print(f"\n{'=' * 64}")
    print(f"  {title}")
    print(f"{'=' * 64}\n")


def subheader(title):
    print(f"\n  --- {title} ---\n")


# ── Main Demo ────────────────────────────────────────────────────

async def main():
    print("""
+======================================================================+
|                                                                      |
|  Rune Nexus — Evolution System Showcase                              |
|                                                                      |
|  Watch a digital twin evolve through 4 sub-systems:                  |
|                                                                      |
|  1. MemoryEvolver     — Extract & store conversation insights        |
|  2. SkillEvolver      — Learn skills from tasks + conversation       |
|  3. KnowledgeCompiler — Compile memories into knowledge articles     |
|  4. PersonaEvolver    — Evolve persona based on accumulated knowledge|
|                                                                      |
|  No API keys needed — uses mock LLM for deterministic output.        |
|                                                                      |
+======================================================================+
    """)

    rune = Rune.builder().mock_backend().build()
    llm = MockLLMClient()
    agent_id = "demo-twin"

    # ─────────────────────────────────────────────────────────────
    # Phase 1: Memory Extraction
    # ─────────────────────────────────────────────────────────────
    header("PHASE 1: MemoryEvolver — Extract Insights from Conversations")

    memory_ev = MemoryEvolver(rune, agent_id, llm.complete)

    conversations = [
        [
            {"role": "user", "content": "I love sushi, especially salmon nigiri"},
            {"role": "assistant", "content": "Great taste! Salmon nigiri is wonderful."},
        ],
        [
            {"role": "user", "content": "I'm planning a trip to Tokyo in March"},
            {"role": "assistant", "content": "Tokyo in March is beautiful — cherry blossom season!"},
        ],
        [
            {"role": "user", "content": "Can you review this Solidity contract for gas issues?"},
            {"role": "assistant", "content": "Sure, I'll focus on gas optimization patterns."},
        ],
    ]

    total_stored = 0
    for i, convo in enumerate(conversations):
        print(f"  Conversation {i+1}: \"{convo[0]['content'][:50]}...\"")
        stored = await memory_ev.extract_and_store(convo, max_memories=5)
        total_stored += len(stored)
        for m in stored:
            print(f"    → [{m['category']:>12}] imp={m['importance']} {m['content'][:55]}...")

    stats = await memory_ev.get_stats()
    print(f"\n  Total memories stored: {stats['total_memories']}")
    print(f"  Categories: {json.dumps(stats['categories'])}")

    # ─────────────────────────────────────────────────────────────
    # Phase 2: Skill Learning (two paths)
    # ─────────────────────────────────────────────────────────────
    header("PHASE 2: SkillEvolver — Learn Skills from Tasks + Conversations")

    skill_ev = SkillEvolver(rune, agent_id, llm.complete)

    # Path 1: Explicit task outcome
    subheader("Path 1: Explicit Task → Skill Learning")
    print("  Task: Review Solidity contract for gas optimization")
    learning = await skill_ev.record_task_outcome(
        task_type="code_review",
        description="Review ERC-20 token contract",
        strategy="Focus on storage slot packing and loop optimization",
        outcome="success",
        feedback="Found 3 gas issues, saved ~30% gas",
    )
    if learning:
        print(f"  Skill learned: {learning['skill_name']}")
        print(f"  Lesson: {learning['lesson']}")
        print(f"  Strategy: {learning['strategy_update']}")
        print(f"  Confidence: {learning['confidence']}")

    # Path 2: Conversation-based learning
    subheader("Path 2: Conversation → Implicit Skill Detection")
    for convo in conversations:
        topic = convo[0]["content"][:40]
        learned = await skill_ev.learn_from_conversation(convo)
        if learned:
            for s in learned:
                print(f"  [{topic}...]")
                print(f"    → Skill: {s['skill_name']} ({s['source']})")
                print(f"      Lesson: {s['lesson']}")

    skill_stats = await skill_ev.get_stats()
    print(f"\n  Total skills: {skill_stats['total_skills']}")
    print(f"  Tasks completed: {skill_stats['total_tasks_completed']}")
    for name, info in skill_stats["skills"].items():
        print(f"    [{name}] tasks={info['tasks']}, "
              f"success={info['success_rate']:.0%}, "
              f"confidence={info['confidence']:.2f}")

    # ─────────────────────────────────────────────────────────────
    # Phase 3: Knowledge Compilation
    # ─────────────────────────────────────────────────────────────
    header("PHASE 3: KnowledgeCompiler — Memories → Structured Articles")

    # Add more memories to reach the compilation threshold
    extra_memories = [
        ("User is allergic to shellfish", "preference", 5),
        ("User prefers spicy Sichuan food", "preference", 4),
        ("User visited Seoul and loved Gangnam", "fact", 3),
        ("User prefers window seats on flights", "preference", 2),
        ("User's team uses Python and Solidity", "fact", 4),
        ("User uses VSCode with Vim keybindings", "preference", 2),
        ("User is researching AI agent frameworks", "fact", 4),
        ("User prefers gas-optimized code patterns", "preference", 3),
    ]
    for content, category, importance in extra_memories:
        await rune.memory.add(content, agent_id, metadata={
            "category": category, "importance": importance,
        })

    all_mem = await rune.memory.list_all(agent_id)
    print(f"  Total memories before compilation: {len(all_mem)}")

    compiler = KnowledgeCompiler(rune, agent_id, llm.complete)
    result = await compiler.compile(min_memories=6)

    print(f"\n  Compilation status: {result['status']}")
    if result["status"] == "compiled":
        print(f"  Clusters found: {result['clusters_found']}")
        print(f"  New articles: {result['new_articles']}")
        print(f"  Total articles: {result['total_articles']}")

        subheader("Compiled Knowledge Articles")
        for topic, article in compiler.get_all_articles().items():
            print(f"  [{topic}]")
            print(f"    Title: {article.get('title', '?')}")
            print(f"    Summary: {article.get('summary', '?')}")
            print(f"    Key facts: {article.get('key_facts', [])}")
            print(f"    Confidence: {article.get('confidence', 0):.2f}")
            print(f"    Version: {article.get('version', 1)}")
            print()

        # Show context retrieval
        subheader("Context for Query: 'sushi restaurant in Tokyo'")
        ctx = await compiler.get_context_for_query("sushi restaurant Tokyo")
        if ctx:
            for line in ctx.split("\n")[:10]:
                print(f"    {line}")
        else:
            print("    (No matching articles)")

    # ─────────────────────────────────────────────────────────────
    # Phase 4: Persona Evolution
    # ─────────────────────────────────────────────────────────────
    header("PHASE 4: PersonaEvolver — Evolve the Twin's Identity")

    persona_ev = PersonaEvolver(rune, agent_id, llm.complete)
    await persona_ev.load_persona("You are Nexus, a helpful digital twin.")

    print(f"  Initial persona: \"{persona_ev.current_persona[:60]}...\"")

    all_mem = await rune.memory.list_all(agent_id)
    memory_texts = [m.content for m in all_mem[-10:]]

    evolution = await persona_ev.evolve(
        memories_sample=memory_texts,
        skills_summary=skill_stats,
    )

    print(f"\n  Evolution result:")
    print(f"    Version: {evolution.get('version', '?')}")
    print(f"    Changes: {evolution.get('changes', '?')}")
    print(f"    Confidence: {evolution.get('confidence', '?')}")
    print(f"\n  Evolved persona:")
    for line in persona_ev.current_persona.split(". "):
        print(f"    {line.strip()}.")

    # ─────────────────────────────────────────────────────────────
    # Phase 5: Full Integration — EvolutionEngine
    # ─────────────────────────────────────────────────────────────
    header("PHASE 5: EvolutionEngine — Full Orchestration")

    engine = EvolutionEngine(rune, "engine-demo", llm.complete, "Default persona.")
    await engine.initialize()

    print("  Simulating 3 conversation turns...\n")
    test_conversations = [
        [{"role": "user", "content": "I want sushi for dinner tonight"},
         {"role": "assistant", "content": "Great choice! Any specific type?"}],
        [{"role": "user", "content": "Help me plan my Tokyo trip itinerary"},
         {"role": "assistant", "content": "I'd love to help! When are you going?"}],
        [{"role": "user", "content": "Review this Solidity code for security issues"},
         {"role": "assistant", "content": "Sure, let me check for common vulnerabilities."}],
    ]

    for i, convo in enumerate(test_conversations):
        result = await engine.after_conversation_turn(convo)
        print(f"  Turn {result['turn']}: \"{convo[0]['content'][:45]}...\"")
        for action in result["actions"]:
            if action["type"] == "memory_extraction":
                print(f"    → Extracted {action['count']} memories")
            elif action["type"] == "skill_learning":
                print(f"    → Learned {action['count']} skills: {action['skills']}")

    subheader("Triggering Full Reflection Cycle")
    # Add enough memories for engine's compilation
    for text in ["engine sushi", "engine ramen", "engine tokyo", "engine shibuya",
                  "engine solidity", "engine hardhat"]:
        await rune.memory.add(f"User likes {text}", "engine-demo",
                              metadata={"category": "fact", "importance": 3})

    reflection = await engine.trigger_reflection()
    print(f"  Reflection type: {reflection['type']}")
    print(f"  Memory stats: {reflection['memory_stats']['total_memories']} memories")
    print(f"  Skill stats: {reflection['skill_stats']['total_skills']} skills")
    pe = reflection.get("persona_evolution", {})
    print(f"  Persona version: {pe.get('version', '?')}")
    kc = reflection.get("knowledge_compilation", {})
    print(f"  Knowledge compilation: {kc.get('status', '?')}")

    subheader("Context-Aware Retrieval")
    context = await engine.get_context_for_query("sushi restaurant")
    if context:
        print("  Context injected into LLM prompt:")
        for line in context.split("\n")[:8]:
            print(f"    {line}")
    else:
        print("  (No context available yet)")

    # ─────────────────────────────────────────────────────────────
    # Summary
    # ─────────────────────────────────────────────────────────────
    full_stats = await engine.get_full_stats()
    print(f"""

{'=' * 64}
  EVOLUTION SHOWCASE COMPLETE
{'=' * 64}

  LLM calls made: {llm.call_count} (all mock — zero cost)

  ┌──────────────────────────────────────────────────────────┐
  │  Sub-System         │  What it does                      │
  ├──────────────────────────────────────────────────────────┤
  │  MemoryEvolver      │  Extract insights → persist memories│
  │  SkillEvolver       │  Learn from tasks + conversations  │
  │  KnowledgeCompiler  │  Cluster → synthesize → articles   │
  │  PersonaEvolver     │  Evolve identity from knowledge    │
  └──────────────────────────────────────────────────────────┘

  All data persisted via Rune Protocol SDK:
    - Memories      → rune.memory   (progressive retrieval ready)
    - Skills        → rune.artifacts (skills_registry.json)
    - Knowledge     → rune.artifacts (knowledge_articles.json)
    - Persona       → rune.artifacts (persona_history.json)
    - Sessions      → rune.sessions (checkpoint/resume)

  In production, swap Rune.builder().mock_backend() for:
    Rune.local()                 → file-based persistence
    Rune.testnet(key="0x...")    → BSC + Greenfield (verifiable)

  The twin evolves with every conversation. No retraining needed.
    """)


if __name__ == "__main__":
    asyncio.run(main())
