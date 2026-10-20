"""Local Windows launcher for demo-only Trading Copilot collection."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Callable

from capture_service import CaptureCoordinator
from collection_audit import CollectionAuditStore, safe_error_code
from continuous_collector import CollectionAttempt, ContinuousCollector
from desktop_context import read_majority_opinion, read_payout_ratio
from desktop_observer import PRICE_TAG_FILL_RGB, read_current_price
from desktop_time import read_desktop_time
from market_data import ALLOWED_SYMBOLS, DEFAULT_REFERENCE_SYMBOLS
from observation_store import ObservationStore
from ocr_support import resolve_tesseract_executable
from windows_capture import BoundedDesktopProvider, CaptureRegion


@dataclass(frozen=True)
class CollectorConfig:
    database_path: Path
    region: CaptureRegion
    interval_seconds: float = 5.0
    max_capture_age_seconds: float = 10.0
    reference_symbols: tuple[str, ...] = DEFAULT_REFERENCE_SYMBOLS
    tesseract_executable: str | None = None
    price_tag_fill_rgb: tuple[int, int, int] = PRICE_TAG_FILL_RGB
    price_tag_color_tolerance: int = 18
    price_tag_roi: tuple[float, float, float, float] = (0.68, 0.08, 0.995, 0.95)
    vision_backend: str = "disabled"
    vision_model: str | None = None
    vision_timeout_seconds: float = 2.0


def load_config(path: str | Path) -> CollectorConfig:
    config_path = Path(path).resolve()
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("collector_config_unreadable") from exc
    expected = {"database_path", "region", "interval_seconds",
                "max_capture_age_seconds", "reference_symbols",
                "tesseract_executable", "price_tag_fill_rgb",
                "price_tag_color_tolerance",
                "price_tag_roi", "vision_backend", "vision_model",
                "vision_timeout_seconds"}
    if not isinstance(payload, dict) or set(payload) - expected:
        raise ValueError("collector_config_has_unknown_fields")
    region = payload.get("region")
    if not isinstance(region, dict) or set(region) != {"left", "top", "width", "height"}:
        raise ValueError("collector_config_region_invalid")
    try:
        capture_region = CaptureRegion(**{key: int(value)
                                         for key, value in region.items()})
        interval = float(payload.get("interval_seconds", 5.0))
        max_age = float(payload.get("max_capture_age_seconds", 10.0))
        symbols = tuple(str(value).upper() for value in payload.get(
            "reference_symbols", list(DEFAULT_REFERENCE_SYMBOLS)))
        tesseract = payload.get("tesseract_executable")
        fill_rgb = tuple(int(value) for value in payload.get(
            "price_tag_fill_rgb", PRICE_TAG_FILL_RGB))
        color_tolerance = int(payload.get("price_tag_color_tolerance", 18))
        roi = tuple(float(value) for value in payload.get(
            "price_tag_roi", (0.68, 0.08, 0.995, 0.95)))
        vision_backend = str(payload.get("vision_backend", "disabled"))
        vision_model = payload.get("vision_model")
        vision_timeout = float(payload.get("vision_timeout_seconds", 2.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("collector_config_value_invalid") from exc
    if not 1 <= interval <= 60:
        raise ValueError("interval_seconds_must_be_between_1_and_60")
    if not 0 < max_age <= 60:
        raise ValueError("max_capture_age_seconds_must_be_in_0_60")
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("reference_symbols_must_be_nonempty_unique")
    if set(symbols) - ALLOWED_SYMBOLS:
        raise ValueError("reference_symbols_not_supported")
    if tesseract is not None and (not isinstance(tesseract, str) or not tesseract.strip()):
        raise ValueError("collector_config_tesseract_path_invalid")
    if len(fill_rgb) != 3 or any(not 0 <= value <= 255 for value in fill_rgb):
        raise ValueError("collector_config_price_tag_color_invalid")
    if not 0 <= color_tolerance <= 64:
        raise ValueError("collector_config_price_tag_tolerance_invalid")
    if (len(roi) != 4 or
            not (0 <= roi[0] < roi[2] <= 1 and 0 <= roi[1] < roi[3] <= 1)):
        raise ValueError("collector_config_price_tag_roi_invalid")
    if vision_backend not in {"disabled", "ollama_loopback"}:
        raise ValueError("collector_config_vision_backend_invalid")
    if vision_model is not None and (
            not isinstance(vision_model, str) or len(vision_model.strip()) > 100):
        raise ValueError("collector_config_vision_model_invalid")
    if vision_backend == "ollama_loopback" and not vision_model:
        raise ValueError("collector_config_vision_model_required")
    if not 0.1 <= vision_timeout <= 10:
        raise ValueError("collector_config_vision_timeout_invalid")
    database = Path(str(payload.get("database_path", "trading_copilot.db")))
    if not database.is_absolute():
        database = config_path.parent / database
    return CollectorConfig(database.resolve(), capture_region, interval,
                           max_age, symbols, tesseract, fill_rgb,
                           color_tolerance, roi, vision_backend, vision_model,
                           vision_timeout)


def calibration_probe(config: CollectorConfig, *,
                      provider_factory: Callable[[CaptureRegion], Callable] =
                      lambda region: BoundedDesktopProvider(region),
                      tesseract_executable: str | None = None) -> dict:
    """Read one frame without network or database side effects."""
    captured_at = datetime.now(timezone.utc)
    image = provider_factory(config.region)()
    executable = tesseract_executable or resolve_tesseract_executable(
        config.tesseract_executable)
    price = read_current_price(
        image, tesseract_executable=executable,
        expected_fill=config.price_tag_fill_rgb,
        color_tolerance=config.price_tag_color_tolerance,
        roi=config.price_tag_roi)
    payout = read_payout_ratio(image, tesseract_executable=executable)
    try:
        majority = read_majority_opinion(
            image, tesseract_executable=executable)
        majority_payload = {"up_ratio": majority.up_ratio,
                            "down_ratio": majority.down_ratio,
                            "quality": "AVAILABLE_EXPLORATORY_ONLY"}
    except ValueError:
        majority_payload = {"up_ratio": None, "down_ratio": None,
                            "quality": "UNAVAILABLE_EXPLORATORY_ONLY"}
    timing = read_desktop_time(
        image, captured_at=captured_at, tesseract_executable=executable)
    age = abs((captured_at - timing.observed_at.astimezone(timezone.utc))
              .total_seconds())
    if age > config.max_capture_age_seconds:
        raise ValueError("platform_clock_stale_or_date_mismatch")
    return {"status": "CALIBRATED", "price": price.price,
            "payout_ratio": payout.payout_ratio,
            "majority_opinion": majority_payload,
            "platform_time": timing.observed_at.isoformat(),
            "expiry": timing.expiry_at.isoformat(),
            "capture_age_seconds": age, "pixels_saved": False}


def run_collection(config: CollectorConfig, *, once: bool = False,
                   provider_factory: Callable[[CaptureRegion], Callable] =
                   lambda region: BoundedDesktopProvider(region),
                   output: Callable[[str], None] = print,
                   tesseract_executable: str | None = None,
                   stop_event: Event | None = None) -> None:
    stopped = stop_event or Event()
    executable = tesseract_executable or resolve_tesseract_executable(
        config.tesseract_executable)
    if not once:
        calibration = calibration_probe(
            config, provider_factory=provider_factory,
            tesseract_executable=executable)
        output(json.dumps({"calibration": calibration}, sort_keys=True))
        if stopped.is_set():
            return
    config.database_path.parent.mkdir(parents=True, exist_ok=True)
    observations = ObservationStore(config.database_path)
    audit = CollectionAuditStore(config.database_path)
    collector = ContinuousCollector(
        CaptureCoordinator(observations),
        image_provider=provider_factory(config.region),
        reference_symbols=config.reference_symbols,
        max_capture_age_seconds=config.max_capture_age_seconds,
        tesseract_executable=executable,
        price_tag_fill_rgb=config.price_tag_fill_rgb,
        price_tag_color_tolerance=config.price_tag_color_tolerance,
        price_tag_roi=config.price_tag_roi,
        audit_recorder=audit.record)
    def report(attempt: CollectionAttempt) -> None:
        if attempt.status == "ACCEPTED":
            assert attempt.result is not None
            message = {"status": "ACCEPTED",
                       "capture_id": attempt.result.capture_id,
                       "quality": attempt.result.quality,
                       "payout_ratio": attempt.result.payout_ratio,
                       "majority_opinion": {
                           "up_ratio": attempt.result.majority_up_ratio,
                           "down_ratio": attempt.result.majority_down_ratio,
                           "quality_reasons":
                               attempt.result.context_quality_reasons},
                       "latency_ms": attempt.result.reference_latency_ms,
                       "max_offset_ms":
                           attempt.result.max_target_reference_offset_ms}
        else:
            message = {"status": "REJECTED",
                       "reason": safe_error_code(attempt.error)}
        output(json.dumps(message, sort_keys=True))
        if once:
            stopped.set()

    try:
        collector.run(should_stop=stopped.is_set,
                      interval_seconds=config.interval_seconds,
                      on_attempt=report)
    except KeyboardInterrupt:
        stopped.set()
    health = audit.summary()
    output(json.dumps({"session_summary": {
        "attempts_in_database": health.attempts,
        "accepted": health.accepted, "rejected": health.rejected,
        "acceptance_rate": health.acceptance_rate,
        "rejection_reasons": health.rejection_reasons}}, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Trading Copilot V2 demo-only observation collector")
    parser.add_argument("--config", default="collector_config.json")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--calibrate", action="store_true")
    modes.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        executable = resolve_tesseract_executable(config.tesseract_executable)
        if args.calibrate:
            print(json.dumps(calibration_probe(
                config, tesseract_executable=executable), sort_keys=True))
        else:
            run_collection(config, once=args.once,
                           tesseract_executable=executable)
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        code = safe_error_code(str(exc))
        error = {"status": "ERROR", "reason": code}
        if code == "OCR_ENGINE":
            error["action"] = (
                "Install Tesseract OCR or set tesseract_executable in "
                "collector_config.json to the full tesseract.exe path.")
        print(json.dumps(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
