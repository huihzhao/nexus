"""Tool framework — BaseTool, ToolResult, ToolRegistry + built-in tools.

Built-in tools:
  - WebSearchTool: Web search via Tavily API
  - URLReaderTool: URL content extraction via Jina API
  - FileGeneratorTool: Generate files for user download
  - ReadUploadedFileTool: Read uploaded file content by section
"""

from .base import BaseTool, ToolResult, ToolCall, ToolRegistry
from .web_search import WebSearchTool
from .url_reader import URLReaderTool
from .file_generator import FileGeneratorTool
from .file_reader import ReadUploadedFileTool

__all__ = [
    "BaseTool", "ToolResult", "ToolCall", "ToolRegistry",
    "WebSearchTool", "URLReaderTool", "FileGeneratorTool",
    "ReadUploadedFileTool",
]
