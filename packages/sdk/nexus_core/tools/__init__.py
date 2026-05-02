"""Tool framework — BaseTool, ToolResult, ToolRegistry + built-in tools.

Built-in tools:
  - WebSearchTool: Web search via Tavily API
  - URLReaderTool: URL content extraction via Jina API
  - FileGeneratorTool: Generate files for user download
  - ReadUploadedFileTool: Read uploaded file content by section
  - SkillInstallerTool: Search + install Anthropic-style skills (LobeHub)
  - McpInstallerTool: Search + install MCP servers (LobeHub)
  - BscQueryTool: Direct read-only BSC chain queries via Web3 RPC
"""

from .base import BaseTool, ToolResult, ToolCall, ToolRegistry
from .web_search import WebSearchTool
from .url_reader import URLReaderTool
from .file_generator import FileGeneratorTool
from .file_reader import ReadUploadedFileTool
from .skill_installer import SkillInstallerTool, McpInstallerTool
from .bsc_query import BscQueryTool
from .chain_query import ChainQueryTool

__all__ = [
    "BaseTool", "ToolResult", "ToolCall", "ToolRegistry",
    "WebSearchTool", "URLReaderTool", "FileGeneratorTool",
    "ReadUploadedFileTool",
    "SkillInstallerTool", "McpInstallerTool",
    "BscQueryTool", "ChainQueryTool",
]
