import tempfile
import unittest
from pathlib import Path

from observation_store import Observation, ObservationStore
from reference_basket import CANDIDATE_BASKETS
from relationship_lab import (
    build_lead_lag_samples,
    chronological_development_split,
    development_report,
    directional_metrics,
    preregistered_v1_registry,
)


class RelationshipLabTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ObservationStore(Path(self.temp.name) / "lab.db")

    def tearDown(self):
        self.temp.cleanup()

    def add(self, source, instrument, at_ms, price, quality="ACCEPTABLE"):
        self.store.append(Observation(
            source, instrument, at_ms, price, "test",
            metadata={"quality": quality}))

    def test_reference_feature_never_uses_tick_after_anchor(self):
        anchor = 1_000_000
        self.add("binomo_screen", "CRYPTO_IDX", anchor, 100)
        self.add("binomo_screen", "CRYPTO_IDX", anchor + 30_000, 101)
        self.add("binance_spot_public", "BTCUSDT", anchor - 5_000, 100)
        self.add("binance_spot_public", "BTCUSDT", anchor - 100, 101)
        self.add("binance_spot_public", "BTCUSDT", anchor + 1, 50)
        samples = build_lead_lag_samples(
            self.store, basket=CANDIDATE_BASKETS["BTC_ONLY_V1"],
            lookback_seconds=5, horizon_seconds=30, feature_max_age_ms=100)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].predicted_direction, "UP")
        self.assertTrue(samples[0].correct)

    def test_low_quality_evidence_is_rejected(self):
        anchor = 2_000_000
        self.add("binomo_screen", "CRYPTO_IDX", anchor, 100)
        self.add("binomo_screen", "CRYPTO_IDX", anchor + 30_000, 101)
        self.add("binance_spot_public", "BTCUSDT", anchor - 5_000, 100)
        self.add("binance_spot_public", "BTCUSDT", anchor, 101, quality="LOW")
        samples = build_lead_lag_samples(
            self.store, basket=CANDIDATE_BASKETS["BTC_ONLY_V1"],
            lookback_seconds=5, horizon_seconds=30)
        self.assertEqual(samples, ())

    def test_split_embargoes_boundaries_and_seals_holdout(self):
        samples = tuple(self._sample(index * 60_000) for index in range(20))
        split = chronological_development_split(samples, embargo_seconds=30)
        self.assertLess(split.train[-1].anchor_ms + 30_000,
                        split.validation[0].anchor_ms)
        self.assertLess(split.validation[-1].anchor_ms + 30_000,
                        split.holdout.start_ms)
        report = development_report(split)
        self.assertEqual(report["holdout"]["status"], "SEALED_NOT_EVALUATED")
        self.assertNotIn("accuracy", report["holdout"])
        self.assertEqual(report["probability_metrics"],
                         "NOT_APPLICABLE_NO_PROBABILITY_MODEL")
        self.assertFalse(hasattr(split.holdout, "_samples"))

    def test_metrics_include_baseline_uncertainty_and_overlap_warning(self):
        samples = tuple(self._sample(index * 5_000,
                                     predicted="UP" if index < 3 else "DOWN",
                                     actual="UP" if index != 3 else "DOWN")
                        for index in range(4))
        metrics = directional_metrics(samples)
        self.assertEqual(metrics["confusion_matrix"], {
            "UP_UP": 3, "UP_DOWN": 0, "DOWN_UP": 0, "DOWN_DOWN": 1})
        self.assertEqual(metrics["accuracy"], 1.0)
        self.assertEqual(metrics["baseline_accuracy"], 0.75)
        self.assertIsNotNone(metrics["accuracy_wilson_95"])
        self.assertTrue(metrics["overlapping_outcomes"])

    def test_registry_accounts_for_multiple_hypotheses(self):
        registry = preregistered_v1_registry(3)
        self.assertEqual(registry["hypothesis_count"], 45)
        self.assertAlmostEqual(registry["bonferroni_alpha_per_hypothesis"],
                               0.05 / 45)
        self.assertEqual(registry["selection_partition"], "VALIDATION_ONLY")

    def test_holdout_fingerprint_includes_observed_values(self):
        first = tuple(self._sample(index * 60_000) for index in range(20))
        second = list(first)
        second[-1] = self._sample(second[-1].anchor_ms,
                                  predicted="DOWN", actual="UP")
        left = chronological_development_split(first).holdout.fingerprint
        right = chronological_development_split(tuple(second)).holdout.fingerprint
        self.assertNotEqual(left, right)

    @staticmethod
    def _sample(anchor, predicted="UP", actual="UP"):
        from relationship_lab import LeadLagSample
        return LeadLagSample(
            anchor, 5, 30, "BTC_ONLY:v1", "a" * 64, 0.01, 0.01,
            predicted, actual, predicted == actual, 0, 0)


if __name__ == "__main__":
    unittest.main()
