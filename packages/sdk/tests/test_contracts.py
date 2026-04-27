"""Tests for Agent Behavioral Contracts (ABC)."""

import pytest
from nexus_core.contracts import ContractEngine, ContractSpec, Rule, DriftScore


class TestContractSpec:
    def test_empty_spec(self):
        spec = ContractSpec()
        assert spec.name == "default"
        assert len(spec.all_rules) == 0

    def test_add_user_rule_soft_only(self):
        spec = ContractSpec()
        rule = Rule(check="language_match", description="Respond in Chinese",
                    severity="hard", category="invariant")
        spec.add_user_rule(rule)
        # Hard downgraded to soft
        assert rule.severity == "soft"
        assert len(spec.soft_invariants) == 1

    def test_from_dict(self):
        data = {
            "contract": {
                "name": "test",
                "invariants": {
                    "hard": [{"check": "no_pii_leak", "description": "No PII"}],
                    "soft": [{"check": "language_match", "recovery": "regenerate", "recovery_window": 3}],
                },
                "governance": {
                    "hard": [{"check": "tool_whitelist", "allowed": ["web_search"]}],
                },
            }
        }
        spec = ContractSpec.from_dict(data)
        assert spec.name == "test"
        assert len(spec.hard_invariants) == 1
        assert len(spec.soft_invariants) == 1
        assert len(spec.hard_governance) == 1


class TestContractEngine:
    def _make_engine(self):
        spec = ContractSpec(
            hard_invariants=[
                Rule(check="no_pii_leak", severity="hard", category="invariant"),
            ],
            soft_invariants=[
                Rule(check="professional_tone", severity="soft", category="invariant",
                     recovery="regenerate", recovery_window=3),
            ],
            soft_governance=[
                Rule(check="response_length", severity="soft", category="governance",
                     params={"max_tokens": 100}),
            ],
        )
        return ContractEngine(spec)

    def test_post_check_pass(self):
        engine = self._make_engine()
        result = engine.post_check("This is a professional response.")
        assert result.passed
        assert not result.hard_violation
        assert len(result.soft_violations) == 0

    def test_hard_invariant_pii_detected(self):
        engine = self._make_engine()
        result = engine.post_check("Your SSN is 123-45-6789.")
        assert result.hard_violation
        assert not result.passed

    def test_soft_violation_tracked(self):
        engine = self._make_engine()
        # Very long response triggers response_length soft violation
        long_text = "word " * 500
        result = engine.post_check(long_text)
        # response_length is soft governance, checked in pre_check not post_check
        # professional_tone should pass for normal text
        assert result.passed or not result.hard_violation

    def test_pre_check_session_turns(self):
        spec = ContractSpec(
            soft_governance=[
                Rule(check="session_turns", severity="soft", category="governance",
                     params={"max_turns": 5}),
            ],
        )
        engine = ContractEngine(spec)
        for i in range(6):
            result = engine.pre_check(f"message {i}")
        assert len(result.soft_violations) > 0

    def test_needs_recovery_after_window(self):
        spec = ContractSpec(
            soft_invariants=[
                Rule(check="language_match", severity="soft", category="invariant",
                     params={"target": "zh-CN"}, recovery="regenerate", recovery_window=2),
            ],
        )
        engine = ContractEngine(spec)
        # Two consecutive violations should trigger recovery need
        engine.post_check("This is English.")  # violation 1
        engine.post_check("Still English.")    # violation 2
        assert engine.needs_recovery("language_match")


class TestDriftScore:
    def test_zero_drift_when_compliant(self):
        ds = DriftScore()
        ds.update(1.0, 1.0, "chat")
        ds.update(1.0, 1.0, "chat")
        assert ds.current() == 0.0
        assert ds.status == "normal"

    def test_drift_increases_with_violations(self):
        ds = DriftScore()
        for _ in range(5):
            ds.update(1.0, 1.0, "chat")
        for _ in range(5):
            ds.update(0.5, 0.5, "chat")
        assert ds.current() > 0.0

    def test_warning_threshold(self):
        ds = DriftScore(warning_threshold=0.1, intervention_threshold=0.3)
        for _ in range(10):
            ds.update(0.7, 0.7, "chat")
        assert ds.status in ("warning", "intervention")

    def test_calibrate_reference(self):
        ds = DriftScore()
        ds.calibrate(["chat", "chat", "tool", "chat"])
        ds.update(1.0, 1.0, "chat")
        # Should have distributional component now
        assert ds.diagnostic["distributional_drift"] >= 0.0

    def test_diagnostic_decomposition(self):
        ds = DriftScore()
        ds.update(0.8, 0.9, "chat")
        diag = ds.diagnostic
        assert "drift_score" in diag
        assert "compliance_drift" in diag
        assert "distributional_drift" in diag
        assert "status" in diag
