import unittest

from screen_extractor import (
    BINOMO_PHONE_PORTRAIT_V1,
    NormalizedRegion,
    OcrReading,
    extract_structured_screen,
)


def valid_readings(interval="1h"):
    return {
        "instrument_payout": OcrReading("Crypto IDX 83%", 0.99),
        "current_price": OcrReading("641.8674208824", 0.98),
        "chart_display_interval": OcrReading(interval, 0.99),
        "amount": OcrReading("₹100", 0.99),
        "expiry": OcrReading("08:46", 0.99),
        "time_to_buy": OcrReading("00:29", 0.98),
        "until_expiration": OcrReading("00:59", 0.98),
    }


class ScreenExtractorTests(unittest.TestCase):
    def test_regions_scale_to_reference_screenshot(self):
        boxes = {region.name: region.pixel_box(688, 1536)
                 for region in BINOMO_PHONE_PORTRAIT_V1.regions}
        self.assertEqual(boxes["instrument_payout"], (96, 169, 385, 276))
        self.assertEqual(
            next(region.locator for region in BINOMO_PHONE_PORTRAIT_V1.regions
                 if region.name == "current_price"),
            "dynamic_price_tag")

    def test_valid_screenshot_fields_are_accepted(self):
        result = extract_structured_screen(valid_readings("1d"))
        self.assertTrue(result.accepted)
        self.assertEqual(result.fields.chart_display_interval, "1d")
        self.assertAlmostEqual(result.fields.payout_ratio, 0.83)
        self.assertEqual(result.fields.time_to_buy_seconds, 29)
        self.assertEqual(result.fields.until_expiration_seconds, 59)

    def test_low_confidence_price_is_rejected(self):
        readings = valid_readings()
        readings["current_price"] = OcrReading("641.8674208824", 0.72)
        result = extract_structured_screen(readings)
        self.assertFalse(result.accepted)
        self.assertIn("low_confidence:current_price", result.reasons)

    def test_multiple_price_candidates_are_rejected(self):
        readings = valid_readings()
        readings["current_price"] = OcrReading(
            "641.8674290000 641.8674208824", 0.99)
        result = extract_structured_screen(readings)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reasons, ("ambiguous_current_price",))

    def test_countdown_cross_check_rejects_inconsistent_ocr(self):
        readings = valid_readings()
        readings["until_expiration"] = OcrReading("01:59", 0.99)
        result = extract_structured_screen(readings)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reasons, ("countdown_window_mismatch",))

    def test_invalid_normalized_region_is_rejected(self):
        with self.assertRaises(ValueError):
            NormalizedRegion("bad", 0.8, 0.1, 0.2, 0.3)


if __name__ == "__main__":
    unittest.main()
