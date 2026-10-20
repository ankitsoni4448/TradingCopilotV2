"""Pluggable visual layout perception; exact prices remain a separate OCR stage."""
from __future__ import annotations

import base64
import json
import math
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.error import URLError
from urllib.request import Request, urlopen

from PIL import Image


@dataclass(frozen=True)
class VisualPerceptionResult:
    status: str
    backend: str
    model: str | None
    latency_ms: int
    asset: str | None = None
    chart_region: tuple[float, float, float, float] | None = None
    current_price_region: tuple[float, float, float, float] | None = None
    candle_region: tuple[float, float, float, float] | None = None
    timer_region: tuple[float, float, float, float] | None = None
    trade_markers: tuple[str, ...] = ()
    confidence: float = 0.0
    reason: str | None = None


class VisualPerceptionBackend(Protocol):
    @property
    def status(self) -> str: ...

    @property
    def model_name(self) -> str | None: ...

    def analyze(self, image: Image.Image) -> VisualPerceptionResult: ...


class DisabledVisualBackend:
    status = "DISABLED_LOCAL_OCR_FALLBACK"
    model_name = None

    def analyze(self, image: Image.Image) -> VisualPerceptionResult:
        return VisualPerceptionResult(
            self.status, "disabled", None, 0,
            reason="vision_backend_not_configured; local OCR fallback active")


class OllamaLoopbackBackend:
    """Optional Ollama adapter restricted to localhost; never contacts cloud APIs."""

    endpoint = "http://127.0.0.1:11434/api/generate"
    backend_name = "ollama_loopback"

    def __init__(self, model: str, *, timeout_seconds: float = 2.0,
                 requester=urlopen):
        if not model.strip():
            raise ValueError("vision_model_name_required")
        if not 0.1 <= timeout_seconds <= 10:
            raise ValueError("vision_timeout_out_of_range")
        self._model = model.strip()
        self.timeout_seconds = timeout_seconds
        self.requester = requester
        self._status = "CONFIGURED_LOCAL_MODEL_UNVERIFIED"

    @property
    def status(self) -> str:
        return self._status

    @property
    def model_name(self) -> str:
        return self._model

    def analyze(self, image: Image.Image) -> VisualPerceptionResult:
        started = time.monotonic()
        buffer = __import__("io").BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=75)
        payload = json.dumps({
            "model": self._model,
            "stream": False,
            "format": "json",
            "prompt": (
                "Inspect this desktop image only. Do not estimate/read any price. "
                "Return JSON keys asset, confidence, chart_region, "
                "current_price_region, candle_region, timer_region, trade_markers. "
                "Each region is [left,top,right,bottom] normalized 0..1 or null. "
                "current_price_region must be the visible current/last quote label, "
                "not stake, balance, payout, countdown, entry marker, axis or history. "
                "Set it null when uncertain. asset is a visible exact label or null."),
            "images": [base64.b64encode(buffer.getvalue()).decode("ascii")],
            "options": {"temperature": 0},
        }).encode("utf-8")
        request = Request(self.endpoint, data=payload,
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self.requester(request, timeout=self.timeout_seconds) as response:
                response_data = json.loads(response.read().decode("utf-8"))
            content = response_data.get("response", "")
            description = json.loads(content) if isinstance(content, str) else content
            if not isinstance(description, dict):
                raise ValueError("vision_response_not_json_object")
            confidence = float(description.get("confidence", 0))
            if not math.isfinite(confidence) or confidence < 0.70:
                self._status = "LOW_CONFIDENCE_LOCAL_MODEL"
                return VisualPerceptionResult(
                    self._status, self.backend_name, self._model,
                    _elapsed_ms(started), confidence=confidence,
                    reason="vision_confidence_below_threshold")
            regions = {}
            for key, area_limit in (
                    ("chart_region", 0.95),
                    ("current_price_region", 0.25),
                    ("candle_region", 0.85),
                    ("timer_region", 0.08)):
                regions[key] = _region(
                    description.get(key), maximum_area=area_limit)
            asset = description.get("asset")
            if not isinstance(asset, str) or len(asset) > 32:
                asset = None
            markers = description.get("trade_markers", [])
            if not isinstance(markers, list):
                markers = []
            self._status = "READY_LOCAL_MODEL"
            return VisualPerceptionResult(
                self._status, self.backend_name, self._model, _elapsed_ms(started),
                asset=asset, confidence=confidence,
                trade_markers=tuple(str(item)[:24] for item in markers[:8]),
                **regions)
        except (URLError, TimeoutError, OSError):
            self._status = "UNAVAILABLE_LOCAL_RUNTIME"
            return VisualPerceptionResult(
                self._status, self.backend_name, self._model, _elapsed_ms(started),
                reason="loopback_vision_runtime_unavailable; local OCR fallback active")
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            self._status = "INVALID_LOCAL_MODEL_RESPONSE"
            return VisualPerceptionResult(
                self._status, self.backend_name, self._model, _elapsed_ms(started),
                reason="vision_response_invalid; local OCR fallback active")


def create_visual_backend(backend: str = "disabled", *,
                          model: str | None = None,
                          timeout_seconds: float = 2.0) -> VisualPerceptionBackend:
    if backend == "disabled":
        return DisabledVisualBackend()
    if backend == "ollama_loopback":
        if not model:
            raise ValueError("vision_model_name_required_for_ollama")
        return OllamaLoopbackBackend(model, timeout_seconds=timeout_seconds)
    raise ValueError("vision_backend_not_supported")


def _region(value, *, maximum_area: float) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    if (not isinstance(value, list) or len(value) != 4 or
            any(not isinstance(item, (float, int)) or not math.isfinite(item)
                for item in value)):
        return None
    left, top, right, bottom = (float(item) for item in value)
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        return None
    if (right - left) * (bottom - top) > maximum_area:
        return None
    return left, top, right, bottom


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))
