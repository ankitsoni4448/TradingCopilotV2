import tempfile
import unittest
from pathlib import Path

from market_data import DEFAULT_REFERENCE_SYMBOLS
from observation_store import Observation, ObservationStore
from readiness_audit import ReadinessPolicy, audit_research_readiness


class ReadinessAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ObservationStore(Path(self.temp.name) / "readiness.db")

    def tearDown(self):
        self.temp.cleanup()

    def populate(self, count=721, *, gap_after=None, quality="ACCEPTABLE",
                 payout_ratio=None, payout_change_after=None):
        base = 2_000_000
        extra = 0
        for index in range(count):
            if gap_after is not None and index == gap_after:
                extra += 120_000
            target_at = base + index * 5_000 + extra
            capture_id = f"capture-{index}"
            common = {"capture_id": capture_id, "quality": quality,
                      "quality_reasons": []}
            observed_payout = payout_ratio
            if payout_change_after is not None and index >= payout_change_after:
                observed_payout = 0.80
            batch = [Observation(
                "binomo_screen", "CRYPTO_IDX", target_at,
                100 + index * 0.001, "desktop_ocr",
                metadata={**common, "server_received_at_ms": target_at + 100,
                          "payout_ratio": observed_payout})]
            for symbol_index, symbol in enumerate(DEFAULT_REFERENCE_SYMBOLS):
                batch.append(Observation(
                    "binance_spot_public", symbol, target_at + 50,
                    100 + symbol_index * 10 + index * 0.001,
                    "public_rest_ticker_batch_midpoint",
                    metadata={**common, "received_at_ms": target_at + 200,
                              "latency_ms": 150,
                              "target_offset_ms": 50}))
            self.store.append_many(batch)

    @staticmethod
    def policy(**kwargs):
        values = {"minimum_continuous_minutes": 20,
                  "minimum_feature_rows": 100,
                  "minimum_train_rows": 60,
                  "minimum_validation_rows": 20}
        values.update(kwargs)
        return ReadinessPolicy(**values)

    def test_complete_continuous_session_passes_all_horizons(self):
        self.populate()
        report = audit_research_readiness(
            self.store, payout_ratio=0.82, policy=self.policy())
        self.assertEqual(report["status"], "READY_FOR_BASELINE_RESEARCH")
        self.assertEqual(report["capture_completeness"], 1.0)
        self.assertEqual(report["session"]["median_gap_seconds"], 5.0)
        self.assertTrue(all(item["ready_for_baseline_research"]
                            for item in report["horizons"]))
        self.assertFalse(report["model_training_permitted"])

    def test_missing_payout_fails_closed(self):
        self.populate()
        report = audit_research_readiness(
            self.store, payout_ratio=None, policy=self.policy())
        self.assertEqual(report["status"], "NOT_READY")
        self.assertTrue(all("missing_session_payout" in item["blocking_reasons"]
                            for item in report["horizons"]))

    def test_consistent_screen_payout_is_accepted_without_manual_default(self):
        self.populate(payout_ratio=0.83)
        report = audit_research_readiness(
            self.store, payout_ratio=None, policy=self.policy())
        self.assertEqual(report["status"], "READY_FOR_BASELINE_RESEARCH")
        self.assertEqual(report["payout_ratio"], 0.83)
        self.assertEqual(report["payout_source"],
                         "SCREEN_OCR_CONSISTENT_SESSION_VALUE")

    def test_changed_or_mismatched_screen_payout_fails_closed(self):
        self.populate(payout_ratio=0.83, payout_change_after=360)
        report = audit_research_readiness(
            self.store, payout_ratio=0.83, policy=self.policy())
        self.assertEqual(report["status"], "NOT_READY")
        self.assertTrue(all("inconsistent_screen_payout" in item["blocking_reasons"]
                            for item in report["horizons"]))

    def test_operator_payout_must_match_screen_ocr(self):
        self.populate(payout_ratio=0.83)
        report = audit_research_readiness(
            self.store, payout_ratio=0.82, policy=self.policy())
        self.assertEqual(report["status"], "NOT_READY")
        self.assertTrue(all("operator_screen_payout_mismatch" in
                            item["blocking_reasons"]
                            for item in report["horizons"]))

    def test_fragmented_session_fails_continuity_gate(self):
        self.populate(gap_after=360)
        report = audit_research_readiness(
            self.store, payout_ratio=0.82,
            policy=self.policy(minimum_continuous_minutes=40))
        self.assertEqual(report["status"], "NOT_READY")
        self.assertTrue(all("insufficient_continuous_session" in
                            item["blocking_reasons"]
                            for item in report["horizons"]))

    def test_empty_database_reports_reasons_without_creating_audit_table(self):
        report = audit_research_readiness(
            self.store, payout_ratio=0.82, policy=self.policy())
        self.assertEqual(report["status"], "NOT_READY")
        self.assertIsNone(report["collection_attempts"])
        with self.store._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='collection_attempts'").fetchone()
        self.assertIsNone(exists)


if __name__ == "__main__":
    unittest.main()
