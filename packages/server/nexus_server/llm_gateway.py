"""LLM Gateway router with tool execution loop.

Routes requests to configured LLM providers (Gemini, OpenAI, Anthropic).
When the LLM returns tool calls (web search, URL read, file generate),
the server executes them and feeds results back until a final text response.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from nexus_server.auth import get_current_user
from nexus_server.config import get_config
from nexus_server.middleware import check_rate_limit

logger = logging.getLogger(__name__)
config = get_config()

router = APIRouter(prefix="/api/v1/llm", tags=["llm"])

# Maximum tool call rounds to prevent infinite loops
MAX_TOOL_ROUNDS = 5


def _twin_enabled() -> bool:
    """Phase D feature flag — when on, /llm/chat goes through TwinManager
    (Nexus DigitalTwin per-user) instead of the direct LLM gateway."""
    return bool(getattr(config, "USE_TWIN", False))


# ───────────────────────────────────────────────────────────────────────────
# Request/Response Models
# ───────────────────────────────────────────────────────────────────────────


class LLMMessage(BaseModel):
    """Message for LLM chat."""
    role: str = Field(..., pattern="^(user|assistant|system|tool)$")
    content: str = Field(..., min_length=1)
    tool_call_id: Optional[str] = None


class ToolCallInfo(BaseModel):
    """Tool call returned by the LLM."""
    id: str
    name: str
    arguments: dict = {}


# ── Attachments ───────────────────────────────────────────────────────────
#
# Two-tier policy:
#
# 1. UPLOAD CAP — how large a payload the server will *accept* on the wire
#    and durably store. Defaults to 100 MB; configurable via env so an
#    operator can tighten it (or a test can drop it dramatically).
#
# 2. INLINE-TEXT CAP — how much of an attachment's text content is folded
#    into the LLM prompt. Above this we splice in only a head excerpt + a
#    "[truncated …]" marker so we don't blow the model's context window
#    (Gemini 2.5 Flash is 1M tokens, but a single multi-MB doc will still
#    starve the rest of the conversation, and most models cost-per-token
#    scales with whatever we send). The full content stays on the wire so
#    downstream sync/anchor code can still durably store it.
import os as _os

MAX_ATTACHMENT_BYTES_TOTAL = int(
    _os.environ.get("NEXUS_MAX_ATTACHMENT_BYTES", str(100 * 1024 * 1024))
)
MAX_INLINE_TEXT_BYTES = int(
    _os.environ.get("NEXUS_MAX_INLINE_TEXT_BYTES", str(256 * 1024))
)


class Attachment(BaseModel):
    """A file attached to a chat turn.

    Either ``content_text`` (for text-decodable files) or ``content_base64``
    (for binary) should be set. The server folds text content into the last
    user message; binary content is summarised as metadata-only for now.
    """

    name: str = Field(..., min_length=1, max_length=512)
    mime: str = Field("application/octet-stream", max_length=255)
    # Per-attachment size matches the total cap — a single big file is fine,
    # only the *sum* across attachments triggers 413.
    size_bytes: int = Field(..., ge=0, le=MAX_ATTACHMENT_BYTES_TOTAL)
    content_text: Optional[str] = None
    content_base64: Optional[str] = None
    # Round 2-B (thin client): the modern path is the desktop uploads
    # files via /api/v1/files/upload, gets a file_id back, and references
    # it here. Server resolves the id, reads bytes from disk, and runs
    # distill. Old path (content_text / content_base64 set inline) still
    # works during transition.
    file_id: Optional[str] = None


class LLMChatRequest(BaseModel):
    """LLM chat request."""
    messages: list[LLMMessage] = Field(..., min_length=1, max_length=100)
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=1, le=8192)
    enable_tools: bool = True
    # Optional file attachments; folded into the last user message
    attachments: list[Attachment] = Field(default_factory=list, max_length=20)


def _fold_attachments_into_messages(
    messages: list[dict], attachments: list[Attachment]
) -> list[dict]:
    """Prepend a synthetic [Attachments] block to the last user message.

    Returns a *new* list; does not mutate the caller's. Each text attachment
    is wrapped in a ``--- name (mime, size) ---`` fence. Binary-only
    attachments get a one-line summary so the model at least knows they
    were sent (and can suggest the user paste the relevant bit).
    """
    if not attachments:
        return messages

    # Locate the last user message to attach to. Fall back to appending
    # a fresh one if there is none.
    out = list(messages)
    target_idx = next(
        (i for i in range(len(out) - 1, -1, -1) if out[i].get("role") == "user"),
        None,
    )

    blocks: list[str] = ["[Attachments]"]
    for att in attachments:
        size_kb = att.size_bytes / 1024
        header = f"--- {att.name} ({att.mime}, {size_kb:.1f} KB) ---"
        if att.content_text is not None:
            blocks.append(header)
            if len(att.content_text) > MAX_INLINE_TEXT_BYTES:
                # Send the head only; the rest still rides through to the
                # event log + Greenfield via the original Attachment object,
                # so durable copies are intact even when the LLM only sees
                # a snippet.
                head = att.content_text[:MAX_INLINE_TEXT_BYTES]
                truncated = len(att.content_text) - len(head)
                blocks.append(head)
                blocks.append(
                    f"[truncated — {truncated} more characters not shown to "
                    f"the model; full content is durably stored]"
                )
            else:
                blocks.append(att.content_text)
            blocks.append(f"--- end {att.name} ---")
        else:
            blocks.append(
                f"{header}\n[binary content omitted — {att.size_bytes} bytes]\n"
                f"--- end {att.name} ---"
            )
    folded = "\n".join(blocks)

    if target_idx is None:
        out.append({"role": "user", "content": folded})
    else:
        original = out[target_idx]["content"]
        out[target_idx] = {
            **out[target_idx],
            "content": f"{folded}\n\n{original}",
        }
    return out


def _validate_attachment_total(attachments: list[Attachment]) -> None:
    """Raise 413 if attachments collectively exceed the cap."""
    total = sum(
        (len(a.content_text) if a.content_text is not None else 0)
        + (len(a.content_base64) if a.content_base64 is not None else 0)
        for a in attachments
    )
    if total > MAX_ATTACHMENT_BYTES_TOTAL:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Attachments total {total} bytes exceeds limit of "
                f"{MAX_ATTACHMENT_BYTES_TOTAL} bytes."
            ),
        )


class AttachmentSummary(BaseModel):
    """A distilled summary of one attachment, returned to the client.

    The desktop appends each of these as an ``attachment_distilled`` event
    in its local event log so subsequent conversation turns naturally
    "remember" what the file was about, even when the file isn't attached
    again.
    """
    name: str
    mime: str
    size_bytes: int
    summary: str
    source: str  # 'text' / 'pdf' / 'binary-stub' / …
    sync_id: Optional[int] = None


class LLMChatResponse(BaseModel):
    """LLM chat response."""
    role: str
    content: str
    model: str
    stop_reason: Optional[str] = None
    tool_calls_executed: list[str] = []
    attachment_summaries: list[AttachmentSummary] = []


# ───────────────────────────────────────────────────────────────────────────
# Tool Definitions & Execution
# ───────────────────────────────────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "web_search",
        "description": "Search the web for current information. Use when the user asks about recent events, facts, or anything that requires up-to-date knowledge.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_url",
        "description": "Read and extract content from a URL. Use when the user provides a link or you need to fetch a specific web page.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to read"},
            },
            "required": ["url"],
        },
    },
]


async def execute_tool(name: str, arguments: dict) -> str:
    """Execute a tool and return the result as text."""
    if name == "web_search":
        return await _web_search(arguments.get("query", ""))
    elif name == "read_url":
        return await _read_url(arguments.get("url", ""))
    else:
        return f"Unknown tool: {name}"


async def _web_search(query: str) -> str:
    """Execute web search via Tavily API."""
    if not config.TAVILY_API_KEY:
        return "Web search unavailable: TAVILY_API_KEY not configured on server."

    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": config.TAVILY_API_KEY,
                    "query": query,
                    "max_results": 5,
                    "include_answer": True,
                },
            )
            data = resp.json()
            # Build a concise result
            parts = []
            if data.get("answer"):
                parts.append(f"Answer: {data['answer']}")
            for r in data.get("results", [])[:5]:
                parts.append(f"- {r.get('title', '')}: {r.get('content', '')[:200]}")
                parts.append(f"  URL: {r.get('url', '')}")
            return "\n".join(parts) if parts else "No results found."
    except Exception as e:
        logger.warning("Web search failed: %s", e)
        return f"Web search error: {e}"


async def _read_url(url: str) -> str:
    """Read URL content via Jina Reader API."""
    if not config.JINA_API_KEY:
        # Fallback: direct fetch
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, follow_redirects=True)
                return resp.text[:5000]
        except Exception as e:
            return f"URL read error: {e}"

    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://r.jina.ai/{url}",
                headers={"Authorization": f"Bearer {config.JINA_API_KEY}"},
            )
            return resp.text[:5000]
    except Exception as e:
        logger.warning("URL read failed: %s", e)
        return f"URL read error: {e}"


# ───────────────────────────────────────────────────────────────────────────
# LLM Calls (with tool support)
# ───────────────────────────────────────────────────────────────────────────


async def call_llm(
    messages: list[dict],
    system_prompt: Optional[str],
    model: Optional[str],
    temperature: Optional[float],
    max_tokens: Optional[int],
    tools: Optional[list[dict]] = None,
) -> tuple[str, str, str, list[dict]]:
    """Call LLM provider. Returns (content, model, stop_reason, tool_calls)."""
    model = model or config.DEFAULT_LLM_MODEL
    provider = config.DEFAULT_LLM_PROVIDER

    if provider == "gemini":
        return await call_gemini(messages, system_prompt, model, temperature, max_tokens, tools)
    elif provider == "openai":
        return await call_openai(messages, system_prompt, model, temperature, max_tokens, tools)
    elif provider == "anthropic":
        return await call_anthropic(messages, system_prompt, model, temperature, max_tokens, tools)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")


async def call_gemini(messages, system_prompt, model, temperature, max_tokens, tools):
    """Call Google Gemini API with tool support.

    Critical: this used to *build* a tool config + gen_config and then
    silently drop them — Gemini never saw the function declarations,
    so the agent permanently answered "I can't search the internet"
    even though TAVILY_API_KEY and the web_search tool were configured.
    The fix below threads tools / temperature / max_tokens through to
    google-genai's `config=` argument and parses function_call parts
    out of the response so the outer tool loop can execute them.
    """
    try:
        from google import genai
    except ImportError:
        raise ValueError("google-genai not installed. Install with: pip install google-genai")

    if not config.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not configured")

    client = genai.Client(api_key=config.GEMINI_API_KEY)

    # Build the unified config the way google-genai expects.
    gen_config: dict = {
        "system_instruction": system_prompt or "You are a helpful assistant.",
    }
    if temperature is not None:
        gen_config["temperature"] = temperature
    if max_tokens is not None:
        gen_config["max_output_tokens"] = max_tokens

    # Tool declarations — the dict shape google-genai accepts for
    # function calling. The function_calling_config "AUTO" lets Gemini
    # decide on its own when to invoke a tool vs. answer directly.
    if tools:
        gen_config["tools"] = [{
            "function_declarations": [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                }
                for t in tools
            ]
        }]
        gen_config["tool_config"] = {
            "function_calling_config": {"mode": "AUTO"}
        }

    try:
        import asyncio
        # google-genai's generate_content is sync — run in thread.
        def _call():
            return client.models.generate_content(
                model=model,
                contents=[
                    {
                        "role": "user" if m["role"] == "user" else "model",
                        "parts": [{"text": m["content"]}],
                    }
                    for m in messages
                ],
                config=gen_config,
            )

        response = await asyncio.get_event_loop().run_in_executor(None, _call)

        # Parse response: text parts go into `content`, function_call parts
        # become tool_calls for the outer tool loop to dispatch + feed back.
        text_chunks: list[str] = []
        tool_calls: list[dict] = []
        try:
            candidates = getattr(response, "candidates", None) or []
            for cand in candidates:
                content_obj = getattr(cand, "content", None)
                if content_obj is None:
                    continue
                for part in getattr(content_obj, "parts", None) or []:
                    text = getattr(part, "text", None)
                    if text:
                        text_chunks.append(text)
                    fc = getattr(part, "function_call", None)
                    if fc is not None and getattr(fc, "name", None):
                        # google-genai returns args as a Mapping; coerce to dict
                        raw_args = getattr(fc, "args", {}) or {}
                        try:
                            args_dict = dict(raw_args)
                        except Exception:
                            args_dict = {}
                        tool_calls.append({
                            "id": f"gemini-{len(tool_calls)}",
                            "name": fc.name,
                            "arguments": args_dict,
                        })
        except Exception as parse_err:
            # Fall back to the legacy convenience accessor if structured
            # parsing throws (older google-genai versions sometimes do).
            logger.debug("Gemini response parse warning: %s", parse_err)

        content = "".join(text_chunks) if text_chunks else (response.text or "")
        stop_reason = "tool_calls" if tool_calls else "stop"
        logger.info(
            "Gemini raw response: %d chars, %d tool_calls",
            len(content), len(tool_calls),
        )
        return content, model, stop_reason, tool_calls

    except Exception as e:
        logger.error("Gemini API error: %s", e, exc_info=True)
        raise


async def call_openai(messages, system_prompt, model, temperature, max_tokens, tools):
    """Call OpenAI API with tool support."""
    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise ValueError("openai not installed. Install with: pip install openai")

    if not config.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not configured")

    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

    chat_messages = []
    if system_prompt:
        chat_messages.append({"role": "system", "content": system_prompt})
    chat_messages.extend(messages)

    kwargs = {"model": model, "messages": chat_messages}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if tools:
        kwargs["tools"] = [
            {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
            for t in tools
        ]

    try:
        response = await client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        content = choice.message.content or ""
        tool_calls = []
        if choice.message.tool_calls:
            import json
            for tc in choice.message.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": json.loads(tc.function.arguments),
                })
        stop_reason = "tool_calls" if tool_calls else (choice.finish_reason or "stop")
        return content, model, stop_reason, tool_calls
    except Exception as e:
        logger.error("OpenAI API error: %s", e)
        raise


async def call_anthropic(messages, system_prompt, model, temperature, max_tokens, tools):
    """Call Anthropic Claude API with tool support."""
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise ValueError("anthropic not installed. Install with: pip install anthropic")

    if not config.ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY not configured")

    client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

    kwargs = {"model": model, "messages": messages, "max_tokens": max_tokens or 4096}
    if system_prompt:
        kwargs["system"] = system_prompt
    if temperature is not None:
        kwargs["temperature"] = temperature
    if tools:
        kwargs["tools"] = [
            {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
            for t in tools
        ]

    try:
        response = await client.messages.create(**kwargs)
        text_parts = []
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input,
                })
        content = "\n".join(text_parts)
        stop_reason = "tool_calls" if tool_calls else (response.stop_reason or "stop")
        return content, model, stop_reason, tool_calls
    except Exception as e:
        logger.error("Anthropic API error: %s", e)
        raise


# ───────────────────────────────────────────────────────────────────────────
# Route with Tool Loop
# ───────────────────────────────────────────────────────────────────────────


@router.post("/chat", response_model=LLMChatResponse)
async def llm_chat(
    request: LLMChatRequest,
    current_user: str = Depends(get_current_user),
) -> LLMChatResponse:
    """Chat with LLM, executing tools server-side when needed.

    The server runs a tool loop: if the LLM requests a tool call (web search,
    URL read), the server executes it and feeds the result back to the LLM.
    This repeats up to MAX_TOOL_ROUNDS times until the LLM gives a final text response.
    """
    if not check_rate_limit(
        current_user, "/api/v1/llm/chat",
        config.RATE_LIMIT_LLM_REQUESTS_PER_MINUTE,
    ):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")

    # Run quick validation OUTSIDE the broad try/except below so structured
    # 4xx responses (413, 400, …) aren't accidentally swallowed and remapped
    # to 500 by the generic exception handler.
    _validate_attachment_total(request.attachments)

    # ── Attachment distillation ──────────────────────────────────────
    # If the user attached files, run them through the distiller BEFORE
    # the main chat call. We replace each attachment's content_text with
    # the distilled summary so the model sees a curated view (saves tokens
    # AND lets future turns reference these files even if not re-attached).
    summaries: list[AttachmentSummary] = []
    if request.attachments:
        from nexus_server.attachment_distiller import distill_attachment
        from nexus_server import files as files_mod

        # Resolve any attachments that reference uploaded files by id —
        # thin-client path. We swap content_base64 in for downstream
        # processing so the rest of the loop stays unchanged.
        ids_to_resolve = [a.file_id for a in request.attachments if a.file_id]
        resolved_by_id: dict[str, dict] = {}
        if ids_to_resolve:
            for row in files_mod.resolve_files(current_user, ids_to_resolve):
                raw = files_mod.read_file_bytes(row["disk_path"])
                if raw is None:
                    continue
                import base64 as _b64
                resolved_by_id[row["file_id"]] = {
                    "name": row["name"],
                    "mime": row["mime"],
                    "size_bytes": row["size_bytes"],
                    "content_base64": _b64.b64encode(raw).decode("ascii"),
                }

        distilled_attachments: list[Attachment] = []
        for att in request.attachments:
            if att.file_id and att.file_id in resolved_by_id:
                r = resolved_by_id[att.file_id]
                att = Attachment(
                    name=r["name"],
                    mime=r["mime"],
                    size_bytes=r["size_bytes"],
                    content_text=None,
                    content_base64=r["content_base64"],
                )
            try:
                summary, source = await distill_attachment(
                    name=att.name,
                    mime=att.mime,
                    size_bytes=att.size_bytes,
                    content_text=att.content_text,
                    content_base64=att.content_base64,
                    llm_fn=call_llm,
                )
            except Exception as e:
                logger.error("Distill failed for %s: %s", att.name, e)
                summary, source = (
                    f"[Could not distill {att.name}: {e}]",
                    "error",
                )
            # Phase B: persistence to sync_events removed. The summary
            # rides back inline in the response; if the desktop wants
            # historical attachment records, twin's own EventLog
            # captures them via the chat flow's event_log.append.
            summaries.append(AttachmentSummary(
                name=att.name,
                mime=att.mime,
                size_bytes=att.size_bytes,
                summary=summary,
                source=source,
                sync_id=None,
            ))
            # Replace the attachment's payload with the distilled view for
            # the actual chat call: the model sees a curated summary, not
            # the raw bytes. This saves tokens and gives a stable reference.
            distilled_attachments.append(Attachment(
                name=att.name,
                mime=att.mime,
                size_bytes=att.size_bytes,
                content_text=summary,
                content_base64=None,
            ))
        # Substitute distilled attachments in for fold-in
        attachments_for_fold = distilled_attachments
    else:
        attachments_for_fold = []

    try:
        messages = [{"role": msg.role, "content": msg.content} for msg in request.messages]
        # Fold (now distilled) attachments into the last user message
        messages = _fold_attachments_into_messages(messages, attachments_for_fold)
        if attachments_for_fold:
            logger.info(
                "Folded %d distilled attachments into chat for user %s",
                len(attachments_for_fold),
                current_user,
            )

        # ── Production path: twin.chat ─────────────────────────────
        # Twin owns the full Nexus 9-step (contract pre-check → DPM
        # projection → LLM → contract post-check → drift → event_log →
        # background evolution). Twin EventLog writes are mirrored to
        # sync_events by twin_manager._build_on_event so existing
        # /sync/anchors, /agent/timeline, /agent/memories endpoints
        # keep working without code changes.
        #
        # S1 (server cleanup): the previous "fall back to legacy LLM
        # gateway when twin throws" path is GONE. Twin failures surface
        # as 502 to the caller — better than silently producing answers
        # the agent's contract / drift / memory will never see. The
        # legacy direct-LLM tool loop below this block is *only* taken
        # when USE_TWIN=0 (i.e. tests mocking call_llm); in production
        # USE_TWIN=1 means we never reach it. S5/S6 will retire that
        # code entirely once tests migrate to twin stubs.
        if _twin_enabled():
            last_user_msg = next(
                (m["content"] for m in reversed(messages)
                 if m.get("role") == "user"),
                "",
            )
            if not last_user_msg:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No user message in chat request",
                )

            from nexus_server.twin_manager import get_twin
            try:
                twin = await get_twin(current_user)
                reply = await twin.chat(last_user_msg)
            except HTTPException:
                raise
            except Exception as twin_err:
                logger.exception(
                    "Twin chat failed for %s: %s", current_user, twin_err,
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Twin chat error: {twin_err}",
                )

            logger.info(
                "Twin chat: %d-char reply for user %s",
                len(reply or ""), current_user,
            )
            return LLMChatResponse(
                role="assistant",
                content=reply or "",
                model="twin",
                stop_reason="stop",
                tool_calls_executed=[],
                attachment_summaries=summaries,
            )

        # ── Legacy direct-LLM gateway (test-only after S1) ─────────
        # Reachable only when USE_TWIN=0. Will be removed in S5/S6
        # along with attachment_distiller, memory_service, and the
        # remaining server-side intelligence layer.
        tools = TOOL_DEFINITIONS if request.enable_tools else None
        tools_executed: list[str] = []

        for round_num in range(MAX_TOOL_ROUNDS + 1):
            content, model_used, stop_reason, tool_calls = await call_llm(
                messages, request.system_prompt, request.model,
                request.temperature, request.max_tokens, tools,
            )

            if not tool_calls:
                # Final response — no more tool calls
                logger.info("LLM reply (%d chars) for user %s via %s", len(content or ""), current_user, model_used)

                # NOTE (S3): the legacy server-side memory_service.maybe_compact
                # scheduler used to fire here. It was removed when we deleted
                # memory_service.py — twin owns compaction now via SDK's
                # EventLogCompactor + CuratedMemory. This branch is only
                # reachable in tests (USE_TWIN=0); in production every chat
                # goes through twin and never touches this code.

                return LLMChatResponse(
                    role="assistant",
                    content=content,
                    model=model_used,
                    stop_reason=stop_reason,
                    tool_calls_executed=tools_executed,
                    attachment_summaries=summaries,
                )

            # Execute tool calls and append results
            for tc in tool_calls:
                logger.info("Executing tool: %s(%s)", tc["name"], tc["arguments"])
                result = await execute_tool(tc["name"], tc["arguments"])
                tools_executed.append(tc["name"])

                # Append assistant's tool request + tool result to messages
                messages.append({"role": "assistant", "content": f"[Calling {tc['name']}]"})
                messages.append({"role": "user", "content": f"[Tool result for {tc['name']}]:\n{result}"})

            logger.info("Tool round %d complete, %d tools executed", round_num + 1, len(tool_calls))

        # Exhausted rounds — return whatever we have
        return LLMChatResponse(
            role="assistant",
            content=content or "I ran out of tool execution rounds. Please try a simpler question.",
            model=model_used,
            stop_reason="max_rounds",
            tool_calls_executed=tools_executed,
            attachment_summaries=summaries,
        )

    except HTTPException:
        # Preserve structured error codes (400, 502, …) — without this,
        # the catch-all below would remap a clean 502 ("Twin chat error")
        # into a misleading 500.
        raise
    except Exception as e:
        import traceback
        logger.error("LLM chat error: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"LLM call failed: {e}")
