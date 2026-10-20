"""Evaluation helpers for fixed-payout demo outcomes."""
from __future__ import annotations


def fixed_payout_metrics(outcomes: list[bool], payout_ratio: float) -> dict:
    """Evaluate unit-stake results; payout_ratio is net profit on a win."""
    if not 0 < payout_ratio <= 1:
        raise ValueError("payout_ratio must be in (0, 1]")
    count = len(outcomes)
    wins = sum(bool(value) for value in outcomes)
    losses = count - wins
    win_rate = wins / count if count else None
    break_even = 1 / (1 + payout_ratio)
    expected_value = (win_rate * payout_ratio - (1 - win_rate)) if count else None

    equity = peak = 0.0
    max_drawdown = 0.0
    max_losing_streak = losing_streak = 0
    for won in outcomes:
        equity += payout_ratio if won else -1.0
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        losing_streak = 0 if won else losing_streak + 1
        max_losing_streak = max(max_losing_streak, losing_streak)
    return {"trades": count, "wins": wins, "losses": losses,
            "win_rate": win_rate, "break_even_win_rate": break_even,
            "expected_value_per_unit_stake": expected_value,
            "net_units": equity, "max_drawdown_units": max_drawdown,
            "max_losing_streak": max_losing_streak}

