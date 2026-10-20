"""Mechanical verification of manually executed demo-trade evidence.

Displayed potential return is not a result. Evidence is scoreable only when an
exit observation is at or after the recorded expiry timestamp.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

Direction = Literal["UP", "DOWN"]
Outcome = Literal["WIN", "LOSS", "TIE"]


@dataclass(frozen=True)
class TradeEvidence:
    direction: Direction
    entry_price: float
    entry_observed_at_ms: int
    expiry_at_ms: int
    exit_price: float
    exit_observed_at_ms: int
    stake: float
    payout_ratio: float
    reported_outcome: Outcome | None = None
    reported_total_return: float | None = None


@dataclass(frozen=True)
class TradeVerification:
    status: Literal["PENDING", "CONSISTENT", "MISMATCH"]
    mechanical_outcome: Outcome | None
    expected_total_return: float | None
    reasons: tuple[str, ...]


def verify_trade(evidence: TradeEvidence, *,
                 price_tolerance: float = 0.0,
                 money_tolerance: float = 0.01) -> TradeVerification:
    _validate(evidence, price_tolerance, money_tolerance)
    if evidence.exit_observed_at_ms < evidence.expiry_at_ms:
        return TradeVerification("PENDING", None, None,
                                 ("exit_observation_before_expiry",))

    movement = evidence.exit_price - evidence.entry_price
    if abs(movement) <= price_tolerance:
        outcome: Outcome = "TIE"
    elif ((evidence.direction == "UP" and movement > 0) or
          (evidence.direction == "DOWN" and movement < 0)):
        outcome = "WIN"
    else:
        outcome = "LOSS"

    if outcome == "WIN":
        expected_return = round(evidence.stake * (1 + evidence.payout_ratio), 2)
    elif outcome == "TIE":
        expected_return = round(evidence.stake, 2)
    else:
        expected_return = 0.0

    reasons: list[str] = []
    if evidence.reported_outcome is None:
        reasons.append("reported_outcome_missing")
    elif evidence.reported_outcome != outcome:
        reasons.append("reported_outcome_mismatch")
    if evidence.reported_total_return is None:
        reasons.append("reported_total_return_missing")
    elif not math.isclose(evidence.reported_total_return, expected_return,
                          rel_tol=0.0, abs_tol=money_tolerance):
        reasons.append("reported_total_return_mismatch")
    return TradeVerification(
        "MISMATCH" if reasons else "CONSISTENT", outcome,
        expected_return, tuple(reasons))


def _validate(evidence: TradeEvidence, price_tolerance: float,
              money_tolerance: float) -> None:
    if evidence.direction not in {"UP", "DOWN"}:
        raise ValueError("direction must be UP or DOWN")
    numeric_positive = (
        evidence.entry_price, evidence.exit_price, evidence.stake)
    if any(not math.isfinite(value) or value <= 0 for value in numeric_positive):
        raise ValueError("prices and stake must be positive and finite")
    if not math.isfinite(evidence.payout_ratio) or not 0 < evidence.payout_ratio <= 1:
        raise ValueError("payout_ratio must be in (0, 1]")
    if evidence.expiry_at_ms <= evidence.entry_observed_at_ms:
        raise ValueError("expiry must follow entry observation")
    if evidence.exit_observed_at_ms < evidence.entry_observed_at_ms:
        raise ValueError("exit observation cannot precede entry")
    if price_tolerance < 0 or money_tolerance < 0:
        raise ValueError("tolerances must be non-negative")
