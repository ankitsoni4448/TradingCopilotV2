"""Validated observer dataset export and non-promoting chronological baseline."""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from contextlib import closing
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class DatasetRow:
    asset: str
    anchor_ms: int
    label_end_ms: int
    features: tuple[float, ...]
    label: int


_FEATURE_NAMES = ("return_15s", "return_30s", "return_60s", "volatility_30s")


def build_validated_dataset(database_path: str | Path, *,
                            horizon_seconds: int = 60) -> tuple[DatasetRow, ...]:
    if horizon_seconds not in {60, 180, 300}:
        raise ValueError("unsupported_observer_horizon")
    with closing(sqlite3.connect(database_path)) as connection:
        records = connection.execute("""
            SELECT captured_at_ms,asset,price,extracted_fields_json
            FROM screen_observations WHERE price IS NOT NULL AND asset IS NOT NULL
            ORDER BY asset,captured_at_ms
        """).fetchall()
    by_asset: dict[str, list[tuple[int, float]]] = {}
    for at_ms, asset, price, fields_json in records:
        try:
            fields = json.loads(fields_json)
        except (TypeError, json.JSONDecodeError):
            continue
        if "TEMPORALLY_CONSISTENT" not in fields.get("quality_flags", []):
            continue
        price = float(price)
        if math.isfinite(price) and price > 0:
            by_asset.setdefault(str(asset), []).append((int(at_ms), price))

    rows = []
    horizon_ms = horizon_seconds * 1000
    for asset, points in by_asset.items():
        times = [point[0] for point in points]
        for index, (anchor, price) in enumerate(points):
            feature_values = []
            ready = True
            for lookback in (15, 30, 60):
                requested_at = anchor - lookback * 1000
                prior_index = bisect_right(times, requested_at) - 1
                if prior_index < 0:
                    ready = False
                    break
                prior_at, prior_price = points[prior_index]
                if requested_at - prior_at > max(10_000, lookback * 100):
                    ready = False
                    break
                feature_values.append(math.log(price / prior_price))
            if not ready:
                continue
            volatility_start = bisect_right(times, anchor - 30_000)
            history = points[volatility_start:index + 1]
            returns = [math.log(right[1] / left[1]) for left, right in
                       zip(history, history[1:]) if left[1] > 0]
            volatility = (float(np.std(returns, ddof=0)) if returns else 0.0)
            exit_index = bisect_right(times, anchor + horizon_ms - 1)
            if exit_index >= len(points):
                continue
            exit_at, exit_price = points[exit_index]
            if exit_at - (anchor + horizon_ms) > max(20_000, horizon_ms // 10):
                continue
            future_return = math.log(exit_price / price)
            label = 1 if future_return > 1e-8 else 0 if future_return < -1e-8 else -1
            if label < 0:
                continue
            rows.append(DatasetRow(asset, anchor, exit_at,
                                   (*feature_values, volatility), label))
    return tuple(sorted(rows, key=lambda row: (row.anchor_ms, row.asset)))


def chronological_partitions(rows: tuple[DatasetRow, ...], *,
                             minimum_each: int = 30) -> dict:
    ordered = tuple(sorted(rows, key=lambda row: row.anchor_ms))
    if len(ordered) < minimum_each * 3:
        return {"status": "NOT_READY", "reason": "insufficient_validated_samples",
                "total_rows": len(ordered), "train": (), "validation": (), "test": ()}
    validation_boundary = ordered[int(len(ordered) * 0.6)].anchor_ms
    test_boundary = ordered[int(len(ordered) * 0.8)].anchor_ms
    train = tuple(row for row in ordered if row.anchor_ms < validation_boundary and
                  row.label_end_ms < validation_boundary)
    validation = tuple(row for row in ordered
                       if validation_boundary <= row.anchor_ms < test_boundary and
                       row.label_end_ms < test_boundary)
    test = tuple(row for row in ordered if row.anchor_ms >= test_boundary)
    if min(len(train), len(validation), len(test)) < minimum_each:
        return {"status": "NOT_READY", "reason": "chronological_split_too_small",
                "total_rows": len(ordered), "train": train,
                "validation": validation, "test": test}
    if any(len({row.label for row in part}) < 2
           for part in (train, validation, test)):
        return {"status": "NOT_READY", "reason": "single_class_partition",
                "total_rows": len(ordered), "train": train,
                "validation": validation, "test": test}
    return {"status": "READY", "reason": None, "total_rows": len(ordered),
            "train": train, "validation": validation, "test": test}


def evaluate_baseline(database_path: str | Path, *, horizon_seconds: int = 60,
                      payout_ratio: float | None = None,
                      minimum_each: int = 100) -> dict:
    if payout_ratio is not None and not 0 < payout_ratio <= 1:
        raise ValueError("payout_ratio_must_be_in_0_1")
    rows = build_validated_dataset(database_path, horizon_seconds=horizon_seconds)
    split = chronological_partitions(rows, minimum_each=minimum_each)
    break_even = (1 / (1 + payout_ratio)
                  if payout_ratio is not None and 0 < payout_ratio <= 1 else None)
    report = {
        "status": split["status"], "reason": split["reason"],
        "horizon_seconds": horizon_seconds, "total_rows": split["total_rows"],
        "feature_names": list(_FEATURE_NAMES),
        "class_balance": {name: sum(row.label == label for row in rows)
                          for label, name in ((1, "UP"), (0, "DOWN"))},
        "splits": {name: len(split[name]) for name in ("train", "validation", "test")},
        "break_even_win_rate": break_even,
        "no_trade_thresholds": {
            "up_probability_at_least": break_even,
            "down_probability_at_least": break_even,
            "require_out_of_sample_approval": True,
        },
        "decision": "NO_TRADE",
        "decision_reason": "baseline_not_out_of_sample_approved",
        "promotion_status": "NOT_PROMOTED",
    }
    if split["status"] != "READY":
        return report
    train, validation, test = (split[name] for name in ("train", "validation", "test"))
    x_train = np.asarray([row.features for row in train], dtype=float)
    mean, scale = x_train.mean(axis=0), x_train.std(axis=0)
    scale[scale == 0] = 1
    x_train = (x_train - mean) / scale
    y_train = np.asarray([row.label for row in train], dtype=float)
    weights = np.zeros(x_train.shape[1] + 1)
    design = np.column_stack((np.ones(len(x_train)), x_train))
    for _ in range(1200):
        probability = _sigmoid(design @ weights)
        gradient = design.T @ (probability - y_train) / len(y_train)
        gradient[1:] += 0.001 * weights[1:]
        weights -= 0.1 * gradient
    validation_metrics = _score(validation, weights, mean, scale)
    test_metrics = _score(test, weights, mean, scale)
    report.update({
        "status": "EVALUATED_NOT_APPROVED",
        "validation": validation_metrics,
        "test": test_metrics,
        "calibration": _calibration(test, weights, mean, scale),
        "no_trade_rule": (
            "NO_TRADE unless validated payout threshold and approved OOS model; "
            "current output remains NO_TRADE"),
        "promotion_status": "NOT_PROMOTED_TEST_IS_REPORT_ONLY",
    })
    return report


def export_dataset(database_path: str | Path, output_path: str | Path, *,
                   horizon_seconds: int = 60, minimum_each: int = 100) -> dict:
    rows = build_validated_dataset(database_path, horizon_seconds=horizon_seconds)
    split = chronological_partitions(rows, minimum_each=minimum_each)
    if split["status"] != "READY":
        return {"status": "NOT_READY", "reason": split["reason"],
                "rows": len(rows), "output_written": False}
    path = Path(output_path)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("asset", "anchor_ms", *_FEATURE_NAMES, "label", "partition"))
        for partition in ("train", "validation", "test"):
            for row in split[partition]:
                writer.writerow((row.asset, row.anchor_ms, *row.features,
                                 "UP" if row.label else "DOWN", partition))
    return {"status": "READY", "rows": len(rows), "output_written": True,
            "path": str(path)}


def _score(rows: tuple[DatasetRow, ...], weights: np.ndarray,
           mean: np.ndarray, scale: np.ndarray) -> dict:
    x = (np.asarray([row.features for row in rows]) - mean) / scale
    design = np.column_stack((np.ones(len(x)), x))
    probabilities = _sigmoid(design @ weights)
    actual = np.asarray([row.label for row in rows])
    return {"rows": len(rows), "class_balance": {
                "UP": int(actual.sum()), "DOWN": int(len(actual) - actual.sum())},
            "accuracy": float(((probabilities >= 0.5) == actual).mean()),
            "brier_score": float(np.mean((probabilities - actual) ** 2)),
            "log_loss": float(-np.mean(actual * np.log(np.clip(probabilities, 1e-9, 1)) +
                                       (1 - actual) * np.log(np.clip(1 - probabilities, 1e-9, 1))))}


def _calibration(rows: tuple[DatasetRow, ...], weights: np.ndarray,
                 mean: np.ndarray, scale: np.ndarray) -> list[dict]:
    x = (np.asarray([row.features for row in rows]) - mean) / scale
    probabilities = _sigmoid(np.column_stack((np.ones(len(x)), x)) @ weights)
    labels = np.asarray([row.label for row in rows])
    output = []
    for lower in np.linspace(0, 0.9, 10):
        selected = (probabilities >= lower) & (probabilities < lower + 0.1)
        if selected.any():
            output.append({"lower_probability": float(lower),
                           "count": int(selected.sum()),
                           "mean_probability": float(probabilities[selected].mean()),
                           "observed_up_rate": float(labels[selected].mean())})
    return output


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-np.clip(values, -40, 40)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--horizon", type=int, choices=(60, 180, 300), default=60)
    parser.add_argument("--payout", type=float)
    parser.add_argument("--min-per-split", type=int, default=100)
    parser.add_argument("--export", type=Path)
    args = parser.parse_args(argv)
    report = evaluate_baseline(
        args.database, horizon_seconds=args.horizon, payout_ratio=args.payout,
        minimum_each=args.min_per_split)
    if args.export:
        report["export"] = export_dataset(
            args.database, args.export, horizon_seconds=args.horizon,
            minimum_each=args.min_per_split)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
