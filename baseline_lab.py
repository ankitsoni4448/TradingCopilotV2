"""Validation-only directional baselines for causal Crypto IDX features.

This module intentionally accepts ``FeatureDevelopmentSplit`` rather than raw
rows.  The split contains development rows and opaque holdout metadata, so a
baseline experiment cannot accidentally score the final chronological holdout.
The rules emit directions, not probabilities.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from evaluation import fixed_payout_metrics
from feature_engine import (
    FeatureDevelopmentSplit,
    FeatureRow,
    label_available_at_ms,
)

BASELINE_REGISTRY_VERSION = "directional_baselines_v1"
FAMILYWISE_ALPHA = 0.05


@dataclass(frozen=True)
class BaselineSpec:
    name: str
    feature_name: str | None


BASELINE_REGISTRY_V1 = (
    BaselineSpec("TRAIN_MAJORITY", None),
    BaselineSpec("TARGET_MOMENTUM_15S", "target_log_return_15s"),
    BaselineSpec("TARGET_MOMENTUM_30S", "target_log_return_30s"),
    BaselineSpec("TARGET_MOMENTUM_60S", "target_log_return_60s"),
    BaselineSpec("MARKET_EQUAL_MOMENTUM_15S", "market_equal_log_return_15s"),
    BaselineSpec("MARKET_EQUAL_MOMENTUM_30S", "market_equal_log_return_30s"),
    BaselineSpec("MARKET_EQUAL_MOMENTUM_60S", "market_equal_log_return_60s"),
)


def evaluate_baseline_development(
        split: FeatureDevelopmentSplit, *, payout_ratio: float,
        minimum_validation_signals: int = 30) -> dict:
    """Evaluate fixed rules on development partitions; never expose holdout rows."""
    if not 0 < payout_ratio <= 1:
        raise ValueError("payout_ratio must be in (0, 1]")
    if minimum_validation_signals <= 0:
        raise ValueError("minimum_validation_signals must be positive")
    if not split.train or not split.validation:
        raise ValueError("non-empty train and validation partitions are required")

    schema = {name for name, _ in split.train[0].features}
    if any(spec.feature_name not in schema for spec in BASELINE_REGISTRY_V1
           if spec.feature_name is not None):
        raise ValueError("feature schema does not satisfy baseline registry v1")
    majority = _majority_direction(split.train)
    candidates = []
    for spec in BASELINE_REGISTRY_V1:
        train = _directional_metrics(
            split.train, spec, majority, payout_ratio=payout_ratio)
        validation = _directional_metrics(
            split.validation, spec, majority, payout_ratio=payout_ratio)
        candidates.append({
            "name": spec.name,
            "feature_name": spec.feature_name,
            "train": train,
            "validation": validation,
        })

    eligible = [candidate for candidate in candidates
                if candidate["validation"]["signals"] >=
                minimum_validation_signals]
    # Python's max retains the first item on an exact key tie. Registry order is
    # therefore the preregistered simplicity preference: majority before a
    # feature rule, then shorter before longer lookbacks.
    selected = max(eligible, key=_selection_key) if eligible else None
    return {
        "registry_version": BASELINE_REGISTRY_VERSION,
        "horizon_seconds": split.train[0].horizon_seconds,
        "payout_ratio_assumption": payout_ratio,
        "break_even_win_rate": 1 / (1 + payout_ratio),
        "minimum_validation_signals": minimum_validation_signals,
        "hypothesis_count": len(BASELINE_REGISTRY_V1),
        "familywise_alpha": FAMILYWISE_ALPHA,
        "bonferroni_alpha_per_hypothesis": (
            FAMILYWISE_ALPHA / len(BASELINE_REGISTRY_V1)),
        "selection_partition": "VALIDATION_ONLY",
        "selection_objective": (
            "MAX_VALIDATION_EV_THEN_ACCURACY_THEN_COVERAGE_FIXED_REGISTRY"),
        "probability_metrics": "NOT_APPLICABLE_DIRECTIONAL_RULES_ONLY",
        "candidates": candidates,
        "selected_development_candidate": (
            selected["name"] if selected is not None else None),
        "promotion_status": "NOT_PROMOTED_VALIDATION_ONLY",
        "current_champion": "NONE_REAL_CRYPTO_IDX_MODEL",
        "holdout": {
            "status": "SEALED_NOT_EVALUATED",
            "count": split.holdout.count,
            "start_ms": split.holdout.start_ms,
            "end_ms": split.holdout.end_ms,
            "fingerprint": split.holdout.fingerprint,
        },
    }


def evaluate_baseline_walk_forward(
        split: FeatureDevelopmentSplit, *, payout_ratio: float,
        folds: int = 4, minimum_train_rows: int = 60,
        minimum_test_signals: int = 20) -> dict:
    """Expanding-window folds inside training data only.

    The outer validation partition and final holdout are not scored.  Each fold
    purges training rows whose labels were not observable before its test start.
    """
    if not 0 < payout_ratio <= 1:
        raise ValueError("payout_ratio must be in (0, 1]")
    if folds < 2:
        raise ValueError("folds must be at least two")
    if minimum_train_rows <= 0 or minimum_test_signals <= 0:
        raise ValueError("minimum row and signal gates must be positive")
    ordered = tuple(sorted(split.train, key=lambda row: row.anchor_ms))
    remaining = len(ordered) - minimum_train_rows
    test_size = remaining // folds
    if test_size < minimum_test_signals:
        raise ValueError("not enough training rows for requested walk-forward folds")
    initial_train = len(ordered) - folds * test_size
    embargo_ms = split.embargo_seconds * 1000
    fold_reports = []
    for fold_index in range(folds):
        test_start_index = initial_train + fold_index * test_size
        test_end_index = (test_start_index + test_size
                          if fold_index < folds - 1 else len(ordered))
        test = ordered[test_start_index:test_end_index]
        boundary = test[0].anchor_ms
        candidate_train = ordered[:test_start_index]
        train = tuple(row for row in candidate_train
                      if max(row.anchor_ms + embargo_ms,
                             label_available_at_ms(row)) < boundary)
        if len(train) < minimum_train_rows:
            raise ValueError("purging leaves insufficient fold training rows")
        majority = _majority_direction(train)
        candidates = []
        for spec in BASELINE_REGISTRY_V1:
            metrics = _directional_metrics(
                test, spec, majority, payout_ratio=payout_ratio)
            candidates.append({"name": spec.name, "metrics": metrics})
        eligible = [candidate for candidate in candidates
                    if candidate["metrics"]["signals"] >=
                    minimum_test_signals]
        selected = max(eligible, key=lambda candidate: _metrics_key(
            candidate["metrics"])) if eligible else None
        fold_reports.append({
            "fold": fold_index + 1,
            "train_rows": len(train),
            "purged_train_rows": len(candidate_train) - len(train),
            "test_rows": len(test),
            "test_start_ms": test[0].anchor_ms,
            "test_end_ms": test[-1].anchor_ms,
            "selected_rule": selected["name"] if selected else None,
            "candidates": candidates,
        })

    stability = []
    for spec in BASELINE_REGISTRY_V1:
        metrics = [next(candidate["metrics"]
                        for candidate in fold["candidates"]
                        if candidate["name"] == spec.name)
                   for fold in fold_reports]
        accuracies = [value["accuracy"] for value in metrics
                      if value["accuracy"] is not None]
        evs = [value["payout_metrics"]["expected_value_per_unit_stake"]
               for value in metrics
               if value["payout_metrics"]["expected_value_per_unit_stake"]
               is not None]
        stability.append({
            "name": spec.name,
            "folds_evaluated": len(metrics),
            "total_signals": sum(value["signals"] for value in metrics),
            "mean_fold_accuracy": sum(accuracies) / len(accuracies),
            "minimum_fold_accuracy": min(accuracies),
            "maximum_fold_accuracy": max(accuracies),
            "mean_fold_ev": sum(evs) / len(evs),
            "minimum_fold_ev": min(evs),
            "positive_ev_folds": sum(value > 0 for value in evs),
            "wilson_support_folds": sum(
                value["wilson_lower_exceeds_break_even"] for value in metrics),
            "worst_fold_drawdown_units": max(
                value["payout_metrics"]["max_drawdown_units"]
                for value in metrics),
            "maximum_losing_streak": max(
                value["payout_metrics"]["max_losing_streak"]
                for value in metrics),
        })
    selection_counts = {
        spec.name: sum(fold["selected_rule"] == spec.name
                       for fold in fold_reports)
        for spec in BASELINE_REGISTRY_V1}
    return {
        "registry_version": BASELINE_REGISTRY_VERSION,
        "scope": "INNER_TRAINING_PARTITION_ONLY",
        "horizon_seconds": ordered[0].horizon_seconds,
        "folds": fold_reports,
        "stability": stability,
        "selection_counts": selection_counts,
        "outer_validation_status": "NOT_EVALUATED_BY_WALK_FORWARD",
        "holdout": {
            "status": "SEALED_NOT_EVALUATED",
            "count": split.holdout.count,
            "start_ms": split.holdout.start_ms,
            "end_ms": split.holdout.end_ms,
            "fingerprint": split.holdout.fingerprint,
        },
        "promotion_status": "NOT_PROMOTED_INNER_DEVELOPMENT_ONLY",
        "probability_metrics": "NOT_APPLICABLE_DIRECTIONAL_RULES_ONLY",
    }


def _majority_direction(rows: tuple[FeatureRow, ...]) -> str:
    ups = sum(row.label == "UP" for row in rows)
    downs = sum(row.label == "DOWN" for row in rows)
    return "UP" if ups >= downs else "DOWN"


def _prediction(row: FeatureRow, spec: BaselineSpec,
                majority: str) -> str:
    if spec.feature_name is None:
        return majority
    value = row.feature_dict()[spec.feature_name]
    if not math.isfinite(value):
        return "NO_TRADE"
    if value > 0:
        return "UP"
    if value < 0:
        return "DOWN"
    return "NO_TRADE"


def _directional_metrics(
        rows: tuple[FeatureRow, ...], spec: BaselineSpec, majority: str,
        *, payout_ratio: float) -> dict:
    eligible = [row for row in rows if row.label in {"UP", "DOWN"}]
    predictions = [(row, _prediction(row, spec, majority)) for row in eligible]
    signals = [(row, prediction) for row, prediction in predictions
               if prediction in {"UP", "DOWN"}]
    outcomes = [prediction == row.label for row, prediction in signals]
    confusion = {"UP_UP": 0, "UP_DOWN": 0,
                 "DOWN_UP": 0, "DOWN_DOWN": 0}
    for row, prediction in signals:
        confusion[f"{row.label}_{prediction}"] += 1
    wins = sum(outcomes)
    payout = fixed_payout_metrics(outcomes, payout_ratio)
    interval = _wilson_interval(wins, len(signals))
    return {
        "rows": len(rows),
        "eligible_binary_labels": len(eligible),
        "signals": len(signals),
        "coverage": len(signals) / len(eligible) if eligible else None,
        "accuracy": wins / len(signals) if signals else None,
        "accuracy_wilson_95": interval,
        "wilson_lower_exceeds_break_even": (
            interval[0] > payout["break_even_win_rate"]
            if interval is not None else False),
        "confusion_matrix": confusion,
        "no_trade_count": len(eligible) - len(signals),
        "flat_label_count": len(rows) - len(eligible),
        "payout_metrics": payout,
    }


def _selection_key(candidate: dict) -> tuple[float, float, float]:
    return _metrics_key(candidate["validation"])


def _metrics_key(metrics: dict) -> tuple[float, float, float]:
    payout = metrics["payout_metrics"]
    return (
        float(payout["expected_value_per_unit_stake"]),
        float(metrics["accuracy"]),
        float(metrics["coverage"]),
    )


def _wilson_interval(successes: int, total: int) -> list[float] | None:
    if total == 0:
        return None
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (z / denominator) * math.sqrt(
        proportion * (1 - proportion) / total +
        z * z / (4 * total * total))
    return [center - margin, center + margin]
