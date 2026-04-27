"""
PersonaEvolver — Self-optimize the twin's system prompt and behavior.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from nexus_core import RuneProvider

logger = logging.getLogger(__name__)

REFLECTION_PROMPT = """You are a digital twin's self-reflection engine.

Analyze the twin's current state and produce an evolved persona.

Current persona:
{current_persona}

Accumulated memories (sample):
{memories_sample}

Learned skills:
{skills_summary}

Recent conversation patterns:
{recent_patterns}

Evolution history (last 3 changes):
{evolution_history}

Instructions:
1. Identify what the twin has learned about its owner
2. Determine how the twin's communication style should adapt
3. Note any new capabilities or knowledge areas
4. Write an EVOLVED persona that incorporates these learnings

Rules:
- Keep the core identity stable — evolution should be incremental
- Make the persona more specific and personalized over time
- Add specific knowledge and preferences learned
- Adjust tone/style based on conversation patterns
- Never remove safety guardrails

Return a JSON object:
{{
  "evolved_persona": "The full updated persona text...",
  "changes_summary": "Brief description of what changed and why",
  "confidence": 0.0-1.0,
  "version_notes": "Short label for this evolution step"
}}

Return ONLY valid JSON, no markdown fences."""


class PersonaEvolver:
    """Evolves the twin's persona through self-reflection."""

    def __init__(self, rune: RuneProvider, agent_id: str, llm_fn: Any):
        self.rune = rune
        self.agent_id = agent_id
        self.llm_fn = llm_fn
        self._current_persona: str = ""
        self._evolution_history: list[dict] = []
        self._version: int = 0
        self._dirty: bool = False  # True when locally modified — prevents background overwrite

    async def load_persona(self, default_persona: str) -> str:
        # If persona was locally modified (e.g. evolved during chat),
        # skip loading — local version is newer than what's on chain.
        if self._dirty:
            logger.debug("Persona load skipped — locally modified (dirty)")
            return self._current_persona

        try:
            art = await self.rune.artifacts.load(
                "persona.json", agent_id=self.agent_id,
            )
            if art:
                data = json.loads(art.data.decode())
                self._current_persona = data.get("persona", default_persona)
                self._evolution_history = data.get("history", [])
                self._version = art.version
                logger.info(f"Loaded persona v{self._version}")
                return self._current_persona
        except Exception:
            pass
        self._current_persona = default_persona
        return self._current_persona

    async def _save_persona(self):
        data = json.dumps({
            "persona": self._current_persona,
            "history": self._evolution_history[-20:],
            "version": self._version,
            "updated_at": time.time(),
        }, indent=2, ensure_ascii=False)

        self._version = await self.rune.artifacts.save(
            filename="persona.json",
            data=data.encode(),
            agent_id=self.agent_id,
            content_type="application/json",
            metadata={"type": "evolution_artifact", "subtype": "persona"},
        )

    async def evolve(
        self,
        memories_sample: list[str],
        skills_summary: dict,
        recent_patterns: str = "",
    ) -> dict:
        history_text = ""
        for h in self._evolution_history[-3:]:
            history_text += f"- v{h.get('version', '?')}: {h.get('changes', 'N/A')}\n"
        if not history_text:
            history_text = "No previous evolutions."

        prompt = REFLECTION_PROMPT.format(
            current_persona=self._current_persona,
            memories_sample=json.dumps(memories_sample[:15], ensure_ascii=False),
            skills_summary=json.dumps(skills_summary, ensure_ascii=False),
            recent_patterns=recent_patterns or "Not enough data yet.",
            evolution_history=history_text,
        )

        try:
            raw = await self.llm_fn(prompt)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
            result = json.loads(raw)
        except Exception as e:
            logger.warning(f"Persona evolution failed: {e}")
            return {"error": str(e)}

        evolved = result.get("evolved_persona", "")
        if not evolved or len(evolved) < 50:
            return {"skipped": True, "reason": "Output too short"}

        confidence = result.get("confidence", 0.5)
        if confidence < 0.3:
            return {"skipped": True, "reason": f"Low confidence: {confidence}"}

        self._current_persona = evolved
        self._dirty = True  # Mark as locally modified — background load won't overwrite

        await self._save_persona()
        # _save_persona updates self._version from the artifact store return value
        self._evolution_history.append({
            "version": self._version,
            "changes": result.get("changes_summary", ""),
            "notes": result.get("version_notes", ""),
            "confidence": confidence,
            "timestamp": time.time(),
        })

        logger.info(f"Persona evolved to v{self._version}")
        return {
            "version": self._version,
            "changes": result.get("changes_summary", ""),
            "notes": result.get("version_notes", ""),
            "confidence": confidence,
        }

    @property
    def current_persona(self) -> str:
        return self._current_persona

    async def get_evolution_history(self) -> list[dict]:
        return list(self._evolution_history)

    async def get_stats(self) -> dict:
        return {
            "persona_version": self._version,
            "total_evolutions": len(self._evolution_history),
            "persona_length": len(self._current_persona),
        }
