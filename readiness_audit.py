"""Fail-closed readiness audit for synchronized Crypto IDX research data."""
from __future__ import annotations
from contextlib import closing

import math
import sqlite3
from dataclasses import dataclass

from feature_engine import (
    build_feature_dataset,
    chronological_feature_split,
    feature_dataset_summary,
)
from market_data import DEFAULT_REFERENCE_SYMBOLS
from observation_store import ObservationStore, PricePoint
from relationship_lab import PREREGISTERED_V1_HORIZONS


@dataclass(frozen=True)
class ReadinessPolicy:
    expected_interval_seconds: float = 5.0
    minimum_continuous_minutes: float = 60.0
    minimum_feature_rows: int = 240
    minimum_train_rows: int = 140
    minimum_validation_rows: int = 30
    minimum_capture_completeness: float = 0.95
    minimum_acceptable_target_rate: float = 0.95
    minimum_attempt_acceptance_rate: float = 0.80
    minimum_feature_coverage: float = 0.80

    def __post_init__(self) -> None:
        if self.expected_interval_seconds <= 0:
            raise ValueError("expected interval must be positive")
        if self.minimum_continuous_minutes <= 0:
            raise ValueError("minimum continuous minutes must be positive")
        for value in (self.minimum_feature_rows, self.minimum_train_rows,
                      self.minimum_validation_rows):
            if value <= 0:
                raise ValueError("minimum row gates must be positive")
        for value in (self.minimum_capture_completeness,
                      self.minimum_acceptable_target_rate,
                      self.minimum_attempt_acceptance_rate,
                      self.minimum_feature_coverage):
            if not 0 < value <= 1:
                raise ValueError("rate gates must be in (0, 1]")


def audit_research_readiness(
        store: ObservationStore, *, payout_ratio: float | None,
        policy: ReadinessPolicy = ReadinessPolicy(),
        symbols: tuple[str, ...] = DEFAULT_REFERENCE_SYMBOLS,
        horizons: tuple[int, ...] = PREREGISTERED_V1_HORIZONS) -> dict:
    if payout_ratio is not None and not 0 < payout_ratio <= 1:
        raise ValueError("payout_ratio must be in (0, 1]")
    targets = store.price_series("binomo_screen", "CRYPTO_IDX")
    target_captures = [_capture_id(point) for point in targets]
    target_capture_ids = {value for value in target_captures if value}
    reference_maps = {
        symbol: {_capture_id(point): point for point in store.price_series(
            "binance_spot_public", symbol) if _capture_id(point)}
        for symbol in symbols}
    complete_ids = {
        capture_id for capture_id in target_capture_ids
        if all(capture_id in reference_maps[symbol] for symbol in symbols)}
    acceptable_targets = sum(_acceptable(point) for point in targets)
    capture_completeness = (len(complete_ids) / len(target_capture_ids)
                            if target_capture_ids else None)
    acceptable_rate = (acceptable_targets / len(targets) if targets else None)
    target_times = tuple(point.observed_at_ms for point in targets)
    gaps = tuple((right - left) / 1000
                 for left, right in zip(target_times, target_times[1:]))
    maximum_continuous_seconds, continuous_rows = _longest_run(
        target_times, policy.expected_interval_seconds * 3)
    attempt_health = _read_attempt_health(store.path)

    observed_payouts = sorted({
        round(float(value), 6) for point in targets
        if isinstance((value := point.metadata.get("payout_ratio")), (int, float))
        and 0 < float(value) <= 1
    })
    observed_payout = observed_payouts[0] if len(observed_payouts) == 1 else None
    effective_payout = payout_ratio if payout_ratio is not None else observed_payout
    payout_source = None
    if payout_ratio is not None:
        payout_source = "OPERATOR_SUPPLIED_VISIBLE_SESSION_VALUE"
    elif observed_payout is not None:
        payout_source = "SCREEN_OCR_CONSISTENT_SESSION_VALUE"

    common_reasons = []
    if len(observed_payouts) > 1:
        common_reasons.append("inconsistent_screen_payout")
    if (payout_ratio is not None and observed_payout is not None and
            not math.isclose(payout_ratio, observed_payout, abs_tol=0.005)):
        common_reasons.append("operator_screen_payout_mismatch")
    if effective_payout is None:
        common_reasons.append("missing_session_payout")
    if not targets:
        common_reasons.append("no_target_observations")
    if (capture_completeness is None or capture_completeness <
            policy.minimum_capture_completeness):
        common_reasons.append("insufficient_capture_completeness")
    if (acceptable_rate is None or acceptable_rate <
            policy.minimum_acceptable_target_rate):
        common_reasons.append("insufficient_acceptable_target_rate")
    required_span = max(
        0.0, policy.minimum_continuous_minutes * 60 -
        policy.expected_interval_seconds)
    if maximum_continuous_seconds < required_span:
        common_reasons.append("insufficient_continuous_session")
    if (attempt_health is not None and
            attempt_health["acceptance_rate"] is not None and
            attempt_health["acceptance_rate"] <
            policy.minimum_attempt_acceptance_rate):
        common_reasons.append("insufficient_attempt_acceptance_rate")

    horizon_reports = []
    for horizon in horizons:
        build = build_feature_dataset(store, horizon_seconds=horizon)
        summary = feature_dataset_summary(build)
        reasons = list(common_reasons)
        split_summary = None
        if len(build.rows) < policy.minimum_feature_rows:
            reasons.append("insufficient_feature_rows")
        if (summary["coverage"] is None or summary["coverage"] <
                policy.minimum_feature_coverage):
            reasons.append("insufficient_feature_coverage")
        try:
            split = chronological_feature_split(build.rows)
            split_summary = {
                "train_rows": len(split.train),
                "validation_rows": len(split.validation),
                "holdout": {
                    "status": "SEALED_NOT_EVALUATED",
                    "count": split.holdout.count,
                    "start_ms": split.holdout.start_ms,
                    "end_ms": split.holdout.end_ms,
                    "fingerprint": split.holdout.fingerprint,
                },
            }
            if len(split.train) < policy.minimum_train_rows:
                reasons.append("insufficient_walk_forward_training_rows")
            if len(split.validation) < policy.minimum_validation_rows:
                reasons.append("insufficient_validation_rows")
        except ValueError:
            reasons.append("development_split_unavailable")
        horizon_reports.append({
            "horizon_seconds": horizon,
            "ready_for_baseline_research": not reasons,
            "blocking_reasons": sorted(set(reasons)),
            "feature_summary": summary,
            "development_split": split_summary,
        })
    return {
        "status": ("READY_FOR_BASELINE_RESEARCH" if horizon_reports and
                   all(item["ready_for_baseline_research"]
                       for item in horizon_reports)
                   else "NOT_READY"),
        "execution_mode": "DEMO_MANUAL_ONLY",
        "payout_ratio": effective_payout,
        "payout_source": payout_source,
        "screen_payout_values": observed_payouts,
        "target_observations": len(targets),
        "distinct_target_capture_ids": len(target_capture_ids),
        "complete_capture_ids": len(complete_ids),
        "capture_completeness": capture_completeness,
        "acceptable_target_rate": acceptable_rate,
        "session": {
            "start_ms": target_times[0] if target_times else None,
            "end_ms": target_times[-1] if target_times else None,
            "duration_seconds": ((target_times[-1] - target_times[0]) / 1000
                                 if len(target_times) > 1 else 0.0),
            "longest_continuous_seconds": maximum_continuous_seconds,
            "longest_continuous_rows": continuous_rows,
            "median_gap_seconds": _percentile(gaps, 0.50),
            "p95_gap_seconds": _percentile(gaps, 0.95),
            "maximum_gap_seconds": max(gaps, default=None),
        },
        "collection_attempts": attempt_health,
        "policy": policy.__dict__,
        "horizons": horizon_reports,
        "model_training_permitted": False,
        "promotion_permitted": False,
    }


def _capture_id(point: PricePoint) -> str | None:
    value = point.metadata.get("capture_id")
    return value if isinstance(value, str) and value else None


def _acceptable(point: PricePoint) -> bool:
    return point.metadata.get("quality") == "ACCEPTABLE"


def _longest_run(times: tuple[int, ...], maximum_gap_seconds: float) -> tuple[float, int]:
    if not times:
        return 0.0, 0
    best_seconds = 0.0
    best_rows = current_rows = 1
    start = times[0]
    for left, right in zip(times, times[1:]):
        if (right - left) / 1000 <= maximum_gap_seconds:
            current_rows += 1
        else:
            best_seconds = max(best_seconds, (left - start) / 1000)
            best_rows = max(best_rows, current_rows)
            start = right
            current_rows = 1
    final_seconds = (times[-1] - start) / 1000
    if final_seconds > best_seconds:
        return final_seconds, current_rows
    return best_seconds, best_rows


def _percentile(values: tuple[float, ...], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def _read_attempt_health(database_path: str) -> dict | None:
    try:
        uri = f"file:{database_path}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='collection_attempts'").fetchone()
            if not exists:
                return None
            rows = connection.execute(
                "SELECT status,error_code,duration_ms FROM collection_attempts "
                "ORDER BY attempted_at_ms").fetchall()
    except sqlite3.Error:
        return None
    accepted = sum(row[0] == "ACCEPTED" for row in rows)
    reasons: dict[str, int] = {}
    for _, error, _ in rows:
        if error:
            reasons[error] = reasons.get(error, 0) + 1
    return {
        "attempts": len(rows),
        "accepted": accepted,
        "rejected": len(rows) - accepted,
        "acceptance_rate": accepted / len(rows) if rows else None,
        "maximum_duration_ms": max((row[2] for row in rows), default=None),
        "rejection_reasons": reasons,
    }
