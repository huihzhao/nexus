# `chat/` — chat path + attachments

What's in here:

| File | Purpose |
|---|---|
| `routes.py` | `POST /api/v1/llm/chat`. After S1 it routes through `TwinManager.get_twin(user_id).chat(...)` exclusively (no fallback). Tools execution loop is the legacy non-twin code path, reachable only when `USE_TWIN=0` (test-only). |
| `attachments.py` | Thin shim that re-exports SDK's `distill` / `extract_text` under the legacy name `distill_attachment`. After Phase B the persistence half (`record_distilled_event`) is gone — summaries ride back inline in the chat response only. |
| `files.py` | `POST /api/v1/files/upload` — multipart upload endpoint that stores files under `~/.nexus_server/uploads/{user_id}/` and returns a `file_id` referenced by the next `ChatRequest.attachments`. |
| `__init__.py` | Re-exports the public surface (`router`, `files`, `attachments`). |

What the new dev needs to know:

- The chat handler is **synchronous w.r.t. the LLM call**. The 9-step twin chat flow runs inside; this module is a thin HTTP wrapper that calls `await twin.chat(...)`.
- Attachments come in two shapes on the wire: legacy inline (`content_text`/`content_base64`) and modern (`file_id` from a prior `/files/upload`). The handler resolves both into bytes before calling distill.
- Distillation produces an `AttachmentSummary` per attachment that's folded into the user message AND returned inline in the response. Twin's own EventLog captures attachment events as part of the chat flow.

What's NOT in here:

- The tool-call loop (`web_search` / `read_url` / etc.) — that's the SDK's `ToolRegistry` driven by twin during chat.
- Memory / context construction — owned by twin (Nexus's `evolution/projection.py`).
