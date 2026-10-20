import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from screen_observer import (
    OcrWord,
    FrameRecord,
    TemporalMarketTracker,
    analyze_frame,
    analyze_captured_frame,
    capture_full_desktop,
    read_ocr_words,
)


class ScreenObserverTests(unittest.TestCase):
    def test_full_desktop_capture_uses_backend_dimensions(self):
        for size in ((1280, 720), (1920, 1080)):
            calls = []
            image = capture_full_desktop(
                lambda **kwargs: calls.append(kwargs) or Image.new("RGB", size))
            self.assertEqual(image.size, size)
            self.assertEqual(calls, [{"all_screens": True}])

    def test_frame_record_retains_actual_image_dimensions(self):
        image = Image.new("RGB", (1920, 1080))
        record = analyze_captured_frame(
            image, captured_at_ms=1,
            ocr_reader=lambda _image: ())
        self.assertEqual(record.extracted_fields["frame_dimensions"], [1920, 1080])

    def test_binomo_market_price_requires_nearby_label_and_confidence(self):
        words = (
            OcrWord("Binomo", 94, 30, 10, 70, 20),
            OcrWord("Crypto", 92, 40, 100, 70, 20),
            OcrWord("IDX", 92, 115, 100, 30, 20),
            OcrWord("Current", 90, 800, 320, 40, 20),
            OcrWord("641.123456", 96, 850, 320, 110, 20),
        )
        result = analyze_frame(
            Image.new("RGB", (1920, 1080), "black"), application="chrome",
            ocr_reader=lambda _image: words)
        self.assertEqual(result.classification, "BINOMO_MARKET")
        self.assertEqual(result.asset, "CRYPTO IDX")
        self.assertEqual(result.price, 641.123456)
        self.assertEqual(result.price_source, "OCR_LABEL_PROXIMITY")

    def test_unlabeled_market_price_is_never_used(self):
        words = (OcrWord("BTCUSDT", 95, 10, 10, 80, 20),
                 OcrWord("641.123456", 95, 700, 350, 100, 20))
        result = analyze_frame(
            Image.new("RGB", (1280, 720), "black"),
            ocr_reader=lambda _image: words)
        self.assertEqual(result.classification, "OTHER_MARKET")
        self.assertIsNone(result.price)
        self.assertEqual(result.error_reason, "current_price_label_not_found")

    def test_diagnostics_explain_empty_ocr_without_trusting_visual_guess(self):
        result = analyze_captured_frame(
            Image.new("RGB", (1920, 1080), "black"), captured_at_ms=1,
            application="msedge", ocr_reader=lambda _image: ())
        self.assertEqual(result.classification, "UNKNOWN")
        self.assertEqual(result.extracted_fields["ocr_engine_status"],
                         "SUCCEEDED_NO_WORDS")
        self.assertEqual(result.extracted_fields["price_candidate_count"], 0)
        self.assertEqual(result.extracted_fields["price_rejection_reason"],
                         "no_numeric_or_current_price_candidate")
        self.assertEqual(result.extracted_fields["temporal_validation_reason"],
                         "NOT_RUN")

    def test_financial_field_proximity_rejects_current_price_candidate(self):
        words = (OcrWord("BTCUSDT", 95, 10, 10, 80, 20),
                 OcrWord("Current", 95, 700, 350, 50, 20),
                 OcrWord("Stake", 95, 710, 325, 45, 20),
                 OcrWord("641.123456", 95, 760, 350, 100, 20))
        result = analyze_frame(
            Image.new("RGB", (1280, 720)),
            ocr_reader=lambda _image: words)
        self.assertIsNone(result.price)
        self.assertEqual(result.error_reason,
                         "candidate_near_forbidden_trade_field")

    def test_confirmed_bounded_price_crop_requires_one_decimal_candidate(self):
        image = Image.new("RGB", (1920, 1080), "black")
        calls = []

        def crop_reader(crop):
            calls.append(crop.size)
            return (OcrWord("641.123456", 92, 2, 2, 100, 20),)

        result = analyze_frame(
            image, confirmed_price_roi=(0.65, 0.25, 0.82, 0.32),
            confirmed_asset="CRYPTO IDX", ocr_reader=lambda _image: (),
            price_ocr_reader=crop_reader)
        self.assertEqual(result.price, 641.123456)
        self.assertEqual(result.asset, "CRYPTO IDX")
        self.assertEqual(result.price_source, "USER_CONFIRMED_PRICE_REGION")
        self.assertGreater(calls[0][0], image.width * 0.17)

    def test_confirmed_price_region_rejects_stake_text(self):
        result = analyze_frame(
            Image.new("RGB", (1280, 720)),
            confirmed_price_roi=(0.6, 0.3, 0.8, 0.4),
            confirmed_asset="CRYPTO IDX", ocr_reader=lambda _image: (),
            price_ocr_reader=lambda _image: (
                OcrWord("Stake", 95, 0, 0, 30, 10),
                OcrWord("641.123456", 95, 40, 0, 100, 10)))
        self.assertIsNone(result.price)
        self.assertEqual(result.error_reason,
                         "price_region_contains_noncurrent_field")

    def test_confirmed_price_region_still_requires_two_temporal_frames(self):
        image = Image.new("RGB", (1280, 720))
        roi = (0.6, 0.3, 0.8, 0.4)

        def crop_reader(_image):
            return (OcrWord("641.123456", 95, 0, 0, 100, 10),)

        first = analyze_captured_frame(
            image, captured_at_ms=1000, confirmed_price_roi=roi,
            confirmed_asset="CRYPTO IDX", ocr_reader=lambda _image: (),
            price_ocr_reader=crop_reader)
        second = analyze_captured_frame(
            image, captured_at_ms=4000, confirmed_price_roi=roi,
            confirmed_asset="CRYPTO IDX", ocr_reader=lambda _image: (),
            price_ocr_reader=crop_reader)
        tracker = TemporalMarketTracker(max_gap_ms=5000)
        self.assertIsNone(tracker.update(first).price)
        confirmed = tracker.update(second)
        self.assertEqual(confirmed.price, 641.123456)
        self.assertEqual(confirmed.price_source,
                         "OCR_LABEL_PROXIMITY+TEMPORAL_CONFIRMATION")

    def test_vision_region_requires_ocr_asset_and_current_label(self):
        image = Image.new("RGB", (1280, 720))
        full_words = (OcrWord("BTCUSDT", 95, 20, 20, 80, 20),)
        wrong_asset = analyze_frame(
            image, ocr_reader=lambda _image: full_words,
            vision_price_roi=(0.7, 0.3, 0.8, 0.35),
            vision_asset="ETHUSDT",
            vision_provenance={"backend": "fake", "status": "READY_LOCAL_MODEL"})
        self.assertIsNone(wrong_asset.price)
        self.assertEqual(wrong_asset.extracted_fields[
            "visual_model_region_rejection"], "vision_region_asset_not_ocr_verified")

        no_label = analyze_frame(
            image, ocr_reader=lambda _image: full_words,
            price_ocr_reader=lambda _image: (
                OcrWord("641.123456", 95, 0, 0, 90, 12),),
            vision_price_roi=(0.7, 0.3, 0.8, 0.35),
            vision_asset="BTCUSDT")
        self.assertIsNone(no_label.price)
        self.assertEqual(no_label.error_reason,
                         "vision_price_label_not_ocr_verified")

        current_label = analyze_frame(
            image, ocr_reader=lambda _image: full_words,
            price_ocr_reader=lambda _image: (
                OcrWord("Current", 95, 0, 0, 40, 12),
                OcrWord("641.123456", 95, 45, 0, 90, 12)),
            vision_price_roi=(0.7, 0.3, 0.8, 0.35),
            vision_asset="BTCUSDT",
            vision_provenance={"backend": "fake", "model": "test"})
        self.assertEqual(current_label.price, 641.123456)
        self.assertEqual(current_label.asset, "BTCUSDT")

    def test_entry_price_is_not_treated_as_current_price(self):
        words = (OcrWord("BTCUSDT", 95, 10, 10, 80, 20),
                 OcrWord("Entry", 95, 700, 350, 50, 20),
                 OcrWord("641.123456", 95, 760, 350, 100, 20))
        result = analyze_frame(
            Image.new("RGB", (1280, 720), "black"),
            ocr_reader=lambda _image: words)
        self.assertIsNone(result.price)

    def test_interval_countdown_and_trade_markers_are_structured(self):
        words = (OcrWord("BTCUSDT", 95, 10, 10, 80, 20),
                 OcrWord("5m", 90, 100, 20, 20, 15),
                 OcrWord("00", 90, 300, 100, 20, 18),
                 OcrWord(":", 90, 321, 100, 5, 18),
                 OcrWord("42", 90, 327, 100, 20, 18),
                 OcrWord("Entry", 95, 400, 100, 40, 18),
                 OcrWord("Current", 90, 700, 300, 50, 20),
                 OcrWord("641.123456", 95, 760, 300, 100, 20))
        result = analyze_frame(
            Image.new("RGB", (1280, 720), "black"),
            ocr_reader=lambda _image: words)
        self.assertEqual(result.extracted_fields["chart_interval"], "5M")
        self.assertEqual(result.extracted_fields["countdown_seconds"], 42)
        self.assertIn("entry", result.extracted_fields["trade_markers"])

    def test_non_market_and_invalid_ocr_are_safe(self):
        image = Image.new("RGB", (1280, 720), "white")
        non_market = analyze_frame(
            image, ocr_reader=lambda _image: (OcrWord("Welcome", 95, 1, 1, 50, 15),))
        self.assertEqual(non_market.classification, "NON_MARKET")
        failed = analyze_frame(
            image, ocr_reader=lambda _image: (_ for _ in ()).throw(
                ValueError("ocr_engine_failed")))
        self.assertEqual(failed.classification, "UNKNOWN")
        self.assertEqual(failed.extraction_status, "FAILED")
        empty = analyze_frame(image, ocr_reader=lambda _image: ())
        self.assertEqual(empty.classification, "UNKNOWN")

    def test_visual_chart_without_ocr_stays_unknown_and_has_no_price(self):
        image = Image.new("RGB", (640, 360), "black")
        from PIL import ImageDraw
        draw = ImageDraw.Draw(image)
        for y in range(40, 340, 30):
            draw.line((0, y, 640, y), fill=(55, 55, 55), width=1)
        draw.line([(x, 190 + (x % 80) // 2) for x in range(30, 610, 4)],
                  fill=(40, 220, 100), width=3)
        result = analyze_frame(
            image, ocr_reader=lambda _image: ())
        self.assertEqual(result.classification, "UNKNOWN")
        self.assertIsNone(result.price)
        self.assertIn("UNVERIFIED_VISUAL_CHART_CANDIDATE",
                  result.extracted_fields["quality_flags"])

    def test_visible_sensitive_app_excludes_frame_before_ocr(self):
        calls = []
        result = analyze_frame(
            Image.new("RGB", (1280, 720)), application="chrome",
            visible_applications=("bank_app",),
            excluded_applications=("bank",),
            ocr_reader=lambda _image: calls.append(True) or ())
        self.assertEqual(result.extraction_status, "SKIPPED")
        self.assertEqual(result.error_reason, "privacy_excluded_application")
        self.assertEqual(calls, [])

    def test_tesseract_reads_png_bytes_from_stdin(self):
        output = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n5\t1\t1\t1\t1\t1\t10\t20\t30\t12\t91\tBTCUSDT\n"
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(returncode=0, stdout=output)

        with patch("screen_observer.resolve_tesseract_executable",
               return_value="configured-tesseract"):
            words = read_ocr_words(
            Image.new("RGB", (1280, 720)),
            executable="configured-tesseract", runner=runner)
        self.assertEqual(words[0].text, "BTCUSDT")
        self.assertEqual(calls[0][0][1:4], ["stdin", "stdout", "tsv"])
        self.assertIsInstance(calls[0][1]["input"], bytes)

    def test_temporal_tracker_requires_consistent_price_and_detects_movement(self):
        tracker = TemporalMarketTracker(max_gap_ms=5000)
        first = FrameRecord(1000, "OTHER_MARKET", 0.9, "SUCCESS", "chrome",
                            "BTCUSDT", 100.0, "OCR_LABEL_PROXIMITY", 10, None, {})
        second = FrameRecord(3000, "OTHER_MARKET", 0.9, "SUCCESS", "chrome",
                             "BTCUSDT", 101.0, "OCR_LABEL_PROXIMITY", 10, None, {})
        unconfirmed = tracker.update(first)
        confirmed = tracker.update(second)
        self.assertIsNone(unconfirmed.price)
        self.assertEqual(unconfirmed.error_reason, "temporal_price_pending")
        self.assertEqual(confirmed.price, 100.5)
        self.assertIn("TEMPORALLY_CONSISTENT",
                      confirmed.extracted_fields["quality_flags"])
        self.assertIn("PRICE_MOVEMENT", [event["type"] for event in
                                         confirmed.extracted_fields["temporal_events"]])


if __name__ == "__main__":
    unittest.main()
