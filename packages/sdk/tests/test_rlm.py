"""Tests for ``nexus_core.rlm`` — the Recursive Language Model primitive.

Each test mocks the root_llm + sub_llm with deterministic
canned-response callables so we test the *runner mechanics* (loop,
sandbox, budget caps, timeout, code extraction, trajectory
recording) without depending on a real LLM provider.
"""

from __future__ import annotations

import asyncio
import pytest

import nexus_core
from nexus_core.rlm import (
    RLMConfig,
    RLMResult,
    RLMRunner,
    TrajectoryEntry,
    extract_code_block,
    run_rlm,
)


# ── Mock LLM helpers ─────────────────────────────────────────────────


def scripted_root_llm(responses: list[str]):
    """Returns an async callable that replays ``responses`` in order
    across calls. Useful for scripted multi-turn test cases."""
    state = {"i": 0}

    async def _root_llm(messages, system):  # noqa: ARG001
        i = state["i"]
        state["i"] += 1
        if i >= len(responses):
            raise AssertionError(
                f"scripted root_llm ran out of responses at call #{i}; "
                f"the test should have terminated by now (likely missing "
                f"_set_result in one of the scripted code blocks)"
            )
        return responses[i]

    return _root_llm


def scripted_sub_llm(responses: dict[str, str] | list[str]):
    """Returns an async sub_llm callable.

    If responses is a dict, looks up by query string.
    If responses is a list, replays in order.
    """
    if isinstance(responses, dict):
        async def _sub_llm(query: str) -> str:
            return responses.get(query, f"<no match for {query[:30]}>")
        return _sub_llm

    state = {"i": 0}
    async def _sub_llm(query: str) -> str:  # noqa: ARG001
        i = state["i"]
        state["i"] += 1
        return responses[i] if i < len(responses) else f"<exhausted at {i}>"
    return _sub_llm


# ── extract_code_block ────────────────────────────────────────────────


def test_extract_code_block_fenced_with_python_lang():
    text = """Some thinking here.

```python
print("hello")
```

Trailing chatter.
"""
    assert extract_code_block(text) == 'print("hello")'


def test_extract_code_block_fenced_no_lang():
    text = "```\nx = 42\n```"
    assert extract_code_block(text) == "x = 42"


def test_extract_code_block_no_fence_falls_through_to_heuristic():
    """Cheaper LLMs sometimes forget to fence."""
    assert extract_code_block("import re\nresult = re.match('a', 'a')") == (
        "import re\nresult = re.match('a', 'a')"
    )


def test_extract_code_block_empty_when_only_prose():
    assert extract_code_block("Hi, I think we should call _set_result soon.") == ""


def test_extract_code_block_handles_none_or_empty():
    assert extract_code_block("") == ""
    assert extract_code_block(None) == ""


# ── End-to-end: simple termination ────────────────────────────────────


@pytest.mark.asyncio
async def test_runner_terminates_on_set_result():
    """The simplest case: root LLM emits one block that calls
    `_set_result` and we exit cleanly."""
    runner = RLMRunner(
        root_llm=scripted_root_llm([
            '```python\n_set_result("done")\n```',
        ]),
        sub_llm=None,
    )
    result = await runner.run(task="trivial", context_vars={})

    assert isinstance(result, RLMResult)
    assert result.output == "done"
    assert result.iterations_used == 1
    assert result.sub_calls_used == 0
    assert result.truncated is False
    assert result.crashed is False
    assert len(result.trajectory) == 1
    assert result.trajectory[0].error is None


@pytest.mark.asyncio
async def test_runner_can_read_context_vars_via_globals():
    """The whole point: context goes in as REPL globals, not in
    the prompt itself."""
    runner = RLMRunner(
        root_llm=scripted_root_llm([
            '```python\n_set_result(prompt[:5])\n```',
        ]),
        sub_llm=None,
    )
    result = await runner.run(
        task="echo first 5 chars",
        context_vars={"prompt": "hello world"},
    )
    assert result.output == "hello"


# ── Sub-LLM recursion ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_runner_calls_sub_llm_and_uses_its_output():
    """Root LLM writes code that awaits `_sub_llm(...)` and emits
    the result via `_set_result`."""
    runner = RLMRunner(
        root_llm=scripted_root_llm([
            '```python\n'
            'summary = await _sub_llm("summarise this")\n'
            '_set_result(summary)\n'
            '```',
        ]),
        sub_llm=scripted_sub_llm({"summarise this": "It is a thing."}),
    )
    result = await runner.run(task="dummy", context_vars={})
    assert result.output == "It is a thing."
    assert result.sub_calls_used == 1
    assert result.trajectory[0].sub_calls == 1


@pytest.mark.asyncio
async def test_sub_llm_disabled_raises_inside_sandbox():
    """If the runner has sub_llm=None, the root LLM's _sub_llm call
    surfaces as an exec error, NOT as a runner crash."""
    runner = RLMRunner(
        root_llm=scripted_root_llm([
            '```python\nresult = await _sub_llm("x")\n```',
            # Recovery turn after the error
            '```python\n_set_result("recovered without sub_llm")\n```',
        ]),
        sub_llm=None,
    )
    result = await runner.run(task="x", context_vars={})
    assert result.iterations_used == 2
    assert result.trajectory[0].error is not None
    assert "sub_llm is disabled" in result.trajectory[0].error
    assert result.output == "recovered without sub_llm"


# ── Budget caps ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_max_sub_calls_budget_enforced():
    """Once max_sub_calls is hit, further sub_llm calls raise."""
    runner = RLMRunner(
        root_llm=scripted_root_llm([
            # First iteration: hit cap (2 calls allowed)
            '```python\n'
            'a = await _sub_llm("a")\n'
            'b = await _sub_llm("b")\n'
            '_set_result(a + " | " + b)\n'
            '```',
        ]),
        sub_llm=scripted_sub_llm({"a": "AA", "b": "BB"}),
        config=RLMConfig(max_sub_calls=2),
    )
    result = await runner.run(task="dummy", context_vars={})
    assert result.output == "AA | BB"
    assert result.sub_calls_used == 2


@pytest.mark.asyncio
async def test_max_sub_calls_exceeded_surfaces_as_exec_error():
    """Trying a third sub_llm call when budget is 2 → the exec
    raises, captured in trajectory[i].error."""
    runner = RLMRunner(
        root_llm=scripted_root_llm([
            '```python\n'
            'a = await _sub_llm("a")\n'
            'b = await _sub_llm("b")\n'
            'c = await _sub_llm("c")\n'  # ← over budget
            '_set_result(a + b + c)\n'
            '```',
            # Recovery turn
            '```python\n_set_result("partial: " + a + b)\n```',
        ]),
        sub_llm=scripted_sub_llm({"a": "AA", "b": "BB", "c": "CC"}),
        config=RLMConfig(max_sub_calls=2),
    )
    result = await runner.run(task="dummy", context_vars={})
    # First iter raised → trajectory captures error
    assert "budget exhausted" in (result.trajectory[0].error or "")
    # Recovery iter ran with vars from iter 1 still in scope
    # (a, b were assigned before the c-call raised)
    assert result.output == "partial: AABB"
    assert result.sub_calls_used == 2  # not 3


@pytest.mark.asyncio
async def test_max_iterations_truncates():
    """If the root LLM never calls _set_result, we stop after
    max_iterations and return truncated=True."""
    runner = RLMRunner(
        root_llm=scripted_root_llm([
            '```python\nx = 1\n```',
            '```python\ny = 2\n```',
            '```python\nz = 3\n```',
        ]),
        sub_llm=None,
        config=RLMConfig(max_iterations=3),
    )
    result = await runner.run(task="never finishes", context_vars={})
    assert result.iterations_used == 3
    assert result.truncated is True
    assert result.output == ""


# ── Trajectory recording ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trajectory_records_stdout_and_stderr():
    runner = RLMRunner(
        root_llm=scripted_root_llm([
            '```python\n'
            'print("hello stdout")\n'
            'import sys; print("err line", file=sys.stderr)\n'
            '_set_result("ok")\n'
            '```',
        ]),
        sub_llm=None,
    )
    result = await runner.run(task="x", context_vars={})
    entry = result.trajectory[0]
    assert "hello stdout" in entry.stdout
    assert "err line" in entry.stderr


@pytest.mark.asyncio
async def test_trajectory_records_syntax_error():
    runner = RLMRunner(
        root_llm=scripted_root_llm([
            # Real syntax error — unterminated parenthesis.
            '```python\ndef f(:\n    pass\n```',
            '```python\n_set_result("recovered")\n```',
        ]),
        sub_llm=None,
    )
    result = await runner.run(task="x", context_vars={})
    assert result.trajectory[0].error is not None
    assert "SyntaxError" in result.trajectory[0].error
    assert result.output == "recovered"


@pytest.mark.asyncio
async def test_trajectory_records_runtime_error():
    runner = RLMRunner(
        root_llm=scripted_root_llm([
            '```python\nx = 1 / 0\n```',
            '```python\n_set_result("recovered")\n```',
        ]),
        sub_llm=None,
    )
    result = await runner.run(task="x", context_vars={})
    assert "ZeroDivisionError" in (result.trajectory[0].error or "")
    assert result.output == "recovered"


# ── Pattern (a) from the paper: regex / slice the prompt ─────────────


@pytest.mark.asyncio
async def test_pattern_a_regex_filtering():
    """RLM trajectory pattern (a): slice the prompt with code,
    then extract the answer without sub-LLM at all."""
    long_prompt = (
        "lorem ipsum " * 100
        + "\nThe secret password is BANANA42.\n"
        + "lorem ipsum " * 100
    )
    runner = RLMRunner(
        root_llm=scripted_root_llm([
            '```python\n'
            'import re\n'
            'm = re.search(r"password is (\\w+)", prompt)\n'
            '_set_result(m.group(1) if m else "not found")\n'
            '```',
        ]),
        sub_llm=None,
    )
    result = await runner.run(
        task="find the secret password",
        context_vars={"prompt": long_prompt},
    )
    assert result.output == "BANANA42"
    assert result.sub_calls_used == 0  # paper pattern (a) — no recursion needed


# ── Pattern (b) from the paper: decompose + sub-LM ───────────────────


@pytest.mark.asyncio
async def test_pattern_b_decompose_with_sub_llm():
    """RLM trajectory pattern (b): split context into chunks, call
    sub-LM on each, aggregate."""
    chunks = ["chunk-A: discusses Tokyo", "chunk-B: discusses Paris", "chunk-C: discusses Tokyo"]

    async def sub_llm(q):
        # Mock: extract the chunk content from the prompt and check
        # whether THAT mentions the target city. The prompt template
        # below (`Classify chunk: <chunk> ...`) puts the chunk in a
        # known position so we can pick it back out without false-
        # positives on the question itself.
        chunk_part = q.split("Classify chunk: ", 1)[-1].split(" ::", 1)[0]
        return "yes" if "Tokyo" in chunk_part else "no"

    runner = RLMRunner(
        root_llm=scripted_root_llm([
            '```python\n'
            'tokyo_count = 0\n'
            'for c in chunks:\n'
            '    q = f"Classify chunk: {c} :: does it mention the city?"\n'
            '    if (await _sub_llm(q)) == "yes":\n'
            '        tokyo_count += 1\n'
            '_set_result(f"tokyo mentions: {tokyo_count}")\n'
            '```',
        ]),
        sub_llm=sub_llm,
    )
    result = await runner.run(
        task="count Tokyo mentions",
        context_vars={"chunks": chunks},
    )
    assert result.output == "tokyo mentions: 2"
    assert result.sub_calls_used == 3


# ── Pattern (c) from the paper: stitched long output ─────────────────


@pytest.mark.asyncio
async def test_pattern_c_stitched_long_output():
    """RLM trajectory pattern (c): build output by concatenating
    multiple sub-LM calls — each call is bounded, total output is
    "essentially unbounded" in the paper's words."""
    sections = ["s1", "s2", "s3"]

    async def sub_llm(q):
        return f"<{q.split('section ')[-1].upper()}>"

    runner = RLMRunner(
        root_llm=scripted_root_llm([
            '```python\n'
            'pieces = []\n'
            'for s in sections:\n'
            '    pieces.append(await _sub_llm(f"expand section {s}"))\n'
            '_set_result(" ".join(pieces))\n'
            '```',
        ]),
        sub_llm=sub_llm,
    )
    result = await runner.run(task="x", context_vars={"sections": sections})
    assert result.output == "<S1> <S2> <S3>"


# ── Public API surface ───────────────────────────────────────────────


def test_top_level_imports_are_exposed_on_nexus_core():
    """rlm primitives are reachable via the package root for
    callers (server / nexus runtime) that don't want to import a
    deep submodule path."""
    assert nexus_core.RLMRunner is RLMRunner
    assert nexus_core.RLMConfig is RLMConfig
    assert nexus_core.RLMResult is RLMResult
    assert nexus_core.run_rlm is run_rlm


@pytest.mark.asyncio
async def test_run_rlm_convenience_wrapper():
    """The functional form ``run_rlm(...)`` does the same thing as
    instantiating + calling RLMRunner manually."""
    result = await run_rlm(
        root_llm=scripted_root_llm([
            '```python\n_set_result(str(prompt))\n```',
        ]),
        task="echo",
        context_vars={"prompt": "hello"},
    )
    assert result.output == "hello"


# ── Multi-iteration: state persists across iterations ────────────────


@pytest.mark.asyncio
async def test_globals_persist_across_iterations():
    """Variables set in one iteration are visible in the next —
    that's the whole point of a "REPL" sandbox vs one-shot exec."""
    runner = RLMRunner(
        root_llm=scripted_root_llm([
            '```python\n'
            'computed = sum(range(10))\n'
            'print("set computed=", computed)\n'
            '```',
            '```python\n_set_result(f"value is {computed}")\n```',
        ]),
        sub_llm=None,
    )
    result = await runner.run(task="x", context_vars={})
    assert result.output == "value is 45"
    assert result.iterations_used == 2


# ── Config defaults are sane ─────────────────────────────────────────


def test_default_config_has_reasonable_limits():
    cfg = RLMConfig()
    assert cfg.max_iterations >= 5
    assert cfg.max_sub_calls >= cfg.max_iterations  # sub-calls > iterations
    assert cfg.timeout_seconds > 0
    assert cfg.max_recursion_depth == 1   # paper's recommended default
