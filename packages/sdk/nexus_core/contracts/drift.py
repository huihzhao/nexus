"""Behavioral Drift Score — early warning for agent misalignment.

D(t) = w_c × D_compliance(t) + w_d × D_distributional(t)

Compliance drift: lagging indicator (violations already happened)
Distributional drift: leading indicator (behavior pattern shifting)

Based on Definition 3.12 from arXiv:2602.22302.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from typing import Optional

logger = logging.getLogger(__name__)


class DriftScore:
    """Behavioral drift score tracker.

    Monitors compliance gaps and action distribution shifts
    over a sliding observation window.
    """

    def __init__(
        self,
        compliance_weight: float = 0.7,
        distributional_weight: float = 0.3,
        observation_window: int = 10,
        warning_threshold: float = 0.15,
        intervention_threshold: float = 0.35,
    ):
        self._w_c = compliance_weight
        self._w_d = distributional_weight
        self._window = observation_window
        self._theta_1 = warning_threshold
        self._theta_2 = intervention_threshold

        # Compliance history
        self._hard_scores: list[float] = []
        self._soft_scores: list[float] = []

        # Action distribution tracking
        self._action_history: list[str] = []  # recent action types
        self._reference_dist: Optional[Counter] = None  # calibrated baseline

    def update(self, hard_score: float, soft_score: float, action_type: str = "") -> float:
        """Update drift score with latest compliance scores and action.

        Returns the current drift score D(t) ∈ [0, 1].
        """
        self._hard_scores.append(hard_score)
        self._soft_scores.append(soft_score)
        if action_type:
            self._action_history.append(action_type)

        return self.current()

    def current(self) -> float:
        """Compute current behavioral drift score D(t)."""
        d_compliance = self._compliance_drift()
        d_distributional = self._distributional_drift()
        return self._w_c * d_compliance + self._w_d * d_distributional

    def _compliance_drift(self) -> float:
        """Weighted compliance gap over recent window."""
        if not self._hard_scores and not self._soft_scores:
            return 0.0

        window = self._window
        recent_hard = self._hard_scores[-window:]
        recent_soft = self._soft_scores[-window:]

        # Average compliance gap (1 - score)
        hard_gap = 1.0 - (sum(recent_hard) / max(len(recent_hard), 1))
        soft_gap = 1.0 - (sum(recent_soft) / max(len(recent_soft), 1))

        # Hard violations weighted more heavily
        return 0.7 * hard_gap + 0.3 * soft_gap

    def _distributional_drift(self) -> float:
        """Jensen-Shannon divergence between observed and reference distributions."""
        if not self._action_history or self._reference_dist is None:
            return 0.0

        window = self._window
        recent = self._action_history[-window:]
        observed = Counter(recent)

        return _jsd(observed, self._reference_dist)

    def calibrate(self, actions: list[str] = None) -> None:
        """Set reference distribution from a compliant baseline session.

        Call after a validated session to establish what "normal" looks like.
        If no actions provided, use current history as reference.
        """
        if actions:
            self._reference_dist = Counter(actions)
        elif self._action_history:
            self._reference_dist = Counter(self._action_history)
        else:
            self._reference_dist = Counter()

    @property
    def status(self) -> str:
        """Current drift status: 'normal', 'warning', or 'intervention'."""
        d = self.current()
        if d > self._theta_2:
            return "intervention"
        elif d > self._theta_1:
            return "warning"
        return "normal"

    @property
    def diagnostic(self) -> dict:
        """Diagnostic decomposition vector."""
        return {
            "drift_score": round(self.current(), 4),
            "compliance_drift": round(self._compliance_drift(), 4),
            "distributional_drift": round(self._distributional_drift(), 4),
            "status": self.status,
            "steps": len(self._hard_scores),
            "hard_avg": round(sum(self._hard_scores[-10:]) / max(len(self._hard_scores[-10:]), 1), 4),
            "soft_avg": round(sum(self._soft_scores[-10:]) / max(len(self._soft_scores[-10:]), 1), 4),
        }


def _jsd(p: Counter, q: Counter) -> float:
    """Jensen-Shannon divergence between two distributions (as Counters)."""
    all_keys = set(p.keys()) | set(q.keys())
    if not all_keys:
        return 0.0

    total_p = sum(p.values()) or 1
    total_q = sum(q.values()) or 1

    jsd = 0.0
    for key in all_keys:
        p_val = p.get(key, 0) / total_p
        q_val = q.get(key, 0) / total_q
        m_val = (p_val + q_val) / 2

        if p_val > 0 and m_val > 0:
            jsd += 0.5 * p_val * math.log2(p_val / m_val)
        if q_val > 0 and m_val > 0:
            jsd += 0.5 * q_val * math.log2(q_val / m_val)

    return min(jsd, 1.0)  # Clamp to [0, 1]
