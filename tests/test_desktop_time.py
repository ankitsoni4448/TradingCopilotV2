import unittest
from datetime import date, datetime, time, timezone
from types import SimpleNamespace

from PIL import Image

from desktop_time import (
    parse_platform_clock,
    parse_selected_expiry,
    read_desktop_time,
    resolve_expiry_at,
)


class DesktopTimeTests(unittest.TestCase):
    def test_noisy_platform_clock_is_parsed(self):
        result = parse_platform_clock("09:52:56 GMT+5 08 :")
        self.assertEqual((result.hour, result.minute, result.second), (9, 52, 56))
        self.assertEqual(result.utc_offset_minutes, 300)

    def test_expiry_rejects_invalid_ocr(self):
        with self.assertRaisesRegex(ValueError, "exactly_one"):
            parse_selected_expiry("99:54")

    def test_same_day_expiry_is_timezone_aware(self):
        clock = parse_platform_clock("09:52:56 GMT+5")
        result = resolve_expiry_at(
            clock, time(9, 54), observed_date=date(2026, 9, 29))
        self.assertEqual(result.isoformat(), "2026-09-29T09:54:00+05:00")

    def test_midnight_rollover_is_explicitly_supported(self):
        clock = parse_platform_clock("23:59:50 GMT+5:30")
        result = resolve_expiry_at(
            clock, time(0, 1), observed_date=date(2026, 9, 29))
        self.assertEqual(result.isoformat(), "2026-09-30T00:01:00+05:30")

    def test_stale_same_day_expiry_is_not_rolled_to_tomorrow(self):
        clock = parse_platform_clock("09:52:56 GMT+5")
        with self.assertRaisesRegex(ValueError, "not_in_future"):
            resolve_expiry_at(
                clock, time(9, 51), observed_date=date(2026, 9, 29))

    def test_implausibly_distant_expiry_is_rejected(self):
        clock = parse_platform_clock("09:52:56 GMT+5")
        with self.assertRaisesRegex(ValueError, "too_far"):
            resolve_expiry_at(
                clock, time(11, 0), observed_date=date(2026, 9, 29))

    def test_adapter_uses_separate_narrow_crops(self):
        outputs = iter(("09:52:56 GMT+5\n", "09:54\n"))
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return SimpleNamespace(returncode=0, stdout=next(outputs))

        image = Image.new("RGB", (1731, 800), "black")
        result = read_desktop_time(
            image, observed_date=date(2026, 9, 29), runner=runner)
        self.assertEqual(result.expiry_at.isoformat(),
                         "2026-09-29T09:54:00+05:00")
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0][1], calls[1][1])

    def test_capture_date_is_derived_in_platform_timezone(self):
        outputs = iter(("00:00:05 GMT+5\n", "00:01\n"))

        def runner(_command, **_kwargs):
            return SimpleNamespace(returncode=0, stdout=next(outputs))

        result = read_desktop_time(
            Image.new("RGB", (1731, 800), "black"),
            captured_at=datetime(2026, 9, 29, 19, 0, 5, tzinfo=timezone.utc),
            runner=runner)
        self.assertEqual(result.observed_at.isoformat(),
                         "2026-09-30T00:00:05+05:00")

    def test_clock_and_expiry_crops_scale_to_1280_by_720(self):
        outputs = iter(("09:52:56 GMT+5", "09:54"))
        result = read_desktop_time(
            Image.new("RGB", (1280, 720), "black"),
            observed_date=date(2026, 9, 29),
            runner=lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0, stdout=next(outputs)))
        self.assertEqual(result.expiry_at.isoformat(),
                         "2026-09-29T09:54:00+05:00")


if __name__ == "__main__":
    unittest.main()
