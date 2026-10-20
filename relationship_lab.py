"""Leakage-resistant lead/lag experiments for Crypto IDX reference hypotheses.

The laboratory creates features solely from reference observations available at
the prediction timestamp. Target outcomes are read from the future only after
the feature boundary has been fixed. It does not train or promote a model.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from observation_store import ObservationStore, PricePoint
from reference_basket import ReferenceBasketSpec, basket_log_return, return_direction

ALLOWED_HORIZONS_SECONDS = frozenset({30, 60, 120, 180, 300})
ALLOWED_LOOKBACK_SECONDS = frozenset({5, 10, 15, 30, 60})
PREREGISTERED_V1_LOOKBACKS = (15, 30, 60)
PREREGISTERED_V1_HORIZONS = (30, 60, 120, 180, 300)
FAMILYWISE_ALPHA = 0.05


@dataclass(frozen=True)
class LeadLagSample:
    anchor_ms: int
    lookback_seconds: int
    horizon_seconds: int
    basket_identity: str
    basket_fingerprint: str
    reference_log_return: float
    target_log_return: float
    predicted_direction: str
    actual_direction: str
    correct: bool | None
    max_feature_age_ms: int
    target_exit_offset_ms: int


@dataclass(frozen=True)
class SealedHoldout:
    """Opaque holdout description; observations are intentionally not exposed."""
    count: int
    start_ms: int | None
    end_ms: int | None
    fingerprint: str

    def __repr__(self) -> str:
        return ("SealedHoldout(count=" + str(self.count) +
                ", start_ms=" + str(self.start_ms) +
                ", end_ms=" + str(self.end_ms) +
                ", fingerprint='" + self.fingerprint + "')")


@dataclass(frozen=True)
class ChronologicalDevelopmentSplit:
    train: tuple[LeadLagSample, ...]
    validation: tuple[LeadLagSample, ...]
    holdout: SealedHoldout
    embargo_seconds: int


def preregistered_v1_registry(basket_count: int) -> dict:
    """Describe the fixed initial search budget before outcomes are inspected."""
    if basket_count <= 0:
        raise ValueError("basket_count must be positive")
    hypotheses = (basket_count * len(PREREGISTERED_V1_LOOKBACKS) *
                  len(PREREGISTERED_V1_HORIZONS))
    return {
        "lookbacks_seconds": list(PREREGISTERED_V1_LOOKBACKS),
        "horizons_seconds": list(PREREGISTERED_V1_HORIZONS),
        "basket_count": basket_count,
        "hypothesis_count": hypotheses,
        "familywise_alpha": FAMILYWISE_ALPHA,
        "bonferroni_alpha_per_hypothesis": FAMILYWISE_ALPHA / hypotheses,
        "selection_partition": "VALIDATION_ONLY",
        "holdout_policy": "SEALED_UNTIL_ONE_FINAL_CONFIRMATION",
    }


def build_lead_lag_samples(
        store: ObservationStore, *, basket: ReferenceBasketSpec,
        lookback_seconds: int, horizon_seconds: int,
        target_source: str = "binomo_screen",
        target_instrument: str = "CRYPTO_IDX",
        reference_source: str = "binance_spot_public",
        feature_max_age_ms: int = 2_000,
        outcome_tolerance_ms: int = 2_000,
        acceptable_quality_only: bool = True,
) -> tuple[LeadLagSample, ...]:
    """Build predictive samples with an explicit information-time boundary."""
    if horizon_seconds not in ALLOWED_HORIZONS_SECONDS:
        raise ValueError("unsupported forecast horizon")
    if lookback_seconds not in ALLOWED_LOOKBACK_SECONDS:
        raise ValueError("unsupported reference lookback")
    if feature_max_age_ms < 0 or outcome_tolerance_ms < 0:
        raise ValueError("timing tolerances must be non-negative")

    targets = store.price_series(target_source, target_instrument)
    samples: list[LeadLagSample] = []
    lookback_ms = lookback_seconds * 1000
    horizon_ms = horizon_seconds * 1000
    for target in targets:
        if acceptable_quality_only and not _acceptable(target):
            continue
        anchor_ms = target.observed_at_ms
        target_exit = _nearest_target_exit(
            targets, anchor_ms + horizon_ms, outcome_tolerance_ms)
        if target_exit is None or (acceptable_quality_only and
                                   not _acceptable(target_exit)):
            continue
        entry_prices: dict[str, float] = {}
        exit_prices: dict[str, float] = {}
        feature_ages: list[int] = []
        complete = True
        for member in basket.members:
            entry = store.at_or_before(
                reference_source, member.symbol, anchor_ms - lookback_ms,
                feature_max_age_ms)
            current = store.at_or_before(
                reference_source, member.symbol, anchor_ms, feature_max_age_ms)
            if (entry is None or current is None or
                    (acceptable_quality_only and
                     (not _acceptable(entry) or not _acceptable(current)))):
                complete = False
                break
            entry_prices[member.symbol] = entry.price
            exit_prices[member.symbol] = current.price
            feature_ages.extend([
                anchor_ms - lookback_ms - entry.observed_at_ms,
                anchor_ms - current.observed_at_ms,
            ])
        if not complete:
            continue
        reference_return = basket_log_return(basket, entry_prices, exit_prices)
        target_return = math.log(target_exit.price / target.price)
        predicted = return_direction(reference_return)
        actual = return_direction(target_return)
        correct = None if "FLAT" in {predicted, actual} else predicted == actual
        samples.append(LeadLagSample(
            anchor_ms, lookback_seconds, horizon_seconds, basket.identity,
            basket.fingerprint, reference_return, target_return, predicted,
            actual, correct, max(feature_ages),
            target_exit.observed_at_ms - (anchor_ms + horizon_ms)))
    return tuple(samples)


def chronological_development_split(
        samples: tuple[LeadLagSample, ...], *, train_fraction: float = 0.60,
        validation_fraction: float = 0.20,
        embargo_seconds: int | None = None) -> ChronologicalDevelopmentSplit:
    """Split chronologically and purge outcome overlap around both boundaries."""
    if not samples:
        raise ValueError("samples are required")
    if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1:
        raise ValueError("split fractions must be in (0, 1)")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("train and validation must leave a holdout")
    ordered = tuple(sorted(samples, key=lambda sample: sample.anchor_ms))
    if len({sample.horizon_seconds for sample in ordered}) != 1:
        raise ValueError("split one forecast horizon at a time")
    horizon = ordered[0].horizon_seconds
    embargo = horizon if embargo_seconds is None else embargo_seconds
    if embargo < horizon:
        raise ValueError("embargo must cover the forecast horizon")
    train_end_index = max(1, int(len(ordered) * train_fraction))
    validation_end_index = max(train_end_index + 1,
                               int(len(ordered) * (train_fraction +
                                                  validation_fraction)))
    if validation_end_index >= len(ordered):
        raise ValueError("not enough samples for train/validation/holdout")
    validation_start_ms = ordered[train_end_index].anchor_ms
    holdout_start_ms = ordered[validation_end_index].anchor_ms
    embargo_ms = embargo * 1000
    train = tuple(sample for sample in ordered[:train_end_index]
                  if sample.anchor_ms + embargo_ms < validation_start_ms)
    validation = tuple(sample for sample in ordered[train_end_index:validation_end_index]
                       if sample.anchor_ms + embargo_ms < holdout_start_ms)
    holdout_samples = ordered[validation_end_index:]
    if not train or not validation or not holdout_samples:
        raise ValueError("embargo leaves an empty split")
    holdout = SealedHoldout(
        len(holdout_samples), holdout_samples[0].anchor_ms,
        holdout_samples[-1].anchor_ms, _sample_fingerprint(holdout_samples))
    return ChronologicalDevelopmentSplit(train, validation, holdout, embargo)


def directional_metrics(samples: tuple[LeadLagSample, ...], *,
                        baseline_direction: str | None = None) -> dict:
    """Report coverage, accuracy, confusion matrix and Wilson uncertainty."""
    if baseline_direction not in {None, "UP", "DOWN"}:
        raise ValueError("baseline_direction must be UP, DOWN or None")
    comparable = tuple(sample for sample in samples if sample.correct is not None)
    correct = sum(sample.correct is True for sample in comparable)
    confusion = {"UP_UP": 0, "UP_DOWN": 0, "DOWN_UP": 0, "DOWN_DOWN": 0}
    for sample in comparable:
        confusion[f"{sample.actual_direction}_{sample.predicted_direction}"] += 1
    actuals = tuple(sample.actual_direction for sample in comparable)
    if baseline_direction is None and actuals:
        ups = actuals.count("UP")
        baseline_direction = "UP" if ups >= len(actuals) - ups else "DOWN"
    baseline_correct = (sum(actual == baseline_direction for actual in actuals)
                        if baseline_direction else 0)
    accuracy = correct / len(comparable) if comparable else None
    return {
        "samples": len(samples),
        "comparable_samples": len(comparable),
        "coverage": len(comparable) / len(samples) if samples else None,
        "accuracy": accuracy,
        "accuracy_wilson_95": _wilson_interval(correct, len(comparable)),
        "baseline_direction": baseline_direction,
        "baseline_accuracy": (baseline_correct / len(comparable)
                              if comparable else None),
        "confusion_matrix": confusion,
        "max_feature_age_ms": (max((sample.max_feature_age_ms
                                    for sample in samples), default=None)),
        "max_absolute_target_exit_offset_ms": (
            max((abs(sample.target_exit_offset_ms) for sample in samples),
                default=None)),
        "overlapping_outcomes": _has_overlapping_outcomes(samples),
    }


def development_report(split: ChronologicalDevelopmentSplit) -> dict:
    """Evaluate development partitions while reporting holdout metadata only."""
    train_metrics = directional_metrics(split.train)
    baseline_direction = train_metrics["baseline_direction"]
    return {
        "train": train_metrics,
        "validation": directional_metrics(
            split.validation, baseline_direction=baseline_direction),
        "holdout": {
            "status": "SEALED_NOT_EVALUATED",
            "count": split.holdout.count,
            "start_ms": split.holdout.start_ms,
            "end_ms": split.holdout.end_ms,
            "fingerprint": split.holdout.fingerprint,
        },
        "embargo_seconds": split.embargo_seconds,
        "probability_metrics": "NOT_APPLICABLE_NO_PROBABILITY_MODEL",
    }


def _acceptable(point: PricePoint) -> bool:
    return point.metadata.get("quality") == "ACCEPTABLE"


def _nearest_target_exit(points: tuple[PricePoint, ...], expected_ms: int,
                         tolerance_ms: int) -> PricePoint | None:
    candidates = [point for point in points
                  if expected_ms - tolerance_ms <= point.observed_at_ms <=
                  expected_ms + tolerance_ms]
    if not candidates:
        return None
    return min(candidates,
               key=lambda point: (abs(point.observed_at_ms - expected_ms),
                                  point.observed_at_ms))


def _wilson_interval(successes: int, total: int) -> list[float] | None:
    if total == 0:
        return None
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (z / denominator) * math.sqrt(
        proportion * (1 - proportion) / total + z * z / (4 * total * total))
    return [center - margin, center + margin]


def _has_overlapping_outcomes(samples: tuple[LeadLagSample, ...]) -> bool:
    ordered = sorted(samples, key=lambda sample: sample.anchor_ms)
    return any(right.anchor_ms < left.anchor_ms + left.horizon_seconds * 1000
               for left, right in zip(ordered, ordered[1:]))


def _sample_fingerprint(samples: tuple[LeadLagSample, ...]) -> str:
    payload = [(
        sample.anchor_ms, sample.lookback_seconds, sample.horizon_seconds,
        sample.basket_fingerprint, sample.reference_log_return,
        sample.target_log_return, sample.predicted_direction,
        sample.actual_direction, sample.max_feature_age_ms,
        sample.target_exit_offset_ms,
    ) for sample in samples]
    encoded = json.dumps(payload, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
