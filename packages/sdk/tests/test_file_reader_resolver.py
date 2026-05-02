"""ReadUploadedFileTool — resolver contract regression tests.

Locks in the contract the server's three-layer file store relies on:

  1. Every read goes through the resolver — the tool keeps no
     bytes of its own, so cross-turn / cross-eviction /
     cross-restart reads all see the same canonical store.
  2. Return is a structured ToolResult with the canonical filename
     (resolver may correct the LLM's filename guess) and a slice
     of the content sized by ``offset`` / ``limit``.
  3. Resolver miss produces an "Available: ..." error so the LLM
     can self-correct on the next call instead of saying "file
     not found" and giving up — that helpful list is what made the
     three-layer design debuggable from chat.
  4. Lister supplies the empty-filename "what files do I have?"
     surface AND the available-list shown on resolver miss.
  5. No resolver wired in → "no files available" (the right answer
     when the host hasn't bound a backing store; never a silent
     in-memory degradation, which was the source of the original
     cross-turn bug).
"""

from __future__ import annotations

import pytest

from nexus_core.tools.file_reader import ReadUploadedFileTool


@pytest.mark.asyncio
async def test_resolver_hit_returns_content_chunk():
    """Resolver returns (real_name, full_text); tool slices and
    decorates with the [File: …] header the LLM expects."""
    captured: list[str] = []

    async def resolve(name: str):
        captured.append(name)
        return ("paper.pdf", "abcdefghij" * 200)  # 2000 chars

    tool = ReadUploadedFileTool(resolver=resolve)
    result = await tool.execute(filename="paper.pdf", limit=50)

    assert result.success
    assert "paper.pdf" in result.output
    assert "Total: 2,000 chars" in result.output
    assert captured == ["paper.pdf"]


@pytest.mark.asyncio
async def test_resolver_miss_lists_available_for_llm_self_correction():
    """When the resolver returns None, the error MUST list what is
    available — without it the LLM gives up and tells the user
    "file not found" (the bug we're fixing)."""
    async def resolve(_name: str):
        return None

    async def lister():
        return {"actual.pdf": 12_345}

    tool = ReadUploadedFileTool(resolver=resolve, lister=lister)
    result = await tool.execute(filename="wrong.pdf")
    assert not result.success
    assert "actual.pdf" in result.error, (
        "available files must be surfaced so the LLM can correct its "
        "filename guess on the next tool call"
    )


@pytest.mark.asyncio
async def test_resolver_mode_supports_search():
    """search='keyword' must run against the resolver's content (not
    the legacy dict). The LLM uses this for big PDFs to find a
    section without paging through the whole file."""
    async def resolve(_name: str):
        return ("paper.pdf",
                "intro... methodology... " + ("filler " * 1000) +
                "RESULTS: we observed a 3x speedup. discussion...")

    tool = ReadUploadedFileTool(resolver=resolve)
    result = await tool.execute(filename="paper.pdf", search="3x speedup")
    assert "3x speedup" in result.output
    assert "Found '3x speedup'" in result.output


@pytest.mark.asyncio
async def test_lister_used_for_empty_filename():
    """``read_uploaded_file()`` with no filename should list files via
    the lister (resolver mode), not the legacy in-memory dict."""
    async def lister():
        return {"x.txt": 1234, "y.pdf": 567_890}

    tool = ReadUploadedFileTool(lister=lister)
    result = await tool.execute()
    assert "x.txt" in result.output
    assert "y.pdf" in result.output
    assert "1,234" in result.output
    assert "567,890" in result.output


@pytest.mark.asyncio
async def test_resolver_failure_doesnt_crash_tool():
    """A resolver that raises must not bubble up — the tool returns a
    "not found" result so the LLM can recover, while the exception is
    logged for ops."""
    async def resolve(_name: str):
        raise RuntimeError("simulated DB outage")

    tool = ReadUploadedFileTool(resolver=resolve)
    result = await tool.execute(filename="paper.pdf")
    assert not result.success
    assert "paper.pdf" in result.error


@pytest.mark.asyncio
async def test_no_resolver_reports_no_files_available():
    """A bare-constructed tool (no resolver bound) must NOT silently
    invent an in-memory cache — that was the original cross-turn
    bug. Until the host wires a resolver, every call cleanly
    reports nothing available so the LLM can produce a sensible
    "uploads aren't reachable yet" message instead of
    hallucinating about a file that's actually persisted but
    unreachable through this tool path.
    """
    tool = ReadUploadedFileTool()  # no resolver, no lister

    # Empty filename → "no files available"
    listing = await tool.execute()
    assert listing.success
    assert "No uploaded files available" in listing.output

    # Filename lookup → not-found error (with available list)
    lookup = await tool.execute(filename="anything.pdf")
    assert not lookup.success
    assert "anything.pdf" in lookup.error
    assert "(none)" in lookup.error


def test_legacy_in_memory_api_is_gone():
    """Guard that ``store()`` / ``store_path()`` / ``list_files()``
    are not resurrected by accident — the rule is "tool keeps no
    state of its own". If a refactor adds them back, this test
    fails immediately and forces a design discussion."""
    tool = ReadUploadedFileTool()
    assert not hasattr(tool, "store"), (
        "ReadUploadedFileTool.store must stay removed — see "
        "ARCHITECTURE.md. In-memory state on the tool was the "
        "source of the cross-turn file-not-found bug."
    )
    assert not hasattr(tool, "store_path"), (
        "ReadUploadedFileTool.store_path must stay removed."
    )
    assert not hasattr(tool, "list_files"), (
        "ReadUploadedFileTool.list_files must stay removed — "
        "use the ``lister=`` resolver callback instead."
    )
