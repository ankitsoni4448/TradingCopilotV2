import unittest
from types import SimpleNamespace

from PIL import Image

from desktop_context import (
    parse_majority_opinion_ocr,
    parse_payout_ocr,
    read_majority_opinion,
    read_payout_ratio,
)


class DesktopContextTests(unittest.TestCase):
    def test_parser_accepts_compact_instrument_and_payout(self):
        result = parse_payout_ocr("CryptoIDX 83%")
        self.assertEqual(result.percent, 83)
        self.assertAlmostEqual(result.payout_ratio, 0.83)

    def test_parser_rejects_wrong_instrument_or_ambiguous_percentages(self):
        with self.assertRaisesRegex(ValueError, "instrument"):
            parse_payout_ocr("BTCUSDT 83%")
        with self.assertRaisesRegex(ValueError, "exactly_one"):
            parse_payout_ocr("Crypto IDX 83% 61%")

    def test_reader_uses_narrow_temporary_crop(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(returncode=0, stdout="Crypto IDX 82%\n")

        result = read_payout_ratio(
            Image.new("RGB", (1731, 800)), runner=runner)
        self.assertAlmostEqual(result.payout_ratio, 0.82)
        self.assertEqual(calls[0][0][0], "tesseract")
        self.assertEqual(calls[0][0][-2:], ["--psm", "7"])

    def test_reader_rejects_too_small_display(self):
        with self.assertRaisesRegex(ValueError, "display_size"):
            read_payout_ratio(Image.new("RGB", (320, 180)))

    def test_reader_accepts_1280_by_720_layout(self):
        result = read_payout_ratio(
            Image.new("RGB", (1280, 720)), runner=lambda *_args, **_kwargs:
            SimpleNamespace(returncode=0, stdout="Crypto IDX 82%"))
        self.assertEqual(result.percent, 82)

    def test_majority_parser_preserves_up_down_order(self):
        result = parse_majority_opinion_ocr(
            "Majority opinion nonsense bar 61% 39%")
        self.assertAlmostEqual(result.up_ratio, 0.61)
        self.assertAlmostEqual(result.down_ratio, 0.39)

    def test_majority_parser_rejects_bad_total_or_missing_label(self):
        with self.assertRaisesRegex(ValueError, "sum"):
            parse_majority_opinion_ocr("Majority opinion 61% 38%")
        with self.assertRaisesRegex(ValueError, "label"):
            parse_majority_opinion_ocr("61% 39%")

    def test_majority_reader_uses_narrow_temporary_crop(self):
        def runner(_command, **_kwargs):
            return SimpleNamespace(
                returncode=0, stdout="Majority opinion bar 74% 26%\n")

        result = read_majority_opinion(
            Image.new("RGB", (1731, 800)), runner=runner)
        self.assertEqual((result.up_percent, result.down_percent), (74, 26))


if __name__ == "__main__":
    unittest.main()
