"""Desktop Crypto IDX price-tag detection and narrowly scoped OCR.

Only the detected price tag is written to a temporary file for OCR. The full
screen is neither persisted nor sent elsewhere. Detection is conservative:
unexpected color geometry is rejected instead of guessed.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image


PRICE_TAG_FILL_RGB = (205, 205, 205)
_PRICE = re.compile(r"(?<!\d)(\d{2,6}\.\d{6,14})(?!\d)")


@dataclass(frozen=True)
class PriceTagDetection:
    box: tuple[int, int, int, int]
    matching_pixels: int
    fill_coverage: float


@dataclass(frozen=True)
class PriceOcrResult:
    price: float
    raw_text: str
    detection: PriceTagDetection


def locate_current_price_tag(
        image: Image.Image, *,
        expected_fill: tuple[int, int, int] = PRICE_TAG_FILL_RGB,
    color_tolerance: int = 55,
    roi: tuple[float, float, float, float] = (0.72, 0.25, 0.92, 0.48)
    ) -> PriceTagDetection:
    """Locate the desktop price tag from its fill and validate its geometry."""
    if color_tolerance < 0:
        raise ValueError("color_tolerance must be non-negative")
    if not (0 <= roi[0] < roi[2] <= 1 and 0 <= roi[1] < roi[3] <= 1):
        raise ValueError("price_tag_roi_invalid")
    rgb = np.asarray(image.convert("RGB"), dtype=np.int16)
    height, width, _ = rgb.shape
    left, right = round(width * roi[0]), round(width * roi[2])
    top, bottom = round(height * roi[1]), round(height * roi[3])
    region = rgb[top:bottom, left:right]
    delta = np.abs(region - np.asarray(expected_fill, dtype=np.int16))
    chroma = region.max(axis=2) - region.min(axis=2)
    brightness = region.mean(axis=2)
    mask = (np.all(delta <= color_tolerance, axis=2) &
            (chroma <= 35) & (brightness >= 140) & (brightness <= 245))
    row_threshold = max(2, round(mask.shape[1] * 0.015))
    active_rows = np.flatnonzero(mask.sum(axis=1) >= row_threshold)
    if not len(active_rows):
        raise ValueError("price_tag_not_found")
    groups = np.split(active_rows, np.flatnonzero(np.diff(active_rows) > 1) + 1)
    candidates = []
    for rows in groups:
        row_mask = mask[rows[0]:rows[-1] + 1]
        ys, xs = np.where(row_mask)
        if not len(xs):
            continue
        x1, y1 = int(xs.min() + left), int(rows[0] + top)
        x2, y2 = int(xs.max() + left + 1), int(rows[-1] + top + 1)
        box_width, box_height = x2 - x1, y2 - y1
        width_ratio, height_ratio = box_width / width, box_height / height
        right_ratio = x2 / width
        coverage = len(xs) / (box_width * box_height)
        if (0.04 <= width_ratio <= 0.20 and
                0.01 <= height_ratio <= 0.08 and
                0.82 <= right_ratio <= 0.995 and coverage >= 0.30):
            candidates.append(PriceTagDetection(
                (x1, y1, x2, y2), len(xs), coverage))
    if not candidates:
        raise ValueError("price_tag_not_found")
    if len(candidates) != 1:
        raise ValueError("price_tag_ambiguous")
    return candidates[0]


def parse_price_ocr(text: str) -> float:
    candidates = _PRICE.findall(text)
    if len(candidates) != 1:
        raise ValueError("price_ocr_must_contain_exactly_one_candidate")
    value = float(candidates[0])
    if not np.isfinite(value) or value <= 0:
        raise ValueError("price_ocr_value_invalid")
    return value


def read_current_price(
        image: Image.Image, *,
        runner: Callable[..., object] = subprocess.run,
        tesseract_executable: str = "tesseract",
        expected_fill: tuple[int, int, int] = PRICE_TAG_FILL_RGB,
        color_tolerance: int = 55,
        roi: tuple[float, float, float, float] = (0.72, 0.25, 0.92, 0.48),
        timeout_seconds: float = 5.0) -> PriceOcrResult:
    """Detect and OCR a price tag; temporary cropped pixels are deleted."""
    detection = locate_current_price_tag(
        image, expected_fill=expected_fill,
        color_tolerance=color_tolerance, roi=roi)
    x1, y1, x2, y2 = detection.box
    padding = max(3, round(image.width * 0.002))
    crop = image.crop((max(0, x1 - padding), max(0, y1 - padding),
                       min(image.width, x2 + padding),
                       min(image.height, y2 + padding)))
    crop = crop.resize((crop.width * 4, crop.height * 4))
    with tempfile.TemporaryDirectory(prefix="copilot-price-") as directory:
        path = Path(directory) / "price-tag.png"
        crop.save(path)
        try:
            completed = runner(
                [tesseract_executable, str(path), "stdout", "--psm", "7", "-c",
                 "tessedit_char_whitelist=0123456789."],
                capture_output=True, text=True, timeout=timeout_seconds,
                check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("ocr_engine_unavailable_or_timeout") from exc
    if getattr(completed, "returncode", 1) != 0:
        raise ValueError("price_ocr_engine_failed")
    raw_text = str(getattr(completed, "stdout", "")).strip()
    return PriceOcrResult(parse_price_ocr(raw_text), raw_text, detection)
