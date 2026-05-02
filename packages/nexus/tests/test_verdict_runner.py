"""Phase O.4: VerdictRunner closes the falsifiable-evolution loop.

Tests cover the full state machine:
  proposal emitted → window elapses → verdict scored → optional revert.

Each test pins one behavior, using a synthetic in-memory EventLog
populated directly via ``append`` so we can construct edge cases
without exercising the full evolver pipeline.
"""

from __future__ import annotations

from typing import Optional

import pytest

from nexus_core.evolution import EvolutionProposal, TaskKindPrediction
from nexus_core.memory import EventLog, PersonaStore, FactsStore, PersonaVersion, Fact
from nexus.evolution.verdict_runner import VerdictRunner


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def event_log(tmp_path):
    return EventLog(base_dir=str(tmp_path / "elog"), agent_id="agent-1")


@pytest.fixture
def persona_store(tmp_path):
    s = PersonaStore(base_dir=str(tmp_path / "p"))
    # Seed two persona versions so we have a v1 to roll back to.
    s.propose_version(PersonaVersion(persona_text="v1 baseline", changes_summary="initial"))
    s.propose_version(PersonaVersion(persona_text="v2 evolved", changes_summary="evolved"))
    return s


@pytest.fixture
def facts_store(tmp_path):
    return FactsStore(base_dir=str(tmp_path / "f"))


# ── Helpers ────────────────────────────────────────────────────────────


def _emit_proposal(
    event_log: EventLog,
    *,
    edit_id: str,
    target_namespace: str = "memory.persona",
    rollback_pointer: str = "",
    expires_after_events: int = 5,
) -> int:
    """Append a synthetic evolution_proposal event. Returns its idx."""
    proposal = EvolutionProposal(
        edit_id=edit_id,
        evolver="TestEvolver",
        target_namespace=target_namespace,
        target_version_pre=rollback_pointer,
        target_version_post=rollback_pointer,
        change_summary=f"test edit {edit_id}",
        rollback_pointer=rollback_pointer,
        expires_after_events=expires_after_events,
    )
    return event_log.append(
        event_type="evolution_proposal",
        content=f"proposal {edit_id}",
        metadata=proposal.to_event_metadata(),
    )


def _emit_filler(event_log: EventLog, n: int) -> None:
    """Fill the log with N noise events to advance the window."""
    for i in range(n):
        event_log.append(event_type="chat", content=f"filler {i}")


def _emit_violation(event_log: EventLog, *, hard: bool = True) -> int:
    """Append a contract_check failure."""
    return event_log.append(
        event_type="contract_check",
        content="contract failure",
        metadata={
            "passed": False,
            "hard_violation": hard,
            "soft_violations": [] if hard else ["soft_rule_x"],
        },
    )


# ── Behavior under no observed regressions ───────────────────────────


def test_proposal_within_window_is_pending(event_log):
    """Window not yet elapsed → no verdict emitted, returned list empty."""
    _emit_proposal(event_log, edit_id="edit-A", expires_after_events=10)
    _emit_filler(event_log, 3)  # only 3 events past proposal — window=10

    runner = VerdictRunner(event_log)
    verdicts = runner.score_pending()
    assert verdicts == []
    # No verdict event yet
    types = {e.event_type for e in event_log.recent(limit=20)}
    assert "evolution_verdict" not in types


def test_clean_window_yields_kept_verdict(event_log):
    """No regressions, no drift → decision = "kept", no revert side-effect."""
    _emit_proposal(event_log, edit_id="edit-A", expires_after_events=3)
    _emit_filler(event_log, 5)  # window elapsed

    runner = VerdictRunner(event_log)
    verdicts = runner.score_pending()
    assert len(verdicts) == 1
    assert verdicts[0].decision == "kept"
    assert verdicts[0].edit_id == "edit-A"
    # Verdict event written back
    types = [e.event_type for e in event_log.recent(limit=20)]
    assert "evolution_verdict" in types
    # No revert event
    assert "evolution_revert" not in types


# ── Idempotence ──────────────────────────────────────────────────────


def test_runner_is_idempotent(event_log):
    """Running twice produces only one verdict per proposal — settled
    edit_ids must not be re-scored."""
    _emit_proposal(event_log, edit_id="edit-A", expires_after_events=2)
    _emit_filler(event_log, 4)

    runner = VerdictRunner(event_log)
    first = runner.score_pending()
    second = runner.score_pending()
    assert len(first) == 1
    assert second == []  # already settled


# ── Force flag ───────────────────────────────────────────────────────


def test_force_scores_pending_proposals_immediately(event_log):
    """force=True bypasses the window-elapsed gate."""
    _emit_proposal(event_log, edit_id="edit-A", expires_after_events=999)
    _emit_filler(event_log, 1)  # nowhere near elapsed

    runner = VerdictRunner(event_log)
    assert runner.score_pending() == []          # gated out
    forced = runner.score_pending(force=True)
    assert len(forced) == 1


# ── Hard violation triggers revert ───────────────────────────────────


def test_hard_violation_in_window_reverts_persona_store(event_log, persona_store):
    """A hard contract violation observed in the window → decision = reverted,
    PersonaStore.rollback called to the proposal's rollback_pointer."""
    pre_version = persona_store.current_version()  # v0002
    # Create an explicit older target — simulate that proposal predates v0002.
    target = persona_store.history()[0]["version"]  # v0001
    _emit_proposal(
        event_log,
        edit_id="edit-bad",
        target_namespace="memory.persona",
        rollback_pointer=target,
        expires_after_events=2,
    )
    _emit_violation(event_log, hard=True)
    _emit_filler(event_log, 3)

    runner = VerdictRunner(
        event_log,
        stores={"memory.persona": persona_store},
    )
    verdicts = runner.score_pending()
    assert len(verdicts) == 1
    assert verdicts[0].decision == "reverted"

    # Persona was rolled back
    assert persona_store.current_version() == target
    assert persona_store.current_version() != pre_version

    # evolution_revert event was emitted
    revert_events = [
        e for e in event_log.recent(limit=20) if e.event_type == "evolution_revert"
    ]
    assert len(revert_events) == 1
    assert revert_events[0].metadata["edit_id"] == "edit-bad"
    assert revert_events[0].metadata["rolled_back_to"] == target


# ── Soft violation alone is "kept_with_warning" ──────────────────────


def test_soft_violation_yields_kept_with_warning(event_log):
    """Only soft (non-hard) contract failures → low severity → warning, not revert."""
    _emit_proposal(event_log, edit_id="edit-soft", expires_after_events=2)
    _emit_violation(event_log, hard=False)
    _emit_filler(event_log, 3)

    runner = VerdictRunner(event_log)
    verdicts = runner.score_pending()
    assert len(verdicts) == 1
    assert verdicts[0].decision == "kept_with_warning"


# ── Drift-driven revert (no contract violations) ─────────────────────


def test_drift_above_intervention_triggers_revert(event_log, persona_store):
    """ABC drift above intervention threshold → decision = reverted even
    without any contract violations in the window."""
    target = persona_store.history()[0]["version"]
    _emit_proposal(
        event_log,
        edit_id="edit-drift",
        target_namespace="memory.persona",
        rollback_pointer=target,
        expires_after_events=1,
    )
    _emit_filler(event_log, 2)

    class FakeDrift:
        def current(self):
            return 0.5  # > default intervention 0.35

    runner = VerdictRunner(
        event_log,
        stores={"memory.persona": persona_store},
        drift=FakeDrift(),
    )
    verdicts = runner.score_pending()
    assert len(verdicts) == 1
    assert verdicts[0].decision == "reverted"


# ── Forward-compat: malformed proposal events are skipped ────────────


def test_malformed_proposal_event_is_skipped(event_log):
    """A row that claims to be a proposal but has no edit_id is
    quietly skipped — the runner doesn't crash and other proposals
    still get scored."""
    # Bad row: no edit_id in metadata
    event_log.append(
        event_type="evolution_proposal",
        content="bad",
        metadata={"evolver": "X"},
    )
    _emit_proposal(event_log, edit_id="good", expires_after_events=1)
    _emit_filler(event_log, 2)

    runner = VerdictRunner(event_log)
    verdicts = runner.score_pending()
    assert [v.edit_id for v in verdicts] == ["good"]


# ── No store wired in: verdict still written, no rollback attempted ──


def test_revert_without_store_writes_verdict_anyway(event_log):
    """If the runner has no store registered for the proposal's
    target_namespace, it still writes the verdict + revert events
    (the UI can use those to alert the user) but skips the
    in-process rollback."""
    _emit_proposal(
        event_log,
        edit_id="edit-orphan",
        target_namespace="memory.persona",
        rollback_pointer="v0001",
        expires_after_events=1,
    )
    _emit_violation(event_log, hard=True)
    _emit_filler(event_log, 2)

    runner = VerdictRunner(event_log)  # no stores
    verdicts = runner.score_pending()
    assert len(verdicts) == 1
    assert verdicts[0].decision == "reverted"
    revert_events = [
        e for e in event_log.recent(limit=20) if e.event_type == "evolution_revert"
    ]
    assert len(revert_events) == 1
    # rolled_back_from is empty (no store to query)
    assert revert_events[0].metadata["rolled_back_from"] == ""
