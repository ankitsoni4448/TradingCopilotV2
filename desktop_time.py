"""Conservative desktop extraction of platform clock and selected expiry.

The platform clock and expiry are OCRed from separate, narrow regions.  An
expiry is never inferred from the host clock and an earlier same-day time is
not silently rolled to tomorrow.  Only the explicit midnight boundary is
eligible for rollover.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable

from PIL import Image, ImageOps


_CLOCK = re.compile(
    r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d):([0-5]\d)\s*GMT\s*"
    r"([+-])\s*(0?\d|1[0-4])(?::?([0-5]\d))?",
    re.IGNORECASE,
)
_EXPIRY = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![:\d])")


@dataclass(frozen=True)
class PlatformClock:
    hour: int
    minute: int
    second: int
    utc_offset_minutes: int
    raw_text: str

    @property
    def tzinfo(self) -> timezone:
        return timezone(timedelta(minutes=self.utc_offset_minutes))


@dataclass(frozen=True)
class DesktopTimeEvidence:
    platform_clock: PlatformClock
    observed_at: datetime
    selected_expiry: time
    expiry_at: datetime
    clock_raw_text: str
    expiry_raw_text: str


def parse_platform_clock(text: str) -> PlatformClock:
    """Parse exactly one complete platform timestamp including GMT offset."""
    matches = list(_CLOCK.finditer(text))
    if len(matches) != 1:
        raise ValueError("platform_clock_must_contain_exactly_one_candidate")
    match = matches[0]
    hour, minute, second = (int(match.group(i)) for i in range(1, 4))
    offset_hour = int(match.group(5))
    offset_minute = int(match.group(6) or 0)
    offset = offset_hour * 60 + offset_minute
    if match.group(4) == "-":
        offset = -offset
    if abs(offset) > 14 * 60:
        raise ValueError("platform_clock_offset_invalid")
    return PlatformClock(hour, minute, second, offset, text.strip())


def parse_selected_expiry(text: str) -> time:
    """Parse exactly one HH:MM value; ambiguous OCR is rejected."""
    matches = list(_EXPIRY.finditer(text))
    if len(matches) != 1:
        raise ValueError("selected_expiry_must_contain_exactly_one_candidate")
    return time(int(matches[0].group(1)), int(matches[0].group(2)))


def resolve_expiry_at(
        clock: PlatformClock, selected_expiry: time, *,
        observed_date: date,
        max_future_seconds: int = 3600) -> datetime:
    """Resolve an expiry against platform time without unsafe date guessing."""
    if max_future_seconds <= 0:
        raise ValueError("max_future_seconds_must_be_positive")
    platform_now = datetime.combine(
        observed_date, time(clock.hour, clock.minute, clock.second), clock.tzinfo)
    expiry_at = datetime.combine(observed_date, selected_expiry, clock.tzinfo)
    if expiry_at <= platform_now:
        # The only safe rollover supported is the visible midnight boundary.
        if clock.hour != 23 or selected_expiry.hour not in {0, 1}:
            raise ValueError("selected_expiry_not_in_future")
        expiry_at += timedelta(days=1)
    delta = (expiry_at - platform_now).total_seconds()
    if delta > max_future_seconds:
        raise ValueError("selected_expiry_too_far_in_future")
    return expiry_at


def _ocr_region(
        image: Image.Image, box: tuple[float, float, float, float], *,
        whitelist: str, runner: Callable[..., object],
        tesseract_executable: str, timeout_seconds: float,
        prefix: str) -> str:
    if image.width < 400 or image.height < 200:
        raise ValueError("display_size_mismatch")
    left = round(image.width * box[0])
    top = round(image.height * box[1])
    right = round(image.width * box[2])
    bottom = round(image.height * box[3])
    crop = image.crop((left, top, right, bottom))
    crop = ImageOps.autocontrast(ImageOps.grayscale(crop), cutoff=2)
    crop = crop.resize((crop.width * 5, crop.height * 5))
    with tempfile.TemporaryDirectory(prefix=prefix) as directory:
        path = Path(directory) / "field.png"
        crop.save(path)
        try:
            completed = runner(
                [tesseract_executable, str(path), "stdout", "--psm", "7", "-c",
                 f"tessedit_char_whitelist={whitelist}"],
                capture_output=True, text=True, timeout=timeout_seconds,
                check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("ocr_engine_unavailable_or_timeout") from exc
    if getattr(completed, "returncode", 1) != 0:
        raise ValueError("desktop_time_ocr_engine_failed")
    return str(getattr(completed, "stdout", "")).strip()


def read_desktop_time(
        image: Image.Image, *, observed_date: date | None = None,
        captured_at: datetime | None = None,
        runner: Callable[..., object] = subprocess.run,
        tesseract_executable: str = "tesseract",
        timeout_seconds: float = 5.0,
        max_future_seconds: int = 3600) -> DesktopTimeEvidence:
    """OCR two fixed desktop fields and return validated, aware timestamps."""
    clock_text = _ocr_region(
        image, (120 / 1731, 88 / 800, 310 / 1731, 118 / 800),
        whitelist="0123456789:GMTgmt+-", runner=runner,
        tesseract_executable=tesseract_executable,
        timeout_seconds=timeout_seconds, prefix="copilot-clock-")
    expiry_text = _ocr_region(
        image, (1560 / 1731, 305 / 800, 1660 / 1731, 337 / 800),
        whitelist="0123456789:", runner=runner,
        tesseract_executable=tesseract_executable,
        timeout_seconds=timeout_seconds, prefix="copilot-expiry-")
    clock = parse_platform_clock(clock_text)
    if (observed_date is None) == (captured_at is None):
        raise ValueError("provide exactly one of observed_date or captured_at")
    if captured_at is not None:
        if captured_at.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")
        observed_date = captured_at.astimezone(clock.tzinfo).date()
    assert observed_date is not None
    observed_at = datetime.combine(
        observed_date, time(clock.hour, clock.minute, clock.second), clock.tzinfo)
    expiry = parse_selected_expiry(expiry_text)
    expiry_at = resolve_expiry_at(
        clock, expiry, observed_date=observed_date,
        max_future_seconds=max_future_seconds)
    return DesktopTimeEvidence(
        clock, observed_at, expiry, expiry_at, clock_text, expiry_text)
