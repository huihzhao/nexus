"""
Evolution Engine — Self-improvement closed loop.
"""

from .memory_evolver import MemoryEvolver
from .skill_evolver import SkillEvolver
from .skill_evaluator import SkillEvaluator
from .persona_evolver import PersonaEvolver
from .knowledge_compiler import KnowledgeCompiler
from .social_engine import SocialEngine
from .engine import EvolutionEngine

__all__ = [
    "EvolutionEngine",
    "MemoryEvolver",
    "SkillEvolver",
    "SkillEvaluator",
    "PersonaEvolver",
    "KnowledgeCompiler",
    "SocialEngine",
]
