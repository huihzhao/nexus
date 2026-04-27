"""Rune Protocol Server - Modular FastAPI application.

A comprehensive server serving as:
- LLM gateway (Gemini, OpenAI, Claude)
- Authentication provider (JWT + WebAuthn)
- Event sync hub (client-server data synchronization)
- Chain proxy (ERC-8004 agent registration)

Components:
    config: Environment-based configuration
    auth: JWT and WebAuthn authentication
    llm_gateway: Multi-provider LLM proxy
    sync_hub: Event synchronization
    chain_proxy: Blockchain operations
    database: SQLite helpers
    middleware: Shared middleware and utilities
    main: FastAPI app assembly
"""

__version__ = "0.1.0"
__author__ = "Rune Protocol Team"
__all__ = [
    "__version__",
    "app",
]

from nexus_server.main import create_app

app = create_app()
