"""Tests for ``nexus_core.evolution`` — falsifiable evolution
schema + verdict-decision rules.

The most important tests here pin the **normative rules** from
BEP-Nexus §3.4:

1. ``decision = reverted`` when an observed regression has
   ``severity ∈ {medium, high}`` OR ``abc_drift_delta >
   intervention``.
2. ``decision = kept_with_warning`` when an observed regression
   has ``severity = low`` OR ``abc_drift_delta > warning``.
3. ``decision = kept`` otherwise.
4. Critically: a runtime MUST NOT revert based on a
   predicted-but-unobserved regression. Predictions of regressions
   are noise-floor-quality (per AHE empirical finding); only
   *observed* regressions trigger reverts.

If any of these tests breaks, BEP §3.4 conformance is broken.
"""

from __future__ import annotations

import nexus_core
from nexus_core.evolution import (
    DriftThresholds,
    EvolutionProposal,
    EvolutionRevert,
    EvolutionVerdict,
    TaskKindPrediction,
    make_proposal_event,
    make_revert_event,
    make_verdict_event,
    score_verdict,
)


# ── Test fixtures (no pytest fixture — just plain helpers) ───────────


def _basic_proposal(**kwargs) -> EvolutionProposal:
    """A valid baseline EvolutionProposal callers tweak per-test."""
    defaults = {
        "edit_id": "evo-test-001",
        "evolver": "MemoryEvolver",
        "target_namespace": "memory.facts",
        "target_version_pre": "memory/facts/v0001.json",
        "target_version_post": "memory/facts/v0002.json",
        "change_summary": "Added user preference for sushi",
        "predicted_fixes": [
            TaskKindPrediction(task_kind="restaurant_recommendation"),
        ],
        "predicted_regressions": [],
        "rollback_pointer": "memory/facts/v0001.json",
    }
    defaults.update(kwargs)
    return EvolutionProposal(**defaults)


# ── Decision rule 1: reverted on severe observed regression ──────────


def test_decision_reverted_on_observed_medium_severity():
    """An observed regression with severity=medium → reverted,
    even if no other signals."""
    p = _basic_proposal()
    verdict = score_verdict(
        p,
        verdict_at_event=100, events_observed=50,
        observed_fixes=[],
        observed_regressions=[("small_talk", 1, "medium", "user complained")],
    )
    assert verdict.decision == "reverted"


def test_decision_reverted_on_observed_high_severity():
    p = _basic_proposal()
    verdict = score_verdict(
        p,
        verdict_at_event=100, events_observed=50,
        observed_fixes=[],
        observed_regressions=[("safety_violation", 1, "high", "got the allergy wrong")],
    )
    assert verdict.decision == "reverted"


def test_decision_reverted_on_high_abc_drift():
    """ABC drift exceeding the intervention threshold → reverted,
    even with no observed regressions."""
    p = _basic_proposal()
    verdict = score_verdict(
        p,
        verdict_at_event=100, events_observed=50,
        observed_fixes=[],
        observed_regressions=[],
        abc_drift_delta=0.45,
        drift_thresholds=DriftThresholds(warning=0.10, intervention=0.30),
    )
    assert verdict.decision == "reverted"


# ── Decision rule 2: kept_with_warning on low-severity ───────────────


def test_decision_kept_with_warning_on_observed_low_severity():
    p = _basic_proposal()
    verdict = score_verdict(
        p,
        verdict_at_event=100, events_observed=50,
        observed_fixes=[],
        observed_regressions=[("small_talk", 1, "low", "slightly off-tone")],
    )
    assert verdict.decision == "kept_with_warning"


def test_decision_kept_with_warning_on_warning_drift():
    """ABC drift between warning and intervention → kept_with_warning."""
    p = _basic_proposal()
    verdict = score_verdict(
        p,
        verdict_at_event=100, events_observed=50,
        observed_fixes=[],
        observed_regressions=[],
        abc_drift_delta=0.20,
        drift_thresholds=DriftThresholds(warning=0.10, intervention=0.30),
    )
    assert verdict.decision == "kept_with_warning"


# ── Decision rule 3: kept ────────────────────────────────────────────


def test_decision_kept_when_clean():
    """No observed regressions, drift below warning, predicted
    fixes matched → kept."""
    p = _basic_proposal()
    verdict = score_verdict(
        p,
        verdict_at_event=100, events_observed=50,
        observed_fixes=[("restaurant_recommendation", 3)],
        observed_regressions=[],
        abc_drift_delta=0.05,
    )
    assert verdict.decision == "kept"


def test_decision_kept_with_no_signal_at_all():
    """No observed fixes, no observed regressions, no drift —
    deadline-fired verdict → kept (innocent until proven guilty)."""
    p = _basic_proposal()
    verdict = score_verdict(
        p,
        verdict_at_event=200, events_observed=100,
        observed_fixes=[],
        observed_regressions=[],
    )
    assert verdict.decision == "kept"


# ── CRITICAL RULE: predicted-but-unobserved regression must NOT revert
# ──────────────────────────────────────────────────────────────────────


def test_predicted_but_unobserved_regression_does_not_trigger_revert():
    """The headline normative rule from BEP §3.4:

    > A compliant runtime MUST NOT revert based on
    > predicted_regressions that have no observed signal.

    The proposal predicts a HIGH-severity regression. Nothing
    actually goes wrong. The verdict MUST be 'kept', not 'reverted'.
    """
    p = _basic_proposal(
        predicted_regressions=[
            TaskKindPrediction(
                task_kind="diet_advice",
                reason="might over-mention allergy",
                severity="high",
            ),
        ],
    )
    verdict = score_verdict(
        p,
        verdict_at_event=100, events_observed=50,
        observed_fixes=[],
        observed_regressions=[],   # ← no actual regression observed
    )
    assert verdict.decision == "kept"
    # The miss is recorded for diagnostics but doesn't alter decision
    assert len(verdict.predicted_regression_miss) == 1
    assert verdict.predicted_regression_miss[0].task_kind == "diet_advice"


def test_predicted_regression_only_triggers_revert_when_actually_observed():
    """When the same predicted regression IS observed, severity DOES
    drive the decision (for high/medium → revert, for low → warning).
    The prediction itself is not the trigger; the *observation* is."""
    p = _basic_proposal(
        predicted_regressions=[
            TaskKindPrediction(task_kind="small_talk", severity="high"),
        ],
    )
    verdict = score_verdict(
        p,
        verdict_at_event=100, events_observed=50,
        observed_fixes=[],
        observed_regressions=[("small_talk", 2, "medium", "actually happened")],
    )
    assert verdict.decision == "reverted"
    # Match (predicted AND observed)
    assert len(verdict.predicted_regression_match) == 1
    assert verdict.predicted_regression_match[0].severity == "medium"


# ── Fix matching / scoring ───────────────────────────────────────────


def test_fix_match_when_predicted_and_observed():
    p = _basic_proposal(
        predicted_fixes=[
            TaskKindPrediction(task_kind="restaurant_recommendation"),
            TaskKindPrediction(task_kind="recipe_search"),
        ],
    )
    verdict = score_verdict(
        p,
        verdict_at_event=100, events_observed=50,
        observed_fixes=[("restaurant_recommendation", 3)],
        observed_regressions=[],
    )
    assert len(verdict.predicted_fix_match) == 1
    assert verdict.predicted_fix_match[0].task_kind == "restaurant_recommendation"
    assert verdict.predicted_fix_match[0].observed_count == 3
    assert len(verdict.predicted_fix_miss) == 1
    assert verdict.predicted_fix_miss[0].task_kind == "recipe_search"
    assert verdict.predicted_fix_miss[0].outcome == "no_signal"

    # fix_score = 1 / (1 + 1) = 0.5
    assert abs(verdict.fix_score - 0.5) < 1e-9


def test_fix_score_is_one_when_all_predicted_fixes_observed():
    p = _basic_proposal(
        predicted_fixes=[
            TaskKindPrediction(task_kind="a"),
            TaskKindPrediction(task_kind="b"),
        ],
    )
    verdict = score_verdict(
        p,
        verdict_at_event=100, events_observed=50,
        observed_fixes=[("a", 1), ("b", 1)],
        observed_regressions=[],
    )
    assert abs(verdict.fix_score - 1.0) < 1e-9


def test_fix_score_zero_when_no_fixes_predicted():
    """Edge case: proposal predicts zero fixes (e.g. a refactor).
    fix_score is 0/(0+0+1) by the impl's safe-division — verify
    it doesn't divide by zero."""
    p = _basic_proposal(predicted_fixes=[])
    verdict = score_verdict(
        p,
        verdict_at_event=100, events_observed=50,
        observed_fixes=[],
        observed_regressions=[],
    )
    assert verdict.fix_score == 0.0  # no division-by-zero crash


# ── Regression score severity weighting ──────────────────────────────


def test_regression_score_weighted_by_severity():
    """Severity weights: low=0.2, medium=0.6, high=1.0. Total
    capped at 1.0 (so 2x medium = 1.0)."""
    p = _basic_proposal()
    # One low only
    v_low = score_verdict(
        p,
        verdict_at_event=100, events_observed=50,
        observed_fixes=[],
        observed_regressions=[("x", 1, "low", "")],
    )
    assert abs(v_low.regression_score - 0.2) < 1e-9

    # One high only
    v_high = score_verdict(
        p,
        verdict_at_event=100, events_observed=50,
        observed_fixes=[],
        observed_regressions=[("x", 1, "high", "")],
    )
    assert abs(v_high.regression_score - 1.0) < 1e-9

    # Two mediums → 0.6 + 0.6 = 1.2, capped to 1.0
    v_two_med = score_verdict(
        p,
        verdict_at_event=100, events_observed=50,
        observed_fixes=[],
        observed_regressions=[
            ("x", 1, "medium", ""),
            ("y", 1, "medium", ""),
        ],
    )
    assert abs(v_two_med.regression_score - 1.0) < 1e-9


# ── Serialisation round-trip ─────────────────────────────────────────


def test_proposal_to_event_metadata_shape_matches_bep():
    """Spot-check the serialised metadata keys match BEP §3.4."""
    p = _basic_proposal(
        predicted_fixes=[TaskKindPrediction("a", "reason a")],
        predicted_regressions=[
            TaskKindPrediction("b", "reason b", severity="medium"),
        ],
    )
    md = p.to_event_metadata()
    expected_keys = {
        "edit_id", "evolver", "target_namespace",
        "target_version_pre", "target_version_post",
        "evidence_event_ids", "evidence_summary", "inferred_root_cause",
        "change_summary", "change_diff",
        "predicted_fixes", "predicted_regressions",
        "rollback_pointer", "expires_after_events",
        # Phase A+ / Phase C: causal lineage for the Pressure
        # Dashboard's "caused_by" view. Default-empty so existing
        # proposals serialise to ``{}`` instead of needing migration.
        "triggered_by",
    }
    assert set(md.keys()) == expected_keys
    assert md["predicted_fixes"][0] == {"task_kind": "a", "reason": "reason a"}
    assert md["predicted_regressions"][0] == {
        "task_kind": "b", "reason": "reason b", "severity": "medium",
    }
    # triggered_by defaults to empty dict — evolvers that haven't been
    # upgraded yet (Phase C4) still produce valid proposals.
    assert md["triggered_by"] == {}


def test_proposal_triggered_by_round_trips_through_event_metadata():
    """Lineage data set on a proposal must survive serialise →
    EventLog.metadata → _proposal_from_event reconstruction so the
    UI's lineage card always sees the same shape the evolver fired.
    """
    from nexus_core.evolution import EvolutionProposal
    p = EvolutionProposal(
        edit_id="x",
        evolver="KnowledgeCompiler",
        target_namespace="memory.knowledge",
        target_version_pre="v0001",
        target_version_post="v0001",
        triggered_by={
            "trigger_reason": "fact_threshold_reached",
            "window": {"start_event_id": 100, "end_event_id": 167},
            "counts": {"facts": 9},
            "ranges": {"facts": [101, 109]},
        },
    )
    md = p.to_event_metadata()
    assert md["triggered_by"]["trigger_reason"] == "fact_threshold_reached"
    assert md["triggered_by"]["counts"]["facts"] == 9
    assert md["triggered_by"]["window"]["start_event_id"] == 100


def test_verdict_to_event_metadata_shape_matches_bep():
    p = _basic_proposal()
    v = score_verdict(
        p,
        verdict_at_event=100, events_observed=50,
        observed_fixes=[("restaurant_recommendation", 2)],
        observed_regressions=[("small_talk", 1, "low", "minor")],
        abc_drift_delta=0.05,
    )
    md = v.to_event_metadata()
    expected_keys = {
        "edit_id", "verdict_at_event", "events_observed",
        "predicted_fix_match", "predicted_fix_miss",
        "predicted_regression_match", "predicted_regression_miss",
        "unpredicted_regressions",
        "fix_score", "regression_score", "abc_drift_delta",
        "decision",
    }
    assert set(md.keys()) == expected_keys
    assert md["decision"] == "kept_with_warning"
    assert md["predicted_fix_match"][0]["outcome"] == "fixed"


# ── Event factories ──────────────────────────────────────────────────


def test_make_proposal_event_produces_valid_event_row():
    p = _basic_proposal()
    row = make_proposal_event(
        p,
        sync_id=42,
        session_id="session_x",
        client_created_at="2026-04-28T12:00:00Z",
        server_received_at="2026-04-28T12:00:01Z",
    )
    assert row["event_type"] == "evolution_proposal"
    assert row["sync_id"] == 42
    assert row["session_id"] == "session_x"
    assert "MemoryEvolver" in row["content"]
    assert row["metadata"]["edit_id"] == p.edit_id


def test_make_verdict_event_produces_valid_event_row():
    p = _basic_proposal()
    v = score_verdict(
        p, verdict_at_event=99, events_observed=50,
        observed_fixes=[], observed_regressions=[],
    )
    row = make_verdict_event(
        v,
        sync_id=100,
        session_id="session_x",
        client_created_at="2026-04-28T18:00:00Z",
        server_received_at="2026-04-28T18:00:01Z",
    )
    assert row["event_type"] == "evolution_verdict"
    assert row["metadata"]["decision"] == "kept"
    assert "kept" in row["content"]


def test_make_revert_event_produces_valid_event_row():
    rev = EvolutionRevert(
        edit_id="evo-test-001",
        rolled_back_to="memory/facts/v0001.json",
        rolled_back_from="memory/facts/v0002.json",
        trigger="unpredicted_regression",
        evidence="user complained",
    )
    row = make_revert_event(
        rev,
        sync_id=120,
        session_id="session_x",
        client_created_at="2026-04-28T18:05:00Z",
        server_received_at="2026-04-28T18:05:01Z",
    )
    assert row["event_type"] == "evolution_revert"
    assert row["metadata"]["trigger"] == "unpredicted_regression"
    assert "v0001" in row["content"]


# ── Public API surface ───────────────────────────────────────────────


def test_top_level_exports():
    """Phase O.1 primitives are reachable on ``nexus_core.*``."""
    assert nexus_core.EvolutionProposal is EvolutionProposal
    assert nexus_core.EvolutionVerdict is EvolutionVerdict
    assert nexus_core.EvolutionRevert is EvolutionRevert
    assert nexus_core.score_verdict is score_verdict
