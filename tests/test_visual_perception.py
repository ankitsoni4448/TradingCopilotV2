import json
import unittest
from types import SimpleNamespace
from urllib.error import URLError

from PIL import Image

from visual_perception import (
    DisabledVisualBackend,
    OllamaLoopbackBackend,
    create_visual_backend,
)


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.data


class VisualPerceptionTests(unittest.TestCase):
    def response(self, value):
        return FakeResponse(json.dumps({"response": json.dumps(value)}).encode())

    def test_default_backend_is_explicit_local_ocr_fallback(self):
        backend = create_visual_backend()
        self.assertIsInstance(backend, DisabledVisualBackend)
        self.assertEqual(backend.status, "DISABLED_LOCAL_OCR_FALLBACK")
        self.assertEqual(backend.analyze(Image.new("RGB", (20, 20))).status,
                         "DISABLED_LOCAL_OCR_FALLBACK")

    def test_ollama_is_loopback_only_and_returns_bounded_region(self):
        body = {"confidence": 0.9, "asset": "CRYPTO IDX",
                "chart_region": [0.2, 0.1, 0.8, 0.8],
                "current_price_region": [0.7, 0.3, 0.8, 0.34],
                "candle_region": [0.3, 0.3, 0.7, 0.75],
                "timer_region": [0.75, 0.2, 0.85, 0.25],
                "trade_markers": ["entry"]}
        calls = []

        def requester(request, timeout):
            calls.append((request.full_url, timeout))
            return self.response(body)

        backend = OllamaLoopbackBackend("small-local-vlm", requester=requester)
        result = backend.analyze(Image.new("RGB", (64, 64)))
        self.assertEqual(calls[0][0], "http://127.0.0.1:11434/api/generate")
        self.assertEqual(result.status, "READY_LOCAL_MODEL")
        self.assertEqual(result.current_price_region, (0.7, 0.3, 0.8, 0.34))
        self.assertEqual(result.chart_region, (0.2, 0.1, 0.8, 0.8))
        self.assertEqual(result.model, "small-local-vlm")

    def test_runtime_unavailable_and_timeout_are_safe_fallbacks(self):
        def unavailable(*_args, **_kwargs):
            raise URLError("not running")

        backend = OllamaLoopbackBackend("local-model", requester=unavailable)
        result = backend.analyze(Image.new("RGB", (20, 20)))
        self.assertEqual(result.status, "UNAVAILABLE_LOCAL_RUNTIME")
        self.assertIn("OCR fallback", result.reason)

        def timeout(*_args, **_kwargs):
            raise TimeoutError()

        backend = OllamaLoopbackBackend("local-model", requester=timeout)
        self.assertEqual(backend.analyze(Image.new("RGB", (20, 20))).status,
                         "UNAVAILABLE_LOCAL_RUNTIME")

    def test_invalid_or_low_confidence_regions_are_not_used(self):
        for value in (
                {"confidence": 0.4, "asset": "BTC", "current_price_region": [0, 0, 1, 1]},
                {"confidence": 0.9, "asset": "BTC",
                 "current_price_region": [0, 0, 1, 1]}):
            backend = OllamaLoopbackBackend(
                "local-model", requester=lambda *_args, **_kwargs:
                self.response(value))
            result = backend.analyze(Image.new("RGB", (40, 40)))
            self.assertIsNone(result.current_price_region)
            if value["confidence"] < 0.7:
                self.assertEqual(result.status, "LOW_CONFIDENCE_LOCAL_MODEL")

    def test_only_supported_local_backend_can_be_created(self):
        with self.assertRaisesRegex(ValueError, "not_supported"):
            create_visual_backend("remote_api", model="anything")
        with self.assertRaisesRegex(ValueError, "model_name_required"):
            create_visual_backend("ollama_loopback")


if __name__ == "__main__":
    unittest.main()
