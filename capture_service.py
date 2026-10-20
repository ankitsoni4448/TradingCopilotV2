"""Coordinate target-screen and reference-market evidence capture."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Callable

from market_data import ReferenceTick, fetch_reference_tick, fetch_reference_ticks
from observation_store import Observation, ObservationStore

ALLOWED_CHART_DISPLAY_INTERVALS = frozenset(
    {"1s", "5s", "1m", "5m", "15m", "1h", "3h", "1d"}
)


@dataclass(frozen=True)
class CaptureResult:
    capture_id: str
    target_observation_id: int
    reference_observation_id: int
    target_observed_at_ms: int
    reference_observed_at_ms: int
    target_reference_offset_ms: int
    client_clock_skew_ms: int
    reference_latency_ms: int
    quality: str
    quality_reasons: tuple[str, ...]


@dataclass(frozen=True)
class BatchCaptureResult:
    capture_id: str
    target_observation_id: int
    reference_observation_ids: tuple[int, ...]
    reference_symbols: tuple[str, ...]
    target_observed_at_ms: int
    max_target_reference_offset_ms: int
    reference_latency_ms: int
    quality: str
    quality_reasons: tuple[str, ...]
    payout_ratio: float | None
    majority_up_ratio: float | None
    majority_down_ratio: float | None
    context_quality_reasons: tuple[str, ...]


class CaptureCoordinator:
    def __init__(self, store: ObservationStore, *,
                 reference_fetcher: Callable[..., ReferenceTick] = fetch_reference_tick,
                 reference_batch_fetcher: Callable[..., tuple[ReferenceTick, ...]] =
                 fetch_reference_ticks,
                 clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
                 id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
                 max_clock_skew_ms: int = 5_000,
                 max_reference_latency_ms: int = 2_000):
        self.store = store
        self.reference_fetcher = reference_fetcher
        self.reference_batch_fetcher = reference_batch_fetcher
        self.clock_ms = clock_ms
        self.id_factory = id_factory
        self.max_clock_skew_ms = max_clock_skew_ms
        self.max_reference_latency_ms = max_reference_latency_ms

    def capture_batch(
            self, *, target_instrument: str, target_price: float,
            target_observed_at_ms: int,
            reference_symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT"),
            reference_batch_fetcher: Callable[..., tuple[ReferenceTick, ...]] | None = None,
            expiry_at_ms: int | None = None,
            chart_display_interval: str | None = None,
            payout_ratio: float | None = None,
            majority_up_ratio: float | None = None,
            majority_down_ratio: float | None = None,
            context_quality_reasons: tuple[str, ...] = ()) -> BatchCaptureResult:
        """Atomically store one screen observation and a reference tick batch."""
        if not reference_symbols or len(set(reference_symbols)) != len(reference_symbols):
            raise ValueError("reference_symbols must be non-empty and unique")
        self._validate_screen_context(
            target_observed_at_ms=target_observed_at_ms, payout_ratio=payout_ratio,
            chart_display_interval=chart_display_interval,
            expiry_at_ms=expiry_at_ms, amount=None,
            time_to_buy_seconds=None, until_expiration_seconds=None)
        if (majority_up_ratio is None) != (majority_down_ratio is None):
            raise ValueError("majority opinion ratios must be supplied together")
        if majority_up_ratio is not None:
            if not (0 <= majority_up_ratio <= 1 and
                    0 <= majority_down_ratio <= 1):
                raise ValueError("majority opinion ratios must be in [0, 1]")
            if abs(majority_up_ratio + majority_down_ratio - 1) > 1e-9:
                raise ValueError("majority opinion ratios must sum to 1")
        server_received_at_ms = int(self.clock_ms())
        batch_fetcher = reference_batch_fetcher or self.reference_batch_fetcher
        references = batch_fetcher(symbols=reference_symbols)
        if tuple(tick.symbol for tick in references) != reference_symbols:
            raise ValueError("reference batch order/symbol mismatch")
        capture_id = self.id_factory()
        clock_skew_ms = target_observed_at_ms - server_received_at_ms
        offsets = tuple(tick.observed_at_ms - target_observed_at_ms
                        for tick in references)
        max_offset = max((abs(value) for value in offsets), default=0)
        latency = max((tick.latency_ms for tick in references), default=0)
        reasons = []
        if abs(clock_skew_ms) > self.max_clock_skew_ms:
            reasons.append("client_clock_skew")
        if latency > self.max_reference_latency_ms:
            reasons.append("reference_network_latency")
        if max_offset > self.max_clock_skew_ms:
            reasons.append("target_reference_time_offset")
        quality = "ACCEPTABLE" if not reasons else "LOW"
        common = {"capture_id": capture_id, "quality": quality,
                  "quality_reasons": reasons}
        observations = [Observation(
            source="binomo_screen", instrument=target_instrument,
            observed_at_ms=target_observed_at_ms, price=target_price,
            capture_method="desktop_ocr",
            metadata={**common, "server_received_at_ms": server_received_at_ms,
                      "client_clock_skew_ms": clock_skew_ms,
                      "payout_ratio": payout_ratio,
                      "majority_up_ratio": majority_up_ratio,
                      "majority_down_ratio": majority_down_ratio,
                      "context_quality_reasons": list(context_quality_reasons),
                      "chart_display_interval": chart_display_interval,
                      "expiry_at_ms": expiry_at_ms})]
        for tick, offset in zip(references, offsets):
            observations.append(Observation(
                source=tick.source, instrument=tick.symbol,
                observed_at_ms=tick.observed_at_ms, price=tick.price,
                capture_method="public_rest_ticker_batch_midpoint",
                metadata={**common, "requested_at_ms": tick.requested_at_ms,
                          "received_at_ms": tick.received_at_ms,
                          "latency_ms": tick.latency_ms,
                          "target_offset_ms": offset,
                          "timestamp_semantics": "local_request_midpoint"}))
        ids = self.store.append_many(observations)
        return BatchCaptureResult(
            capture_id, ids[0], tuple(ids[1:]), reference_symbols,
            target_observed_at_ms, max_offset, latency, quality, tuple(reasons),
            payout_ratio, majority_up_ratio, majority_down_ratio,
            tuple(context_quality_reasons))

    def capture(self, *, target_instrument: str, target_price: float,
                target_observed_at_ms: int, reference_symbol: str = "ETHUSDT",
                screenshot_sha256: str | None = None,
                capture_method: str = "screen_manual",
                payout_ratio: float | None = None,
                chart_display_interval: str | None = None,
                expiry_at_ms: int | None = None,
                amount: float | None = None,
                time_to_buy_seconds: int | None = None,
                until_expiration_seconds: int | None = None) -> CaptureResult:
        self._validate_screen_context(
            target_observed_at_ms=target_observed_at_ms,
            payout_ratio=payout_ratio,
            chart_display_interval=chart_display_interval,
            expiry_at_ms=expiry_at_ms, amount=amount,
            time_to_buy_seconds=time_to_buy_seconds,
            until_expiration_seconds=until_expiration_seconds)
        server_received_at_ms = int(self.clock_ms())
        reference = self.reference_fetcher(symbol=reference_symbol)
        capture_id = self.id_factory()
        clock_skew_ms = target_observed_at_ms - server_received_at_ms
        offset_ms = reference.observed_at_ms - target_observed_at_ms
        reasons = []
        if abs(clock_skew_ms) > self.max_clock_skew_ms:
            reasons.append("client_clock_skew")
        if reference.latency_ms > self.max_reference_latency_ms:
            reasons.append("reference_network_latency")
        if abs(offset_ms) > self.max_clock_skew_ms:
            reasons.append("target_reference_time_offset")
        quality = "ACCEPTABLE" if not reasons else "LOW"

        common = {"capture_id": capture_id, "quality": quality,
                  "quality_reasons": reasons}
        target = Observation(
            source="binomo_screen", instrument=target_instrument,
            observed_at_ms=target_observed_at_ms, price=target_price,
            capture_method=capture_method, screenshot_sha256=screenshot_sha256,
            metadata={**common, "server_received_at_ms": server_received_at_ms,
                      "client_clock_skew_ms": clock_skew_ms,
                      "payout_ratio": payout_ratio,
                      "chart_display_interval": chart_display_interval,
                      "expiry_at_ms": expiry_at_ms,
                      "amount": amount,
                      "time_to_buy_seconds": time_to_buy_seconds,
                      "until_expiration_seconds": until_expiration_seconds},
        )
        reference_observation = Observation(
            source=reference.source, instrument=reference.symbol,
            observed_at_ms=reference.observed_at_ms, price=reference.price,
            capture_method="public_rest_ticker_midpoint",
            metadata={**common, "requested_at_ms": reference.requested_at_ms,
                      "received_at_ms": reference.received_at_ms,
                      "latency_ms": reference.latency_ms,
                      "timestamp_semantics": "local_request_midpoint"},
        )
        target_id, reference_id = self.store.append_many([target, reference_observation])
        return CaptureResult(
            capture_id, target_id, reference_id, target_observed_at_ms,
            reference.observed_at_ms, offset_ms, clock_skew_ms,
            reference.latency_ms, quality, tuple(reasons))

    @staticmethod
    def _validate_screen_context(*, target_observed_at_ms: int,
                                 payout_ratio: float | None,
                                 chart_display_interval: str | None,
                                 expiry_at_ms: int | None,
                                 amount: float | None,
                                 time_to_buy_seconds: int | None,
                                 until_expiration_seconds: int | None):
        if payout_ratio is not None and not 0 < payout_ratio <= 1:
            raise ValueError("payout_ratio must be in (0, 1]")
        if (chart_display_interval is not None and
                chart_display_interval not in ALLOWED_CHART_DISPLAY_INTERVALS):
            allowed = ", ".join(sorted(ALLOWED_CHART_DISPLAY_INTERVALS))
            raise ValueError(f"chart_display_interval must be one of: {allowed}")
        if expiry_at_ms is not None and expiry_at_ms <= target_observed_at_ms:
            raise ValueError("expiry_at_ms must be after target_observed_at_ms")
        if amount is not None and amount <= 0:
            raise ValueError("amount must be positive")
        for name, value in (("time_to_buy_seconds", time_to_buy_seconds),
                            ("until_expiration_seconds", until_expiration_seconds)):
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")
