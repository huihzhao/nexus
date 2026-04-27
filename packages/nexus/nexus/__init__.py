"""
Rune Nexus — Self-Evolving AI Avatar on BNBChain.

Built on top of the Rune Protocol SDK (bnbchain-agent-sdk).

    from nexus import DigitalTwin

    twin = await DigitalTwin.create("my-twin", llm_api_key="AIza...")
    await twin.chat("Help me plan a trip to Tokyo")
"""

from .twin import DigitalTwin
from .config import TwinConfig, LLMProvider
from .tools import ExtendedToolRegistry

# Re-export SDK classes for convenience
from nexus_core.tools import BaseTool, ToolResult, ToolCall, ToolRegistry
from nexus_core.mcp import MCPManager, MCPServerConfig
from nexus_core.skills import SkillManager

__version__ = "0.1.0"
__all__ = [
    "DigitalTwin",
    "TwinConfig",
    "LLMProvider",
    "ExtendedToolRegistry",
    # Re-exported from SDK
    "BaseTool",
    "ToolResult",
    "ToolCall",
    "ToolRegistry",
    "MCPManager",
    "MCPServerConfig",
    "SkillManager",
]
