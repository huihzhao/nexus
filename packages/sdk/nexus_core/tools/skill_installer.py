"""SkillInstallerTool + MCPInstallerTool — let the agent expand its
own capabilities at chat time.

Background
==========
Twin's persona prompt has long advertised "you can install new skills
from the LobeHub marketplace" but no actual install tool was ever
registered. The agent would dutifully tell the user it was "installing
a skill", then admit a few turns later that it could not actually
execute the install — frustrating for the user and a credibility hit
for the "self-evolving agent" pitch.

These two tools wrap the existing :class:`SkillManager` API
(``search_lobehub`` / ``install`` for Anthropic-style skills, and
``search_mcp`` / ``install_mcp`` for MCP servers) as ToolRegistry
entries the LLM can invoke via function calling.

Design notes
------------
* Each tool is split into search + install rather than one mega-tool,
  matching how the SDK's SkillManager exposes it. The LLM finds the
  right marketplace entry first, confirms the choice with the user
  (it's the LLM's job to ask), then installs by id. This keeps the
  install step explicit — the agent never installs something it
  hasn't first surfaced to the user.

* Both tools are NO-OP-safe when network is unavailable — the
  underlying manager catches httpx errors and returns empty lists /
  error dicts, which we surface to the LLM verbatim. The LLM can then
  apologise and suggest copying-pasting the relevant snippet, etc.

* MCP install needs a ``tool_registry`` so the newly installed MCP's
  tools can be registered into the live session. We pass ``None`` to
  the SDK helper, which installs the MCP definitions but doesn't
  register MCP tools onto the live ToolRegistry. Tools that need a
  full hot-load (without restart) can subclass and inject the live
  registry — for now the user restarts twin to pick up new MCP tools,
  matching the same pattern as Cursor / VS Code MCP integrations.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from .base import BaseTool, ToolResult

if TYPE_CHECKING:
    from ..skills import SkillManager
    from .registry import ToolRegistry

logger = logging.getLogger(__name__)


# Cap how many marketplace results we surface to the LLM in one search.
# More than this and the model spends most of its context on the list
# instead of reasoning about which one fits the user's need.
_MAX_RESULTS = 8

# Hard ceilings on each operation so the chat surface never sits on
# "Agent is thinking…" for more than this on a stuck network. The LLM
# still gets a clean failure ToolResult and can choose another path
# (try the other marketplace, ask the user to paste content, …).
_SEARCH_TIMEOUT = 25.0   # one search; both backends in parallel
_INSTALL_TIMEOUT = 60.0  # one install; bounded by per-download timeout
_LIST_TIMEOUT = 5.0      # local read, should be instant


def _safe_truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


# Query expansion: small hand-curated synonym map covering the
# capability gaps users hit most. Each input token expands to several
# search variants we run in parallel against every backend, then merge
# + dedupe. This costs almost nothing (extra GitHub API calls are
# parallel) and dramatically improves recall — "pdf" alone hits one
# narrow LobeHub query, but "pdf reader / pdf extract / pypdf /
# document parser" between them surface every skill that touches PDF
# handling.
#
# Keys are matched as substrings (case-insensitive) against the user's
# query; values are appended to the variant list.  Duplicates from
# overlapping rules are deduped before fan-out.
_QUERY_EXPANSIONS: dict[str, list[str]] = {
    "pdf":      ["pdf", "pdf reader", "pdf extract", "pypdf", "document parser"],
    "excel":    ["excel", "xlsx", "spreadsheet", "openpyxl", "tabular"],
    "spreadsheet": ["spreadsheet", "xlsx", "csv", "tabular"],
    "csv":      ["csv", "spreadsheet", "tabular data"],
    "image":    ["image", "image edit", "vision", "ocr"],
    "ocr":      ["ocr", "image text", "tesseract"],
    "deck":     ["pptx", "slides", "presentation", "powerpoint"],
    "slides":   ["pptx", "slides", "presentation", "powerpoint"],
    "diagram":  ["diagram", "mermaid", "plantuml", "drawio"],
    "chart":    ["chart", "plot", "matplotlib", "graph"],
    "plot":     ["plot", "chart", "matplotlib"],
    "translate": ["translate", "translation", "i18n"],
    "wallet":   ["wallet", "ethers", "web3", "blockchain"],
    "github":   ["github", "git"],
    "search":   ["web search", "search api", "tavily"],
}


def _expand_query(query: str) -> list[str]:
    """Return a deduped list of search variants for ``query``.

    Always includes the original. If the query matches any of the
    synonym table's keys, add those variants too. Cap at 6 variants
    so we don't blow up the GitHub API rate budget on every call.
    """
    q_lower = query.lower().strip()
    out: list[str] = [query.strip()]
    seen = {q_lower}
    for key, variants in _QUERY_EXPANSIONS.items():
        if key in q_lower:
            for v in variants:
                if v.lower() not in seen:
                    out.append(v)
                    seen.add(v.lower())
    return out[:6]


class SkillInstallerTool(BaseTool):
    """Search the LobeHub Skills marketplace and install a chosen skill.

    Two operations under one tool name (``manage_skill``) so the LLM
    can invoke either via the same function — the schema's ``action``
    field discriminates. Keeping it as one function (rather than two)
    matches the pattern most LLMs train on for "manage X" CRUD tools
    and is cheaper on the function-list token budget.
    """

    def __init__(self, skill_manager: "SkillManager"):
        self._mgr = skill_manager

    @property
    def name(self) -> str:
        return "manage_skill"

    @property
    def description(self) -> str:
        return (
            "Search and install Anthropic-style skills (SKILL.md format) from "
            "THREE marketplaces — LobeHub (community catalog, ~100K skills via "
            "npx CLI), Google's official Gemini Skills repo "
            "(google-gemini/gemini-skills), and the GitHub `claude-skills` "
            "topic (third-party skills with a topic tag, several hundred "
            "repos) — or list installed skills. Search auto-expands the "
            "query into synonym variants (e.g. 'pdf' → 'pdf, pdf reader, "
            "pdf extract, pypdf, document parser') and fans out across all "
            "three sources in parallel, dramatically improving recall. Use "
            "this when the user asks for a capability you don't have: "
            "action='search' returns interleaved matches with a source tag, "
            "action='install' adds a chosen skill by identifier, "
            "action='list' shows what's already installed."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "install", "show", "list"],
                    "description": (
                        "search → query marketplaces (with synonym expansion) "
                        "and return matches; "
                        "install → add a specific skill by id (the 'identifier' "
                        "field from a previous search); "
                        "show → load the FULL SKILL.md instructions for an "
                        "already-installed skill into your context. CALL THIS "
                        "FIRST whenever the user asks you to do something a "
                        "skill covers (e.g. 'analyze this PDF' → first "
                        "show(name='pdf') to load the operations, THEN follow "
                        "the instructions). The system prompt only carries "
                        "skill names + 80-char descriptions, not the actual "
                        "operations — show is how you read those.; "
                        "list → show currently installed skill names."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "For action='search': the natural-language query.",
                },
                "identifier": {
                    "type": "string",
                    "description": (
                        "For action='install': the skill identifier returned "
                        "by a previous search. LobeHub identifiers are bare "
                        "slugs; Gemini official skills use the prefix "
                        "'gemini:<name>'; GitHub-topic results use full "
                        "https://github.com/... URLs (raw URLs also accepted)."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "For action='show': the installed skill's name "
                        "(see action='list' to find it). Returns the full "
                        "SKILL.md content + any reference docs."
                    ),
                },
                "source": {
                    "type": "string",
                    "enum": ["all", "anthropic", "gemini", "lobehub", "github"],
                    "description": (
                        "Which marketplace to search. 'all' (default) queries "
                        "all four sources and interleaves results. "
                        "'anthropic' = anthropics/skills (canonical, includes "
                        "pdf/docx/xlsx/pptx/skill-creator). "
                        "'gemini' = google-gemini/gemini-skills. "
                        "'lobehub' = LobeHub community catalog (~100K skills, "
                        "via npx CLI). "
                        "'github' = GitHub claude-skills topic search. "
                        "Ignored for action!=search."
                    ),
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        query: str = "",
        identifier: str = "",
        name: str = "",
        source: str = "all",
        **kwargs,
    ) -> ToolResult:
        # Outer hard timeout. Per-action budgets are enforced via
        # asyncio.wait_for around the actual work below — failure
        # surfaces as a clean ToolResult so the LLM moves on instead
        # of holding the chat surface hostage.
        a = (action or "").strip().lower()
        src = (source or "all").strip().lower()
        try:
            if a == "search":
                if not query.strip():
                    return ToolResult(
                        success=False,
                        output="action=search requires a non-empty 'query' field.",
                    )

                # Audit fix A: expand the query into synonym variants so
                # one narrow input ("pdf") still hits skills filed under
                # adjacent capability names ("pdf reader", "pypdf",
                # "document parser"). Each backend gets every variant in
                # parallel; results are merged + deduped by identifier.
                variants = _expand_query(query)
                logger.info(
                    "manage_skill search: '%s' → %d variant(s): %s",
                    query, len(variants), variants,
                )

                # Build the task fan-out. For 'all' source we pay one
                # request per (variant × backend); 6 variants × 3
                # backends = 18 parallel calls in the worst case, all
                # short. _MAX_RESULTS per call keeps the response bounded.
                tasks: list = []
                task_meta: list[tuple[str, str]] = []  # (source, variant)
                for v in variants:
                    # Order matters: query the highest-quality canonical
                    # sources first so when we sort by stars / appearance
                    # later, anthropic/gemini land before community hits.
                    if src in ("all", "anthropic"):
                        tasks.append(self._mgr.search_anthropic_official(v, limit=_MAX_RESULTS))
                        task_meta.append(("anthropic", v))
                    if src in ("all", "gemini"):
                        tasks.append(self._mgr.search_gemini_official(v, limit=_MAX_RESULTS))
                        task_meta.append(("gemini", v))
                    if src in ("all", "lobehub"):
                        tasks.append(self._mgr.search_lobehub(v, limit=_MAX_RESULTS))
                        task_meta.append(("lobehub", v))
                    if src in ("all", "github"):
                        tasks.append(self._mgr.search_github_topic(v, limit=_MAX_RESULTS))
                        task_meta.append(("github-topic", v))

                try:
                    gathered = await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True),
                        timeout=_SEARCH_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    return ToolResult(
                        success=False,
                        output=(
                            f"Skill marketplace search timed out after "
                            f"{_SEARCH_TIMEOUT:.0f}s — network may be slow "
                            f"or LobeHub CLI is downloading for the first "
                            f"time. Try again with source='gemini' or "
                            f"source='github' to skip the slow LobeHub CLI."
                        ),
                    )

                # Merge results across all (source × variant) buckets,
                # dedupe by identifier so the same skill from a same
                # source isn't surfaced multiple times when several
                # variants matched it.
                seen_ids: set[str] = set()
                slim: list[dict] = []
                for (source_name, _variant), result in zip(task_meta, gathered):
                    if not isinstance(result, list):
                        continue
                    for r in result:
                        ident = r.get("identifier") or r.get("id") or r.get("url", "")
                        if not ident or ident in seen_ids:
                            continue
                        seen_ids.add(ident)
                        slim.append({
                            "identifier": ident,
                            "name": r.get("name") or r.get("title") or ident,
                            "description": _safe_truncate(
                                r.get("description") or r.get("summary", ""), 200,
                            ),
                            "source": r.get("source") or source_name,
                            "stars": r.get("stars"),
                            "tags": r.get("tags", []),
                        })
                # Rank: canonical Anthropic / Gemini sources first
                # (highest signal-to-noise), then GitHub stars, then
                # name. The LLM will still pass the final choice up to
                # the user, but anthropic/skills/pdf landing at the
                # top of the list when the query is "pdf" matches what
                # most users would want as the default.
                _SOURCE_PRIORITY = {
                    "anthropic": 0,
                    "gemini": 1,
                    "github-topic": 2,
                    "lobehub": 3,
                }
                slim.sort(
                    key=lambda x: (
                        _SOURCE_PRIORITY.get(x.get("source", ""), 9),
                        -(x.get("stars") or 0),
                        x.get("name", ""),
                    ),
                )
                slim = slim[: _MAX_RESULTS * 2]  # cap final list

                if not slim:
                    # Audit fix D: empty-everywhere → return a clear
                    # fallback so the LLM can give the user actionable
                    # next steps instead of just shrugging.
                    return ToolResult(
                        success=True,
                        output=json.dumps({
                            "matches": [],
                            "fallback_options": [
                                {
                                    "kind": "paste_content",
                                    "instruction": (
                                        "Ask the user to paste the file's "
                                        "content directly into chat — you "
                                        "can already read text from any "
                                        "uploaded text-friendly file."
                                    ),
                                },
                                {
                                    "kind": "github_url",
                                    "instruction": (
                                        "If the user knows a specific skill "
                                        "repo (e.g. they saw it on GitHub), "
                                        "ask them for the URL and pass it "
                                        "directly to manage_skill(action="
                                        "'install', identifier='https://"
                                        "github.com/owner/repo/tree/main/"
                                        "skills/<name>')."
                                    ),
                                },
                                {
                                    "kind": "broaden_query",
                                    "instruction": (
                                        f"The query '{query}' returned 0 "
                                        f"matches across LobeHub + Gemini + "
                                        f"GitHub-topic with {len(variants)} "
                                        f"synonym variants. Try a higher-"
                                        f"level term ('document' instead "
                                        f"of 'pdf-reader')."
                                    ),
                                },
                            ],
                            "queries_tried": variants,
                        }, ensure_ascii=False),
                    )
                return ToolResult(
                    success=True,
                    output=json.dumps({
                        "matches": slim,
                        "queries_tried": variants,
                    }, ensure_ascii=False),
                )

            if a == "install":
                if not identifier.strip():
                    return ToolResult(
                        success=False,
                        output="action=install requires the 'identifier' field (use action=search first).",
                    )
                try:
                    installed = await asyncio.wait_for(
                        self._mgr.install(identifier),
                        timeout=_INSTALL_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    return ToolResult(
                        success=False,
                        output=(
                            f"Skill install timed out after {_INSTALL_TIMEOUT:.0f}s — "
                            f"the marketplace fetch (or `npx` first-run) is taking too long. "
                            f"Try a different skill or marketplace (source='gemini'/'lobehub')."
                        ),
                    )
                return ToolResult(
                    success=True,
                    output=json.dumps({
                        "installed": True,
                        "name": getattr(installed, "name", str(installed)),
                        "version": getattr(installed, "version", ""),
                        "note": (
                            "Skill installed. Reference its capabilities in "
                            "your next replies; the system prompt has already "
                            "been updated."
                        ),
                    }, ensure_ascii=False),
                )

            if a == "show":
                # On-demand load of full SKILL.md content. Without
                # this branch the LLM only ever saw 80-char skill
                # blurbs in the system prompt — it knew "pdf skill
                # exists" but not what operations it provided, so
                # users hitting "analyze this PDF" got hallucinated
                # "I don't have an analyze function" replies. Now
                # the LLM is told (via the action enum's docstring)
                # to call show first, gets the actual instructions,
                # and can follow them.
                if not name.strip():
                    return ToolResult(
                        success=False,
                        output="action=show requires the 'name' field (use action=list to find it).",
                    )
                target = None
                for s in self._mgr.installed:
                    if getattr(s, "name", "") == name:
                        target = s
                        break
                if target is None:
                    return ToolResult(
                        success=False,
                        output=(
                            f"Skill '{name}' is not installed. Available: "
                            f"{', '.join(getattr(s, 'name', '') for s in self._mgr.installed) or '(none)'}. "
                            f"Use manage_skill(action='install', identifier=...) first."
                        ),
                    )
                payload = {
                    "name": getattr(target, "name", ""),
                    "title": getattr(target, "title", ""),
                    "description": getattr(target, "description", ""),
                    "version": getattr(target, "version", ""),
                    "instructions": getattr(target, "instructions", ""),
                    # references is dict[str, str] — name → markdown body
                    "references": dict(getattr(target, "references", {}) or {}),
                }
                return ToolResult(
                    success=True,
                    output=json.dumps(payload, ensure_ascii=False),
                )

            if a == "list":
                items = self._mgr.installed
                return ToolResult(
                    success=True,
                    output=json.dumps({
                        "installed": [
                            {
                                "name": getattr(s, "name", ""),
                                "version": getattr(s, "version", ""),
                                "description": _safe_truncate(
                                    getattr(s, "description", ""), 160,
                                ),
                            }
                            for s in items
                        ],
                    }, ensure_ascii=False),
                )

            return ToolResult(
                success=False,
                output=f"Unknown action '{action}'. Valid: search, install, list.",
            )
        except Exception as e:
            logger.warning("manage_skill %s failed: %s", action, e)
            return ToolResult(
                success=False,
                output=f"manage_skill action={action} failed: {e}",
            )


class McpInstallerTool(BaseTool):
    """Search and install MCP servers from the LobeHub MCP marketplace.

    MCP (Model Context Protocol) servers add real backend integrations
    — Slack, GitHub, Google Drive, databases — as new tools the agent
    can call. This tool lets the agent broker that install at chat
    time so a user request like "read my Slack DMs" can be handled
    end-to-end without the operator pre-wiring everything.
    """

    def __init__(self, skill_manager: "SkillManager", tool_registry=None):
        self._mgr = skill_manager
        # Optional: when set, install_mcp registers the new server's
        # tools into the live ToolRegistry so they're callable in the
        # SAME chat turn. When None, the install lands on disk and
        # the next twin restart picks them up.
        self._tool_registry = tool_registry

    @property
    def name(self) -> str:
        return "manage_mcp"

    @property
    def description(self) -> str:
        return (
            "Search and install MCP (Model Context Protocol) servers from "
            "the LobeHub marketplace. MCP servers are pre-built "
            "integrations — Slack, GitHub, GDrive, Postgres, "
            "Ethereum/Polygon/Arbitrum/Solana/Starknet RPC, etc — that "
            "expose new function-callable tools at chat time.\n"
            "\n"
            "MUST USE this tool — NOT web_search, NOT 'I can't do that' — "
            "as your FIRST move whenever the user requests live data from a "
            "SaaS / chain / database / service you don't currently have a "
            "tool for. Sequence: action='search' to find matches, then "
            "action='install' on the best one (it becomes a callable tool "
            "in the SAME turn), then call it. action='list' shows what's "
            "already wired up.\n"
            "\n"
            "Examples that MUST trigger this tool first:\n"
            "  * 'What is Starknet's block height?' → search='starknet'\n"
            "  * 'Send a Slack message' → search='slack'\n"
            "  * 'Query my Postgres' → search='postgres'\n"
            "  * 'Latest Ethereum gas price' → search='ethereum'\n"
            "Falling through to web_search before searching this registry "
            "is a behavioural error — the marketplace likely has a real "
            "RPC / API integration you can install in 5 seconds."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "install", "list"],
                    "description": "search / install / list",
                },
                "query": {
                    "type": "string",
                    "description": "For action=search: natural-language query.",
                },
                "identifier": {
                    "type": "string",
                    "description": (
                        "For action=install: the MCP identifier returned "
                        "by a previous search."
                    ),
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        query: str = "",
        identifier: str = "",
        **kwargs,
    ) -> ToolResult:
        a = (action or "").strip().lower()
        try:
            if a == "search":
                if not query.strip():
                    return ToolResult(
                        success=False,
                        output="action=search requires a non-empty 'query' field.",
                    )
                results = await self._mgr.search_mcp(query, limit=_MAX_RESULTS)
                if not results:
                    return ToolResult(
                        success=True,
                        output="No MCP servers matched.",
                    )
                slim = [
                    {
                        "identifier": r.get("identifier") or r.get("id") or r.get("url", ""),
                        "name": r.get("name") or r.get("title", ""),
                        "description": _safe_truncate(
                            r.get("description") or r.get("summary", ""), 200,
                        ),
                        "tools": r.get("tools", []),
                    }
                    for r in results
                ]
                return ToolResult(
                    success=True,
                    output=json.dumps({"matches": slim}, ensure_ascii=False),
                )

            if a == "install":
                if not identifier.strip():
                    return ToolResult(
                        success=False,
                        output="action=install requires the 'identifier' field.",
                    )
                result = await self._mgr.install_mcp(
                    identifier, tool_registry=self._tool_registry,
                )
                # install_mcp returns a dict; pass it back so the LLM
                # can read what tools the user just gained.
                return ToolResult(
                    success=bool(result.get("success", True)),
                    output=json.dumps(result, ensure_ascii=False),
                )

            if a == "list":
                # SkillManager doesn't expose installed MCPs separately
                # in v1 — fall back to listing the registry's tool names
                # so the LLM can see what's wired up right now.
                names: list[str] = []
                if self._tool_registry is not None:
                    names = list(getattr(self._tool_registry, "tool_names", []))
                return ToolResult(
                    success=True,
                    output=json.dumps({"available_tools": names}, ensure_ascii=False),
                )

            return ToolResult(
                success=False,
                output=f"Unknown action '{action}'. Valid: search, install, list.",
            )
        except Exception as e:
            logger.warning("manage_mcp %s failed: %s", action, e)
            return ToolResult(
                success=False,
                output=f"manage_mcp action={action} failed: {e}",
            )
