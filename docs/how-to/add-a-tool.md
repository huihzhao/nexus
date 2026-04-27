# How-to: add a new tool the agent can call

A "tool" is a function the LLM can invoke during chat — `web_search`,
`read_url`, `generate_file`, `read_uploaded_file` are the built-ins.
This recipe walks through adding a new one (we'll do `get_weather` as
the running example).

## Decide where the tool lives

Three options, picked from cheapest to heaviest:

| You want… | Put it in |
|---|---|
| A simple Python function with no chain / persistence concerns | `nexus_core` (SDK) at `packages/sdk/nexus_core/tools/` |
| Something specific to the DigitalTwin agent (uses twin state, evolution, etc.) | `nexus` (framework) at `packages/nexus/nexus/extensions/tools.py` |
| A skill loaded from a YAML registry at runtime | Skills hub (no code change in this repo) |
| An external service via Model Context Protocol | MCP server (no code change in this repo) |

For our `get_weather` example we'll go with option 1 — it's a pure
function, doesn't need twin state, useful to anyone using the SDK.

## Step 1 — implement the tool class

```python
# packages/sdk/nexus_core/tools/weather.py

from typing import Any
from .base import BaseTool, ToolResult


class WeatherTool(BaseTool):
    """Look up current weather for a city via OpenWeather."""

    name = "get_weather"
    description = (
        "Fetch the current weather for a named city. Returns a concise "
        "human-readable summary (temperature, conditions, wind)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "City name. Country / state optional, "
                               "e.g. 'Paris' or 'Paris, TX'.",
            },
            "units": {
                "type": "string",
                "enum": ["metric", "imperial"],
                "description": "Temperature units. Default: metric.",
            },
        },
        "required": ["city"],
    }

    def __init__(self, api_key: str = ""):
        self._api_key = api_key

    async def execute(self, city: str, units: str = "metric") -> ToolResult:
        if not self._api_key:
            return ToolResult.error(
                "Weather lookup unavailable: OPENWEATHER_API_KEY "
                "not configured."
            )

        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://api.openweathermap.org/data/2.5/weather",
                params={"q": city, "units": units, "appid": self._api_key},
            )
        if r.status_code != 200:
            return ToolResult.error(
                f"Weather API returned HTTP {r.status_code} for "
                f"city={city!r}: {r.text[:200]}"
            )
        data = r.json()
        unit_label = "°C" if units == "metric" else "°F"
        wind_label = "m/s" if units == "metric" else "mph"
        summary = (
            f"{data.get('name')}: "
            f"{data['main']['temp']:.1f}{unit_label}, "
            f"{data['weather'][0]['description']}, "
            f"wind {data['wind']['speed']:.1f} {wind_label}"
        )
        return ToolResult.ok(summary)
```

A few things to note:

- **`name`** is the function name the LLM will see. Stable across
  versions — once shipped, don't rename it.
- **`description`** is the function description the LLM uses for
  tool-selection. Be concrete about what it does AND what it costs / what
  inputs are required. The model uses this prose to decide whether to
  call you.
- **`parameters`** is JSON Schema. Be specific. Required fields go in
  `required`; everything else is opt-in with a default in `execute`.
- **`execute`** is async. Always return a `ToolResult` — never raise
  for a "I couldn't fetch this" case. Raises propagate as fatal errors
  and may abort the turn.
- API keys go through `__init__`, never read from env inside `execute`
  (testability + dependency injection).

## Step 2 — export it from the package

```python
# packages/sdk/nexus_core/tools/__init__.py

from .web_search import WebSearchTool
from .url_reader import URLReaderTool
from .weather import WeatherTool          # ← add

__all__ = [
    "BaseTool", "ToolResult", "ToolCall", "ToolRegistry",
    "WebSearchTool", "URLReaderTool", "WeatherTool",
]
```

If you want it importable from the package root:

```python
# packages/sdk/nexus_core/__init__.py

from .tools import BaseTool, ToolResult, WebSearchTool, URLReaderTool, WeatherTool
```

## Step 3 — register it in the twin

The DigitalTwin's `_register_default_tools` (in
`packages/nexus/nexus/twin.py`) wires tools into the LLM at twin
creation time. Two places to wire:

```python
# packages/nexus/nexus/twin.py

@classmethod
async def create(
    cls,
    ...,
    openweather_api_key: str = "",   # ← new kwarg
    ...,
):
    ...
    if enable_tools:
        twin._register_default_tools(
            tavily_api_key=tavily_api_key,
            jina_api_key=jina_api_key,
            openweather_api_key=openweather_api_key,   # ← thread through
        )

def _register_default_tools(
    self,
    tavily_api_key: str = "",
    jina_api_key: str = "",
    openweather_api_key: str = "",   # ← new
) -> None:
    ...
    if openweather_api_key:
        from nexus_core.tools import WeatherTool
        self.tools.register(WeatherTool(api_key=openweather_api_key))
```

The pattern: pass the API key through twin → registers the tool only
when the key is set. Missing key → tool isn't registered, LLM doesn't
even see it as an option.

## Step 4 — surface the env var in server config

```python
# packages/server/nexus_server/config.py

class ServerConfig:
    ...
    OPENWEATHER_API_KEY: Optional[str] = os.getenv("OPENWEATHER_API_KEY")
```

```python
# packages/server/nexus_server/twin_manager.py
# in _create_twin:
twin = await DigitalTwin.create(
    ...,
    tavily_api_key=config.TAVILY_API_KEY or "",
    openweather_api_key=config.OPENWEATHER_API_KEY or "",   # ← thread through
    ...,
)
```

Now operators add `OPENWEATHER_API_KEY=...` to their `.env` and the
weather tool becomes available to all their users' twins.

## Step 5 — write a test

```python
# packages/sdk/tests/test_weather.py

import asyncio
import pytest
from unittest.mock import patch, MagicMock
from nexus_core.tools.weather import WeatherTool


def test_weather_tool_metadata():
    tool = WeatherTool(api_key="fake")
    assert tool.name == "get_weather"
    assert "city" in tool.parameters["properties"]
    assert tool.parameters["required"] == ["city"]


def test_weather_tool_returns_error_when_no_key():
    tool = WeatherTool(api_key="")
    result = asyncio.run(tool.execute(city="Paris"))
    assert not result.success
    assert "not configured" in result.output


def test_weather_tool_happy_path():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "name": "Paris",
        "main": {"temp": 22.5},
        "weather": [{"description": "clear sky"}],
        "wind": {"speed": 3.0},
    }
    with patch("httpx.AsyncClient.get", return_value=fake_response):
        result = asyncio.run(WeatherTool(api_key="fake").execute(city="Paris"))
    assert result.success
    assert "Paris" in result.output
    assert "22.5" in result.output
```

Three test shapes: **metadata** (name / parameters / required), **error
path** (no API key → graceful), **happy path** (API call works).

## Step 6 — verify the LLM picks it up

End-to-end smoke test:

```bash
# packages/server
OPENWEATHER_API_KEY=your-key uv run nexus-server
```

Then in the desktop, ask the agent something the tool would help with:

> "What's the weather in Tokyo right now?"

In the server logs you should see a `tool_call` for `get_weather` with
`city=Tokyo`, then the tool result fed back, then the agent's response
incorporating the data.

## Where things commonly go wrong

- **Tool registered but LLM never calls it.** Usually a prompt issue —
  the description doesn't make it obvious *when* to use this tool. Be
  more concrete: "Use this when the user asks about current weather for
  a specific city" instead of "Look up the weather".
- **Tool calls but parameters are wrong.** Tighten the JSON Schema —
  add `enum`, `pattern`, `minimum`/`maximum`. The LLM follows the
  schema.
- **Tool fires but the response says "I can't access weather data".**
  The LLM didn't see the result. Check that you returned
  `ToolResult.ok(summary)`, not just `summary` (string).
- **Tool registered for some users, not others.** Check the twin
  cache — twins created before you set the env var won't pick up the
  tool until they're evicted (30 min idle) and recreated. Restart the
  server to be sure.

## Related concepts

- [DPM](../concepts/dpm.md) — tool calls land in the event log as
  events, eligible for compaction
- [ABC](../concepts/abc.md) — pre/post-check runs around the chat
  containing tool calls; you can write contract rules about tool use
- [data-flow](../concepts/data-flow.md) — where in the 9-step flow a
  tool call happens (step 4, between LLM emit and post-check)
