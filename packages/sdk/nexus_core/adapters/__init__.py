"""
Nexus — Framework Adapters.

Each adapter is a thin translation layer between a specific agent
framework and Rune's provider interfaces. Adapters only do type
conversion + delegation — no persistence logic.

Available adapters:
  - adk:           Google ADK (NexusSessionService, NexusMemoryService, NexusArtifactService)
  - langgraph:     LangGraph (RuneCheckpointer)
  - crewai:        CrewAI (RuneCrewStorage, RuneCrewCheckpointStorage)
  - a2a:           A2A Protocol (StatelessA2AAgent, A2ARuntime)
  - a2a_task_store: A2A TaskStore (BNBChainTaskStore)
  - adk_memory:    Legacy ADK memory adapter (NexusMemoryService)

Use AdapterRegistry to discover available adapters:

    from nexus_core.adapters.registry import AdapterRegistry
    print(AdapterRegistry.available())  # ['adk', 'langgraph', 'crewai']
"""

from .registry import AdapterRegistry

__all__ = ["AdapterRegistry"]
