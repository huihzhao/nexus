"""Generic file distillation: text extraction + LLM-driven summarization.

Originally lived in ``nexus_server.attachment_distiller`` — moved here so
any consumer of the SDK can use the same pipeline without re-implementing
it. The server-side persistence (writing the resulting summary into
``sync_events`` so it rides Greenfield + BSC anchors) is intentionally
NOT in this module: that's a deployment-specific concern that the
server keeps in its own thin shim. This module only does:

    raw bytes / base64 / text  →  extract_text()  →  distill()  →  summary

Persistence, request shape, and storage are all caller-owned.

Why split this way:
  * Server hosts a multi-tenant chat where attachments are folded into
    the prompt + persisted into per-user audit logs.
  * Future P2P / standalone twin agents will want the same "summarise
    this file before stuffing it into the prompt" behaviour without any
    of the server's HTTP / SQL infrastructure.
  * Tests don't need to spin up a database to verify summary quality.

Soft dependencies:
  * ``pypdf`` for PDF text extraction. If it's missing, PDF inputs fall
    back to a metadata stub — the LLM still gets a useful description
    via filename/mime, just no body text.
"""

from __future__ import annotations

import base64
import binascii
import io
import logging
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


# ── Tunables ──────────────────────────────────────────────────────────

# Cap on how many characters of file text we send to the distiller LLM.
# Large enough to cover most papers + small books; the rest the model
# summarises from the head it sees. Keeps token cost predictable.
DISTILL_INPUT_CHAR_BUDGET = 60_000

# Cap on the distilled summary itself — what shows up in subsequent
# prompts. Roughly 1k tokens.
DISTILL_OUTPUT_CHAR_BUDGET = 4_000


DISTILL_SYSTEM_PROMPT = (
    "You are a careful file summarizer. Given a single file's contents, "
    "produce a structured summary the original user can rely on later.\n\n"
    "Output format (markdown, plain text — NO code fences):\n"
    "- One-line description of what this file is.\n"
    "- 'Key points:' bulleted list (5–12 items): the most important "
    "facts, claims, decisions, or data the reader would want preserved.\n"
    "- 'Entities:' inline list of important named entities (people, "
    "products, dates, places, IDs) — comma separated.\n"
    "- 'Structure:' brief note on how the document is organized.\n"
    "- If the file is mostly tabular, list the column names and one or "
    "two representative rows.\n"
    "- Be concrete — quote short fragments where helpful.\n"
    f"- Hard limit: {DISTILL_OUTPUT_CHAR_BUDGET} characters total.\n"
    "- If you cannot read the content (binary or empty), still describe "
    "what you can infer from the filename and mime type."
)


# ── Type alias for an LLM caller ──────────────────────────────────────
#
# Callers thread their own LLM in. The signature mirrors the server's
# ``llm_gateway.call_llm`` historical contract (which both server and
# twin/SDK callers can adapt to):
#
#   async fn(messages, system_prompt, model, temperature, max_tokens,
#            tools) -> (content, model_used, stop_reason, tool_calls)
#
# The first arg is a list of {"role", "content"} dicts. ``tools`` is
# always None for distillation — this is a leaf call, no tool use.

LlmFn = Callable[
    [
        list[dict],          # messages
        Optional[str],       # system_prompt
        Optional[str],       # model (None → caller's default)
        Optional[float],     # temperature
        Optional[int],       # max_tokens
        Any,                 # tools (always None for distillation)
    ],
    Awaitable[tuple[str, str, str, list]],  # (content, model, stop, tool_calls)
]


# ── Text extraction ───────────────────────────────────────────────────


def extract_text(
    name: str,
    mime: str,
    content_text: Optional[str],
    content_base64: Optional[str],
) -> tuple[str, str]:
    """Pull plain text out of an attachment for the distiller.

    Returns ``(text, source_label)`` where ``source_label`` tells
    downstream code *how* the text was obtained ("text" / "pdf" /
    "binary-stub" / "empty"). Always returns *something* — never raises
    — so callers can assume a distill attempt is always possible.

    Args:
        name: Filename — used in the no-content stub messages.
        mime: MIME type — used to pick the extraction path.
        content_text: Pre-decoded UTF-8 content (preferred when
            available; the caller did the decode).
        content_base64: Raw bytes encoded as base64. Used as fallback.
    """
    if content_text is not None and content_text:
        return content_text[:DISTILL_INPUT_CHAR_BUDGET], "text"

    if content_base64 is None:
        return f"[empty attachment named {name!r} ({mime})]", "empty"

    try:
        raw = base64.b64decode(content_base64, validate=False)
    except (binascii.Error, ValueError) as e:
        return (
            f"[unreadable attachment {name!r}: bad base64 ({e})]",
            "binary-stub",
        )

    # PDF: try pypdf if it's installed; not a hard dependency.
    if mime == "application/pdf" or name.lower().endswith(".pdf"):
        text = _extract_pdf_text(raw)
        if text:
            return text[:DISTILL_INPUT_CHAR_BUDGET], "pdf"
        return (
            f"[PDF {name!r} — text extraction unavailable; "
            f"{len(raw)} bytes]",
            "binary-stub",
        )

    # Plain text dressed up as binary by an over-cautious client?
    try:
        decoded = raw.decode("utf-8")
        return decoded[:DISTILL_INPUT_CHAR_BUDGET], "text"
    except UnicodeDecodeError:
        pass

    return (
        f"[binary attachment {name!r} ({mime}, {len(raw)} bytes); "
        f"content not extracted]",
        "binary-stub",
    )


def _extract_pdf_text(raw: bytes) -> str:
    """Try to pull text out of a PDF. Returns empty string if pypdf
    isn't available or extraction fails — the caller falls back to a
    metadata-only summary.
    """
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        logger.debug("pypdf not installed; skipping PDF text extraction")
        return ""

    try:
        reader = PdfReader(io.BytesIO(raw))
        chunks: list[str] = []
        running_total = 0
        for i, page in enumerate(reader.pages):
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            if not t:
                continue
            chunks.append(f"\n--- Page {i + 1} ---\n{t}")
            running_total += len(t)
            if running_total >= DISTILL_INPUT_CHAR_BUDGET:
                break
        return "".join(chunks)
    except Exception as e:
        logger.debug("PDF extraction failed: %s", e)
        return ""


# ── Distillation (LLM-driven) ─────────────────────────────────────────


async def distill(
    *,
    name: str,
    mime: str,
    size_bytes: int,
    content_text: Optional[str],
    content_base64: Optional[str],
    llm_fn: LlmFn,
) -> tuple[str, str]:
    """Distill a single attachment via the LLM.

    Returns ``(summary, source_label)``. On any LLM failure, falls
    back to a head-truncation of whatever text we extracted, so the
    caller is never blocked. ``source_label`` includes a ``+fallback``
    suffix when the LLM call failed and the head excerpt was used.
    """
    text, source = extract_text(name, mime, content_text, content_base64)

    user_msg = (
        f"Filename: {name}\nMIME: {mime}\nSize: {size_bytes} bytes\n"
        f"Source: {source}\n\n"
        f"--- begin content ---\n{text}\n--- end content ---"
    )

    try:
        content, _model, _stop, _tools = await llm_fn(
            [{"role": "user", "content": user_msg}],
            DISTILL_SYSTEM_PROMPT,
            None,    # default model
            0.2,     # low temperature: we want consistent factual summaries
            1024,    # output budget
            None,    # no tools — this is a leaf LLM call
        )
        summary = (content or "").strip()
        if summary:
            if len(summary) > DISTILL_OUTPUT_CHAR_BUDGET:
                summary = summary[:DISTILL_OUTPUT_CHAR_BUDGET] + "…"
            return summary, source
    except Exception as e:
        logger.warning("Distillation LLM call failed for %s: %s", name, e)

    # LLM unavailable or returned nothing — fall back to a head excerpt
    # so the caller still has *something* to fold into the chat prompt.
    head = text[:1024]
    fallback = (
        f"[Distillation unavailable; head-only excerpt of {name} "
        f"({mime}, {size_bytes} bytes)]\n\n{head}"
    )
    return fallback, source + "+fallback"
