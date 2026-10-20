"""Validation-first contract for structured Binomo screen extraction.

This module deliberately separates region selection and OCR from evidence
acceptance. It never emits a trading signal. The initial layout is provisional
and must be calibrated on the local device before automatic collection.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from capture_service import ALLOWED_CHART_DISPLAY_INTERVALS


@dataclass(frozen=True)
class NormalizedRegion:
    name: str
    left: float
    top: float
    right: float
    bottom: float
    locator: str = "fixed"

    def __post_init__(self) -> None:
        if not (0 <= self.left < self.right <= 1 and
                0 <= self.top < self.bottom <= 1):
            raise ValueError(f"invalid normalized region: {self.name}")

    def pixel_box(self, width: int, height: int) -> tuple[int, int, int, int]:
        if width <= 0 or height <= 0:
            raise ValueError("image dimensions must be positive")
        return (round(self.left * width), round(self.top * height),
                round(self.right * width), round(self.bottom * height))


@dataclass(frozen=True)
class ScreenLayout:
    version: str
    calibration_status: str
    reference_aspect_ratio: float
    regions: tuple[NormalizedRegion, ...]


BINOMO_PHONE_PORTRAIT_V1 = ScreenLayout(
    version="binomo_phone_portrait_v1",
    calibration_status="PROVISIONAL",
    reference_aspect_ratio=688 / 1536,
    regions=(
        NormalizedRegion("instrument_payout", 0.14, 0.11, 0.56, 0.18),
        NormalizedRegion("time_to_buy", 0.18, 0.18, 0.37, 0.24),
        NormalizedRegion("until_expiration", 0.37, 0.18, 0.59, 0.24),
        NormalizedRegion("chart", 0.00, 0.18, 1.00, 0.69),
        # The price tag moves vertically. First locate its high-contrast tag in
        # this band, then OCR the detected tag rather than the entire band.
        NormalizedRegion("current_price", 0.73, 0.20, 1.00, 0.68,
                         locator="dynamic_price_tag"),
        NormalizedRegion("chart_display_interval", 0.24, 0.69, 0.38, 0.76),
        NormalizedRegion("amount", 0.02, 0.79, 0.42, 0.87),
        NormalizedRegion("expiry", 0.58, 0.79, 0.98, 0.87),
    ),
)


@dataclass(frozen=True)
class OcrReading:
    text: str
    confidence: float

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("OCR confidence must be in [0, 1]")


@dataclass(frozen=True)
class ScreenFields:
    instrument: str
    payout_ratio: float
    current_price: float
    chart_display_interval: str
    amount: float
    expiry_hhmm: str
    time_to_buy_seconds: int
    until_expiration_seconds: int


@dataclass(frozen=True)
class ExtractionResult:
    accepted: bool
    layout_version: str
    fields: ScreenFields | None
    reasons: tuple[str, ...]
    minimum_confidence: float


_DECIMAL_PRICE = re.compile(r"(?<!\d)(\d{2,6}\.\d{6,14})(?!\d)")
_PERCENT = re.compile(r"(?<!\d)(\d{1,3})\s*%")
_MONEY = re.compile(r"(?:₹|Rs\.?\s*)?\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)
_HHMM = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
_COUNTDOWN = re.compile(r"(?<!\d)(\d{1,2}):([0-5]\d)(?!\d)")


def extract_structured_screen(
        readings: Mapping[str, OcrReading], *,
        layout: ScreenLayout = BINOMO_PHONE_PORTRAIT_V1,
        minimum_confidence: float = 0.90,
        expected_buy_window_seconds: int = 30,
        countdown_tolerance_seconds: int = 2) -> ExtractionResult:
    """Parse OCR outputs and reject ambiguous or inconsistent evidence."""
    reasons: list[str] = []
    required = {
        "instrument_payout", "current_price", "chart_display_interval",
        "amount", "expiry", "time_to_buy", "until_expiration",
    }
    missing = sorted(required - readings.keys())
    reasons.extend(f"missing:{name}" for name in missing)
    for name in sorted(required & readings.keys()):
        if readings[name].confidence < minimum_confidence:
            reasons.append(f"low_confidence:{name}")
    if reasons:
        return ExtractionResult(False, layout.version, None, tuple(reasons),
                                minimum_confidence)

    try:
        instrument_text = readings["instrument_payout"].text
        normalized_instrument = " ".join(instrument_text.upper().split())
        if "CRYPTO IDX" not in normalized_instrument:
            raise ValueError("instrument_mismatch")
        payout_match = _PERCENT.search(instrument_text)
        if not payout_match:
            raise ValueError("invalid_payout")
        payout_ratio = int(payout_match.group(1)) / 100
        if not 0 < payout_ratio <= 1:
            raise ValueError("invalid_payout")

        price_matches = _DECIMAL_PRICE.findall(readings["current_price"].text)
        if len(price_matches) != 1:
            raise ValueError("ambiguous_current_price")
        current_price = float(price_matches[0])

        interval = readings["chart_display_interval"].text.strip().lower()
        if interval not in ALLOWED_CHART_DISPLAY_INTERVALS:
            raise ValueError("invalid_chart_display_interval")

        amount_match = _MONEY.fullmatch(readings["amount"].text.strip())
        if not amount_match:
            raise ValueError("invalid_amount")
        amount = float(amount_match.group(1).replace(",", ""))
        if amount <= 0:
            raise ValueError("invalid_amount")

        expiry_match = _HHMM.fullmatch(readings["expiry"].text.strip())
        if not expiry_match:
            raise ValueError("invalid_expiry")
        expiry_hhmm = f"{int(expiry_match.group(1)):02d}:{expiry_match.group(2)}"

        time_to_buy = _parse_countdown(readings["time_to_buy"].text)
        until_expiration = _parse_countdown(readings["until_expiration"].text)
        observed_window = until_expiration - time_to_buy
        if abs(observed_window - expected_buy_window_seconds) > countdown_tolerance_seconds:
            raise ValueError("countdown_window_mismatch")
    except ValueError as exc:
        reason = str(exc) or "parse_failure"
        return ExtractionResult(False, layout.version, None, (reason,),
                                minimum_confidence)

    fields = ScreenFields(
        instrument="CRYPTO_IDX", payout_ratio=payout_ratio,
        current_price=current_price, chart_display_interval=interval,
        amount=amount, expiry_hhmm=expiry_hhmm,
        time_to_buy_seconds=time_to_buy,
        until_expiration_seconds=until_expiration,
    )
    return ExtractionResult(True, layout.version, fields, (), minimum_confidence)


def _parse_countdown(text: str) -> int:
    match = _COUNTDOWN.fullmatch(text.strip())
    if not match:
        raise ValueError("invalid_countdown")
    return int(match.group(1)) * 60 + int(match.group(2))
