import tempfile
import unittest
from pathlib import Path

from feature_engine import (
    build_feature_dataset,
    chronological_feature_split,
    feature_dataset_summary,
    feature_development_summary,
)
from market_data import DEFAULT_REFERENCE_SYMBOLS
from observation_store import Observation, ObservationStore


class FeatureEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ObservationStore(Path(self.temp.name) / "features.db")

    def tearDown(self):
        self.temp.cleanup()

    def add(self, source, instrument, at_ms, price, quality="ACCEPTABLE",
            **metadata):
        self.store.append(Observation(
            source, instrument, at_ms, price, "test",
            metadata={"quality": quality, **metadata}))

    def add_complete_anchor(self, anchor, *, future_price=101,
                            after_anchor_spike=False):
        for seconds, price in ((-60, 98), (-30, 99), (-15, 99.5)):
            at = anchor + seconds * 1000
            self.add("binomo_screen", "CRYPTO_IDX", at, price,
                     capture_id=f"target-{at}", server_received_at_ms=at)
        self.add("binomo_screen", "CRYPTO_IDX", anchor, 100,
                 capture_id="candidate", server_received_at_ms=anchor + 100)
        self.add("binomo_screen", "CRYPTO_IDX", anchor + 5_000, 100.1,
                 capture_id="entry", server_received_at_ms=anchor + 5_000)
        self.add("binomo_screen", "CRYPTO_IDX", anchor + 35_000, future_price,
                 capture_id="exit", server_received_at_ms=anchor + 35_000)
        for index, symbol in enumerate(DEFAULT_REFERENCE_SYMBOLS):
            base = 100 + index * 10
            for seconds, price in ((-60, base), (-30, base + 1),
                                   (-15, base + 2)):
                at = anchor + seconds * 1000
                self.add("binance_spot_public", symbol, at, price,
                         capture_id=f"history-{at}", received_at_ms=at)
            self.add("binance_spot_public", symbol, anchor - 100, base + 3,
                     capture_id="candidate", received_at_ms=anchor + 200)
            if after_anchor_spike:
                self.add("binance_spot_public", symbol, anchor + 1, base / 2,
                         capture_id="later", received_at_ms=anchor + 300)

    def test_builds_causal_multi_market_features(self):
        anchor = 2_000_000
        self.add_complete_anchor(anchor, after_anchor_spike=True)
        result = build_feature_dataset(
            self.store, horizon_seconds=30, feature_max_age_ms=500)
        row = next(row for row in result.rows
                   if row.target_observed_at_ms == anchor)
        features = row.feature_dict()
        self.assertEqual(row.label, "UP")
        self.assertEqual(len(features), 27)
        self.assertGreater(features["BTCUSDT_log_return_15s"], 0)
        self.assertEqual(features["market_breadth_15s"], 1.0)
        self.assertGreaterEqual(features["market_dispersion_15s"], 0)
        self.assertEqual(row.anchor_ms, anchor + 200)
        self.assertEqual(row.entry_delay_ms, 4_800)
        self.assertEqual(row.max_feature_age_ms, 200)

    def test_future_target_changes_label_not_features(self):
        first = self._single_row(101)
        second = self._single_row(99)
        self.assertEqual(first.features, second.features)
        self.assertEqual(first.label, "UP")
        self.assertEqual(second.label, "DOWN")

    def test_missing_one_reference_rejects_complete_row(self):
        anchor = 3_000_000
        self.add_complete_anchor(anchor)
        with self.store._connect() as connection:
            connection.execute(
                "DELETE FROM observations WHERE instrument = ?",
                ("XRPUSDT",))
        result = build_feature_dataset(
            self.store, horizon_seconds=30, feature_max_age_ms=500)
        self.assertFalse(any(row.anchor_ms == anchor for row in result.rows))
        self.assertGreater(
            dict(result.rejection_reasons)["missing_same_capture_reference"], 0)

    def test_summary_reports_schema_coverage_and_no_probability_claim(self):
        self.add_complete_anchor(4_000_000)
        summary = feature_dataset_summary(build_feature_dataset(
            self.store, horizon_seconds=30, feature_max_age_ms=500))
        self.assertEqual(summary["feature_count"], 27)
        self.assertIsNotNone(summary["schema_fingerprint"])
        self.assertEqual(summary["probability_metrics"],
                         "NOT_APPLICABLE_FEATURE_DATASET_ONLY")

    def test_entry_delay_gate_rejects_unexecutable_label_start(self):
        anchor = 4_500_000
        self.add_complete_anchor(anchor)
        result = build_feature_dataset(
            self.store, horizon_seconds=30, feature_max_age_ms=500,
            max_entry_delay_ms=1_000)
        self.assertFalse(any(row.target_observed_at_ms == anchor
                             for row in result.rows))
        self.assertGreater(
            dict(result.rejection_reasons)["missing_post_decision_entry"], 0)

    def test_split_embargoes_and_hides_holdout_rows(self):
        from feature_engine import FeatureRow
        rows = tuple(FeatureRow(
            anchor_ms=index * 60_000,
            target_observed_at_ms=index * 60_000,
            entry_observed_at_ms=index * 60_000,
            entry_delay_ms=0, horizon_seconds=30,
            features=(("x", float(index)),),
            label="UP" if index % 2 else "DOWN", target_log_return=0.01,
            max_feature_age_ms=0, target_exit_offset_ms=0)
            for index in range(20))
        split = chronological_feature_split(rows)
        summary = feature_development_summary(split)
        self.assertEqual(summary["holdout"]["status"],
                         "SEALED_NOT_EVALUATED")
        self.assertFalse(hasattr(split.holdout, "rows"))
        self.assertLess(split.train[-1].anchor_ms + 30_000,
                        split.validation[0].anchor_ms)

    def test_split_purges_label_that_finishes_after_boundary(self):
        from feature_engine import FeatureRow, label_available_at_ms
        rows = tuple(FeatureRow(
            anchor_ms=index * 60_000,
            target_observed_at_ms=index * 60_000,
            entry_observed_at_ms=index * 60_000 + (90_000 if index == 11 else 0),
            entry_delay_ms=90_000 if index == 11 else 0,
            horizon_seconds=30,
            features=(("x", float(index)),),
            label="UP" if index % 2 else "DOWN", target_log_return=0.01,
            max_feature_age_ms=0, target_exit_offset_ms=0)
            for index in range(20))
        split = chronological_feature_split(rows)
        self.assertNotIn(11 * 60_000,
                         {row.anchor_ms for row in split.train})
        self.assertTrue(all(label_available_at_ms(row) <
                            split.validation[0].anchor_ms
                            for row in split.train))

    def _single_row(self, future_price):
        with tempfile.TemporaryDirectory() as directory:
            store = ObservationStore(Path(directory) / "one.db")
            original = self.store
            self.store = store
            try:
                anchor = 5_000_000
                self.add_complete_anchor(anchor, future_price=future_price)
                result = build_feature_dataset(
                    store, horizon_seconds=30, feature_max_age_ms=500)
                return next(row for row in result.rows
                            if row.target_observed_at_ms == anchor)
            finally:
                self.store = original


if __name__ == "__main__":
    unittest.main()
