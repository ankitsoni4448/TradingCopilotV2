import unittest
from PIL import Image
from windows_capture import BoundedDesktopProvider, CaptureRegion


class WindowsCaptureTests(unittest.TestCase):
    def test_explicit_region_is_passed_to_grabber(self):
        calls = []
        provider = BoundedDesktopProvider(
            CaptureRegion(100, 200, 1731, 800),
            display_size=(1920, 1080),
            grabber=lambda **kwargs: calls.append(kwargs) or
            Image.new("RGB", (1731, 800)))
        self.assertEqual(provider().size, (1731, 800))
        self.assertEqual(calls[0]["bbox"], (100, 200, 1831, 1000))

    def test_actual_1280_by_720_display_is_supported(self):
        provider = BoundedDesktopProvider(
            CaptureRegion(0, 0, 1280, 720), display_size=(1280, 720),
            grabber=lambda **_: Image.new("RGB", (1280, 720)))
        self.assertEqual(provider().size, (1280, 720))

    def test_original_capture_size_remains_supported(self):
        provider = BoundedDesktopProvider(
            CaptureRegion(0, 0, 1731, 800), display_size=(1920, 1080),
            grabber=lambda **_: Image.new("RGB", (1731, 800)))
        self.assertEqual(provider().size, (1731, 800))

    def test_region_outside_display_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "display_size_mismatch"):
            BoundedDesktopProvider(
                CaptureRegion(0, 0, 1731, 800), display_size=(1280, 720))

    def test_unexpected_grab_size_is_rejected(self):
        provider = BoundedDesktopProvider(
            CaptureRegion(0, 0, 1731, 800),
            display_size=(1920, 1080),
            grabber=lambda **_: Image.new("RGB", (100, 100)))
        with self.assertRaisesRegex(ValueError, "display_size_mismatch"):
            provider()
