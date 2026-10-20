"""Responsive background worker for full-desktop observation sessions."""
from __future__ import annotations

import queue
import json
import threading
import time
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from typing import Callable

from observer_store import ObserverStore
from screen_observer import (
    FrameRecord,
    TemporalMarketTracker,
    analyze_captured_frame,
    capture_full_desktop,
    foreground_application,
    read_ocr_words,
    visible_applications,
)
from visual_perception import (
    DisabledVisualBackend,
    VisualPerceptionBackend,
    VisualPerceptionResult,
)


class DesktopObserverSession:
    def __init__(self, database_path: str | Path, *,
                 capture: Callable = capture_full_desktop,
                 foreground: Callable[[], str | None] = foreground_application,
                 visible_apps: Callable[[], tuple[str, ...]] = visible_applications,
                 analyzer: Callable = analyze_captured_frame,
                 tesseract_executable: str | None = None,
                 visual_backend: VisualPerceptionBackend | None = None):
        self.database_path = Path(database_path)
        self.capture = capture
        self.foreground = foreground
        self.visible_apps = visible_apps
        self.analyzer = analyzer
        self.tesseract_executable = tesseract_executable
        self.visual_backend = visual_backend or DisabledVisualBackend()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_event = threading.Event()
        self.active_event = threading.Event()
        self.active_event.set()
        self._worker: threading.Thread | None = None
        self._processing_worker: threading.Thread | None = None
        self._frame_queue: queue.Queue = queue.Queue(maxsize=1)
        self._dropped_frames = 0
        self._start_lock = threading.Lock()
        self.store: ObserverStore | None = None
        self.session_id: str | None = None
        self.interval_seconds = 5
        self.excluded_applications: tuple[str, ...] = ()
        self.confirmed_price_roi: tuple[float, float, float, float] | None = None
        self.confirmed_asset: str | None = None

    def set_confirmed_price_region(
            self, roi: tuple[float, float, float, float], asset: str) -> None:
        normalized = asset.strip().upper()
        if (len(roi) != 4 or
                not (0 <= roi[0] < roi[2] <= 1 and 0 <= roi[1] < roi[3] <= 1)):
            raise ValueError("confirmed_price_region_invalid")
        if not normalized:
            raise ValueError("confirmed_price_asset_required")
        self.confirmed_price_roi = tuple(float(value) for value in roi)
        self.confirmed_asset = normalized

    @property
    def is_running(self) -> bool:
        return any(worker is not None and worker.is_alive()
                   for worker in (self._worker, self._processing_worker))

    @property
    def is_paused(self) -> bool:
        return self.is_running and not self.active_event.is_set()

    def start(self, *, interval_seconds: int = 5,
              excluded_applications: tuple[str, ...] = ()) -> bool:
        with self._start_lock:
            if self.is_running:
                return False
            if interval_seconds not in {2, 3, 5}:
                raise ValueError("observer_interval_must_be_2_3_or_5_seconds")
            self.interval_seconds = interval_seconds
            self.excluded_applications = tuple(
                value.strip().lower() for value in excluded_applications
                if value.strip())
            self.stop_event.clear()
            self.active_event.set()
            self._frame_queue = queue.Queue(maxsize=1)
            self._dropped_frames = 0
            self.store = ObserverStore(self.database_path)
            self.session_id = self.store.new_session_id()
            self.store.start_session(self.session_id, interval_seconds)
            self._worker = threading.Thread(
                target=self._capture_loop, name="trading-copilot-capture", daemon=True)
            self._processing_worker = threading.Thread(
                target=self._process_loop, name="trading-copilot-perception", daemon=True)
            self._processing_worker.start()
            self._worker.start()
            return True

    def stop(self) -> None:
        self.stop_event.set()
        self.active_event.set()

    def pause(self) -> None:
        if self.is_running:
            self.active_event.clear()
            self.events.put(("status", "PAUSED"))

    def resume(self) -> None:
        if self.is_running:
            self.active_event.set()
            self.events.put(("status", "CAPTURING"))

    def join(self, timeout: float | None = None) -> None:
        deadline = None if timeout is None else time.monotonic() + timeout
        for worker in (self._worker, self._processing_worker):
            if worker is not None:
                remaining = None if deadline is None else max(0, deadline - time.monotonic())
                worker.join(remaining)

    def add_trade(self, **trade) -> int:
        if not self.store or not self.session_id:
            raise ValueError("no_observer_session_available")
        return self.store.add_trade(self.session_id, **trade)

    def update_trade(self, trade_id: int, **trade) -> None:
        if not self.store:
            raise ValueError("no_observer_session_available")
        self.store.update_trade(trade_id, **trade)

    def trades(self) -> tuple[dict, ...]:
        if not self.store or not self.session_id:
            return ()
        return self.store.trades(self.session_id)

    def refresh_report(self) -> Path | None:
        if not self.store or not self.session_id:
            return None
        return self.store.generate_report(self.session_id)

    def _capture_loop(self) -> None:
        assert self.session_id is not None
        try:
            while not self.stop_event.is_set():
                if not self.active_event.wait(0.1):
                    continue
                cycle_started = time.monotonic()
                captured_at_ms = round(datetime.now(timezone.utc).timestamp() * 1000)
                try:
                    image = self.capture()
                    frame = {
                        "image": image,
                        "captured_at_ms": captured_at_ms,
                        "application": self.foreground(),
                        "visible_applications": self.visible_apps(),
                    }
                except Exception as exc:
                    frame = {"record": FrameRecord(
                        captured_at_ms, "UNKNOWN", 0.0, "FAILED", None, None,
                        None, None, 0, _capture_error_code(exc), {})}
                try:
                    self._frame_queue.put_nowait(frame)
                except queue.Full:
                    try:
                        self._frame_queue.get_nowait()
                    except queue.Empty:
                        pass
                    self._dropped_frames += 1
                    self.events.put(("status", "CAPTURED_NEWEST_FRAME; STALE_FRAME_DROPPED"))
                    self._frame_queue.put_nowait(frame)
                remaining = self.interval_seconds - (time.monotonic() - cycle_started)
                while remaining > 0 and not self.stop_event.is_set():
                    if not self.active_event.wait(0.1):
                        break
                    self.stop_event.wait(min(0.1, remaining))
                    remaining = self.interval_seconds - (time.monotonic() - cycle_started)
        finally:
            try:
                self._frame_queue.put_nowait(None)
            except queue.Full:
                try:
                    self._frame_queue.get_nowait()
                except queue.Empty:
                    pass
                self._frame_queue.put_nowait(None)
            if self._processing_worker is not None:
                self._processing_worker.join()

    def _process_loop(self) -> None:
        assert self.store is not None and self.session_id is not None
        tracker = TemporalMarketTracker(
            max_gap_ms=self.interval_seconds * 3000)
        try:
            while True:
                frame = self._frame_queue.get()
                if frame is None:
                    break
                if "record" in frame:
                    record = frame["record"]
                else:
                    started = time.monotonic()
                    image = frame["image"]
                    captured_at_ms = frame["captured_at_ms"]
                    try:
                        visual = self.visual_backend.analyze(image)
                    except Exception as exc:
                        visual = VisualPerceptionResult(
                            "BACKEND_ERROR_LOCAL_OCR_FALLBACK", "visual_backend",
                            self.visual_backend.model_name,
                            max(0, round((time.monotonic() - started) * 1000)),
                            reason=type(exc).__name__)
                    vision_provenance = {
                        "backend": visual.backend,
                        "status": visual.status,
                        "model": visual.model,
                        "latency_ms": visual.latency_ms,
                        "confidence": visual.confidence,
                    }
                    try:
                        record = self.analyzer(
                            image, captured_at_ms=captured_at_ms,
                            application=frame["application"],
                            visible_applications=frame["visible_applications"],
                            excluded_applications=self.excluded_applications,
                            confirmed_price_roi=self.confirmed_price_roi,
                            confirmed_asset=self.confirmed_asset,
                            vision_price_roi=visual.current_price_region
                            if visual.status == "READY_LOCAL_MODEL" else None,
                            vision_asset=visual.asset
                            if visual.status == "READY_LOCAL_MODEL" else None,
                            vision_provenance=vision_provenance,
                            ocr_reader=lambda source: read_ocr_words(
                                source, executable=self.tesseract_executable, psm=11),
                            price_ocr_reader=lambda source: read_ocr_words(
                                source, executable=self.tesseract_executable, psm=7))
                    except Exception as exc:
                        record = FrameRecord(
                            captured_at_ms, "UNKNOWN", 0.0, "FAILED",
                            frame["application"], None, None, None,
                            max(0, round((time.monotonic() - started) * 1000)),
                            _capture_error_code(exc), {
                                "visual_model": vision_provenance,
                                "price_rejection_reason": "perception_stage_failed"})
                    fields = dict(record.extracted_fields)
                    fields["visual_model"] = vision_provenance
                    fields["visual_model_latency_ms"] = visual.latency_ms
                    fields["ocr_engine_status"] = fields.get(
                        "ocr_engine_status", record.extraction_status)
                    fields["dropped_stale_frames"] = self._dropped_frames
                    record = replace(
                        record, processing_ms=max(
                            record.processing_ms,
                            round((time.monotonic() - started) * 1000)),
                        extracted_fields=fields)
                    record = tracker.update(record)
                observation_id = self.store.append_frame(self.session_id, record)
                counts = self.store.counts(self.session_id)
                self.events.put(("frame", {
                    "observation_id": observation_id,
                    "record": record,
                    "counts": counts,
                    "result": _display_result(record),
                }))
            report_path = self.store.generate_report(self.session_id, finish=True)
            report_data = json.loads(
                report_path.with_suffix(".json").read_text(encoding="utf-8"))
            self.events.put(("finished", {"report_path": str(report_path),
                                           "counts": self.store.counts(self.session_id),
                                           "model_readiness": report_data[
                                               "ml_readiness"]["status"]}))
        except Exception as exc:
            self.events.put(("failure", _capture_error_code(exc)))
        finally:
            self.events.put(("worker_exited", None))


def _capture_error_code(exc: Exception) -> str:
    message = str(exc).lower()
    if "ocr" in message or "tesseract" in message:
        return "OCR_ERROR"
    if "desktop" in message or "capture" in message or "imagegrab" in message:
        return "CAPTURE_ERROR"
    return "OBSERVER_ERROR"


def _display_result(record: FrameRecord) -> str:
    if record.price is not None:
        return f"{record.classification}: {record.asset or 'asset unknown'} {record.price}"
    if record.error_reason:
        return f"{record.classification}: {record.error_reason}"
    return record.classification
