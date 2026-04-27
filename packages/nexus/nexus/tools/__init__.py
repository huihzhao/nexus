"""Nexus-specific tool extensions.

Phase E note — the canonical home for the tool framework is
:mod:`nexus_core.tools` (``BaseTool``, ``ToolResult``, ``ToolCall``,
``ToolRegistry``, ``WebSearchTool``, ``URLReaderTool``). Import those
directly from the SDK:

    from nexus_core.tools import BaseTool, ToolResult, ToolRegistry
    from nexus_core.tools import WebSearchTool, URLReaderTool

This package now exists *only* to host
:class:`ExtendedToolRegistry`, the MCP-aware subclass that
:class:`nexus.DigitalTwin` uses. Nothing else is re-exported — the
old shim that pulled SDK names through here was removed because it
inverted the dependency story (Nexus depends on Core, not the
reverse) and made it ambiguous where built-in tools lived.
"""

from .base import ExtendedToolRegistry

__all__ = ["ExtendedToolRegistry"]
