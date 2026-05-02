"""Phase P.1 tests — `ProjectionMemory` mode switching.

Covers:
* default ``single_call`` mode keeps the canonical DPM behaviour
  (one LLM call, deterministic).
* opt-in ``rlm`` mode delegates to ``RLMRunner`` for long
  trajectories.
* fast-path: short trajectories run as ``single_call`` even when
  ``mode="rlm"`` was configured.
* graceful fallback: an RLM run that truncates / errors falls back
  to ``single_call`` instead of returning empty.
* the ``last_mode_used`` observability field reports which path
  actually ran each call.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nexus_core.memory import EventLog
from nexus_core.rlm import RLMConfig

from nexus.evolution.projection import ProjectionMemory


# ── Helpers ──────────────────────────────────────────────────────────


@pytest.fixture
def event_log_with_events(tmp_path):
    """An EventLog seeded with N user_message events totalling
    enough chars to either stay under or cross the fast-path
    threshold (callers control N)."""
    def _factory(n_events: int, content_repeat: int = 50):
        log = EventLog(base_dir=str(tmp_path), agent_id="test-agent")
        for i in range(n_events):
            log.append(
                event_type="user_message",
                content=f"event {i}: " + ("lorem ipsum " * content_repeat),
                metadata={"sync_id": i},
                session_id="s1",
            )
        return log
    return _factory


def fixed_response_llm(response: str):
    """Async LLM that returns the same text for every prompt."""
    async def _llm(prompt, **kwargs):
        return response
    return _llm


# ── single_call mode (existing behaviour) ────────────────────────────


@pytest.mark.asyncio
async def test_single_call_mode_default_is_unchanged(event_log_with_events):
    """The default mode behaves exactly as before Phase P — one LLM
    call, output passed through."""
    log = event_log_with_events(n_events=3)
    proj = ProjectionMemory(log, fixed_response_llm("FACTS\n- x [0]"))
    out = await proj.project(query="hi", budget=2000)
    assert out == "FACTS\n- x [0]"
    assert proj._last_mode_used == "single_call"


@pytest.mark.asyncio
async def test_single_call_mode_returns_empty_when_log_empty(tmp_path):
    log = EventLog(base_dir=str(tmp_path), agent_id="empty")
    proj = ProjectionMemory(log, fixed_response_llm("never seen"))
    out = await proj.project(query="anything", budget=100)
    assert out == ""
    assert proj._last_mode_used == "empty"


# ── rlm mode + fast-path ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rlm_mode_short_trajectory_uses_fastpath(event_log_with_events):
    """Per paper Observation 3, short logs should NOT pay the RLM
    overhead. Even when configured for rlm, short trajectories run
    as single_call."""
    log = event_log_with_events(n_events=2, content_repeat=10)  # tiny
    proj = ProjectionMemory(
        log,
        fixed_response_llm("FACTS\n- short [0]"),
        mode="rlm",
        sub_llm_fn=fixed_response_llm("sub"),
        fastpath_char_threshold=10_000,
    )
    out = await proj.project(query="hi", budget=2000)
    assert "short" in out
    assert proj._last_mode_used == "single_call"  # fast-path taken


@pytest.mark.asyncio
async def test_rlm_mode_long_trajectory_uses_rlm(event_log_with_events):
    """When trajectory crosses the fast-path threshold, rlm mode
    actually runs the recursive runner."""
    log = event_log_with_events(n_events=20, content_repeat=200)  # big

    # The root LLM (driven via single-prompt LLMClient.complete
    # adapter inside ProjectionMemory) sees a "[USER]\n{task}"
    # message after the system prompt is flattened into the user
    # turn. We respond with a single code block that calls
    # _set_result with a synthetic projection.
    async def root_llm(prompt, **kwargs):
        return (
            '```python\n'
            '_set_result("FACTS\\n- from rlm\\n\\nCONTEXT\\n- x\\n\\nUSER_PROFILE\\n- y")\n'
            '```'
        )

    proj = ProjectionMemory(
        log,
        root_llm,
        mode="rlm",
        sub_llm_fn=fixed_response_llm("sub"),
        fastpath_char_threshold=1_000,  # force RLM path
        rlm_config=RLMConfig(max_iterations=3, max_sub_calls=5, timeout_seconds=10.0),
    )
    out = await proj.project(query="anything", budget=2000)
    assert "from rlm" in out
    assert proj._last_mode_used == "rlm"


@pytest.mark.asyncio
async def test_rlm_mode_truncated_falls_back_to_single_call(event_log_with_events):
    """If the RLM hits max_iterations without committing a result,
    we fall back to single_call so chat doesn't silently degrade."""
    log = event_log_with_events(n_events=20, content_repeat=200)

    # Root LLM never calls _set_result → RLM truncates.
    iteration_counter = {"n": 0}

    async def stalling_root_llm(prompt, **kwargs):
        iteration_counter["n"] += 1
        return '```python\nx = 1\n```'  # never terminates

    proj = ProjectionMemory(
        log,
        stalling_root_llm,
        mode="rlm",
        sub_llm_fn=None,
        fastpath_char_threshold=1_000,
        rlm_config=RLMConfig(max_iterations=2, max_sub_calls=2, timeout_seconds=10.0),
    )

    # Track whether single_call was invoked as fallback by counting
    # how many times the LLM was called: 2 RLM iterations + 1
    # single_call fallback = 3 total.
    out = await proj.project(query="x", budget=1000)
    assert iteration_counter["n"] == 3  # 2 RLM iters + 1 fallback
    assert proj._last_mode_used == "rlm_truncated_fallback"
    # Output is the stall_root_llm's response when called as
    # single_call, which is still the "x = 1" code block — good
    # enough; the test verifies fallback PATH was taken, not LLM
    # quality.


@pytest.mark.asyncio
async def test_rlm_mode_exception_falls_back_gracefully(event_log_with_events):
    """If the RLM runner raises (e.g. sub_llm raises), we fall back
    to single_call instead of bubbling."""
    log = event_log_with_events(n_events=20, content_repeat=200)

    call_count = {"n": 0}

    async def root_llm(prompt, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First call: simulate an unrecoverable error path inside
            # RLM by emitting code that raises during exec — but the
            # runner catches that and would normally surface to
            # trajectory; we want a HARD failure instead, so raise
            # from the LLM itself.
            raise RuntimeError("simulated LLM outage")
        return "FACTS\n- single_call recovery"

    proj = ProjectionMemory(
        log, root_llm, mode="rlm",
        sub_llm_fn=None, fastpath_char_threshold=1_000,
        rlm_config=RLMConfig(max_iterations=2, max_sub_calls=2),
    )
    out = await proj.project(query="hi", budget=1000)
    assert "single_call recovery" in out
    assert proj._last_mode_used == "rlm_failed_fallback"


# ── Constructor validation ───────────────────────────────────────────


def test_invalid_mode_raises(tmp_path):
    log = EventLog(base_dir=str(tmp_path), agent_id="bad")
    with pytest.raises(ValueError, match="mode must be"):
        ProjectionMemory(log, fixed_response_llm("x"), mode="not_a_mode")


# ── Observability ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_last_mode_used_reports_path_taken(event_log_with_events):
    """`last_mode_used` is the observability hook — operators can
    sample this in metrics to see what path each call took."""
    log = event_log_with_events(n_events=2)
    proj = ProjectionMemory(
        log, fixed_response_llm("FACTS\n- x"),
        mode="rlm",
        sub_llm_fn=fixed_response_llm("sub"),
        fastpath_char_threshold=10_000_000,  # always fast-path
    )
    await proj.project("q", 1000)
    assert proj._last_mode_used == "single_call"
