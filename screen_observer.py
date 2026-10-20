"""In-memory full-desktop capture and conservative local screen understanding."""
from __future__ import annotations

import csv
import io
import os
import re
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageEnhance, ImageGrab, ImageOps

from ocr_support import resolve_tesseract_executable


_MARKET_WORDS = {"market", "trading", "trade", "chart", "forex", "stocks",
                 "crypto", "price", "bid", "ask", "candlestick"}
_ASSETS = ("CRYPTO IDX", "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
           "XRPUSDT", "BTC", "ETH", "SOL", "XRP", "BNB", "EURUSD",
           "GBPUSD", "USDJPY", "GOLD", "XAUUSD", "NASDAQ", "SPX")
_ASSET_RE = re.compile(r"(?<![A-Z0-9])(" + "|".join(
    re.escape(value) for value in sorted(_ASSETS, key=len, reverse=True)) +
    r")(?![A-Z0-9])", re.IGNORECASE)
_NUMBER_RE = re.compile(r"(?<![\w.])\d{1,9}(?:[.,]\d{2,14})(?![\w.])")
_PRICE_LABELS = {"last", "current", "spot"}
_MARKET_APPS = {"tradingview", "metatrader", "terminal", "thinkorswim"}
_PRICE_REJECTION_WORDS = {"stake", "payout", "balance", "amount", "entry",
                          "result", "profit", "loss", "win", "deposit"}


@dataclass(frozen=True)
class OcrWord:
    text: str
    confidence: float
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class FrameAnalysis:
    classification: str
    confidence: float
    extraction_status: str
    application_context: str | None
    asset: str | None
    price: float | None
    price_source: str | None
    error_reason: str | None
    extracted_fields: dict[str, object]


@dataclass(frozen=True)
class FrameRecord:
    captured_at_ms: int
    classification: str
    confidence: float
    extraction_status: str
    application_context: str | None
    asset: str | None
    price: float | None
    price_source: str | None
    processing_ms: int
    error_reason: str | None
    extracted_fields: dict[str, object]


class TemporalMarketTracker:
    """Require repeatable same-asset readings before memory treats price as valid."""

    def __init__(self, *, max_gap_ms: int = 15_000,
                 max_relative_change: float = 0.03):
        self.max_gap_ms = max_gap_ms
        self.max_relative_change = max_relative_change
        self.previous: FrameRecord | None = None
        self.previous_classification: str | None = None
        self.previous_asset: str | None = None
        self.previous_application: str | None = None

    def update(self, record: FrameRecord) -> FrameRecord:
        raw_record = record
        fields = dict(record.extracted_fields)
        events: list[dict[str, object]] = []
        market = record.classification in {"BINOMO_MARKET", "OTHER_MARKET"}
        if record.classification != self.previous_classification:
            events.append({"type": "CLASSIFICATION_CHANGED",
                           "from": self.previous_classification,
                           "to": record.classification})
        if record.asset and self.previous_asset and record.asset != self.previous_asset:
            events.append({"type": "ASSET_CHANGED", "from": self.previous_asset,
                           "to": record.asset})
        if (record.application_context and self.previous_application and
                record.application_context != self.previous_application):
            events.append({"type": "APPLICATION_CHANGED",
                           "from": self.previous_application,
                           "to": record.application_context})
        previous = self.previous
        confirmed = False
        if (market and record.price is not None and record.asset and previous and
                previous.classification in {"BINOMO_MARKET", "OTHER_MARKET"} and
                previous.asset == record.asset and previous.price is not None and
                0 < record.captured_at_ms - previous.captured_at_ms <= self.max_gap_ms):
            change = record.price / previous.price - 1
            if abs(change) <= self.max_relative_change:
                confirmed = True
                fields["quality_flags"] = ["OCR_LABEL_CONFIRMED",
                                           "TEMPORALLY_CONSISTENT"]
                events.append({"type": "PRICE_CONFIRMED"})
                if abs(change) > 1e-9:
                    events.append({"type": "PRICE_MOVEMENT",
                                   "direction": "UP" if change > 0 else "DOWN",
                                   "relative_change": change})
                record = replace(
                    record, price=(record.price + previous.price) / 2,
                    price_source="OCR_LABEL_PROXIMITY+TEMPORAL_CONFIRMATION",
                    error_reason=None, extracted_fields=fields)
            else:
                events.append({"type": "PRICE_READING_DISCONTINUITY"})
        if market and record.price is not None and not confirmed:
            fields["candidate_price"] = record.price
            fields["quality_flags"] = ["SINGLE_FRAME_PRICE_UNCONFIRMED"]
            record = replace(record, price=None, price_source=None,
                             error_reason="temporal_price_pending",
                             extracted_fields=fields)
        if market:
            fields["temporal_validation_reason"] = (
                "consecutive_price_confirmed" if confirmed else
                "awaiting_second_consistent_frame" if record.price is None and
                fields.get("candidate_price") is not None else
                "price_candidate_unavailable")
        else:
            fields["temporal_validation_reason"] = "not_market_frame"
        countdown = fields.get("countdown_seconds")
        if isinstance(countdown, (int, float)):
            old_countdown = (self.previous.extracted_fields.get("countdown_seconds")
                             if self.previous else None)
            if isinstance(old_countdown, (int, float)) and countdown < old_countdown:
                events.append({"type": "COUNTDOWN_ADVANCED",
                               "remaining_seconds": countdown})
        if events:
            fields["temporal_events"] = events
        self.previous = raw_record if market else None
        self.previous_classification = record.classification
        self.previous_asset = record.asset if market else None
        self.previous_application = record.application_context
        return replace(record, extracted_fields=fields)


def enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes_shcore = __import__("ctypes").windll.shcore
        ctypes_shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            __import__("ctypes").windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def capture_full_desktop(
        grabber: Callable[..., Image.Image] = ImageGrab.grab) -> Image.Image:
    """Capture all visible monitors and retain pixels only in process memory."""
    enable_dpi_awareness()
    image = grabber(all_screens=True)
    if not isinstance(image, Image.Image):
        raise ValueError("desktop_grabber_did_not_return_image")
    if image.width <= 0 or image.height <= 0:
        raise ValueError("desktop_capture_empty")
    return image.convert("RGB").copy()


def foreground_application() -> str | None:
    """Return only the foreground process basename, never its window title."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    hwnd = ctypes.windll.user32.GetForegroundWindow()
    return _window_process(hwnd) if hwnd else None


def visible_applications() -> tuple[str, ...]:
    if os.name != "nt":
        return ()
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    names: set[str] = set()
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def collect(hwnd, _parameter):
        if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            name = _window_process(hwnd)
            if name:
                names.add(name)
        return True

    user32.EnumWindows(callback_type(collect), 0)
    return tuple(sorted(names))


def _window_process(hwnd) -> str | None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, process_id.value)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)):
            return None
        return Path(buffer.value).stem.lower()
    finally:
        kernel32.CloseHandle(handle)


def read_ocr_words(
        image: Image.Image, *, executable: str | None = None,
        runner: Callable[..., object] = subprocess.run,
    timeout_seconds: float = 12.0, psm: int = 11) -> tuple[OcrWord, ...]:
    """Run local Tesseract TSV OCR through stdin; no image or transcript file."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    command = [resolve_tesseract_executable(executable), "stdin", "stdout",
               "tsv", "--psm", str(psm)]
    try:
        result = runner(command, input=buffer.getvalue(), capture_output=True,
                        timeout=timeout_seconds, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("ocr_unavailable_or_timeout") from exc
    if getattr(result, "returncode", 1) != 0:
        raise ValueError("ocr_engine_failed")
    raw = getattr(result, "stdout", "")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    words = []
    for row in csv.DictReader(io.StringIO(str(raw)), delimiter="\t"):
        if row.get("level") != "5" or not row.get("text", "").strip():
            continue
        try:
            confidence = float(row["conf"])
            box = tuple(int(row[key]) for key in ("left", "top", "width", "height"))
        except (KeyError, TypeError, ValueError):
            continue
        if confidence >= 0:
            words.append(OcrWord(row["text"].strip(), confidence, *box))
    return tuple(words)


def numeric_candidate_boxes(words: tuple[OcrWord, ...]) -> tuple[tuple[int, int, int, int], ...]:
    return tuple((word.left, word.top, word.left + word.width,
                  word.top + word.height) for word in words
                 if word.confidence >= 60 and
                 _NUMBER_RE.fullmatch(word.text.replace(",", "")))


def estimate_chart_region(image: Image.Image) -> list[float] | None:
    return _estimate_chart_region(image)


def analyze_frame(
        image: Image.Image, *, application: str | None = None,
        visible_applications: tuple[str, ...] = (),
        excluded_applications: tuple[str, ...] = (),
    ocr_reader: Callable[[Image.Image], tuple[OcrWord, ...]] = read_ocr_words,
    confirmed_price_roi: tuple[float, float, float, float] | None = None,
    confirmed_asset: str | None = None,
    price_ocr_reader: Callable[[Image.Image], tuple[OcrWord, ...]] | None = None,
        vision_price_roi: tuple[float, float, float, float] | None = None,
        vision_asset: str | None = None,
        vision_provenance: dict[str, object] | None = None,
        ) -> FrameAnalysis:
    app = (application or "").lower()
    excluded = tuple(term.strip().lower() for term in excluded_applications
                     if term.strip())
    if any(term in candidate.lower() for term in excluded
           for candidate in (app, *visible_applications) if candidate):
        return FrameAnalysis("NON_MARKET", 1.0, "SKIPPED", app, None, None,
                             None, "privacy_excluded_application", {})
    try:
        words = ocr_reader(image)
    except (OSError, RuntimeError, ValueError) as exc:
        fields = _stage_diagnostics(
            image, app, "FAILED", None, 0, "ocr_engine_failed", "NOT_RUN")
        return FrameAnalysis("UNKNOWN", 0.0, "FAILED", app or None, None,
                             None, None, _safe_ocr_error(str(exc)), fields)

    confident = tuple(word for word in words if word.confidence >= 35)
    chart_region = _estimate_chart_region(image)
    if not confident:
        visual_chart_candidate = _looks_like_chart(image, words)
        classification = "UNKNOWN"
        fields = {"chart_region": chart_region,
                  "quality_flags": (["UNVERIFIED_VISUAL_CHART_CANDIDATE"]
                                    if visual_chart_candidate else [])}
        reason = "ocr_no_confident_words" if words else "ocr_returned_no_words"
        fields.update(_stage_diagnostics(
            image, app, "SUCCEEDED_NO_WORDS", chart_region, 0,
            "no_numeric_or_current_price_candidate", "NOT_RUN"))
        if confirmed_price_roi is not None and confirmed_asset:
            roi_price, roi_count, roi_reason, roi_words = _read_confirmed_price_roi(
                image, confirmed_price_roi,
                price_ocr_reader or ocr_reader)
            fields["price_candidate_count"] = roi_count
            fields["price_region_ocr_word_count"] = len(roi_words)
            fields["price_rejection_reason"] = roi_reason
            if roi_price is not None:
                classification = ("BINOMO_MARKET" if
                                  confirmed_asset.strip().upper() == "CRYPTO IDX"
                                  else "OTHER_MARKET")
                fields["quality_flags"] = ["USER_CONFIRMED_CURRENT_PRICE_REGION"]
                fields["candidate_price"] = roi_price
                fields["confirmed_price_region"] = list(confirmed_price_roi)
                fields["price_candidate_region"] = list(confirmed_price_roi)
                fields["price_rejection_reason"] = None
                return FrameAnalysis(
                    classification, 0.8, "SUCCESS", app or None,
                    confirmed_asset.strip().upper(), roi_price,
                    "USER_CONFIRMED_PRICE_REGION", None, fields)
            classification = ("BINOMO_MARKET" if
                              confirmed_asset.strip().upper() == "CRYPTO IDX"
                              else "OTHER_MARKET")
            fields["ocr_engine_status"] = (
                "PRICE_REGION_FAILED" if roi_reason == "price_region_ocr_failed"
                else "PRICE_REGION_SUCCEEDED")
            fields["price_candidate_region"] = list(confirmed_price_roi)
            return FrameAnalysis(
                classification, 0.8, "FAILED" if roi_reason ==
                "price_region_ocr_failed" else "EMPTY", app or None,
                confirmed_asset.strip().upper(), None, None, roi_reason, fields)
        if vision_price_roi is not None:
            fields["price_rejection_reason"] = "vision_region_asset_not_ocr_verified"
            fields["visual_model"] = vision_provenance or {}
        return FrameAnalysis(classification, 0.55 if classification != "UNKNOWN" else 0.0,
                             "EMPTY", app or None, None, None, None,
                             reason, fields)
        return FrameAnalysis("UNKNOWN", 0.0, "EMPTY" if words else "SUCCESS",
                             app or None, None, None, None,
                             "ocr_no_confident_text" if words else "ocr_no_text", {})
    visible = " ".join(word.text for word in confident)
    normalized = re.sub(r"\s+", " ", visible).strip()
    upper = normalized.upper()
    asset_match = _ASSET_RE.search(upper)
    asset = asset_match.group(1).upper() if asset_match else None
    binomo = "BINOMO" in upper or app in {"binomo", "binomo.exe"}
    visual_chart = _looks_like_chart(image, confident)
    term_hits = {word for word in _MARKET_WORDS
                 if re.search(rf"\b{re.escape(word)}\b", upper.lower())}
    market_terms = bool(asset or len(term_hits) >= 2 or visual_chart or
                        app in _MARKET_APPS)
    if binomo and (market_terms or "IDX" in upper):
        classification = "BINOMO_MARKET"
    elif market_terms:
        classification = "OTHER_MARKET"
    else:
        classification = "NON_MARKET"
    confidence = min(0.99, 0.35 +
                     min(sum(word.confidence for word in confident) /
                         (len(confident) * 100), 0.45) +
                     (0.2 if market_terms else 0.0) +
                     (0.05 if visual_chart else 0.0))

    price_word, price_candidate_count, price_rejection = _validated_price(
        confident, image.size)
    price = None
    price_source = None
    error = None
    use_vision_region = bool(
        vision_price_roi and vision_asset and asset and
        vision_asset.strip().upper() == asset)
    selected_roi = (confirmed_price_roi if confirmed_price_roi is not None
                    else vision_price_roi if use_vision_region else None)
    selected_asset = (confirmed_asset if confirmed_price_roi is not None
                      else asset if use_vision_region else None)
    if selected_roi is not None and selected_asset:
        roi_price, roi_count, roi_reason, roi_words = _read_confirmed_price_roi(
            image, selected_roi, price_ocr_reader or ocr_reader,
            require_current_label=confirmed_price_roi is None)
        price_candidate_count = roi_count
        price_rejection = roi_reason
        if roi_price is not None:
            price = roi_price
            asset = selected_asset.strip().upper()
            classification = ("BINOMO_MARKET" if asset == "CRYPTO IDX"
                              else "OTHER_MARKET")
            price_source = "USER_CONFIRMED_PRICE_REGION"
    elif price_word is not None and classification in {
            "BINOMO_MARKET", "OTHER_MARKET"}:
        try:
            price = float(price_word.text.replace(",", ""))
            if not np.isfinite(price) or price <= 0:
                price = None
            else:
                price_source = "OCR_LABEL_PROXIMITY"
        except ValueError:
            price = None
    if classification in {"BINOMO_MARKET", "OTHER_MARKET"} and price is None:
        error = price_rejection or "price_context_unverified"
    fields: dict[str, object] = {}
    if asset:
        fields["asset"] = asset
    if classification in {"BINOMO_MARKET", "OTHER_MARKET"}:
        fields["price_candidates"] = _candidate_prices(confident, image.size)
        fields["chart_region"] = chart_region
    fields.update(_stage_diagnostics(
        image, app, "SUCCEEDED", chart_region, price_candidate_count,
        price_rejection, "NOT_RUN"))
    if vision_provenance is not None:
        fields["visual_model"] = vision_provenance
        fields["visual_model_region_rejection"] = (
            None if use_vision_region else "vision_region_asset_not_ocr_verified")
    if price_source:
        fields["price_source"] = price_source
        fields["candidate_price"] = price
        if confirmed_price_roi is not None:
            fields["price_candidate_region"] = list(confirmed_price_roi)
        elif use_vision_region:
            fields["price_candidate_region"] = list(vision_price_roi)
        elif price_word is not None:
            fields["price_candidate_region"] = [
                round(price_word.left / image.width, 4),
                round(price_word.top / image.height, 4),
                round((price_word.left + price_word.width) / image.width, 4),
                round((price_word.top + price_word.height) / image.height, 4)]
    fields["chart_interval"] = _chart_interval(confident)
    fields["trade_markers"] = _trade_markers(confident)
    fields["countdown_seconds"] = _countdown_seconds(confident)
    return FrameAnalysis(classification, confidence, "SUCCESS", app or None,
                         asset, price, price_source, error, fields)


def _stage_diagnostics(
        image: Image.Image, app: str, ocr_status: str,
        chart_region: list[float] | None, candidate_count: int,
        rejection: str | None, temporal_reason: str) -> dict[str, object]:
    return {
        "frame_dimensions": [image.width, image.height],
        "detected_market_application": app or None,
        "chart_candidate_region": chart_region,
        "price_candidate_region": None,
        "ocr_engine_status": ocr_status,
        "price_candidate_count": candidate_count,
        "price_rejection_reason": rejection,
        "temporal_validation_reason": temporal_reason,
    }


def _read_confirmed_price_roi(
        image: Image.Image, roi: tuple[float, float, float, float],
    ocr_reader: Callable[[Image.Image], tuple[OcrWord, ...]], *,
    require_current_label: bool = False) -> tuple[
            float | None, int, str | None, tuple[OcrWord, ...]]:
    if not (len(roi) == 4 and 0 <= roi[0] < roi[2] <= 1 and
            0 <= roi[1] < roi[3] <= 1):
        return None, 0, "confirmed_price_region_invalid", ()
    box = (round(image.width * roi[0]), round(image.height * roi[1]),
           round(image.width * roi[2]), round(image.height * roi[3]))
    crop = image.crop(box)
    if crop.width < 8 or crop.height < 6:
        return None, 0, "confirmed_price_region_too_small", ()
    prepared = ImageOps.autocontrast(ImageOps.grayscale(crop))
    prepared = ImageEnhance.Contrast(prepared).enhance(1.6)
    prepared = prepared.resize((prepared.width * 4, prepared.height * 4))
    try:
        words = ocr_reader(prepared)
    except (OSError, RuntimeError, ValueError):
        return None, 0, "price_region_ocr_failed", ()
    text = " ".join(word.text.strip() for word in words
                     if word.confidence >= 60).lower()
    if any(re.search(rf"\b{word}\b", text) for word in _PRICE_REJECTION_WORDS):
        return None, 0, "price_region_contains_noncurrent_field", words
    if require_current_label and not any(
            re.search(rf"\b{label}\b", text) for label in _PRICE_LABELS):
        return None, 0, "vision_price_label_not_ocr_verified", words
    candidates = []
    for word in words:
        value = word.text.replace(",", "")
        if word.confidence < 75 or not _NUMBER_RE.fullmatch(value):
            continue
        if "," in word.text and "." not in word.text:
            continue
        try:
            number = float(value)
        except ValueError:
            continue
        if np.isfinite(number) and number > 0:
            candidates.append(number)
    if len(candidates) != 1:
        return None, len(candidates), (
            "price_region_no_numeric_candidate" if not candidates else
            "price_region_ambiguous_numeric_candidates"), words
    return candidates[0], 1, None, words


def analyze_captured_frame(
        image: Image.Image, *, captured_at_ms: int,
        application: str | None = None,
    visible_applications: tuple[str, ...] = (),
        excluded_applications: tuple[str, ...] = (),
    ocr_reader: Callable[[Image.Image], tuple[OcrWord, ...]] = read_ocr_words,
    confirmed_price_roi: tuple[float, float, float, float] | None = None,
    confirmed_asset: str | None = None,
    price_ocr_reader: Callable[[Image.Image], tuple[OcrWord, ...]] | None = None,
        vision_price_roi: tuple[float, float, float, float] | None = None,
        vision_asset: str | None = None,
        vision_provenance: dict[str, object] | None = None,
        ) -> FrameRecord:
    started = time.monotonic()
    try:
        analysis = analyze_frame(
            image, application=application,
            visible_applications=visible_applications,
            excluded_applications=excluded_applications,
            ocr_reader=ocr_reader,
            confirmed_price_roi=confirmed_price_roi,
            confirmed_asset=confirmed_asset,
            price_ocr_reader=price_ocr_reader,
            vision_price_roi=vision_price_roi,
            vision_asset=vision_asset,
            vision_provenance=vision_provenance)
        fields = dict(analysis.extracted_fields)
        fields["frame_dimensions"] = [image.width, image.height]
        return FrameRecord(
            captured_at_ms, analysis.classification, analysis.confidence,
            analysis.extraction_status, analysis.application_context,
            analysis.asset, analysis.price, analysis.price_source,
            max(0, round((time.monotonic() - started) * 1000)),
            analysis.error_reason, fields)
    except Exception as exc:
        return FrameRecord(captured_at_ms, "UNKNOWN", 0.0, "FAILED",
                           application, None, None, None,
                           max(0, round((time.monotonic() - started) * 1000)),
                           _safe_ocr_error(str(exc)), {})


def _validated_price(
        words: tuple[OcrWord, ...],
        image_size: tuple[int, int]) -> tuple[OcrWord | None, int, str | None]:
    labels = [word for word in words if word.text.strip().lower() in _PRICE_LABELS
              and word.confidence >= 70]
    blocked = [word for word in words
               if word.text.strip().lower() in _PRICE_REJECTION_WORDS and
               word.confidence >= 60]
    prices = []
    for word in words:
        normalized = word.text.replace(",", "")
        if (word.confidence < 75 or not _NUMBER_RE.fullmatch(normalized) or
            not any(char in normalized for char in ".," ) or
            ("," in word.text and "." not in word.text)):
            continue
        try:
            value = float(normalized)
        except ValueError:
            continue
        if not np.isfinite(value) or value <= 0:
            continue
        prices.append(word)
    candidates = []
    blocked_count = 0
    max_x = max(180, round(image_size[0] * 0.18))
    max_y = max(70, round(image_size[1] * 0.08))
    for price in prices:
        px, py = price.left + price.width / 2, price.top + price.height / 2
        nearby = [label for label in labels
                  if abs((label.left + label.width / 2) - px) <= max_x and
                  abs((label.top + label.height / 2) - py) <= max_y]
        nearby_blocked = [label for label in blocked
                          if abs((label.left + label.width / 2) - px) <= max_x and
                          abs((label.top + label.height / 2) - py) <= max_y]
        if nearby_blocked:
            blocked_count += 1
            continue
        if len(nearby) == 1:
            candidates.append(price)
    if len(candidates) == 1:
        return candidates[0], len(prices), None
    if len(candidates) > 1:
        return None, len(prices), "multiple_current_price_candidates"
    if blocked_count:
        return None, len(prices), "candidate_near_forbidden_trade_field"
    if prices and not labels:
        return None, len(prices), "current_price_label_not_found"
    if prices:
        return None, len(prices), "current_price_label_ambiguous"
    return None, 0, "no_numeric_price_candidate"


def _looks_like_chart(image: Image.Image, words: tuple[OcrWord, ...]) -> bool:
    numeric_words = sum(bool(re.search(r"\d", word.text)) for word in words)
    sample = np.asarray(image.convert("RGB").resize((64, 36)), dtype=np.int16)
    gray = sample.mean(axis=2)
    horizontal = np.abs(np.diff(gray, axis=1))
    vertical = np.abs(np.diff(gray, axis=0))
    edges = ((horizontal > 28).mean() + (vertical > 28).mean()) / 2
    chroma = sample.max(axis=2) - sample.min(axis=2)
    color_coverage = (chroma > 35).mean()
    if numeric_words >= 6:
        return edges >= 0.12 and color_coverage >= 0.04
    grid_rows = (vertical > 20).mean(axis=1)
    horizontal_grid_coverage = (grid_rows >= 0.15).mean()
    return (color_coverage >= 0.01 and
            horizontal_grid_coverage >= 0.08)


def _candidate_prices(words: tuple[OcrWord, ...],
                      image_size: tuple[int, int]) -> list[dict[str, float]]:
    candidates = []
    for word in words:
        normalized = word.text.replace(",", "")
        if word.confidence < 60 or not _NUMBER_RE.fullmatch(normalized):
            continue
        x = (word.left + word.width / 2) / image_size[0]
        y = (word.top + word.height / 2) / image_size[1]
        if not 0.20 <= x <= 0.90 or not 0.15 <= y <= 0.90:
            continue
        try:
            value = float(normalized)
        except ValueError:
            continue
        if np.isfinite(value) and value > 0:
            candidates.append({"value": value, "x_ratio": round(x, 4),
                               "y_ratio": round(y, 4),
                               "confidence": round(word.confidence, 1)})
    return candidates[:20]


def _chart_interval(words: tuple[OcrWord, ...]) -> str | None:
    text = " ".join(word.text for word in words).upper()
    match = re.search(r"(?<!\d)(\d{1,3})\s*(SEC|S|MIN|M|H)(?![A-Z])", text)
    return f"{match.group(1)}{match.group(2)}" if match else None


def _trade_markers(words: tuple[OcrWord, ...]) -> list[str]:
    text = " ".join(word.text for word in words).lower()
    return [marker for marker in ("entry", "win", "loss", "result", "trade")
            if re.search(rf"\b{marker}\b", text)]


def _countdown_seconds(words: tuple[OcrWord, ...]) -> int | None:
    text = re.sub(r"\s*:\s*", ":", " ".join(word.text for word in words))
    match = re.search(r"(?<!\d)(\d{1,2}):([0-5]\d)(?!\d)", text)
    return int(match.group(1)) * 60 + int(match.group(2)) if match else None


def _estimate_chart_region(image: Image.Image) -> list[float] | None:
    sample = np.asarray(image.convert("RGB").resize((96, 54)), dtype=np.int16)
    gray = sample.mean(axis=2)
    edge = np.zeros(gray.shape, dtype=bool)
    edge[:, 1:] |= np.abs(np.diff(gray, axis=1)) > 25
    edge[1:, :] |= np.abs(np.diff(gray, axis=0)) > 25
    row_density, col_density = edge.mean(axis=1), edge.mean(axis=0)
    rows = np.flatnonzero(row_density >= 0.12)
    cols = np.flatnonzero(col_density >= 0.10)
    if len(rows) < 8 or len(cols) < 12:
        return None
    return [round(float(cols.min() / sample.shape[1]), 3),
            round(float(rows.min() / sample.shape[0]), 3),
            round(float((cols.max() + 1) / sample.shape[1]), 3),
            round(float((rows.max() + 1) / sample.shape[0]), 3)]


def _safe_ocr_error(message: str) -> str:
    normalized = message.lower()
    if "unavailable" in normalized or "timeout" in normalized:
        return "ocr_unavailable_or_timeout"
    if "engine" in normalized:
        return "ocr_engine_failed"
    return "ocr_extraction_failed"
