"""One-cycle and continuous observation-only desktop collection.

This module has no input-control or trade-execution capability. The caller
supplies screenshots; accepted observations are paired with public reference
prices and written atomically.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from PIL import Image

from capture_service import BatchCaptureResult, CaptureCoordinator
from desktop_context import read_majority_opinion, read_payout_ratio
from desktop_observer import PRICE_TAG_FILL_RGB, read_current_price
from desktop_time import read_desktop_time
from market_data import DEFAULT_REFERENCE_SYMBOLS


@dataclass(frozen=True)
class CollectionAttempt:
    status: str
    result: BatchCaptureResult | None
    error: str | None
    attempted_at_ms: int
    duration_ms: int


class ContinuousCollector:
    def __init__(
            self, coordinator: CaptureCoordinator, *,
            image_provider: Callable[[], Image.Image],
            reference_symbols: tuple[str, ...] = DEFAULT_REFERENCE_SYMBOLS,
            max_capture_age_seconds: float = 10.0,
            tesseract_executable: str = "tesseract",
            price_tag_fill_rgb: tuple[int, int, int] = PRICE_TAG_FILL_RGB,
            price_tag_color_tolerance: int = 18,
            price_tag_roi: tuple[float, float, float, float] = (0.68, 0.08, 0.995, 0.95),
            audit_recorder: Callable[[CollectionAttempt], object] | None = None):
        if max_capture_age_seconds <= 0:
            raise ValueError("max_capture_age_seconds must be positive")
        self.coordinator = coordinator
        self.image_provider = image_provider
        self.reference_symbols = reference_symbols
        self.max_capture_age_seconds = max_capture_age_seconds
        self.tesseract_executable = tesseract_executable
        self.price_tag_fill_rgb = price_tag_fill_rgb
        self.price_tag_color_tolerance = price_tag_color_tolerance
        self.price_tag_roi = price_tag_roi
        self.audit_recorder = audit_recorder

    def capture_once(self, *, captured_at: datetime | None = None) -> CollectionAttempt:
        captured_at = captured_at or datetime.now(timezone.utc)
        if captured_at.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")
        started = time.monotonic()
        attempted_at_ms = round(captured_at.timestamp() * 1000)
        try:
            image = self.image_provider()
            if not isinstance(image, Image.Image):
                raise ValueError("image_provider_must_return_pillow_image")
            price = read_current_price(
                image, tesseract_executable=self.tesseract_executable,
                expected_fill=self.price_tag_fill_rgb,
                color_tolerance=self.price_tag_color_tolerance,
                roi=self.price_tag_roi)
            payout = read_payout_ratio(
                image, tesseract_executable=self.tesseract_executable)
            timing = read_desktop_time(
                image, captured_at=captured_at,
                tesseract_executable=self.tesseract_executable)
            age = abs((captured_at - timing.observed_at.astimezone(timezone.utc))
                      .total_seconds())
            if age > self.max_capture_age_seconds:
                raise ValueError("platform_clock_stale_or_date_mismatch")
            context_reasons = []
            majority = None
            try:
                majority = read_majority_opinion(
                    image, tesseract_executable=self.tesseract_executable)
            except ValueError:
                context_reasons.append("majority_opinion_unavailable")
            result = self.coordinator.capture_batch(
                target_instrument="CRYPTO_IDX", target_price=price.price,
                target_observed_at_ms=round(timing.observed_at.timestamp() * 1000),
                expiry_at_ms=round(timing.expiry_at.timestamp() * 1000),
                payout_ratio=payout.payout_ratio,
                majority_up_ratio=(majority.up_ratio if majority else None),
                majority_down_ratio=(majority.down_ratio if majority else None),
                context_quality_reasons=tuple(context_reasons),
                reference_symbols=self.reference_symbols)
            attempt = CollectionAttempt(
                "ACCEPTED", result, None, attempted_at_ms,
                max(0, round((time.monotonic() - started) * 1000)))
        except (OSError, RuntimeError, ValueError) as exc:
            attempt = CollectionAttempt(
                "REJECTED", None, str(exc), attempted_at_ms,
                max(0, round((time.monotonic() - started) * 1000)))
        if self.audit_recorder is not None:
            self.audit_recorder(attempt)
        return attempt

    def run(
            self, *, should_stop: Callable[[], bool], interval_seconds: float = 5.0,
            on_attempt: Callable[[CollectionAttempt], None] | None = None,
            sleeper: Callable[[float], None] = time.sleep) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        while not should_stop():
            started = time.monotonic()
            attempt = self.capture_once()
            if on_attempt is not None:
                on_attempt(attempt)
            remaining = interval_seconds - (time.monotonic() - started)
            if remaining > 0 and not should_stop():
                sleeper(remaining)
