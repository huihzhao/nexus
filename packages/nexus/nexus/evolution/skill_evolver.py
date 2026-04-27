"""
SkillEvolver — Learn from task outcomes AND conversations.

Architecture inspired by Hermes Agent's progressive disclosure:
  Level 0: Skill index (name + description, ~3k tokens total) — always in context
  Level 1: Full procedure (markdown, ~500-2000 tokens each) — loaded on demand

Two learning paths:
  1. Explicit tasks:   create_task → complete_task → record_task_outcome
  2. Conversation:     chat → learn_from_conversation (auto-detect tasks & patterns)

Usage tracking:
  Each skill tracks times_used, success_count, failure_count for
  evaluation feedback and smart eviction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import Counter
from typing import Any, Optional

from nexus_core import AgentRuntime
from .memory_evolver import _robust_json_parse

logger = logging.getLogger(__name__)

# ── Prompts ──────────────────────────────────────────────────────

SKILL_ANALYSIS_PROMPT = """Analyze this completed task and extract a skill learning.

Task:
  Type: {task_type}
  Description: {description}
  Strategy used: {strategy}
  Outcome: {outcome}
  User feedback: {feedback}

Existing skills for this task type:
{existing_skills}

Return a JSON object with:
- "skill_name": Short name for this skill (e.g., "travel_planning", "code_review")
- "description": One-line description of when to use this skill (max 100 chars)
- "procedure": Step-by-step procedure in markdown (3-8 steps, include pitfalls)
- "lesson": What was learned (1-2 sentences)
- "confidence": 0.0-1.0 (how confident based on evidence)
- "tags": List of relevant tags

Return ONLY valid JSON, no markdown fences."""


CONVERSATION_SKILL_PROMPT = """Analyze this conversation between a user and their AI twin.
Identify any skills being demonstrated or requested.

Conversation:
{conversation}

Current skill registry:
{existing_skills}

Look for:
1. IMPLICIT TASKS — Did the user ask the twin to DO something? (research, plan, write, analyze, compare, translate, explain, code, etc.)
   If the twin provided a helpful response, treat it as a successful task completion.
2. TOPIC EXPERTISE — Is the conversation about a specific domain? (travel, finance, coding, health, cooking, etc.)
   Repeated conversations on a topic mean the twin is developing expertise.
3. INTERACTION PATTERNS — How does the user prefer to interact? (wants step-by-step, prefers concise answers, asks follow-ups, etc.)

Return a JSON object:
{{
  "implicit_tasks": [
    {{
      "skill_name": "short_skill_name",
      "description": "One-line description of when to use this skill (max 100 chars)",
      "procedure": "## Procedure\\n1. Step one\\n2. Step two\\n\\n## Pitfalls\\n- Common mistake",
      "lesson": "What to remember for next time (1 sentence)",
      "confidence": 0.0-1.0,
      "tags": ["tag1", "tag2"]
    }}
  ],
  "topic_signals": [
    {{
      "topic": "topic_name",
      "evidence": "Why this topic was detected (1 sentence)"
    }}
  ]
}}

Rules:
- Only extract genuinely useful skills, not trivial chat (greetings, small talk)
- If nothing skill-worthy happened, return {{"implicit_tasks": [], "topic_signals": []}}
- Be conservative — only high-confidence observations
- skill_name should be snake_case (e.g., "travel_planning", "code_debugging")
- procedure should be markdown with ## headers for sections

IMPORTANT: You MUST always respond with a JSON object. Even if the conversation is trivial, respond with:
{{"implicit_tasks": [], "topic_signals": []}}

Return ONLY valid JSON, no markdown fences, no explanation."""


class SkillEvolver:
    """
    Tracks and evolves task execution strategies with progressive disclosure.

    Skills have two layers:
      Level 0 (index): name + description + tags (~20 tokens per skill)
      Level 1 (full):  procedure markdown + lessons + history (~500-2000 tokens)

    Two learning paths:
      1. record_task_outcome()      — explicit task delegation (API-driven)
      2. learn_from_conversation()  — auto-detect skills from chat (LLM-driven)
    """

    def __init__(self, rune: AgentRuntime, agent_id: str, llm_fn: Any):
        self.rune = rune
        self.agent_id = agent_id
        self.llm_fn = llm_fn
        self._skills_cache: dict[str, dict] = {}
        # Topic frequency tracker: {"travel": 3, "coding": 5, ...}
        self._topic_counts: dict[str, int] = {}
        # Threshold: after N occurrences of a topic, synthesize a skill
        self._topic_skill_threshold: int = 3
        self._dirty: bool = False  # True when locally modified — triggers merge on load
        self._lock = asyncio.Lock()  # Protects load/save from concurrent access
        # Skill names that are blocked from learning (e.g., names that conflict
        # with registered tools). Set by EvolutionEngine on init.
        self._blocked_names: set[str] = set()

    # ── Progressive Disclosure (Level 0 / Level 1) ──────────────

    def get_skill_index(self) -> list[dict]:
        """Level 0: Return lightweight summaries for all skills.

        Designed to be always injected into LLM context.
        ~20 tokens per skill, so 50 skills ≈ 1000 tokens.
        """
        index = []
        for name, skill in self._skills_cache.items():
            if name.startswith("_"):
                continue
            index.append({
                "name": name,
                "description": skill.get("description", skill.get("best_strategy", "")[:100]),
                "tags": skill.get("tags", []),
                "times_used": skill.get("times_used", 0),
                "success_rate": (
                    skill["success_count"] / skill["times_used"]
                    if skill.get("times_used", 0) > 0
                    else 0.0
                ),
            })
        return index

    def get_full_content(self, skill_name: str) -> Optional[str]:
        """Level 1: Return full procedure for a specific skill.

        Only loaded when LLM decides it needs this skill.
        """
        skill = self._skills_cache.get(skill_name)
        if not skill:
            return None

        parts = []

        # Procedure (markdown)
        procedure = skill.get("procedure", "")
        if procedure:
            parts.append(procedure)

        # Best strategy (legacy format, still useful)
        strategy = skill.get("best_strategy", "")
        if strategy and not procedure:
            parts.append(f"**Strategy:** {strategy}")

        # Recent lessons
        lessons = skill.get("lessons", [])
        if lessons:
            recent = lessons[-3:]  # Last 3 lessons
            parts.append("\n## Recent Lessons")
            for l in recent:
                parts.append(f"- {l.get('lesson', '')}")

        return "\n".join(parts) if parts else None

    def match_skills(self, query: str, top_k: int = 2) -> list[dict]:
        """Match query to skills using tag overlap + description TF-IDF.

        Returns the top_k most relevant skills (Level 0 info).
        """
        if not self._skills_cache:
            return []

        query_tokens = set(self._tokenize(query))
        if not query_tokens:
            return []

        scored = []
        for name, skill in self._skills_cache.items():
            if name.startswith("_"):
                continue

            # Tag matching (weight: 2x)
            skill_tags = set(t.lower() for t in skill.get("tags", []))
            tag_score = len(query_tokens & skill_tags) * 2

            # Skill name matching (weight: 3x)
            name_tokens = set(self._tokenize(name))
            name_score = len(query_tokens & name_tokens) * 3

            # Description TF-IDF (weight: 1x)
            desc = skill.get("description", "") + " " + skill.get("best_strategy", "")
            desc_tokens = set(self._tokenize(desc))
            desc_score = len(query_tokens & desc_tokens)

            # Usage frequency boost (up to 0.5 extra)
            usage_boost = min(skill.get("times_used", 0) / 20, 0.5)

            total = tag_score + name_score + desc_score + usage_boost
            if total > 0:
                scored.append((total, skill))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s[1] for s in scored[:top_k]]

    def record_skill_usage(self, skill_name: str, success: bool = True):
        """Record that a skill was used in context generation.

        Called by EvolutionEngine after injecting a skill into LLM context.
        Enables evaluation feedback loop.
        """
        skill = self._skills_cache.get(skill_name)
        if not skill:
            return
        skill["times_used"] = skill.get("times_used", 0) + 1
        if success:
            skill["success_count"] = skill.get("success_count", 0) + 1
        else:
            skill["failure_count"] = skill.get("failure_count", 0) + 1
        skill["last_used"] = time.time()
        self._dirty = True

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Simple word tokenizer for matching."""
        return [w for w in re.findall(r'\w+', text.lower()) if len(w) > 1]

    # ── Load / Save ─────────────────────────────────────────────

    async def load_skills(self) -> dict[str, dict]:
        async with self._lock:
            return await self._load_skills_unlocked()

    async def _load_skills_unlocked(self) -> dict[str, dict]:
        try:
            art = await self.rune.artifacts.load(
                "skills_registry.json", agent_id=self.agent_id,
            )
            if art:
                data = json.loads(art.data.decode())
                remote_skills = data.get("skills", data) if isinstance(data, dict) else {}
                remote_topics = data.get("_topic_counts", {}) if isinstance(data, dict) else {}

                if self._dirty:
                    # Merge: remote first, then local overwrites (local wins on conflict)
                    merged = {**remote_skills}
                    merged.update(self._skills_cache)  # local takes precedence
                    self._skills_cache = merged
                    # Topic counts: take the max of each
                    for k, v in remote_topics.items():
                        self._topic_counts[k] = max(self._topic_counts.get(k, 0), v)
                    self._dirty = False  # Merge complete — state is now consistent
                    logger.info(
                        "Skills merged: %d remote + %d local → %d total",
                        len(remote_skills), len(self._skills_cache) - len(remote_skills),
                        len(self._skills_cache),
                    )
                else:
                    self._skills_cache = remote_skills
                    self._topic_counts = remote_topics
        except Exception:
            if not self._dirty:
                self._skills_cache = {}
        return self._skills_cache

    async def _save_skills_unlocked(self):
        data = json.dumps({
            "skills": {k: v for k, v in self._skills_cache.items() if not k.startswith("_")},
            "_topic_counts": self._topic_counts,
        }, indent=2, ensure_ascii=False)
        await self.rune.artifacts.save(
            filename="skills_registry.json",
            data=data.encode(),
            agent_id=self.agent_id,
            content_type="application/json",
            metadata={"type": "evolution_artifact", "subtype": "skills"},
        )

    # ── Path 1: Explicit Task Learning ───────────────────────────

    async def record_task_outcome(
        self,
        task_type: str,
        description: str,
        strategy: str,
        outcome: str,
        feedback: str = "",
    ) -> Optional[dict]:
        async with self._lock:
            return await self._record_task_outcome_unlocked(
                task_type, description, strategy, outcome, feedback,
            )

    async def _record_task_outcome_unlocked(
        self,
        task_type: str,
        description: str,
        strategy: str,
        outcome: str,
        feedback: str = "",
    ) -> Optional[dict]:
        await self._load_skills_unlocked()

        existing = self._skills_cache.get(task_type, {})
        prompt = SKILL_ANALYSIS_PROMPT.format(
            task_type=task_type,
            description=description,
            strategy=strategy,
            outcome=outcome,
            feedback=feedback or "None provided",
            existing_skills=json.dumps(existing, ensure_ascii=False) if existing else "None yet",
        )

        try:
            raw = await self.llm_fn(prompt)
            logger.debug(
                "Skill extraction: LLM raw response (%d chars): %.200s%s",
                len(raw), raw, "..." if len(raw) > 200 else "",
            )
            learning = _robust_json_parse(raw)
        except Exception as e:
            raw_preview = repr(raw[:300]) if 'raw' in dir() and raw else "<no response>"
            logger.warning(
                "Skill extraction failed: %s | LLM response (%s chars): %s",
                e, len(raw) if 'raw' in dir() and raw else 0, raw_preview,
            )
            return None

        if not isinstance(learning, dict):
            logger.warning(f"Skill analysis returned non-dict: {type(learning)}")
            return None

        skill_name = learning.get("skill_name", task_type)
        self._upsert_skill(
            skill_name=skill_name,
            description=learning.get("description", ""),
            procedure=learning.get("procedure", ""),
            lesson=learning.get("lesson", ""),
            strategy=learning.get("strategy_update", learning.get("procedure", "")),
            confidence=learning.get("confidence", 0.5),
            tags=learning.get("tags", []),
            outcome=outcome,
            source="task",
        )
        await self._save_skills_unlocked()
        logger.info(f"Skill updated [{skill_name}]: {learning.get('lesson', '')[:60]}...")
        return learning

    # ── Path 2: Conversation-Based Learning ──────────────────────

    async def learn_from_conversation(
        self,
        conversation: list[dict],
        max_skills: int = 3,
    ) -> list[dict]:
        """
        Analyze a conversation and extract skills automatically.

        Returns list of learned skills (may be empty if conversation
        was trivial or no skills were detected).
        """
        async with self._lock:
            return await self._learn_from_conversation_unlocked(conversation, max_skills)

    async def _learn_from_conversation_unlocked(
        self,
        conversation: list[dict],
        max_skills: int = 3,
    ) -> list[dict]:
        await self._load_skills_unlocked()

        convo_text = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Nexus'}: {m['content']}"
            for m in conversation[-10:]
        )

        # Filter out skills cache internal keys
        visible_skills = {
            k: v for k, v in self._skills_cache.items()
            if not k.startswith("_")
        }

        prompt = CONVERSATION_SKILL_PROMPT.format(
            conversation=convo_text,
            existing_skills=json.dumps(
                {name: {
                    "description": s.get("description", ""),
                    "strategy": s.get("best_strategy", ""),
                    "tasks": s.get("task_count", 0),
                }
                 for name, s in visible_skills.items()},
                ensure_ascii=False,
            ) if visible_skills else "Empty — no skills learned yet",
        )

        try:
            raw = await self.llm_fn(prompt)
            # Gemini often returns empty for trivial conversations (greetings,
            # small talk) where no skills are detected. This is expected — skip
            # silently instead of logging a warning every turn.
            if not raw or not raw.strip():
                logger.debug("Skill detection: LLM returned empty (no skills in conversation)")
                return []
            logger.debug(
                "Skill detection: LLM raw response (%d chars): %.200s%s",
                len(raw), raw, "..." if len(raw) > 200 else "",
            )
            result = _robust_json_parse(raw)
        except Exception as e:
            # Log the raw LLM response so we can see what failed to parse
            raw_preview = repr(raw[:300]) if 'raw' in dir() and raw else "<no response>"
            logger.warning(
                "Conversation skill detection failed: %s | LLM response (%s chars): %s",
                e, len(raw) if 'raw' in dir() and raw else 0, raw_preview,
            )
            return []

        if not isinstance(result, dict):
            return []

        learned = []

        # ── Process implicit tasks ──
        implicit_tasks = result.get("implicit_tasks", [])
        if isinstance(implicit_tasks, list):
            for task in implicit_tasks[:max_skills]:
                if not isinstance(task, dict):
                    continue
                skill_name = task.get("skill_name", "")
                if not skill_name:
                    continue

                # Skip skills that conflict with registered tool names.
                # Without this, the LLM learns "web_search" as a text skill
                # and then role-plays using it instead of calling the real tool.
                if skill_name in self._blocked_names:
                    logger.debug(
                        "Skipping skill '%s' — conflicts with registered tool",
                        skill_name,
                    )
                    continue

                self._upsert_skill(
                    skill_name=skill_name,
                    description=task.get("description", ""),
                    procedure=task.get("procedure", ""),
                    lesson=task.get("lesson", ""),
                    strategy=task.get("strategy", task.get("procedure", "")),
                    confidence=task.get("confidence", 0.5),
                    tags=task.get("tags", []),
                    outcome="success",
                    source="conversation",
                )
                learned.append({
                    "skill_name": skill_name,
                    "lesson": task.get("lesson", ""),
                    "source": "implicit_task",
                    "description": task.get("description", ""),
                })
                logger.info(
                    f"Skill from conversation [{skill_name}]: "
                    f"{task.get('lesson', '')[:60]}..."
                )

        # ── Process topic signals → accumulate and promote ──
        topic_signals = result.get("topic_signals", [])
        if isinstance(topic_signals, list):
            promoted = self._accumulate_topics(topic_signals)
            for topic_skill in promoted:
                learned.append(topic_skill)

        if learned:
            await self._save_skills_unlocked()

        return learned

    def _accumulate_topics(self, signals: list[dict]) -> list[dict]:
        """
        Track topic frequencies. When a topic crosses the threshold,
        promote it to a skill entry.
        """
        promoted = []
        for signal in signals:
            if not isinstance(signal, dict):
                continue
            topic = signal.get("topic", "").lower().strip()
            if not topic:
                continue

            self._topic_counts[topic] = self._topic_counts.get(topic, 0) + 1
            count = self._topic_counts[topic]

            # Check if this topic should be promoted to a skill
            skill_name = topic.replace(" ", "_").replace("-", "_")
            already_skill = skill_name in self._skills_cache

            if count >= self._topic_skill_threshold and not already_skill:
                self._upsert_skill(
                    skill_name=skill_name,
                    description=f"Domain expertise in {topic}",
                    procedure=f"## When to Use\nConversations about {topic}.\n\n## Approach\nDraw on accumulated knowledge from {count} past conversations about {topic}.",
                    lesson=f"User frequently discusses {topic} ({count} conversations). Developing domain expertise.",
                    strategy=f"Draw on accumulated knowledge about {topic} from past conversations.",
                    confidence=min(0.3 + count * 0.1, 0.9),
                    tags=[topic, "topic_expertise"],
                    outcome="pattern",
                    source="topic_accumulation",
                )
                promoted.append({
                    "skill_name": skill_name,
                    "lesson": f"Topic expertise unlocked: {topic} ({count} conversations)",
                    "source": "topic_pattern",
                })
                logger.info(
                    f"Topic promoted to skill [{skill_name}] "
                    f"after {count} conversations"
                )
            elif already_skill and count % self._topic_skill_threshold == 0:
                # Reinforce existing topic skill
                skill = self._skills_cache[skill_name]
                skill["confidence"] = min(skill.get("confidence", 0.5) + 0.05, 0.95)
                skill["task_count"] = skill.get("task_count", 0) + 1
                skill["updated_at"] = time.time()

        return promoted

    # ── Shared: Upsert Skill Entry ───────────────────────────────

    def _upsert_skill(
        self,
        skill_name: str,
        description: str = "",
        procedure: str = "",
        lesson: str = "",
        strategy: str = "",
        confidence: float = 0.5,
        tags: list[str] = None,
        outcome: str = "success",
        source: str = "task",
    ):
        """Create or update a skill entry in the cache."""
        if tags is None:
            tags = []

        if skill_name not in self._skills_cache:
            self._skills_cache[skill_name] = {
                "name": skill_name,
                "description": "",
                "procedure": "",
                "lessons": [],
                "best_strategy": "",
                "confidence": 0.0,
                "tags": [],
                "task_count": 0,
                "success_count": 0,
                "times_used": 0,
                "failure_count": 0,
                "last_used": 0.0,
                "version": 1,
                "created_at": time.time(),
            }

        skill = self._skills_cache[skill_name]

        # Update description (keep latest non-empty)
        if description:
            skill["description"] = description[:200]

        # Update procedure (keep latest non-empty)
        if procedure:
            skill["procedure"] = procedure

        # Append lesson
        if lesson:
            skill["lessons"].append({
                "lesson": lesson,
                "outcome": outcome,
                "source": source,
                "timestamp": time.time(),
            })
            skill["lessons"] = skill["lessons"][-10:]  # keep last 10

        if strategy:
            skill["best_strategy"] = strategy
        skill["confidence"] = max(skill["confidence"], confidence)
        skill["tags"] = list(set(skill.get("tags", []) + tags))
        skill["task_count"] = skill.get("task_count", 0) + 1
        if outcome.lower() in ("success", "pattern"):
            skill["success_count"] = skill.get("success_count", 0) + 1
        skill["updated_at"] = time.time()
        self._dirty = True  # Mark as locally modified — background load will merge

    # ── Legacy Query (backward compatible) ───────────────────────

    def get_strategy_from_cache(self, task_type: str) -> Optional[str]:
        """
        Get strategy from in-memory cache ONLY.

        Never triggers a load — if skills aren't loaded yet, returns None.
        This is the non-blocking version used during chat.
        """
        return self._match_strategy(task_type)

    async def get_strategy_for(self, task_type: str, context: str = "") -> Optional[str]:
        async with self._lock:
            await self._load_skills_unlocked()
            return self._match_strategy(task_type)

    def _match_strategy(self, task_type: str) -> Optional[str]:
        """Pure in-memory strategy lookup."""
        if task_type in self._skills_cache:
            return self._skills_cache[task_type].get("best_strategy")
        for name, skill in self._skills_cache.items():
            if name.startswith("_"):
                continue
            if task_type.lower() in [t.lower() for t in skill.get("tags", [])]:
                return skill.get("best_strategy")
        return None

    async def get_stats(self) -> dict:
        await self.load_skills()
        visible = {k: v for k, v in self._skills_cache.items() if not k.startswith("_")}
        return {
            "total_skills": len(visible),
            "total_tasks_completed": sum(
                s.get("task_count", 0) for s in visible.values()
            ),
            "topic_tracking": dict(self._topic_counts),
            "skills": {
                name: {
                    "tasks": s.get("task_count", 0),
                    "times_used": s.get("times_used", 0),
                    "success_rate": (
                        s["success_count"] / s["task_count"]
                        if s.get("task_count", 0) > 0 else 0
                    ),
                    "confidence": s.get("confidence", 0),
                    "has_procedure": bool(s.get("procedure")),
                }
                for name, s in visible.items()
            },
        }
