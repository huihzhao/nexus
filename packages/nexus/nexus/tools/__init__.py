"""Tool Use framework — gives the Digital Twin ability to interact with the external world.

Core classes and built-in tools live in the SDK.
Nexus adds the MCP-aware ExtendedToolRegistry.
"""

# Re-export from SDK (canonical location)
from nexus_core.tools import BaseTool, ToolResult, ToolCall, ToolRegistry
from nexus_core.tools import WebSearchTool, URLReaderTool
from .base import ExtendedToolRegistry  # MCP + skill aware

__all__ = [
    "BaseTool",
    "ToolResult",
    "ToolCall",
    "ToolRegistry",
    "ExtendedToolRegistry",
    "WebSearchTool",
    "URLReaderTool",
]
