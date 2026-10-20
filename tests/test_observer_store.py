import tempfile
import unittest
from pathlib import Path

from observer_store import ObserverStore
from screen_observer import FrameRecord


class ObserverStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "research.db"
        self.store = ObserverStore(self.database)
        self.session_id = self.store.new_session_id()
        self.store.start_session(self.session_id, 5)

    def tearDown(self):
        self.temp.cleanup()

    def record(self, at, price=None, classification="OTHER_MARKET",
               status="SUCCESS"):
        return FrameRecord(at, classification, 0.8, status, "chrome", "BTCUSDT",
                           price, "OCR_LABEL_PROXIMITY" if price else None,
                           42, None if price else "price_context_unverified",
                           {"asset": "BTCUSDT"})

    def test_structured_frames_and_reports_persist_without_transcripts(self):
        self.store.append_frame(self.session_id, self.record(10000, 100.0))
        self.store.append_frame(self.session_id, self.record(15000, 101.0))
        self.store.append_frame(self.session_id, self.record(
            20000, classification="NON_MARKET"))
        counts = self.store.counts(self.session_id)
        self.assertEqual(counts["total"], 3)
        self.assertEqual(counts["market"], 2)
        self.assertEqual(counts["non_market"], 1)

        with self.store._connect() as connection:
            columns = {row["name"] for row in connection.execute(
                "PRAGMA table_info(screen_observations)")}
            self.assertNotIn("ocr_transcript", columns)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM screen_observations WHERE price IS NOT NULL"
                ).fetchone()[0], 2)

        path = self.store.generate_report(self.session_id, finish=True)
        self.assertTrue(path.is_file())
        report = path.with_suffix(".json").read_text(encoding="utf-8")
        self.assertIn('"valid_price_observations": 2', report)
        self.assertIn('"absolute_change": 1.0', report)
        self.assertIn('"ocr_failures": 1', report)
        self.assertIn('"screenshots_saved": false', report)

    def test_demo_trade_record_edit_and_observation_association(self):
        self.store.append_frame(self.session_id, self.record(100000, 100.0))
        trade_id = self.store.add_trade(
            self.session_id, asset="BTCUSDT", direction="UP",
            entry_time_ms=100000, duration_seconds=60, stake=2.0,
            entry_price=100.0, outcome="OPEN")
        trade = self.store.trades(self.session_id)[0]
        self.assertEqual(trade["trade_id"], trade_id)
        self.assertEqual(trade["nearby_observation_ids"], [1])

        self.store.update_trade(
            trade_id, asset="BTCUSDT", direction="DOWN",
            entry_time_ms=100000, duration_seconds=60, stake=2.0,
            entry_price=100.0, outcome="LOSS")
        self.assertEqual(self.store.trades(self.session_id)[0]["outcome"], "LOSS")

    def test_empty_session_can_still_generate_report(self):
        path = self.store.generate_report(self.session_id, finish=True)
        self.assertTrue(path.is_file())
        self.assertIn('"total_frames_captured": 0',
                  path.with_suffix(".json").read_text(encoding="utf-8"))

    def test_report_calculates_demo_outcome_only_from_confirmed_prices(self):
        self.store.append_frame(self.session_id, self.record(100000, 100.0))
        self.store.append_frame(self.session_id, self.record(160000, 101.0))
        self.store.add_trade(
            self.session_id, asset="BTCUSDT", direction="UP",
            entry_time_ms=100000, duration_seconds=60, stake=1.0,
            entry_price=100.0, outcome="OPEN")
        path = self.store.generate_report(self.session_id, finish=True)
        report = __import__("json").loads(
            path.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(report["manual_demo_trades"][0]["calculated_outcome"], "WIN")


if __name__ == "__main__":
    unittest.main()
