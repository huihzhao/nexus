"""SkillManager — install, load, and manage external skills.

Compatible with Binance Skills Hub format:
  - SKILL.md with YAML frontmatter + markdown instructions
  - Optional reference files (references/*.md)
  - Optional .local.md for user-specific config (not distributed)

Skills are installed to a local directory and loaded into the LLM system prompt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote as urllib_quote

logger = logging.getLogger(__name__)


# Process-level latch so we attempt the Node bootstrap at most once
# per session. Repeated calls just await the cached result — even when
# it failed, we don't retry within the same process (the user would
# need to install Node manually + restart twin).
_node_bootstrap_state: dict[str, Any] = {
    "checked": False,
    "available": False,
    "method": "",  # "preinstalled" | "brew" | "failed"
    "error": "",
}
_node_bootstrap_lock = asyncio.Lock()


async def _ensure_node_available() -> bool:
    """Make sure ``npx`` is on PATH; try to install Node if it's not.

    Returns True if npx is callable after this function returns. The
    LobeHub paths call this as a preflight so the agent can self-heal
    from a missing Node install instead of repeatedly bouncing back
    "I need npx" errors at the user.

    Auto-install logic (best-effort, OS-aware):
      * macOS  — try ``brew install node`` if Homebrew is on PATH.
      * Linux  — leave it. Distro variance (apt/yum/pacman) plus sudo
        prompts make automatic install too risky to attempt headlessly.
        We surface a clean error instead.
      * Windows — same as Linux: surface a clean error.

    Idempotent within a process: caches the outcome in
    :data:`_node_bootstrap_state`. Subsequent calls return the cached
    answer instantly.
    """
    async with _node_bootstrap_lock:
        if _node_bootstrap_state["checked"]:
            return bool(_node_bootstrap_state["available"])

        # Fast path: already installed.
        if shutil.which("npx") is not None:
            _node_bootstrap_state.update(
                checked=True, available=True, method="preinstalled",
            )
            return True

        sysname = platform.system()
        if sysname == "Darwin":
            brew = shutil.which("brew")
            if brew is None:
                _node_bootstrap_state.update(
                    checked=True, available=False, method="failed",
                    error="Homebrew not installed — can't auto-install Node. "
                          "Install Homebrew (https://brew.sh) or Node directly "
                          "(https://nodejs.org/).",
                )
                logger.warning("Node bootstrap: brew missing on macOS")
                return False

            logger.info("Auto-installing Node.js via Homebrew (one-time)…")
            try:
                # ``brew install node`` is interactive on first prompt
                # but with no tty it just proceeds. Cap at 5 minutes
                # — typical install is 30-90s, slow networks may push
                # past that and we'd rather surface a timeout than
                # appear hung.
                proc = await asyncio.to_thread(
                    subprocess.run,
                    [brew, "install", "node"],
                    capture_output=True, text=True, timeout=300,
                )
                if proc.returncode != 0:
                    err = (proc.stderr or proc.stdout or "")[:300]
                    _node_bootstrap_state.update(
                        checked=True, available=False, method="failed",
                        error=f"brew install node failed: {err}",
                    )
                    logger.warning(
                        "Node bootstrap via brew failed: %s", err,
                    )
                    return False
            except subprocess.TimeoutExpired:
                _node_bootstrap_state.update(
                    checked=True, available=False, method="failed",
                    error="brew install node timed out (5min). Network may be slow — retry manually.",
                )
                return False
            except Exception as e:
                _node_bootstrap_state.update(
                    checked=True, available=False, method="failed",
                    error=f"brew install node raised: {e}",
                )
                return False

            # Re-check PATH after install. Homebrew sometimes installs
            # to /opt/homebrew/bin which the parent process's PATH
            # already includes, but not always — the next subprocess
            # spawn will pick it up either way.
            if shutil.which("npx") is None:
                _node_bootstrap_state.update(
                    checked=True, available=False, method="failed",
                    error="brew finished but npx still not on PATH — restart the server to pick it up.",
                )
                return False

            _node_bootstrap_state.update(
                checked=True, available=True, method="brew",
            )
            logger.info("Node.js installed successfully via Homebrew")
            return True

        # Non-mac: don't try to apt/yum/choco — too varied + needs
        # sudo. Surface a helpful message and let the LLM relay it.
        _node_bootstrap_state.update(
            checked=True, available=False, method="failed",
            error=(
                f"Node.js (npx) not found on this {sysname} system. "
                "Install it from https://nodejs.org/ then retry — "
                "auto-install on this OS is not supported."
            ),
        )
        return False


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
        """Install a skill from the Anthropic skills hub or a GitHub URL.

        Supported identifier shapes (in priority order):
          - Full GitHub tree URL:
              https://github.com/anthropics/skills/tree/main/document-skills/pdf
          - 'anthropic:<name>' shortcut → rewritten to the canonical
            anthropics/skills tree URL.
          - Bare skill name ('pdf', 'docx', ...) → assumed to be an
            Anthropic skill. The GitHub installer searches the repo
            for a directory matching the name.
          - GitHub-style path 'org/repo/...' → treated as a GitHub URL.

        Earlier revs also supported lobehub: and gemini: prefixes; both
        marketplaces were dropped (LobeHub requires creds; Gemini's repo
        layout drifted from the SKILL.md convention). The Anthropic
        path is the only fully-automatable, no-auth flow we ship.

        Args:
            source: GitHub URL, anthropic: shortcut, or bare skill name.

        Returns:
            The installed skill.
        """
        # Full GitHub URL — pass straight through
        if "github.com" in source:
            return await self._install_from_github(source)

        # Anthropic official skills repo shortcut. anthropics/skills
        # hosts pdf, docx, xlsx, pptx, mcp-builder, skill-creator and
        # friends — the canonical reference set.
        if source.startswith("anthropic:"):
            name = source[len("anthropic:"):]
            return await self._install_from_github(
                f"https://github.com/anthropics/skills/tree/main/skills/{name}"
            )

        # GitHub-style path (org/repo/...)
        if "/" in source and not source.startswith("/"):
            return await self._install_from_github(f"https://github.com/{source}")

        # Default: bare skill name → try Anthropic skills hub.
        # _install_from_github will walk the repo and surface a clean
        # error if no matching skill is found.
        return await self._install_from_github(
            f"https://github.com/anthropics/skills/tree/main/skills/{source}"
        )

    async def search_anthropic_official(
        self, query: str, limit: int = 10,
    ) -> list[dict]:
        """Search Anthropic's official Skills repo (anthropics/skills).

        Same shape as :meth:`search_gemini_official` — lists the
        ``/skills`` folder via GitHub's contents API, pulls each
        SKILL.md frontmatter for name + description, returns rows
        prefixed ``anthropic:<name>`` so the install path knows to
        rewrite into a tree URL.

        We hard-code this as a built-in source (rather than relying on
        the ``claude-skills`` GitHub topic) because the canonical
        Anthropic repo doesn't have that topic tag set — yet it's the
        single highest-quality skill catalog out there. Hard-coding
        guarantees PDF / docx / pptx / xlsx etc always surface from
        the canonical source even if topic-search misses them.
        """
        # In-process cache, same pattern as the Gemini search.
        if not hasattr(self, "_anthropic_listing_cache"):
            self._anthropic_listing_cache = None
        if self._anthropic_listing_cache is None:
            try:
                api_url = (
                    "https://api.github.com/repos/anthropics/skills"
                    "/contents/skills?ref=main"
                )
                listing = await asyncio.to_thread(self._http_get_json, api_url)
            except Exception as e:
                logger.warning("Anthropic skills listing failed: %s", e)
                return []
            if not isinstance(listing, list):
                return []

            async def _meta(item):
                if item.get("type") != "dir":
                    return None
                name = item.get("name", "")
                if not name:
                    return None
                raw = (
                    f"https://raw.githubusercontent.com/anthropics/"
                    f"skills/main/skills/{name}/SKILL.md"
                )
                title = name
                description = ""
                try:
                    text = await asyncio.to_thread(self._http_get_text, raw)
                    fm = self._parse_frontmatter(text)
                    title = fm.get("name") or fm.get("title") or name
                    description = (
                        fm.get("description") or fm.get("summary") or ""
                    )
                except Exception:
                    pass
                return {
                    "identifier": f"anthropic:{name}",
                    "name": str(title),
                    "description": str(description)[:200],
                    "source": "anthropic",
                    "url": (
                        f"https://github.com/anthropics/skills/"
                        f"tree/main/skills/{name}"
                    ),
                }

            metas = await asyncio.gather(*[_meta(it) for it in listing])
            self._anthropic_listing_cache = [m for m in metas if m]

        if not query:
            return self._anthropic_listing_cache[:limit]

        q = query.lower()
        scored: list[tuple[int, dict]] = []
        for row in self._anthropic_listing_cache:
            haystack = (
                row["name"].lower() + " " + row["description"].lower()
            )
            score = haystack.count(q)
            if score > 0 or q in row["identifier"].lower():
                scored.append((score, row))
        scored.sort(key=lambda x: -x[0])
        return [r for _, r in scored[:limit]]

    async def search_gemini_official(
        self, query: str, limit: int = 10,
    ) -> list[dict]:
        """Search Google's official Gemini Skills repo (google-gemini/gemini-skills).

        Lists the ``/skills`` folder via GitHub's contents API, then for each
        directory pulls the SKILL.md frontmatter (name + description) so we
        can match against the user's query. Cached on the instance for the
        process lifetime — the repo is small (~dozens of skills) and the
        listing changes slowly, so a one-shot fetch is fine.

        Returns rows shaped like ``search_lobehub`` — same keys, ``identifier``
        prefixed with ``gemini:`` so the install side can route correctly.
        """
        # Cheap in-process cache: the listing rarely changes within one
        # conversation. Repeated searches in a chat don't re-hit GitHub.
        if not hasattr(self, "_gemini_listing_cache"):
            self._gemini_listing_cache = None
        if self._gemini_listing_cache is None:
            try:
                api_url = (
                    "https://api.github.com/repos/google-gemini/gemini-skills"
                    "/contents/skills?ref=main"
                )
                listing = await asyncio.to_thread(self._http_get_json, api_url)
            except Exception as e:
                logger.warning("Gemini skills listing failed: %s", e)
                return []
            if not isinstance(listing, list):
                logger.warning("Unexpected Gemini skills listing shape")
                return []

            # Pull each SKILL.md frontmatter so search has something to
            # match against beyond the directory name. We do this in
            # parallel-ish via to_thread to avoid serialising the round
            # trips. ``asyncio.gather`` keeps it bounded by the listing
            # size (no separate concurrency cap needed for ~dozens).
            async def _meta(item):
                if item.get("type") != "dir":
                    return None
                name = item.get("name", "")
                if not name:
                    return None
                raw = (
                    f"https://raw.githubusercontent.com/google-gemini/"
                    f"gemini-skills/main/skills/{name}/SKILL.md"
                )
                title = name
                description = ""
                try:
                    text = await asyncio.to_thread(self._http_get_text, raw)
                    fm = self._parse_frontmatter(text)
                    title = fm.get("name") or fm.get("title") or name
                    description = (
                        fm.get("description") or fm.get("summary") or ""
                    )
                except Exception:
                    pass  # missing SKILL.md → still keep the dir, just no desc
                return {
                    "identifier": f"gemini:{name}",
                    "name": str(title),
                    "description": str(description)[:200],
                    "source": "gemini",
                    "url": (
                        f"https://github.com/google-gemini/gemini-skills/"
                        f"tree/main/skills/{name}"
                    ),
                }

            metas = await asyncio.gather(*[_meta(it) for it in listing])
            self._gemini_listing_cache = [m for m in metas if m]

        if not query:
            return self._gemini_listing_cache[:limit]

        # Naive substring match on name + description (case-insensitive).
        # Good enough for ~dozens of skills; if the official repo grows
        # past a few hundred entries, swap in BM25 / embedding rank.
        q = query.lower()
        scored: list[tuple[int, dict]] = []
        for row in self._gemini_listing_cache:
            haystack = (
                row["name"].lower() + " " + row["description"].lower()
            )
            score = haystack.count(q)
            if score > 0 or q in row["identifier"].lower():
                scored.append((score, row))
        scored.sort(key=lambda x: -x[0])
        return [r for _, r in scored[:limit]]

    @staticmethod
    def _http_get_text(url: str) -> str:
        """HTTP GET returning text, with the User-Agent GitHub requires."""
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "rune-nexus/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode("utf-8", errors="replace")

    async def search_github_topic(
        self, query: str, limit: int = 10,
    ) -> list[dict]:
        """Search every public GitHub repo tagged ``claude-skills`` for
        ``query`` matches.

        This is the 3rd skill marketplace alongside LobeHub (community
        catalog via npx CLI) and ``google-gemini/gemini-skills`` (Google's
        official curated repo). The ``claude-skills`` topic is the de
        facto convention third-party skill authors apply to their
        repos — querying it brings in dozens of skills neither of the
        first two sources index.

        Implementation: GitHub Search API filters via ``topic:`` qualifier.
        Anonymous calls are rate-limited to 10/min, plenty for chat-driven
        searches. Results are projected to the same ``identifier`` /
        ``name`` / ``description`` shape as the other two sources so the
        caller can interleave them transparently. Identifier prefix is
        the raw ``https://github.com/...`` URL — :meth:`install` already
        routes those through ``_install_from_github``.
        """
        if not query.strip():
            return []
        # GitHub Search API: q=topic:claude-skills+<query>
        # Sort by stars to surface the most-maintained repos first.
        encoded = urllib_quote(f"topic:claude-skills {query}")
        url = (
            f"https://api.github.com/search/repositories"
            f"?q={encoded}&sort=stars&order=desc&per_page={limit}"
        )
        try:
            data = await asyncio.to_thread(self._http_get_json, url)
        except Exception as e:
            logger.debug("GitHub topic search failed: %s", e)
            return []
        if not isinstance(data, dict):
            return []
        out: list[dict] = []
        for item in (data.get("items") or [])[:limit]:
            html_url = item.get("html_url", "")
            full_name = item.get("full_name", "")
            description = (item.get("description") or "")[:200]
            stars = item.get("stargazers_count", 0)
            if not html_url:
                continue
            out.append({
                "identifier": html_url,
                "name": item.get("name") or full_name,
                "description": description,
                "source": "github-topic",
                "stars": stars,
                "url": html_url,
            })
        return out

    @staticmethod
    def _parse_frontmatter(markdown_text: str) -> dict:
        """Extract YAML frontmatter from a SKILL.md file (best-effort).

        Returns an empty dict when the file has no frontmatter or YAML fails
        to parse — search still gets a row, just without title/description.
        """
        if not markdown_text.startswith("---"):
            return {}
        # Frontmatter spans from line 1 to the next "---" line.
        try:
            _, fm, _ = markdown_text.split("---", 2)
        except ValueError:
            return {}
        try:
            import yaml
            data = yaml.safe_load(fm)
            return data if isinstance(data, dict) else {}
        except Exception:
            # No yaml installed or invalid YAML — pull a couple of common
            # keys via regex as a last-ditch effort. Keeps search useful
            # even when yaml isn't available in the runtime.
            out: dict = {}
            for line in fm.splitlines():
                if ":" in line:
                    key, _, val = line.partition(":")
                    out[key.strip()] = val.strip().strip("\"'")
            return out

    # Process-level cache for LobeHub availability. The CLI requires
    # `MARKET_CLIENT_ID` + `MARKET_CLIENT_SECRET` env vars (or a prior
    # `lhm register`). Without credentials EVERY search returns
    # "No credentials found" to stderr, exits non-zero, and we waste
    # a 30s subprocess timeout per search request — across N synonym
    # variants that's minutes of dead time per agent action. We probe
    # once, cache the answer, and fast-skip subsequent calls.
    _lobehub_credentials_state: str = ""  # "" | "ok" | "missing"

    async def search_lobehub(self, query: str, limit: int = 10) -> list[dict]:
        """Search LobeHub Skills Marketplace.

        Returns [] silently if LobeHub credentials aren't configured —
        the CLI is auth-only as of @lobehub/market-cli 0.0.28. Set
        MARKET_CLIENT_ID + MARKET_CLIENT_SECRET in the agent env to
        enable.
        """
        import subprocess

        # Fast-path: we already know creds are missing → skip silently.
        if SkillManager._lobehub_credentials_state == "missing":
            return []

        # Preflight: try to install Node if missing. Best-effort — on
        # failure we just return [] (caller treats it as no results).
        await _ensure_node_available()
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["npx", "-y", "@lobehub/market-cli", "skills", "search",
                 "--q", query, "--page-size", str(limit), "--output", "json"],
                capture_output=True, text=True, timeout=30,
            )
            # Detect the auth-required failure mode and disable for the
            # rest of the process. The CLI prints this exact phrase to
            # stdout / stderr depending on version.
            combined = (result.stdout or "") + "\n" + (result.stderr or "")
            if "No credentials found" in combined:
                if SkillManager._lobehub_credentials_state != "missing":
                    logger.warning(
                        "LobeHub backend disabled — `lhm` CLI requires "
                        "MARKET_CLIENT_ID / MARKET_CLIENT_SECRET env vars "
                        "or `lhm register`. Falling back to anthropic / "
                        "gemini / GitHub topic sources only. Set those "
                        "env vars to re-enable."
                    )
                SkillManager._lobehub_credentials_state = "missing"
                return []

            if result.stdout.strip():
                import json
                data = json.loads(result.stdout.strip())
                items = data.get("items", [])
                SkillManager._lobehub_credentials_state = "ok"
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

        # Preflight: make sure ``npx`` exists. If it doesn't, try to
        # install Node ourselves so the agent can keep going instead of
        # bouncing the work back to the user. Best-effort — if we
        # can't install Node (no brew, no admin, …) we surface a clean
        # error instead of hanging.
        await _ensure_node_available()

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
            raise RuntimeError(
                "npx not found and auto-install of Node.js failed. "
                "Please install Node.js from https://nodejs.org/ or via Homebrew "
                "(`brew install node`)."
            )

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

        # ── Multi-skill repo handling (Phase Q follow-up) ────────────
        # When the URL points at the repo root (path is empty) — common
        # for github-topic search hits — we need to figure out where
        # the SKILL.md actually lives. Three conventions:
        #   * Single-skill repo: SKILL.md at root.
        #   * Multi-skill repo:  skills/<name>/SKILL.md (Anthropic +
        #     Gemini convention) — pick the only one if there's just
        #     one, else surface a clear error listing the choices so
        #     the LLM can re-ask the user.
        #   * Other layouts: error out, ask for an explicit path.
        if path.rstrip("/") == "":
            # Probe root for SKILL.md first.
            root_check = (
                f"https://raw.githubusercontent.com/{org}/{repo}/{branch}/SKILL.md"
            )
            try:
                # HEAD via urlopen — cheaper than GET if the file exists.
                await asyncio.to_thread(self._http_get_text, root_check)
                # Found at root — keep path="" so the existing logic
                # downloads from {raw_base}/SKILL.md.
            except Exception:
                # Not at root — list /skills/ via the contents API.
                listing_url = (
                    f"https://api.github.com/repos/{org}/{repo}"
                    f"/contents/skills?ref={branch}"
                )
                try:
                    listing = await asyncio.to_thread(
                        self._http_get_json, listing_url,
                    )
                except Exception as e:
                    raise RuntimeError(
                        f"This URL points to a multi-skill repo "
                        f"({org}/{repo}) but I can't list its skills/ "
                        f"folder ({e}). Please pass a more specific URL "
                        f"like https://github.com/{org}/{repo}/tree/{branch}/skills/<name>."
                    )
                names = (
                    [it.get("name", "") for it in listing if it.get("type") == "dir"]
                    if isinstance(listing, list) else []
                )
                if not names:
                    raise RuntimeError(
                        f"{org}/{repo} has no SKILL.md at root and no "
                        f"skills/ subfolder. Not a recognised skill repo "
                        f"layout — please verify the URL."
                    )
                if len(names) == 1:
                    path = f"skills/{names[0]}"
                    logger.info(
                        "Multi-skill repo %s/%s has one skill: %s",
                        org, repo, names[0],
                    )
                else:
                    raise RuntimeError(
                        f"{org}/{repo} is a multi-skill repo with "
                        f"{len(names)} skills: {', '.join(names[:10])}"
                        f"{', …' if len(names) > 10 else ''}. "
                        f"Pick one and pass identifier="
                        f"'https://github.com/{org}/{repo}/tree/{branch}/"
                        f"skills/<name>'."
                    )

        # Derive skill name from path
        skill_name = path.rstrip("/").split("/")[-1]
        if not skill_name:
            raise RuntimeError(
                f"Could not derive a skill name from URL {url}. "
                f"Pass a URL ending in the skill folder name."
            )
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

    async def _download_file(self, url: str, dest: Path, timeout: float = 15.0) -> None:
        """Download a file from URL to local path with a hard timeout.

        ``urllib.request.urlretrieve`` does NOT accept a timeout — if the
        remote is slow or the file is missing in a way that hangs the
        connection, the call blocks forever. That bug took down the
        whole "install skill" flow: the LLM's tool call never returned,
        the desktop sat on "Agent is thinking…" indefinitely, and the
        user couldn't even send a new message to escape.
        We use ``urlopen`` (which DOES accept timeout) + a streamed
        copy to disk instead, so any single download is bounded.
        """
        import urllib.request
        import shutil

        def _do_download() -> None:
            req = urllib.request.Request(
                url, headers={"User-Agent": "rune-nexus/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp, \
                 open(dest, "wb") as out:
                shutil.copyfileobj(resp, out)

        await asyncio.to_thread(_do_download)

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

    # ── Curated MCP catalog (#159 续) ────────────────────────────────
    #
    # Hand-vetted shortlist baked into the SDK so search_mcp doesn't
    # depend on LobeHub auth (the lhm CLI requires MARKET_CLIENT_ID,
    # which 99% of self-hosted deployments don't have). Loaded once,
    # cached process-wide. See curated_mcp.json for the schema.

    _curated_cache: Optional[list[dict]] = None

    @classmethod
    def _curated_catalog(cls) -> list[dict]:
        if cls._curated_cache is not None:
            return cls._curated_cache
        catalog_path = Path(__file__).parent / "curated_mcp.json"
        try:
            with open(catalog_path, "r") as f:
                data = json.load(f)
            cls._curated_cache = data.get("items", [])
        except Exception as e:  # noqa: BLE001
            logger.warning("curated_mcp.json load failed: %s", e)
            cls._curated_cache = []
        return cls._curated_cache

    @staticmethod
    def _match_curated(items: list[dict], query: str) -> list[dict]:
        """Substring-match the query against name + description + keywords."""
        q = query.lower().strip()
        if not q:
            return []
        out: list[tuple[int, dict]] = []
        for it in items:
            score = 0
            if q in (it.get("name", "") or "").lower():
                score += 5
            for kw in it.get("keywords", []):
                if q in kw.lower() or kw.lower() in q:
                    score += 3
            if q in (it.get("description", "") or "").lower():
                score += 1
            if q in (it.get("category", "") or "").lower():
                score += 1
            if score > 0:
                out.append((score, it))
        out.sort(key=lambda r: -r[0])
        return [it for _, it in out]

    async def search_mcp(self, query: str, limit: int = 10) -> list[dict]:
        """Search the curated MCP catalog.

        Reads ``curated_mcp.json`` (hand-vetted, ~30 servers covering
        the common cases — chains, SaaS, dev tools, databases). Zero
        auth, zero network — ideal for the agent to surface vetted
        options without hitting external services.

        Earlier revs also tried LobeHub and Smithery as supplementary
        backends. Both ended up not earning their keep:
          * LobeHub (@lobehub/market-cli) silently requires
            MARKET_CLIENT_ID/_SECRET — no creds = 30 s subprocess per
            query for zero results.
          * Smithery search is fully public and finds ~3000 servers,
            but every hosted entry needs OAuth to actually install
            (out of scope for a server-side agent). Surfaced 3000
            results that the agent then couldn't act on.
        Both removed; if/when we want the long tail back, do it via
        a dedicated tool with a clear UX rather than a silent layer.

        Returns: list of {identifier, name, description, tools_count, source}.
        Empty list = nothing matched in the catalog — caller can
        legitimately say "nothing found, fall back to web_search".
        """
        results: list[dict] = []
        for it in self._match_curated(self._curated_catalog(), query)[:limit]:
            results.append({
                "identifier":  it.get("identifier", ""),
                "name":        it.get("name", ""),
                "description": (it.get("description") or "")[:140],
                "author":      it.get("trust", "curated"),
                "tools_count": it.get("tools_count", 0),
                "category":    it.get("category", ""),
                "source":      "curated",
            })
        return results

    async def install_mcp(self, identifier: str, tool_registry=None) -> dict:
        """Install an MCP server and register its tools.

        Identifier formats:
          * ``npm:<package>``       → run ``npx -y <package>`` directly.
            Used by curated catalog entries pointing at npm-published
            MCP servers (most Anthropic-official ones live here).
          * ``github:owner/repo``  → not implemented yet — log + return.
          * Anything else          → treat as a LobeHub marketplace id
                                     and go through the lhm CLI (needs
                                     MARKET_CLIENT_ID auth).

        Args:
            identifier: prefixed identifier as above
            tool_registry: ToolRegistry to register the new tools into

        Returns:
            {"name": ..., "tools": [...]}, or {..., "error": ...}
        """
        logger.info("Installing MCP server '%s'...", identifier)

        # Curated path: npm:@scope/pkg
        if identifier.startswith("npm:"):
            package = identifier[len("npm:"):]
            if not tool_registry:
                return {"name": package, "tools": [],
                        "note": "No tool_registry provided"}
            try:
                from ..mcp import MCPServerConfig
                config = MCPServerConfig(
                    name=package,
                    transport="stdio",
                    command="npx",
                    args=["-y", package],
                )
                tool_names = await tool_registry.register_mcp_server(config)
                logger.info(
                    "MCP server '%s' installed (npm): %d tools",
                    package, len(tool_names),
                )
                return {"name": package, "tools": tool_names, "source": "npm"}
            except Exception as e:  # noqa: BLE001
                logger.warning("npm MCP install failed for %s: %s", package, e)
                return {"name": package, "tools": [], "error": str(e)}

        # Curated path: github:owner/repo (deferred — agent can fall
        # back to manual git clone via Bash if it really wants this).
        if identifier.startswith("github:"):
            return {
                "name": identifier,
                "tools": [],
                "error": (
                    "github:* MCP install not implemented yet — try the "
                    "npm: equivalent if available, or install via Bash + "
                    "register the resulting binary manually."
                ),
            }

        # Smithery path: smithery:<qualified-name>
        # Hosted Smithery MCP servers require an OAuth round-trip
        # (auth.smithery.ai/<server>/authorize) which we cannot drive
        # headlessly from a server-side agent. Return a structured
        # error with actionable next steps so the agent can tell the
        # user exactly how to complete the install. Self-hosted
        # Smithery entries (rare) would have surfaced as `npm:` in
        # their search payload, hitting the curated branch above.
        if identifier.startswith("smithery:"):
            qn = identifier[len("smithery:"):]
            return {
                "name": qn,
                "tools": [],
                "source": "smithery",
                "error": (
                    f"Smithery hosted MCP servers need a one-time OAuth "
                    f"authorization that the server-side agent can't "
                    f"complete on its own. To finish install:\n"
                    f"  1. Open https://smithery.ai/server/{qn} in a "
                    f"browser and click Connect.\n"
                    f"  2. On the agent host, run "
                    f"`npx -y @smithery/cli install {qn}` once — "
                    f"it'll register the server in your local config "
                    f"using the auth from step 1.\n"
                    f"Hosted-server auto-install is tracked separately."
                ),
            }

        # No matching backend. Earlier rev would have tried a LobeHub
        # marketplace fallback here, but that path required
        # MARKET_CLIENT_ID/_SECRET and silently failed for every
        # un-credentialed agent. With LobeHub removed the only valid
        # identifiers are `npm:` (curated) and `smithery:` (above);
        # anything else is a typo or hallucination from the LLM.
        return {
            "name": identifier, "tools": [],
            "error": (
                f"Unknown MCP identifier '{identifier}'. Valid prefixes:\n"
                f"  * npm:<package>   (curated catalog — direct install)\n"
                f"  * smithery:<name> (Smithery registry — needs OAuth)\n"
                f"Run manage_mcp(action='search', query='...') to see "
                f"matching identifiers."
            ),
        }


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
