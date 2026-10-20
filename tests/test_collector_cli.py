import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from collector_cli import calibration_probe, load_config, run_collection
from collection_audit import safe_error_code
from ocr_support import resolve_tesseract_executable


class CollectorCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "collector.json"

    def tearDown(self):
        self.temp.cleanup()

    def payload(self):
        return {"database_path": "evidence.db",
                "region": {"left": 10, "top": 20,
                           "width": 1731, "height": 800},
                "interval_seconds": 5, "max_capture_age_seconds": 10,
                "reference_symbols": ["BTCUSDT", "ETHUSDT"]}

    def write(self, payload):
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def test_relative_database_is_resolved_from_config_directory(self):
        self.write(self.payload())
        config = load_config(self.path)
        self.assertEqual(config.database_path,
                         (self.path.parent / "evidence.db").resolve())
        self.assertEqual(config.region.bbox, (10, 20, 1741, 820))

    def test_unknown_config_field_is_rejected(self):
        payload = self.payload()
        payload["api_key"] = "must-not-be-accepted"
        self.write(payload)
        with self.assertRaisesRegex(ValueError, "unknown"):
            load_config(self.path)

    def test_unsafe_sampling_interval_is_rejected(self):
        payload = self.payload()
        payload["interval_seconds"] = 0
        self.write(payload)
        with self.assertRaisesRegex(ValueError, "between"):
            load_config(self.path)

    def test_unsupported_reference_symbol_is_rejected_at_startup(self):
        payload = self.payload()
        payload["reference_symbols"].append("NOTAREALPAIR")
        self.write(payload)
        with self.assertRaisesRegex(ValueError, "not_supported"):
            load_config(self.path)

    def test_major_five_reference_symbols_are_supported(self):
        payload = self.payload()
        payload["reference_symbols"] = [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
        self.write(payload)
        self.assertEqual(load_config(self.path).reference_symbols,
                         tuple(payload["reference_symbols"]))

    def test_visual_backend_defaults_disabled_and_requires_explicit_model(self):
        payload = self.payload()
        self.write(payload)
        self.assertEqual(load_config(self.path).vision_backend, "disabled")
        payload["vision_backend"] = "ollama_loopback"
        self.write(payload)
        with self.assertRaisesRegex(ValueError, "model_required"):
            load_config(self.path)

    def test_1280_by_720_config_and_explicit_tesseract_path(self):
        payload = self.payload()
        payload["region"] = {"left": 0, "top": 0,
                             "width": 1280, "height": 720}
        payload["tesseract_executable"] = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        payload["price_tag_fill_rgb"] = [100, 110, 120]
        self.write(payload)
        config = load_config(self.path)
        self.assertEqual((config.region.width, config.region.height), (1280, 720))
        self.assertEqual(config.tesseract_executable,
                         payload["tesseract_executable"])
        self.assertEqual(config.price_tag_fill_rgb, (100, 110, 120))

    def test_missing_ocr_has_actionable_failure_and_safe_code(self):
        with patch("ocr_support.shutil.which", return_value=None), \
                patch("ocr_support.Path.is_file", return_value=False):
            with self.assertRaisesRegex(ValueError, "set tesseract_executable"):
                resolve_tesseract_executable()
        self.assertEqual(safe_error_code("ocr_engine_not_found"), "OCR_ENGINE")

    def test_required_failures_have_distinct_safe_codes(self):
        self.assertEqual(safe_error_code("collector_config_unreadable"), "CONFIG")
        self.assertEqual(safe_error_code("display_size_mismatch"), "DISPLAY_SIZE")
        self.assertEqual(safe_error_code("price_tag_not_found"), "PRICE_TAG")
        self.assertEqual(safe_error_code("platform_clock_invalid"), "PLATFORM_CLOCK")
        self.assertEqual(safe_error_code("failed to fetch reference ticks"),
                         "NETWORK_FAILURE")

    def test_continuous_mode_stops_before_database_on_calibration_failure(self):
        self.write(self.payload())
        config = load_config(self.path)
        with patch("collector_cli.resolve_tesseract_executable",
                   return_value="tesseract"), \
                patch("collector_cli.calibration_probe",
                      side_effect=ValueError("price_tag_not_found")):
            with self.assertRaisesRegex(ValueError, "price_tag_not_found"):
                run_collection(config)
        self.assertFalse(config.database_path.exists())

    @patch("collector_cli.datetime")
    @patch("collector_cli.read_majority_opinion")
    @patch("collector_cli.read_payout_ratio")
    @patch("collector_cli.read_desktop_time")
    @patch("collector_cli.read_current_price")
    def test_calibration_has_no_database_or_network_side_effects(
            self, price_reader, time_reader, payout_reader, majority_reader,
            datetime_mock):
        self.write(self.payload())
        config = load_config(self.path)
        captured = datetime(2026, 9, 30, 14, 0, tzinfo=timezone.utc)
        datetime_mock.now.return_value = captured
        price_reader.return_value = SimpleNamespace(price=641.8674)
        payout_reader.return_value = SimpleNamespace(payout_ratio=0.83)
        majority_reader.return_value = SimpleNamespace(
            up_ratio=0.61, down_ratio=0.39)
        time_reader.return_value = SimpleNamespace(
            observed_at=captured, expiry_at=captured.replace(minute=1))
        result = calibration_probe(
            config, provider_factory=lambda _:
            lambda: Image.new("RGB", (1731, 800)))
        self.assertEqual(result["status"], "CALIBRATED")
        self.assertEqual(result["payout_ratio"], 0.83)
        self.assertEqual(result["majority_opinion"]["up_ratio"], 0.61)
        self.assertFalse(result["pixels_saved"])
        self.assertFalse(config.database_path.exists())
