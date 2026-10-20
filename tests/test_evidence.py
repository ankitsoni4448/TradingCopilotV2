import tempfile
import unittest
from pathlib import Path

from evaluation import fixed_payout_metrics
from observation_store import (Observation, ObservationStore,
                               alignment_metrics, screenshot_digest)


class ObservationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ObservationStore(Path(self.temp.name) / "evidence.db")

    def tearDown(self):
        self.temp.cleanup()

    def add(self, source, instrument, at_ms, price, method="test"):
        return self.store.append(Observation(source, instrument, at_ms, price, method))

    def test_duplicate_timestamp_is_rejected_not_overwritten(self):
        self.add("binomo_screen", "CRYPTO_IDX", 1_000_000, 100)
        with self.assertRaises(ValueError):
            self.add("binomo_screen", "CRYPTO_IDX", 1_000_000, 999)

    def test_alignment_uses_exact_horizon_and_reports_offsets(self):
        anchor = 2_000_000
        self.add("binomo_screen", "CRYPTO_IDX", anchor, 100)
        self.add("binance_spot_public", "ETHUSDT", anchor + 200, 200)
        self.add("binomo_screen", "CRYPTO_IDX", anchor + 60_000 + 500, 101)
        self.add("binance_spot_public", "ETHUSDT", anchor + 60_000 - 300, 202)
        samples = self.store.alignment_samples(
            target_source="binomo_screen", target_instrument="CRYPTO_IDX",
            reference_source="binance_spot_public", reference_instrument="ETHUSDT",
            horizon_seconds=60, tolerance_ms=1000,
            start_ms=anchor, end_ms=anchor)
        self.assertEqual(len(samples), 1)
        sample = samples[0]
        self.assertTrue(sample.agrees)
        self.assertEqual(sample.entry_reference_offset_ms, 200)
        self.assertEqual(sample.exit_target_offset_ms, 500)
        self.assertEqual(sample.exit_reference_offset_ms, -300)
        metrics = alignment_metrics(samples, total_anchors=2)
        self.assertEqual(metrics["agreement_rate"], 1.0)
        self.assertEqual(metrics["coverage"], 0.5)
        self.assertEqual(metrics["max_absolute_timing_offset_ms"], 500)

    def test_missing_exit_produces_no_sample(self):
        anchor = 3_000_000
        self.add("binomo_screen", "CRYPTO_IDX", anchor, 100)
        self.add("binance_spot_public", "ETHUSDT", anchor, 200)
        self.assertEqual(self.store.alignment_samples(
            target_source="binomo_screen", target_instrument="CRYPTO_IDX",
            reference_source="binance_spot_public", reference_instrument="ETHUSDT",
            horizon_seconds=180), [])

    def test_screenshot_digest_is_reproducible(self):
        self.assertEqual(screenshot_digest(b"same"), screenshot_digest(b"same"))
        self.assertNotEqual(screenshot_digest(b"same"), screenshot_digest(b"different"))


class RiskMetricTests(unittest.TestCase):
    def test_fixed_payout_math_and_losing_streak(self):
        metrics = fixed_payout_metrics([True, False, False, True], payout_ratio=0.80)
        self.assertAlmostEqual(metrics["break_even_win_rate"], 1 / 1.8)
        self.assertAlmostEqual(metrics["expected_value_per_unit_stake"], -0.10)
        self.assertAlmostEqual(metrics["net_units"], -0.40)
        self.assertEqual(metrics["max_losing_streak"], 2)


if __name__ == "__main__":
    unittest.main()

