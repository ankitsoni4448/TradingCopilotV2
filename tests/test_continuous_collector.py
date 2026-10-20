import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from continuous_collector import ContinuousCollector
from market_data import DEFAULT_REFERENCE_SYMBOLS


class FakeCoordinator:
    def __init__(self):
        self.calls = []

    def capture_batch(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(capture_id="capture-1")


class ContinuousCollectorTests(unittest.TestCase):
    @patch("continuous_collector.read_majority_opinion")
    @patch("continuous_collector.read_payout_ratio")
    @patch("continuous_collector.read_desktop_time")
    @patch("continuous_collector.read_current_price")
    def test_one_cycle_connects_validated_screen_to_batch_capture(
            self, price_reader, time_reader, payout_reader, majority_reader):
        captured = datetime(2026, 9, 30, 3, 0, 5, tzinfo=timezone.utc)
        observed = captured - timedelta(seconds=1)
        price_reader.return_value = SimpleNamespace(price=641.8674)
        payout_reader.return_value = SimpleNamespace(payout_ratio=0.83)
        majority_reader.return_value = SimpleNamespace(
            up_ratio=0.61, down_ratio=0.39)
        time_reader.return_value = SimpleNamespace(
            observed_at=observed, expiry_at=observed + timedelta(seconds=60))
        coordinator = FakeCoordinator()
        collector = ContinuousCollector(
            coordinator, image_provider=lambda: Image.new("RGB", (1731, 800)))
        attempt = collector.capture_once(captured_at=captured)
        self.assertEqual(attempt.status, "ACCEPTED")
        self.assertIsNone(attempt.error)
        self.assertEqual(len(coordinator.calls), 1)
        self.assertEqual(coordinator.calls[0]["reference_symbols"],
                         DEFAULT_REFERENCE_SYMBOLS)
        self.assertEqual(coordinator.calls[0]["payout_ratio"], 0.83)
        self.assertEqual(coordinator.calls[0]["majority_up_ratio"], 0.61)
        self.assertNotIn("chart_display_interval", coordinator.calls[0])

    @patch("continuous_collector.read_payout_ratio")
    @patch("continuous_collector.read_desktop_time")
    @patch("continuous_collector.read_current_price")
    def test_stale_platform_frame_is_rejected_before_network_capture(
            self, price_reader, time_reader, payout_reader):
        captured = datetime(2026, 9, 30, 3, 0, 30, tzinfo=timezone.utc)
        price_reader.return_value = SimpleNamespace(price=641.8674)
        payout_reader.return_value = SimpleNamespace(payout_ratio=0.83)
        time_reader.return_value = SimpleNamespace(
            observed_at=captured - timedelta(seconds=30),
            expiry_at=captured + timedelta(seconds=60))
        coordinator = FakeCoordinator()
        collector = ContinuousCollector(
            coordinator, image_provider=lambda: Image.new("RGB", (1731, 800)),
            max_capture_age_seconds=10)
        attempt = collector.capture_once(captured_at=captured)
        self.assertEqual(attempt.status, "REJECTED")
        self.assertIn("stale", attempt.error)
        self.assertEqual(coordinator.calls, [])

    @patch("continuous_collector.read_majority_opinion",
           side_effect=ValueError("not readable"))
    @patch("continuous_collector.read_payout_ratio")
    @patch("continuous_collector.read_desktop_time")
    @patch("continuous_collector.read_current_price")
    def test_optional_majority_failure_does_not_discard_core_capture(
            self, price_reader, time_reader, payout_reader, _majority_reader):
        captured = datetime(2026, 9, 30, 3, 0, 5, tzinfo=timezone.utc)
        price_reader.return_value = SimpleNamespace(price=641.8674)
        payout_reader.return_value = SimpleNamespace(payout_ratio=0.83)
        time_reader.return_value = SimpleNamespace(
            observed_at=captured, expiry_at=captured + timedelta(seconds=60))
        coordinator = FakeCoordinator()
        collector = ContinuousCollector(
            coordinator, image_provider=lambda: Image.new("RGB", (1731, 800)))
        attempt = collector.capture_once(captured_at=captured)
        self.assertEqual(attempt.status, "ACCEPTED")
        self.assertIsNone(coordinator.calls[0]["majority_up_ratio"])
        self.assertEqual(coordinator.calls[0]["context_quality_reasons"],
                         ("majority_opinion_unavailable",))

    def test_run_uses_fixed_cycle_budget_without_trading_actions(self):
        coordinator = FakeCoordinator()
        collector = ContinuousCollector(
            coordinator, image_provider=lambda: Image.new("RGB", (1731, 800)))
        attempts = []
        stops = iter((False, False, True))
        with patch.object(collector, "capture_once",
                          return_value=SimpleNamespace(status="REJECTED")):
            collector.run(should_stop=lambda: next(stops), interval_seconds=5,
                          on_attempt=attempts.append, sleeper=lambda _seconds: None)
        self.assertEqual(len(attempts), 1)


if __name__ == "__main__":
    unittest.main()
