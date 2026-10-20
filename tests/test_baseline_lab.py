import unittest
from dataclasses import replace

from baseline_lab import (
    evaluate_baseline_development,
    evaluate_baseline_walk_forward,
)
from feature_engine import FeatureRow, chronological_feature_split


class BaselineLabTests(unittest.TestCase):
    def rows(self, *, market_predicts=True):
        result = []
        for index in range(240):
            actual_up = index % 2 == 0
            market_up = actual_up if market_predicts else index % 4 < 2
            features = []
            for lookback in (15, 30, 60):
                features.extend((
                    (f"target_log_return_{lookback}s", -0.01 if actual_up else 0.01),
                    (f"market_equal_log_return_{lookback}s", 0.01 if market_up else -0.01),
                ))
            result.append(FeatureRow(
                anchor_ms=index * 310_000,
                target_observed_at_ms=index * 310_000,
                entry_observed_at_ms=index * 310_000,
                entry_delay_ms=0,
                horizon_seconds=300,
                features=tuple(sorted(features)),
                label="UP" if actual_up else "DOWN",
                target_log_return=0.01 if actual_up else -0.01,
                max_feature_age_ms=0,
                target_exit_offset_ms=0,
            ))
        return tuple(result)

    def test_selects_injected_market_rule_on_validation_only(self):
        split = chronological_feature_split(self.rows())
        report = evaluate_baseline_development(
            split, payout_ratio=0.82, minimum_validation_signals=30)
        self.assertEqual(report["selected_development_candidate"],
                         "MARKET_EQUAL_MOMENTUM_15S")
        selected = next(candidate for candidate in report["candidates"]
                        if candidate["name"] ==
                        report["selected_development_candidate"])
        self.assertEqual(selected["validation"]["accuracy"], 1.0)
        self.assertEqual(report["promotion_status"],
                         "NOT_PROMOTED_VALIDATION_ONLY")
        self.assertEqual(report["holdout"]["status"],
                         "SEALED_NOT_EVALUATED")
        self.assertNotIn("accuracy", report["holdout"])

    def test_null_market_rule_does_not_beat_majority(self):
        split = chronological_feature_split(self.rows(market_predicts=False))
        report = evaluate_baseline_development(
            split, payout_ratio=0.82, minimum_validation_signals=30)
        candidates = {item["name"]: item for item in report["candidates"]}
        self.assertEqual(
            candidates["MARKET_EQUAL_MOMENTUM_15S"]["validation"]["accuracy"],
            0.5)
        self.assertEqual(
            candidates["TRAIN_MAJORITY"]["validation"]["accuracy"], 0.5)
        self.assertEqual(report["selected_development_candidate"],
                         "TRAIN_MAJORITY")

    def test_reports_payout_drawdown_streak_and_no_probabilities(self):
        split = chronological_feature_split(self.rows())
        report = evaluate_baseline_development(
            split, payout_ratio=0.75, minimum_validation_signals=30)
        metrics = report["candidates"][0]["validation"]["payout_metrics"]
        self.assertIn("max_drawdown_units", metrics)
        self.assertIn("max_losing_streak", metrics)
        self.assertAlmostEqual(report["break_even_win_rate"], 1 / 1.75)
        self.assertAlmostEqual(report["bonferroni_alpha_per_hypothesis"],
                               0.05 / 7)
        self.assertEqual(report["probability_metrics"],
                         "NOT_APPLICABLE_DIRECTIONAL_RULES_ONLY")

    def test_minimum_sample_gate_prevents_selection(self):
        split = chronological_feature_split(self.rows())
        report = evaluate_baseline_development(
            split, payout_ratio=0.82, minimum_validation_signals=10_000)
        self.assertIsNone(report["selected_development_candidate"])

    def test_rejects_invalid_payout(self):
        split = chronological_feature_split(self.rows())
        with self.assertRaises(ValueError):
            evaluate_baseline_development(split, payout_ratio=1.1)

    def test_walk_forward_uses_training_only_and_keeps_holdout_sealed(self):
        split = chronological_feature_split(self.rows())
        report = evaluate_baseline_walk_forward(
            split, payout_ratio=0.82, folds=4,
            minimum_train_rows=60, minimum_test_signals=20)
        self.assertEqual(len(report["folds"]), 4)
        self.assertEqual(report["outer_validation_status"],
                         "NOT_EVALUATED_BY_WALK_FORWARD")
        self.assertEqual(report["holdout"]["status"],
                         "SEALED_NOT_EVALUATED")
        self.assertNotIn("accuracy", report["holdout"])
        market = next(item for item in report["stability"]
                      if item["name"] == "MARKET_EQUAL_MOMENTUM_15S")
        self.assertEqual(market["positive_ev_folds"], 4)
        self.assertEqual(market["minimum_fold_accuracy"], 1.0)

    def test_walk_forward_rejects_insufficient_rows(self):
        split = chronological_feature_split(self.rows())
        with self.assertRaises(ValueError):
            evaluate_baseline_walk_forward(
                split, payout_ratio=0.82, folds=10,
                minimum_train_rows=100, minimum_test_signals=20)

    def test_walk_forward_exposes_regime_reversal(self):
        shifted = []
        for index, row in enumerate(self.rows()):
            if index >= 100:
                row = replace(row, features=tuple(
                    (name, -value if name.startswith("market_equal_") else value)
                    for name, value in row.features))
            shifted.append(row)
        split = chronological_feature_split(tuple(shifted))
        report = evaluate_baseline_walk_forward(
            split, payout_ratio=0.82, folds=4,
            minimum_train_rows=60, minimum_test_signals=20)
        market = next(item for item in report["stability"]
                      if item["name"] == "MARKET_EQUAL_MOMENTUM_15S")
        self.assertEqual(market["positive_ev_folds"], 2)
        self.assertEqual(market["minimum_fold_accuracy"], 0.0)
        self.assertEqual(market["maximum_losing_streak"], 21)
        self.assertEqual(market["worst_fold_drawdown_units"], 21.0)


if __name__ == "__main__":
    unittest.main()
