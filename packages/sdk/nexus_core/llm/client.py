"""
LLM abstraction — pluggable language model interface.

Supports Google Gemini (default), OpenAI GPT, and Anthropic Claude.

Tool Use:
  When tools are provided, chat() returns text as before — tool calls are
  handled internally via a multi-turn loop. The LLM can call tools multiple
  times before producing a final text response.

  chat_with_tools() exposes the raw tool loop for callers that need to
  intercept tool calls (e.g., for logging or custom routing).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional, TYPE_CHECKING

from .providers import LLMProvider

if TYPE_CHECKING:
    from nexus_core.tools.base import ToolCall, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

# Maximum tool call rounds to prevent infinite loops
MAX_TOOL_ROUNDS = 10


class LLMClient:
    """Unified LLM client interface with function calling support."""

    def __init__(self, provider: LLMProvider, api_key: str, model: str):
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return

        if self.provider == LLMProvider.GEMINI:
            try:
                from google import genai
                self._client = genai.Client(api_key=self.api_key)
            except ImportError:
                raise ImportError("pip install google-genai")
        elif self.provider == LLMProvider.ANTHROPIC:
            try:
                import anthropic
                self._client = anthropic.AsyncAnthropic(api_key=self.api_key)
            except ImportError:
                raise ImportError("pip install anthropic")
        else:
            try:
                import openai
                self._client = openai.AsyncOpenAI(api_key=self.api_key)
            except ImportError:
                raise ImportError("pip install openai")

    # ── Main chat interface ───────────────────────────────────────

    async def chat(
        self,
        messages: list[dict],
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False,
        tools: Optional["ToolRegistry"] = None,
    ) -> str:
        """Chat with the LLM, optionally using tools.

        When tools are provided:
          1. LLM receives tool definitions alongside the conversation
          2. If LLM requests a tool call, it's executed automatically
          3. Tool results are fed back to the LLM
          4. Loop continues until LLM produces a text response (or MAX_TOOL_ROUNDS)

        When no tools are provided, behaves exactly as before.
        """
        self._ensure_client()

        if not tools:
            # No tools — use the simple path (unchanged from original)
            return await self._chat_simple(messages, system, temperature, max_tokens, json_mode)

        # Tool-enabled path
        return await self._chat_with_tool_loop(
            messages, system, temperature, max_tokens, tools,
        )

    async def _chat_simple(
        self, messages, system, temperature, max_tokens, json_mode=False,
    ) -> str:
        """Simple chat without tools — original behavior."""
        if self.provider == LLMProvider.GEMINI:
            return await self._chat_gemini(messages, system, temperature, max_tokens, json_mode=json_mode)
        elif self.provider == LLMProvider.ANTHROPIC:
            return await self._chat_anthropic(messages, system, temperature, max_tokens)
        else:
            return await self._chat_openai(messages, system, temperature, max_tokens)

    # ── Tool Loop ─────────────────────────────────────────────────

    async def _chat_with_tool_loop(
        self,
        messages: list[dict],
        system: str,
        temperature: float,
        max_tokens: int,
        tools: "ToolRegistry",
    ) -> str:
        """Multi-turn tool loop: LLM calls tools, we execute, feed back results.

        Returns the final text response after all tool calls are resolved.
        """
        from nexus_core.tools.base import ToolCall

        tool_defs = tools.get_definitions()
        logger.info(
            "Entering tool loop with %d tool(s): %s",
            len(tool_defs), [t["name"] for t in tool_defs],
        )
        # Maintain a working copy of messages for the tool loop
        working_messages = list(messages)
        tool_calls_log: list[dict] = []

        for round_num in range(MAX_TOOL_ROUNDS):
            # Call LLM with tools
            response = await self._call_with_tools(
                working_messages, system, temperature, max_tokens, tool_defs,
            )

            # Check if LLM wants to call tools
            if not response.get("tool_calls"):
                # LLM produced a text response — we're done
                return response.get("text", "")

            # Execute each tool call
            for tc in response["tool_calls"]:
                tool_call = ToolCall(
                    id=tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                    name=tc["name"],
                    arguments=tc.get("arguments", {}),
                )

                logger.info("Tool call [round %d]: %s(%s)", round_num + 1, tool_call.name, tool_call.arguments)
                result = await tools.execute(tool_call)
                tool_calls_log.append({
                    "tool": tool_call.name,
                    "args": tool_call.arguments,
                    "result": result.to_str()[:500],
                    "success": result.success,
                })

                # Append tool call + result to working messages
                # Each provider has its own format — _call_with_tools handles normalization
                working_messages.append({
                    "role": "assistant",
                    "tool_calls": [tc],
                })
                working_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_call.name,
                    "content": result.to_str(),
                })

            # Continue loop — LLM may want more tool calls or produce final text

        # Hit max rounds — force a text response
        logger.warning("Tool loop hit max rounds (%d)", MAX_TOOL_ROUNDS)
        return response.get("text", "[Tool loop reached maximum rounds]")

    async def _call_with_tools(
        self,
        messages: list[dict],
        system: str,
        temperature: float,
        max_tokens: int,
        tool_defs: list[dict],
    ) -> dict:
        """Call LLM with tool definitions. Returns unified response format.

        Returns:
            {
                "text": "response text" or "",
                "tool_calls": [{"id": "...", "name": "...", "arguments": {...}}, ...] or []
            }
        """
        if self.provider == LLMProvider.GEMINI:
            return await self._call_gemini_tools(messages, system, temperature, max_tokens, tool_defs)
        elif self.provider == LLMProvider.ANTHROPIC:
            return await self._call_anthropic_tools(messages, system, temperature, max_tokens, tool_defs)
        else:
            return await self._call_openai_tools(messages, system, temperature, max_tokens, tool_defs)

    # ── Provider-specific tool implementations ────────────────────

    async def _call_gemini_tools(
        self, messages, system, temperature, max_tokens, tool_defs,
    ) -> dict:
        """Gemini function calling.

        Key implementation details:
          - Parameters must be converted from JSON Schema dicts to types.Schema
            objects — Gemini silently ignores raw dicts, causing the model to
            generate text about tools instead of actually calling them.
          - tool_config=AUTO tells Gemini it MAY call functions (default behavior
            can be text-only on some model versions).
        """
        from google.genai import types
        import asyncio

        # Convert tool definitions to Gemini format with proper Schema objects
        gemini_tools = []
        for td in tool_defs:
            schema = self._json_schema_to_gemini(td["parameters"])
            gemini_tools.append(types.FunctionDeclaration(
                name=td["name"],
                description=td["description"],
                parameters=schema,
            ))
            logger.debug("Gemini tool registered: %s", td["name"])

        # Convert messages to Gemini format (filter out tool-loop messages)
        contents = self._messages_to_gemini_contents(messages)

        config = types.GenerateContentConfig(
            system_instruction=system or None,
            temperature=temperature,
            max_output_tokens=max_tokens,
            tools=[types.Tool(function_declarations=gemini_tools)],
            # AUTO = model decides whether to call a function or respond with text
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode="AUTO",
                ),
            ),
        )

        def _call():
            try:
                response = self._client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
                return response
            except Exception as e:
                logger.error("Gemini tool call failed: %s", e)
                raise

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, _call)

        # Parse Gemini response for tool calls or text
        if not response.candidates:
            logger.warning("Gemini returned no candidates (tool call path)")
            return {"text": "", "tool_calls": []}

        candidate = response.candidates[0]
        tool_calls = []
        text_parts = []

        for part in candidate.content.parts:
            if hasattr(part, 'function_call') and part.function_call:
                fc = part.function_call
                args = dict(fc.args) if fc.args else {}
                tool_calls.append({
                    "id": f"gemini_{uuid.uuid4().hex[:8]}",
                    "name": fc.name,
                    "arguments": args,
                })
                logger.info("Gemini requested tool: %s(%s)", fc.name, args)
            elif hasattr(part, 'text') and part.text:
                text_parts.append(part.text)

        if not tool_calls:
            logger.debug("Gemini chose text response (no tool calls)")

        return {
            "text": "\n".join(text_parts) if text_parts else "",
            "tool_calls": tool_calls,
        }

    @staticmethod
    def _json_schema_to_gemini(schema: dict) -> dict:
        """Convert a JSON Schema dict to Gemini-compatible schema dict.

        Gemini's FunctionDeclaration.parameters accepts a dict but requires
        OpenAPI-style schema with specific conventions:
          - No 'required' at property level (must be at object level)
          - 'type' values must be uppercase strings: STRING, INTEGER, OBJECT, etc.
          - Nested objects must also follow this format

        Returns a cleaned dict that Gemini's API will interpret correctly.
        """
        TYPE_MAP = {
            "string": "STRING",
            "integer": "INTEGER",
            "number": "NUMBER",
            "boolean": "BOOLEAN",
            "array": "ARRAY",
            "object": "OBJECT",
        }

        def _convert(s: dict) -> dict:
            if not isinstance(s, dict):
                return s

            result = {}
            schema_type = s.get("type", "object")
            result["type"] = TYPE_MAP.get(schema_type, schema_type.upper())

            if "description" in s:
                result["description"] = s["description"]

            if "properties" in s:
                result["properties"] = {
                    k: _convert(v) for k, v in s["properties"].items()
                }

            if "required" in s:
                result["required"] = s["required"]

            if "items" in s:
                result["items"] = _convert(s["items"])

            if "enum" in s:
                result["enum"] = s["enum"]

            return result

        return _convert(schema)

    def _messages_to_gemini_contents(self, messages: list[dict]) -> list:
        """Convert unified messages (including tool results) to Gemini Content format."""
        from google.genai import types

        contents = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            role = msg.get("role", "user")

            if role == "tool":
                # Gemini expects tool results as FunctionResponse in a "user" turn
                # (this is how Gemini's multi-turn function calling works)
                func_responses = []
                while i < len(messages) and messages[i].get("role") == "tool":
                    tmsg = messages[i]
                    func_responses.append(types.Part(
                        function_response=types.FunctionResponse(
                            name=tmsg.get("name", "unknown"),
                            response={"result": tmsg.get("content", "")},
                        )
                    ))
                    i += 1
                contents.append(types.Content(role="user", parts=func_responses))
                continue

            elif role == "assistant" and msg.get("tool_calls"):
                # Assistant message with tool calls → model turn with FunctionCall parts
                fc_parts = []
                for tc in msg["tool_calls"]:
                    fc_parts.append(types.Part(
                        function_call=types.FunctionCall(
                            name=tc["name"],
                            args=tc.get("arguments", {}),
                        )
                    ))
                contents.append(types.Content(role="model", parts=fc_parts))
                i += 1
                continue

            else:
                # Regular text message
                gemini_role = "user" if role == "user" else "model"
                content_text = msg.get("content", "")
                if content_text:
                    contents.append(types.Content(
                        role=gemini_role,
                        parts=[types.Part(text=content_text)],
                    ))
                i += 1

        return contents

    async def _call_openai_tools(
        self, messages, system, temperature, max_tokens, tool_defs,
    ) -> dict:
        """OpenAI function calling."""
        # Convert tool definitions to OpenAI format
        openai_tools = []
        for td in tool_defs:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": td["name"],
                    "description": td["description"],
                    "parameters": td["parameters"],
                },
            })

        # Build messages with system prompt
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})

        for msg in messages:
            if msg.get("role") == "tool":
                # OpenAI expects tool results as role=tool with tool_call_id
                full_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                })
            elif msg.get("role") == "assistant" and msg.get("tool_calls"):
                # OpenAI assistant message with tool_calls
                openai_tcs = []
                for tc in msg["tool_calls"]:
                    openai_tcs.append({
                        "id": tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("arguments", {})),
                        },
                    })
                full_messages.append({
                    "role": "assistant",
                    "tool_calls": openai_tcs,
                })
            else:
                full_messages.append({"role": msg["role"], "content": msg.get("content", "")})

        response = await self._client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=openai_tools,
        )

        choice = response.choices[0]
        message = choice.message

        # Parse tool calls
        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": args,
                })

        return {
            "text": message.content or "",
            "tool_calls": tool_calls,
        }

    async def _call_anthropic_tools(
        self, messages, system, temperature, max_tokens, tool_defs,
    ) -> dict:
        """Anthropic Claude function calling (tool_use)."""
        # Convert tool definitions to Anthropic format
        anthropic_tools = []
        for td in tool_defs:
            anthropic_tools.append({
                "name": td["name"],
                "description": td["description"],
                "input_schema": td["parameters"],
            })

        # Build messages — Anthropic has specific format for tool results
        api_messages = []
        i = 0
        while i < len(messages):
            msg = messages[i]

            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                # Assistant with tool calls → content blocks
                content_blocks = []
                for tc in msg["tool_calls"]:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:8]}"),
                        "name": tc["name"],
                        "input": tc.get("arguments", {}),
                    })
                api_messages.append({"role": "assistant", "content": content_blocks})
                i += 1

            elif msg.get("role") == "tool":
                # Anthropic: tool results go in a "user" message with tool_result blocks
                tool_results = []
                while i < len(messages) and messages[i].get("role") == "tool":
                    tmsg = messages[i]
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tmsg.get("tool_call_id", ""),
                        "content": tmsg.get("content", ""),
                    })
                    i += 1
                api_messages.append({"role": "user", "content": tool_results})

            else:
                api_messages.append({"role": msg["role"], "content": msg.get("content", "")})
                i += 1

        response = await self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system or "You are a helpful assistant.",
            messages=api_messages,
            tools=anthropic_tools,
        )

        # Parse response — Anthropic returns content blocks
        tool_calls = []
        text_parts = []

        for block in response.content:
            if block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input,
                })
            elif block.type == "text":
                text_parts.append(block.text)

        return {
            "text": "\n".join(text_parts) if text_parts else "",
            "tool_calls": tool_calls,
        }

    # ── Simple provider implementations (no tools) ────────────────

    async def _chat_gemini(
        self, messages, system, temperature, max_tokens,
        json_mode: bool = False,
    ) -> str:
        from google.genai import types
        import asyncio

        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(types.Content(
                role=role,
                parts=[types.Part(text=msg["content"])],
            ))

        config = types.GenerateContentConfig(
            system_instruction=system or None,
            temperature=temperature,
            max_output_tokens=max_tokens,
            # Force structured JSON output when requested — prevents empty
            # responses from Gemini on extraction prompts.
            response_mime_type="application/json" if json_mode else None,
        )

        def _call():
            response = self._client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )
            # Gemini can return None/empty when blocked by safety filters
            # or when the model has nothing to say. Return empty string
            # so callers can handle it gracefully.
            text = response.text
            if text is None:
                # Check if blocked by safety filter
                if hasattr(response, 'candidates') and response.candidates:
                    candidate = response.candidates[0]
                    reason = getattr(candidate, 'finish_reason', None)
                    if reason and str(reason) not in ('STOP', '1', 'FinishReason.STOP'):
                        logger.debug(
                            "Gemini response blocked: finish_reason=%s", reason
                        )
                return ""
            return text

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _call)

    async def _chat_anthropic(self, messages, system, temperature, max_tokens) -> str:
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system or "You are a helpful assistant.",
            messages=messages,
        )
        return response.content[0].text

    async def _chat_openai(self, messages, system, temperature, max_tokens) -> str:
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        response = await self._client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content

    async def complete(
        self,
        prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """Single-turn completion for evolution prompts.

        Does NOT use json_mode — Gemini's json_mode (response_mime_type=
        "application/json") causes output truncation at ~200-300 chars on
        some prompts. Since all callers already use _robust_json_parse()
        which handles markdown fences and prose wrapping, plain text mode
        is both more reliable and produces longer, complete responses.

        max_tokens defaults to 4096 (up from 2048) to give Gemini enough
        room for skill detection responses that enumerate multiple skills.
        """
        return await self.chat(
            messages=[{"role": "user", "content": prompt}],
            system="You are a precise JSON extraction engine. Return only valid JSON, no markdown fences.",
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=False,
        )

    async def close(self):
        if self._client and hasattr(self._client, "close"):
            await self._client.close()
        self._client = None
