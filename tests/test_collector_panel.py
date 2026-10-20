import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from collector_cli import CollectorConfig
from collector_panel import CollectorSession
from windows_capture import CaptureRegion


class CollectorPanelTests(unittest.TestCase):
    def setUp(self):
        self.config = CollectorConfig(
            Path("evidence.db"), CaptureRegion(0, 0, 1280, 720))

    def test_start_stop_restart_and_worker_cleanup(self):
        ready = [threading.Event(), threading.Event()]
        calls = []

        def fake_run(_config, *, stop_event, output):
            index = len(calls)
            calls.append(index)
            output(json.dumps({"calibration": {"status": "CALIBRATED"}}))
            ready[index].set()
            self.assertTrue(stop_event.wait(2))
            output(json.dumps({"session_summary": {
                "attempts_in_database": 0, "accepted": 0, "rejected": 0}}))

        with patch("collector_panel.run_collection", side_effect=fake_run):
            session = CollectorSession(self.config)
            self.assertTrue(session.start())
            self.assertFalse(session.start())
            self.assertTrue(ready[0].wait(1))
            session.stop()
            session.join(2)
            self.assertFalse(session.is_running)

            self.assertTrue(session.start())
            self.assertTrue(ready[1].wait(1))
            session.stop()
            session.join(2)

        self.assertEqual(len(calls), 2)
        self.assertFalse(session.is_running)

    def test_calibration_failure_is_reported_and_worker_exits(self):
        with patch("collector_panel.run_collection",
                   side_effect=ValueError("price_tag_not_found")):
            session = CollectorSession(self.config)
            self.assertTrue(session.start())
            session.join(2)

        messages = []
        while not session.events.empty():
            messages.append(session.events.get_nowait())
        self.assertIn(("failure", "PRICE_TAG"), messages)
        self.assertEqual(messages[-1], ("finished", ""))
        self.assertFalse(session.is_running)


if __name__ == "__main__":
    unittest.main()