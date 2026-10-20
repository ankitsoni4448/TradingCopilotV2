"""Narrow, fail-closed OCR for stable Binomo desktop context fields."""
from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageOps


_PERCENT = re.compile(r"(?<!\d)(\d{1,3})\s*%(?!\d)")


@dataclass(frozen=True)
class PayoutEvidence:
    payout_ratio: float
    percent: int
    raw_text: str


@dataclass(frozen=True)
class MajorityOpinionEvidence:
    up_ratio: float
    down_ratio: float
    up_percent: int
    down_percent: int
    raw_text: str


def parse_payout_ocr(text: str) -> PayoutEvidence:
    """Require one Crypto IDX percentage rather than guessing among fields."""
    normalized = " ".join(text.upper().split())
    compact = normalized.replace(" ", "")
    if "CRYPTOIDX" not in compact:
        raise ValueError("payout_ocr_instrument_mismatch")
    candidates = _PERCENT.findall(normalized)
    if len(candidates) != 1:
        raise ValueError("payout_ocr_must_contain_exactly_one_candidate")
    percent = int(candidates[0])
    if not 1 <= percent <= 100:
        raise ValueError("payout_ocr_value_invalid")
    return PayoutEvidence(percent / 100, percent, text.strip())


def parse_majority_opinion_ocr(text: str) -> MajorityOpinionEvidence:
    normalized = " ".join(text.upper().split())
    if "MAJORITY OPINION" not in normalized:
        raise ValueError("majority_opinion_label_missing")
    candidates = [int(value) for value in _PERCENT.findall(normalized)]
    if len(candidates) != 2:
        raise ValueError("majority_opinion_requires_two_percentages")
    up_percent, down_percent = candidates
    if not all(0 <= value <= 100 for value in candidates):
        raise ValueError("majority_opinion_value_invalid")
    if up_percent + down_percent != 100:
        raise ValueError("majority_opinion_does_not_sum_to_100")
    return MajorityOpinionEvidence(
        up_percent / 100, down_percent / 100,
        up_percent, down_percent, text.strip())


def _scaled_box(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    return tuple(round(value * scale) for value, scale in zip(
        box, (image.width / 1731, image.height / 800,
              image.width / 1731, image.height / 800)))


def read_payout_ratio(
        image: Image.Image, *, runner: Callable[..., object] = subprocess.run,
        tesseract_executable: str = "tesseract",
        timeout_seconds: float = 5.0) -> PayoutEvidence:
    """OCR only the top instrument tab; temporary cropped pixels are deleted."""
    if image.width < 400 or image.height < 200:
        raise ValueError("display_size_mismatch")
    crop = image.crop(_scaled_box(image, (350, 0, 610, 86)))
    crop = ImageOps.grayscale(crop).point(lambda value: 255 if value >= 140 else 0)
    crop = crop.resize((crop.width * 3, crop.height * 3))
    with tempfile.TemporaryDirectory(prefix="copilot-payout-") as directory:
        path = Path(directory) / "instrument-payout.png"
        crop.save(path)
        try:
            completed = runner(
                [tesseract_executable, str(path), "stdout", "--psm", "7"],
                capture_output=True, text=True, timeout=timeout_seconds,
                check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("ocr_engine_unavailable_or_timeout") from exc
    if getattr(completed, "returncode", 1) != 0:
        raise ValueError("payout_ocr_engine_failed")
    return parse_payout_ocr(str(getattr(completed, "stdout", "")))


def read_majority_opinion(
        image: Image.Image, *, runner: Callable[..., object] = subprocess.run,
        tesseract_executable: str = "tesseract",
        timeout_seconds: float = 5.0) -> MajorityOpinionEvidence:
    """Read left/up and right/down crowd percentages as exploratory context."""
    if image.width < 400 or image.height < 200:
        raise ValueError("display_size_mismatch")
    crop = image.crop(_scaled_box(image, (1485, 440, 1725, 540)))
    crop = ImageOps.autocontrast(ImageOps.grayscale(crop), cutoff=1)
    crop = crop.resize((crop.width * 4, crop.height * 4))
    with tempfile.TemporaryDirectory(prefix="copilot-majority-") as directory:
        path = Path(directory) / "majority-opinion.png"
        crop.save(path)
        try:
            completed = runner(
                [tesseract_executable, str(path), "stdout", "--psm", "6"],
                capture_output=True, text=True, timeout=timeout_seconds,
                check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("ocr_engine_unavailable_or_timeout") from exc
    if getattr(completed, "returncode", 1) != 0:
        raise ValueError("majority_opinion_ocr_engine_failed")
    return parse_majority_opinion_ocr(
        str(getattr(completed, "stdout", "")))
