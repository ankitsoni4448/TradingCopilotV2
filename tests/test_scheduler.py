import tempfile
import unittest
from pathlib import Path

from outcome_scheduler import OutcomeScheduler


class OutcomeSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.scheduler = OutcomeScheduler(Path(self.temp.name) / "jobs.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_horizons_are_scheduled_separately(self):
        self.scheduler.schedule("entry-1", 1_000_000)
        rows = self.scheduler.rows()
        self.assertEqual([row["horizon_seconds"] for row in rows], [60, 180, 300])
        self.assertEqual([row["due_at_ms"] for row in rows],
                         [1_060_000, 1_180_000, 1_300_000])

    def test_jobs_cannot_be_claimed_early_or_twice(self):
        self.scheduler.schedule("entry-2", 2_000_000, horizons=(60,))
        self.assertEqual(self.scheduler.claim_due(2_059_999), [])
        claimed = self.scheduler.claim_due(2_060_000)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(self.scheduler.claim_due(2_060_001), [])

    def test_failed_capture_is_recorded_not_fabricated(self):
        self.scheduler.schedule("entry-3", 3_000_000, horizons=(60,))
        job = self.scheduler.claim_due(3_060_000)[0]
        self.scheduler.fail(job.id, "screen price unavailable", 3_061_000)
        row = self.scheduler.rows()[0]
        self.assertEqual(row["status"], "FAILED")
        self.assertIsNone(row["outcome_capture_id"])
        self.assertEqual(row["error"], "screen price unavailable")

    def test_duplicate_schedule_is_rejected(self):
        self.scheduler.schedule("entry-4", 4_000_000, horizons=(180,))
        with self.assertRaises(ValueError):
            self.scheduler.schedule("entry-4", 4_000_000, horizons=(180,))


if __name__ == "__main__":
    unittest.main()

