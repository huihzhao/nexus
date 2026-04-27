"""SkillManager — install, load, and manage external skills.

Compatible with Binance Skills Hub format:
  - SKILL.md with YAML frontmatter + markdown instructions
  - Optional reference files (references/*.md)
  - Optional .local.md for user-specific config (not distributed)

Skills are installed to a local directory and loaded into the LLM system prompt.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class InstalledSkill:
    """Metadata for an installed skill."""
    name: str
    title: str
    description: str
    version: str
    author: str
    path: Path                          # Local directory
    instructions: str                   # Full SKILL.md content (after frontmatter)
    references: dict[str, str] = field(default_factory=dict)  # filename -> content
    metadata: dict[str, Any] = field(default_factory=dict)


class SkillManager:
    """Manages skill installation, loading, and prompt injection.

    Skills are stored in `{base_dir}/skills/{skill_name}/` and loaded on startup.
    The LLM sees skill instructions as part of its system prompt.
    """

    def __init__(self, base_dir: str | Path = ".nexus"):
        self._base_dir = Path(base_dir)
        self._skills_dir = self._base_dir / "skills"
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        self._skills: dict[str, InstalledSkill] = {}

        # Auto-load existing installed skills
        self._load_all()

    def _load_all(self) -> None:
        """Load all skills from the skills directory."""
        if not self._skills_dir.exists():
            return
        for skill_dir in sorted(self._skills_dir.iterdir()):
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
                try:
                    skill = self._load_skill(skill_dir)
                    self._skills[skill.name] = skill
                    logger.info("Loaded skill: %s (%s)", skill.name, skill.title)
                except Exception as e:
                    logger.warning("Failed to load skill from %s: %s", skill_dir, e)

    def _load_skill(self, skill_dir: Path) -> InstalledSkill:
        """Parse a SKILL.md file and load the skill."""
        skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

        # Parse YAML frontmatter
        frontmatter, body = _parse_frontmatter(skill_md)

        name = skill_dir.name
        title = frontmatter.get("title", name)
        description = frontmatter.get("description", "")
        metadata = frontmatter.get("metadata", {})
        version = metadata.get("version", "0.0.0") if isinstance(metadata, dict) else "0.0.0"
        author = metadata.get("author", "") if isinstance(metadata, dict) else ""

        # Load reference files
        references = {}
        refs_dir = skill_dir / "references"
        if refs_dir.exists():
            for ref_file in refs_dir.glob("*.md"):
                references[ref_file.name] = ref_file.read_text(encoding="utf-8")

        # Load .local.md if exists (user-specific config)
        local_md = skill_dir / ".local.md"
        local_content = ""
        if local_md.exists():
            local_content = local_md.read_text(encoding="utf-8")

        # Combine instructions: main body + local overrides
        instructions = body.strip()
        if local_content:
            instructions += f"\n\n## User Configuration\n{local_content}"

        return InstalledSkill(
            name=name,
            title=title,
            description=description,
            version=str(version),
            author=str(author),
            path=skill_dir,
            instructions=instructions,
            references=references,
            metadata=frontmatter,
        )

    async def install(self, source: str) -> InstalledSkill:
        """Install a skill from GitHub URL, LobeHub marketplace, or identifier.

        Supports:
          - GitHub tree URL: https://github.com/org/repo/tree/main/skills/...
          - LobeHub identifier: lobehub:<identifier> or just <identifier>
          - GitHub raw folder: org/repo/skills/category/skill-name

        Args:
            source: GitHub URL, LobeHub identifier, or skill name

        Returns:
            The installed skill.
        """
        # GitHub URL
        if "github.com" in source:
            return await self._install_from_github(source)

        # Explicit LobeHub prefix
        if source.startswith("lobehub:"):
            identifier = source[8:]
            return await self._install_from_lobehub(identifier)

        # GitHub-style path (org/repo/...)
        if "/" in source and not source.startswith("/"):
            return await self._install_from_github(f"https://github.com/{source}")

        # Default: try LobeHub marketplace by identifier
        return await self._install_from_lobehub(source)

    async def search_lobehub(self, query: str, limit: int = 10) -> list[dict]:
        """Search LobeHub Skills Marketplace.

        Args:
            query: Search keyword (e.g., "pdf editor", "wallet", "deploy")
            limit: Max results to return

        Returns:
            List of skill info dicts: [{identifier, name, description, installs, stars}]
        """
        import subprocess
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["npx", "-y", "@lobehub/market-cli", "skills", "search",
                 "--q", query, "--page-size", str(limit), "--output", "json"],
                capture_output=True, text=True, timeout=30,
            )
            if result.stdout.strip():
                import json
                data = json.loads(result.stdout.strip())
                items = data.get("items", [])
                return [
                    {
                        "identifier": item.get("identifier", ""),
                        "name": item.get("name", ""),
                        "description": item.get("description", "")[:100],
                        "installs": item.get("installCount", 0),
                        "stars": item.get("github", {}).get("stars", 0),
                        "author": item.get("author", ""),
                    }
                    for item in items
                ]
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            logger.warning("LobeHub search failed: %s", e)
        return []

    async def _install_from_lobehub(self, identifier: str) -> InstalledSkill:
        """Install a skill from LobeHub marketplace via CLI."""
        import subprocess

        dest = str(self._skills_dir / identifier)
        logger.info("Installing skill '%s' from LobeHub marketplace...", identifier)

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["npx", "-y", "@lobehub/market-cli", "skills", "install",
                 identifier, "--dir", str(self._skills_dir)],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                error = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(f"LobeHub install failed: {error[:200]}")

            logger.info("LobeHub install output: %s", result.stdout.strip()[:200])

        except subprocess.TimeoutExpired:
            raise RuntimeError("LobeHub install timed out (60s)")
        except FileNotFoundError:
            raise RuntimeError("npx not found — install Node.js to use LobeHub marketplace")

        # Load the installed skill
        skill_dir = self._skills_dir / identifier
        if not (skill_dir / "SKILL.md").exists():
            # Try without nested directory
            for d in self._skills_dir.iterdir():
                if d.is_dir() and (d / "SKILL.md").exists() and identifier in d.name:
                    skill_dir = d
                    break
            else:
                raise RuntimeError(f"Skill installed but SKILL.md not found in {skill_dir}")

        skill = self._load_skill(skill_dir)
        self._skills[skill.name] = skill
        logger.info("Installed LobeHub skill: %s (%s)", skill.name, skill.title)
        return skill

    async def _install_from_github(self, url: str) -> InstalledSkill:
        """Download a skill folder from GitHub."""
        # Parse URL: https://github.com/org/repo/tree/branch/path/to/skill
        match = re.match(
            r"https?://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.+)",
            url,
        )
        if not match:
            # Try without /tree/branch/
            match = re.match(
                r"https?://github\.com/([^/]+)/([^/]+)/?(.*)$",
                url,
            )
            if not match:
                raise ValueError(f"Cannot parse GitHub URL: {url}")
            org, repo, path = match.group(1), match.group(2), match.group(3)
            branch = "main"
        else:
            org, repo, branch, path = match.groups()

        # Derive skill name from path
        skill_name = path.rstrip("/").split("/")[-1]
        dest = self._skills_dir / skill_name

        # Clone just the skill directory using git sparse-checkout
        logger.info("Installing skill '%s' from %s/%s...", skill_name, org, repo)

        # Use degit-style: download the folder via GitHub API
        raw_base = f"https://raw.githubusercontent.com/{org}/{repo}/{branch}/{path}"

        # Download SKILL.md first
        dest.mkdir(parents=True, exist_ok=True)
        await self._download_file(f"{raw_base}/SKILL.md", dest / "SKILL.md")

        # Try to download common reference files
        refs_dir = dest / "references"
        refs_dir.mkdir(exist_ok=True)

        # Fetch directory listing via GitHub API to find reference files
        api_url = f"https://api.github.com/repos/{org}/{repo}/contents/{path}/references?ref={branch}"
        try:
            result = await asyncio.to_thread(
                self._http_get_json, api_url
            )
            if isinstance(result, list):
                for item in result:
                    if item.get("name", "").endswith(".md"):
                        await self._download_file(
                            item["download_url"],
                            refs_dir / item["name"],
                        )
        except Exception as e:
            logger.debug("No references directory or API error: %s", e)

        # Load the installed skill
        skill = self._load_skill(dest)
        self._skills[skill.name] = skill
        logger.info("Installed skill: %s (%s) — %d reference files",
                     skill.name, skill.title, len(skill.references))
        return skill

    async def _download_file(self, url: str, dest: Path) -> None:
        """Download a file from URL to local path."""
        import urllib.request
        await asyncio.to_thread(urllib.request.urlretrieve, url, str(dest))

    def _http_get_json(self, url: str) -> Any:
        """Simple HTTP GET returning JSON."""
        import urllib.request
        import json
        req = urllib.request.Request(url, headers={"User-Agent": "rune-nexus/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())

    def install_local(self, path: str | Path) -> InstalledSkill:
        """Install a skill from a local directory.

        Copies the skill folder to the skills directory.
        """
        src = Path(path)
        if not (src / "SKILL.md").exists():
            raise FileNotFoundError(f"No SKILL.md found in {src}")

        skill_name = src.name
        dest = self._skills_dir / skill_name

        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)

        skill = self._load_skill(dest)
        self._skills[skill.name] = skill
        logger.info("Installed local skill: %s (%s)", skill.name, skill.title)
        return skill

    def uninstall(self, name: str) -> bool:
        """Remove an installed skill."""
        skill = self._skills.pop(name, None)
        if skill and skill.path.exists():
            shutil.rmtree(skill.path)
            logger.info("Uninstalled skill: %s", name)
            return True
        return False

    def get(self, name: str) -> Optional[InstalledSkill]:
        """Get an installed skill by name."""
        return self._skills.get(name)

    @property
    def installed(self) -> list[InstalledSkill]:
        """List all installed skills."""
        return list(self._skills.values())

    @property
    def names(self) -> list[str]:
        """List installed skill names."""
        return list(self._skills.keys())

    def get_prompt_context(self) -> str:
        """Generate the skill context block for LLM system prompt.

        Returns a formatted string containing all skill instructions,
        ready to be appended to the system prompt.
        """
        if not self._skills:
            return ""

        # Hermes-style: only inject skill INDEX (name + description),
        # not full instructions. Agent loads full skill via tool when needed.
        parts = ["\n\n## Installed Skills"]
        parts.append("Before replying, scan these skills. If one matches the user's request, "
                      "use the skill's name as a reference. Full instructions are loaded on demand.\n")
        for skill in self._skills.values():
            desc = skill.description[:80] + "..." if len(skill.description) > 80 else skill.description
            refs = f" (refs: {', '.join(skill.references.keys())})" if skill.references else ""
            parts.append(f"- **{skill.name}**: {desc}{refs}")

        parts.append("")

        return "\n".join(parts)

    # ── LobeHub MCP Marketplace ──

    async def search_mcp(self, query: str, limit: int = 10) -> list[dict]:
        """Search LobeHub MCP Servers Marketplace.

        Args:
            query: Search keyword (e.g., "postgres", "playwright", "finance")
            limit: Max results

        Returns:
            List of MCP server info: [{identifier, name, description, tools_count}]
        """
        import subprocess
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["npx", "-y", "@lobehub/market-cli", "mcp", "search",
                 "--q", query, "--page-size", str(limit), "--output", "json"],
                capture_output=True, text=True, timeout=30,
            )
            if result.stdout.strip():
                import json
                data = json.loads(result.stdout.strip())
                items = data.get("items", [])
                return [
                    {
                        "identifier": item.get("identifier", ""),
                        "name": item.get("name", ""),
                        "description": item.get("description", "")[:100],
                        "author": item.get("author", ""),
                        "tools_count": item.get("toolsCount", 0),
                        "category": item.get("category", ""),
                    }
                    for item in items
                ]
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            logger.warning("LobeHub MCP search failed: %s", e)
        return []

    async def install_mcp(self, identifier: str, tool_registry=None) -> dict:
        """Install an MCP server from LobeHub marketplace and register its tools.

        Downloads the MCP server config and connects it via MCPClient.
        Tools are automatically registered in the provided ToolRegistry.

        Args:
            identifier: LobeHub MCP server identifier
            tool_registry: Optional ToolRegistry to register tools into

        Returns:
            {"name": ..., "tools": [...tool_names...]}
        """
        import subprocess

        logger.info("Installing MCP server '%s' from LobeHub...", identifier)

        # Get MCP server details
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["npx", "-y", "@lobehub/market-cli", "mcp", "info",
                 identifier, "--output", "json"],
                capture_output=True, text=True, timeout=30,
            )
            if result.stdout.strip():
                import json
                info = json.loads(result.stdout.strip())

                # Extract connection config
                name = info.get("name", identifier)
                command = info.get("command", "")
                args = info.get("args", [])

                if command and tool_registry:
                    # Connect via MCPClient
                    from ..mcp import MCPServerConfig
                    config = MCPServerConfig(
                        name=name,
                        transport="stdio",
                        command=command,
                        args=args,
                    )
                    tool_names = await tool_registry.register_mcp_server(config)
                    logger.info("MCP server '%s' installed: %d tools", name, len(tool_names))
                    return {"name": name, "tools": tool_names}

                return {"name": name, "tools": [], "note": "No tool_registry provided"}

        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            logger.warning("MCP install failed: %s", e)
            return {"name": identifier, "tools": [], "error": str(e)}


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a markdown file.

    Returns (frontmatter_dict, body_text).
    Simple parser — no PyYAML dependency.
    """
    if not text.startswith("---"):
        return {}, text

    # Find closing ---
    end = text.find("---", 3)
    if end < 0:
        return {}, text

    yaml_block = text[3:end].strip()
    body = text[end + 3:].strip()

    # Simple YAML parser (handles key: value and nested metadata:)
    frontmatter: dict[str, Any] = {}
    current_dict = frontmatter
    current_key = None

    for line in yaml_block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()

            if indent > 0 and current_key and isinstance(frontmatter.get(current_key), dict):
                # Nested value
                frontmatter[current_key][key] = value
            elif value:
                frontmatter[key] = value
                current_key = key
            else:
                # Key with no value — start nested dict
                frontmatter[key] = {}
                current_key = key

    return frontmatter, body
