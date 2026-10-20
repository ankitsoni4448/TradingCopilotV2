import tempfile
import unittest
import math
from pathlib import Path

from observer_ml import (
    build_validated_dataset,
    chronological_partitions,
    evaluate_baseline,
    export_dataset,
)
from observer_store import ObserverStore
from screen_observer import FrameRecord


class ObserverMlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "observer.db"
        self.store = ObserverStore(self.database)
        self.session_id = self.store.new_session_id()
        self.store.start_session(self.session_id, 5)

    def tearDown(self):
        self.temp.cleanup()

    def add_series(self, count=520):
        for index in range(count):
            price = 100 + index * 0.02 + 2 * math.sin(index * 0.25)
            self.store.append_frame(self.session_id, FrameRecord(
                1_700_000_000_000 + index * 5000,
                "OTHER_MARKET", 0.95, "SUCCESS", "chrome", "BTCUSDT",
                price, "OCR_LABEL_PROXIMITY+TEMPORAL_CONFIRMATION", 5, None,
                {"quality_flags": ["TEMPORALLY_CONSISTENT"]}))

    def test_sparse_data_is_not_ready_and_does_not_export(self):
        report = evaluate_baseline(self.database)
        self.assertEqual(report["status"], "NOT_READY")
        self.assertEqual(report["decision"], "NO_TRADE")
        result = export_dataset(self.database, Path(self.temp.name) / "data.csv")
        self.assertFalse(result["output_written"])

    def test_validated_data_exports_chronological_splits_without_promotion(self):
        self.add_series()
        rows = build_validated_dataset(self.database)
        split = chronological_partitions(rows, minimum_each=30)
        self.assertEqual(split["status"], "READY", split["reason"])
        self.assertLess(max(row.label_end_ms for row in split["train"]),
                        min(row.anchor_ms for row in split["validation"]))
        self.assertLess(max(row.label_end_ms for row in split["validation"]),
                        min(row.anchor_ms for row in split["test"]))

        path = Path(self.temp.name) / "dataset.csv"
        exported = export_dataset(self.database, path, minimum_each=30)
        self.assertTrue(exported["output_written"])
        report = evaluate_baseline(
            self.database, payout_ratio=0.83, minimum_each=30)
        self.assertEqual(report["status"], "EVALUATED_NOT_APPROVED")
        self.assertEqual(report["decision"], "NO_TRADE")
        self.assertEqual(report["promotion_status"],
                         "NOT_PROMOTED_TEST_IS_REPORT_ONLY")
        self.assertAlmostEqual(report["break_even_win_rate"], 1 / 1.83)
        self.assertIn("calibration", report)


if __name__ == "__main__":
    unittest.main()
