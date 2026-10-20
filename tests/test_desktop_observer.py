import unittest
from types import SimpleNamespace

from PIL import Image

from desktop_observer import (
    PRICE_TAG_FILL_RGB,
    locate_current_price_tag,
    parse_price_ocr,
    read_current_price,
)


def synthetic_screen():
    image = Image.new("RGB", (1000, 500), (30, 31, 35))
    # Expected desktop anchor: right edge at 85% of the image width.
    for x in range(760, 850):
        for y in range(220, 240):
            image.putpixel((x, y), PRICE_TAG_FILL_RGB)
    return image


class DesktopObserverTests(unittest.TestCase):
    def test_price_tag_is_located_and_geometry_checked(self):
        result = locate_current_price_tag(synthetic_screen())
        self.assertEqual(result.box, (760, 220, 850, 240))
        self.assertEqual(result.fill_coverage, 1.0)

    def test_wrongly_anchored_tag_is_rejected(self):
        image = Image.new("RGB", (1000, 500), (30, 31, 35))
        for x in range(725, 805):
            for y in range(220, 240):
                image.putpixel((x, y), PRICE_TAG_FILL_RGB)
        with self.assertRaisesRegex(ValueError, "price_tag_not_found"):
            locate_current_price_tag(image)

    def test_detects_tolerant_tag_at_1280_by_720(self):
        image = Image.new("RGB", (1280, 720), (30, 31, 35))
        for x in range(980, 1094):
            for y in range(245, 274):
                image.putpixel((x, y), (180, 180, 180))
        detection = locate_current_price_tag(image)
        self.assertEqual(detection.box, (980, 245, 1094, 274))

    def test_supplied_1731_by_800_measurements(self):
        image = Image.new("RGB", (1731, 800), (30, 31, 35))
        for x in range(1325, 1480):
            for y in range(272, 304):
                image.putpixel((x, y), (210, 210, 210))
        detection = locate_current_price_tag(image)
        self.assertEqual(detection.box, (1325, 272, 1480, 304))

    def test_missing_and_ambiguous_price_tags_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "price_tag_not_found"):
            locate_current_price_tag(Image.new("RGB", (1280, 720), "black"))
        image = synthetic_screen()
        for x in range(760, 850):
            for y in range(170, 190):
                image.putpixel((x, y), PRICE_TAG_FILL_RGB)
        with self.assertRaisesRegex(ValueError, "price_tag_ambiguous"):
            locate_current_price_tag(image)

    def test_price_parser_rejects_axis_plus_current_price(self):
        with self.assertRaisesRegex(ValueError, "exactly_one"):
            parse_price_ocr("641.8674000000 641.8673994907")

    def test_narrow_ocr_returns_structured_price(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(returncode=0, stdout="641.8673994907\n")

        result = read_current_price(synthetic_screen(), runner=runner)
        self.assertAlmostEqual(result.price, 641.8673994907)
        self.assertEqual(calls[0][0][0], "tesseract")
        self.assertIn("tessedit_char_whitelist=0123456789.", calls[0][0])


if __name__ == "__main__":
    unittest.main()
