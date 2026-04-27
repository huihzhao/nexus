"""FileGeneratorTool — generate files (txt, md, html, csv, json) for user download.

The agent can create files and place them in an output directory.
The web demo serves these via /api/files/{filename} for download.

For complex formats (docx, pdf, pptx), generates HTML that the user
can open in a browser and print/save as the desired format.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class FileGeneratorTool(BaseTool):
    """Generate files for user download."""

    def __init__(self, output_dir: str | Path = "."):
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return "generate_file"

    @property
    def description(self) -> str:
        return (
            "Generate a file for the user to download. "
            "Supports: txt, md, html, csv, json. "
            "For documents (reports, articles), generate HTML with styling. "
            "Returns the download URL."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Output filename (e.g., 'report.html', 'data.csv')",
                },
                "content": {
                    "type": "string",
                    "description": "File content to write",
                },
            },
            "required": ["filename", "content"],
        }

    async def execute(self, filename: str = "", content: str = "", **kwargs) -> ToolResult:
        if not filename or not content:
            return ToolResult(success=False, error="filename and content are required")

        # Sanitize filename
        safe_name = filename.replace("/", "_").replace("\\", "_").replace("..", "_")

        # Only allow safe extensions
        allowed_ext = {".txt", ".md", ".html", ".csv", ".json", ".xml", ".svg"}
        ext = os.path.splitext(safe_name)[1].lower()
        if ext not in allowed_ext:
            # Default to HTML for unknown extensions
            safe_name = safe_name.rsplit(".", 1)[0] + ".html"

        file_path = self._output_dir / safe_name
        try:
            file_path.write_text(content, encoding="utf-8")
            size = file_path.stat().st_size
            logger.info("Generated file: %s (%d bytes)", safe_name, size)
            return ToolResult(
                output=f"File generated: [{safe_name}](/api/files/{safe_name}) ({size} bytes). "
                       f"The user can download it from the link.",
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to write file: {e}")
