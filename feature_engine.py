"""Causal multi-market feature datasets for separate Crypto IDX horizons.

Every feature is computed from observations at or before the anchor timestamp.
Future target observations are used only for the label. This module builds
datasets; it neither trains nor promotes a model.
"""
from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from market_data import DEFAULT_REFERENCE_SYMBOLS
from observation_store import ObservationStore, PricePoint
from reference_basket import return_direction
from relationship_lab import ALLOWED_HORIZONS_SECONDS, PREREGISTERED_V1_LOOKBACKS

FEATURE_SCHEMA_VERSION = "multi_market_causal_v1"


@dataclass(frozen=True)
class FeatureRow:
    anchor_ms: int
    target_observed_at_ms: int
    entry_observed_at_ms: int
    entry_delay_ms: int
    horizon_seconds: int
    features: tuple[tuple[str, float], ...]
    label: str
    target_log_return: float
    max_feature_age_ms: int
    target_exit_offset_ms: int

    def feature_dict(self) -> dict[str, float]:
        return dict(self.features)


@dataclass(frozen=True)
class FeatureBuildResult:
    rows: tuple[FeatureRow, ...]
    target_anchors: int
    rejection_reasons: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class SealedFeatureHoldout:
    count: int
    start_ms: int
    end_ms: int
    fingerprint: str


@dataclass(frozen=True)
class FeatureDevelopmentSplit:
    train: tuple[FeatureRow, ...]
    validation: tuple[FeatureRow, ...]
    holdout: SealedFeatureHoldout
    embargo_seconds: int


def build_feature_dataset(
        store: ObservationStore, *, horizon_seconds: int,
        symbols: tuple[str, ...] = DEFAULT_REFERENCE_SYMBOLS,
        lookbacks_seconds: tuple[int, ...] = PREREGISTERED_V1_LOOKBACKS,
        target_source: str = "binomo_screen",
        target_instrument: str = "CRYPTO_IDX",
        reference_source: str = "binance_spot_public",
        feature_max_age_ms: int = 2_000,
        outcome_tolerance_ms: int = 2_000,
        max_decision_delay_ms: int = 5_000,
        max_entry_delay_ms: int = 7_000,
        acceptable_quality_only: bool = True,
) -> FeatureBuildResult:
    if horizon_seconds not in ALLOWED_HORIZONS_SECONDS:
        raise ValueError("unsupported forecast horizon")
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("symbols must be non-empty and unique")
    if not lookbacks_seconds or len(set(lookbacks_seconds)) != len(lookbacks_seconds):
        raise ValueError("lookbacks must be non-empty and unique")
    if any(value <= 0 for value in lookbacks_seconds):
        raise ValueError("lookbacks must be positive")
    if (feature_max_age_ms < 0 or outcome_tolerance_ms < 0 or
            max_decision_delay_ms < 0 or max_entry_delay_ms < 0):
        raise ValueError("timing tolerances must be non-negative")

    targets = store.price_series(target_source, target_instrument)
    target_index = _index(targets)
    target_available_index = _availability_index(targets)
    reference_series = {
        symbol: store.price_series(reference_source, symbol)
        for symbol in symbols}
    reference_available_indices = {
        symbol: _availability_index(points)
        for symbol, points in reference_series.items()}
    reference_captures = {
        symbol: _capture_map(points)
        for symbol, points in reference_series.items()}
    rows: list[FeatureRow] = []
    rejected: dict[str, int] = {}
    horizon_ms = horizon_seconds * 1000
    for target in targets:
        if acceptable_quality_only and not _acceptable(target):
            _reject(rejected, "target_quality")
            continue
        capture_id = target.metadata.get("capture_id")
        target_available_at = _availability_ms(target)
        if not isinstance(capture_id, str) or not capture_id or target_available_at is None:
            _reject(rejected, "missing_capture_context")
            continue
        current_references = [reference_captures[symbol].get(capture_id)
                              for symbol in symbols]
        if any(point is None for point in current_references):
            _reject(rejected, "missing_same_capture_reference")
            continue
        current_references = [point for point in current_references
                              if point is not None]
        if acceptable_quality_only and any(
                not _acceptable(point) for point in current_references):
            _reject(rejected, "reference_quality")
            continue
        reference_available = [_availability_ms(point)
                               for point in current_references]
        if any(value is None for value in reference_available):
            _reject(rejected, "missing_availability_time")
            continue
        decision_at_ms = max(target_available_at,
                             *(int(value) for value in reference_available
                               if value is not None))
        if abs(decision_at_ms - target.observed_at_ms) > max_decision_delay_ms:
            _reject(rejected, "decision_delay")
            continue
        label_entry = _first_point_at_or_after(
            target_index, decision_at_ms, max_entry_delay_ms)
        if label_entry is None:
            _reject(rejected, "missing_post_decision_entry")
            continue
        if acceptable_quality_only and not _acceptable(label_entry):
            _reject(rejected, "entry_quality")
            continue
        target_exit = _nearest_point(
            target_index, label_entry.observed_at_ms + horizon_ms,
            outcome_tolerance_ms)
        if target_exit is None:
            _reject(rejected, "missing_target_exit")
            continue
        if acceptable_quality_only and not _acceptable(target_exit):
            _reject(rejected, "target_exit_quality")
            continue

        feature_values: dict[str, float] = {}
        feature_ages: list[int] = []
        failure: str | None = None
        for lookback in sorted(lookbacks_seconds):
            expected_entry_ms = decision_at_ms - lookback * 1000
            target_entry = _at_or_before_available(
                target_available_index, expected_entry_ms,
                feature_max_age_ms)
            if target_entry is None:
                failure = "missing_target_history"
                break
            if acceptable_quality_only and not _acceptable(target_entry):
                failure = "target_history_quality"
                break
            target_return = math.log(target.price / target_entry.price)
            feature_values[f"target_log_return_{lookback}s"] = target_return
            feature_ages.extend([
                expected_entry_ms - int(_availability_ms(target_entry)),
                decision_at_ms - target_available_at,
            ])

            reference_returns: list[float] = []
            for symbol, current in zip(symbols, current_references):
                entry = _at_or_before_available(
                    reference_available_indices[symbol], expected_entry_ms,
                    feature_max_age_ms)
                if entry is None:
                    failure = "missing_reference"
                    break
                if acceptable_quality_only and (
                        not _acceptable(entry) or not _acceptable(current)):
                    failure = "reference_quality"
                    break
                value = math.log(current.price / entry.price)
                feature_values[f"{symbol}_log_return_{lookback}s"] = value
                reference_returns.append(value)
                feature_ages.extend([
                    expected_entry_ms - int(_availability_ms(entry)),
                    decision_at_ms - int(_availability_ms(current)),
                ])
            if failure is not None:
                break
            mean_return = sum(reference_returns) / len(reference_returns)
            feature_values[f"market_equal_log_return_{lookback}s"] = mean_return
            feature_values[f"market_breadth_{lookback}s"] = (
                sum(_sign(value) for value in reference_returns) /
                len(reference_returns))
            feature_values[f"market_dispersion_{lookback}s"] = math.sqrt(
                sum((value - mean_return) ** 2 for value in reference_returns) /
                len(reference_returns))
        if failure is not None:
            _reject(rejected, failure)
            continue
        if not all(math.isfinite(value) for value in feature_values.values()):
            _reject(rejected, "non_finite_feature")
            continue
        target_future_return = math.log(target_exit.price / label_entry.price)
        rows.append(FeatureRow(
            anchor_ms=decision_at_ms,
            target_observed_at_ms=target.observed_at_ms,
            entry_observed_at_ms=label_entry.observed_at_ms,
            entry_delay_ms=label_entry.observed_at_ms - decision_at_ms,
            horizon_seconds=horizon_seconds,
            features=tuple(sorted(feature_values.items())),
            label=return_direction(target_future_return),
            target_log_return=target_future_return,
            max_feature_age_ms=max(feature_ages, default=0),
            target_exit_offset_ms=(target_exit.observed_at_ms -
                                   (label_entry.observed_at_ms + horizon_ms)),
        ))
    return FeatureBuildResult(
        tuple(rows), len(targets), tuple(sorted(rejected.items())))


def chronological_feature_split(
        rows: tuple[FeatureRow, ...], *, train_fraction: float = 0.60,
        validation_fraction: float = 0.20,
        embargo_seconds: int | None = None) -> FeatureDevelopmentSplit:
    if not rows:
        raise ValueError("feature rows are required")
    if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1:
        raise ValueError("split fractions must be in (0, 1)")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("train and validation must leave a holdout")
    ordered = tuple(sorted(rows, key=lambda row: row.anchor_ms))
    horizons = {row.horizon_seconds for row in ordered}
    schemas = {tuple(name for name, _ in row.features) for row in ordered}
    if len(horizons) != 1:
        raise ValueError("split one forecast horizon at a time")
    if len(schemas) != 1:
        raise ValueError("all feature rows must share one schema")
    horizon = ordered[0].horizon_seconds
    embargo = horizon if embargo_seconds is None else embargo_seconds
    if embargo < horizon:
        raise ValueError("embargo must cover the forecast horizon")
    train_end = max(1, int(len(ordered) * train_fraction))
    validation_end = max(train_end + 1,
                         int(len(ordered) * (train_fraction +
                                            validation_fraction)))
    if validation_end >= len(ordered):
        raise ValueError("not enough rows for train/validation/holdout")
    validation_start_ms = ordered[train_end].anchor_ms
    holdout_start_ms = ordered[validation_end].anchor_ms
    embargo_ms = embargo * 1000
    train = tuple(row for row in ordered[:train_end]
                  if _purged_before(row, validation_start_ms, embargo_ms))
    validation = tuple(row for row in ordered[train_end:validation_end]
                       if _purged_before(row, holdout_start_ms, embargo_ms))
    holdout_rows = ordered[validation_end:]
    if not train or not validation or not holdout_rows:
        raise ValueError("embargo leaves an empty split")
    holdout = SealedFeatureHoldout(
        count=len(holdout_rows), start_ms=holdout_rows[0].anchor_ms,
        end_ms=holdout_rows[-1].anchor_ms,
        fingerprint=_dataset_fingerprint(holdout_rows))
    return FeatureDevelopmentSplit(train, validation, holdout, embargo)


def feature_dataset_summary(result: FeatureBuildResult) -> dict:
    rows = result.rows
    labels = {label: sum(row.label == label for row in rows)
              for label in ("UP", "DOWN", "FLAT")}
    schema = tuple(name for name, _ in rows[0].features) if rows else ()
    return {
        "schema_version": FEATURE_SCHEMA_VERSION,
        "target_anchors": result.target_anchors,
        "rows": len(rows),
        "coverage": (len(rows) / result.target_anchors
                     if result.target_anchors else None),
        "feature_count": len(schema),
        "schema_fingerprint": _schema_fingerprint(
            rows[0].horizon_seconds, schema) if rows else None,
        "dataset_fingerprint": _dataset_fingerprint(rows) if rows else None,
        "label_counts": labels,
        "max_feature_age_ms": max(
            (row.max_feature_age_ms for row in rows), default=None),
        "max_absolute_target_exit_offset_ms": max(
            (abs(row.target_exit_offset_ms) for row in rows), default=None),
        "max_entry_delay_ms": max(
            (row.entry_delay_ms for row in rows), default=None),
        "rejection_reasons": dict(result.rejection_reasons),
        "probability_metrics": "NOT_APPLICABLE_FEATURE_DATASET_ONLY",
    }


def feature_development_summary(split: FeatureDevelopmentSplit) -> dict:
    return {
        "train_rows": len(split.train),
        "validation_rows": len(split.validation),
        "holdout": {
            "status": "SEALED_NOT_EVALUATED",
            "count": split.holdout.count,
            "start_ms": split.holdout.start_ms,
            "end_ms": split.holdout.end_ms,
            "fingerprint": split.holdout.fingerprint,
        },
        "embargo_seconds": split.embargo_seconds,
    }


def _acceptable(point: PricePoint) -> bool:
    return point.metadata.get("quality") == "ACCEPTABLE"


def _index(points: tuple[PricePoint, ...]) -> tuple[tuple[int, ...],
                                                   tuple[PricePoint, ...]]:
    return tuple(point.observed_at_ms for point in points), points


def _at_or_before_available(
        index: tuple[tuple[int, ...], tuple[PricePoint, ...]],
        timestamp_ms: int, max_age_ms: int) -> PricePoint | None:
    times, points = index
    position = bisect_right(times, timestamp_ms) - 1
    if position < 0 or timestamp_ms - times[position] > max_age_ms:
        return None
    return points[position]


def _first_point_at_or_after(
        index: tuple[tuple[int, ...], tuple[PricePoint, ...]],
        timestamp_ms: int, max_delay_ms: int) -> PricePoint | None:
    times, points = index
    position = bisect_left(times, timestamp_ms)
    if position >= len(points) or times[position] - timestamp_ms > max_delay_ms:
        return None
    return points[position]


def _nearest_point(index: tuple[tuple[int, ...], tuple[PricePoint, ...]],
                   expected_ms: int, tolerance_ms: int) -> PricePoint | None:
    times, points = index
    position = bisect_left(times, expected_ms)
    candidates = [points[value] for value in (position - 1, position)
                  if 0 <= value < len(points) and
                  abs(times[value] - expected_ms) <= tolerance_ms]
    if not candidates:
        return None
    return min(candidates,
               key=lambda point: (abs(point.observed_at_ms - expected_ms),
                                  point.observed_at_ms))


def _sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _reject(reasons: dict[str, int], reason: str) -> None:
    reasons[reason] = reasons.get(reason, 0) + 1


def _availability_ms(point: PricePoint) -> int | None:
    value = point.metadata.get("received_at_ms",
                               point.metadata.get("server_received_at_ms"))
    return int(value) if isinstance(value, (int, float)) and value > 0 else None


def _availability_index(
        points: tuple[PricePoint, ...]) -> tuple[tuple[int, ...],
                                                 tuple[PricePoint, ...]]:
    available = [(value, point) for point in points
                 if (value := _availability_ms(point)) is not None]
    available.sort(key=lambda item: (item[0], item[1].observed_at_ms))
    return (tuple(item[0] for item in available),
            tuple(item[1] for item in available))


def _capture_map(points: tuple[PricePoint, ...]) -> dict[str, PricePoint]:
    result: dict[str, PricePoint] = {}
    for point in points:
        capture_id = point.metadata.get("capture_id")
        if isinstance(capture_id, str) and capture_id:
            result[capture_id] = point
    return result


def _schema_fingerprint(horizon: int, schema: tuple[str, ...]) -> str:
    payload = [FEATURE_SCHEMA_VERSION, horizon, schema]
    return hashlib.sha256(json.dumps(
        payload, separators=(",", ":")).encode("utf-8")).hexdigest()


def _dataset_fingerprint(rows: tuple[FeatureRow, ...]) -> str:
    payload = [(
        row.anchor_ms, row.target_observed_at_ms, row.entry_observed_at_ms,
        row.entry_delay_ms, row.horizon_seconds, row.features, row.label,
        row.target_log_return, row.max_feature_age_ms,
        row.target_exit_offset_ms,
    ) for row in rows]
    return hashlib.sha256(json.dumps(
        payload, separators=(",", ":")).encode("utf-8")).hexdigest()


def label_available_at_ms(row: FeatureRow) -> int:
    """Timestamp when the outcome used by ``row.label`` was observable."""
    return (row.entry_observed_at_ms + row.horizon_seconds * 1000 +
            row.target_exit_offset_ms)


def _purged_before(row: FeatureRow, boundary_ms: int,
                   embargo_ms: int) -> bool:
    return max(row.anchor_ms + embargo_ms,
               label_available_at_ms(row)) < boundary_ms
