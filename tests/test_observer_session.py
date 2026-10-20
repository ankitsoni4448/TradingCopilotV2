import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path

from PIL import Image

from observer_session import DesktopObserverSession
from screen_observer import FrameRecord
from observer_store import ObserverStore
from visual_perception import VisualPerceptionResult


class ObserverSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "observer.db"

    def tearDown(self):
        self.temp.cleanup()

    def next_frame(self, session):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                kind, value = session.events.get(timeout=0.1)
            except queue.Empty:
                continue
            if kind == "frame":
                return value
        self.fail("observer did not produce a frame")

    def test_start_pause_resume_stop_and_repeated_session_cleanup(self):
        captures = []

        def capture():
            captures.append(time.monotonic())
            return Image.new("RGB", (1280, 720))

        def analyzer(_image, *, captured_at_ms, **_kwargs):
            return FrameRecord(captured_at_ms, "NON_MARKET", 0.9, "SUCCESS",
                               "notepad", None, None, None, 10, None, {})

        session = DesktopObserverSession(
            self.database, capture=capture, foreground=lambda: "notepad",
            visible_apps=lambda: ("notepad",), analyzer=analyzer)
        self.assertTrue(session.start(interval_seconds=2))
        self.assertFalse(session.start(interval_seconds=2))
        self.next_frame(session)
        session.pause()
        self.assertTrue(session.is_paused)
        before = len(captures)
        time.sleep(0.25)
        self.assertEqual(len(captures), before)
        session.resume()
        self.assertFalse(session.is_paused)
        self.next_frame(session)
        session.stop()
        session.join(2)
        self.assertFalse(session.is_running)
        first_session = session.session_id
        self.assertEqual(ObserverStore(self.database).counts(first_session)["total"], 2)

        while not session.events.empty():
            session.events.get_nowait()
        self.assertTrue(session.start(interval_seconds=2))
        self.next_frame(session)
        second_session = session.session_id
        session.stop()
        session.join(2)
        self.assertFalse(session.is_running)
        self.assertNotEqual(first_session, second_session)
        self.assertTrue(ObserverStore(self.database).session_report_path(second_session))

    def test_capture_failure_is_persisted_as_unknown(self):
        def fail_capture():
            raise OSError("screen capture unavailable")

        session = DesktopObserverSession(self.database, capture=fail_capture)
        session.start(interval_seconds=2)
        result = self.next_frame(session)
        self.assertEqual(result["record"].classification, "UNKNOWN")
        self.assertEqual(result["record"].extraction_status, "FAILED")
        session.stop()
        session.join(2)
        self.assertFalse(session.is_running)

    def test_backend_exception_falls_back_and_records_provenance(self):
        class BrokenBackend:
            status = "BROKEN"
            model_name = "fake"

            def analyze(self, _image):
                raise RuntimeError("backend failed")

        def analyzer(_image, *, captured_at_ms, **_kwargs):
            return FrameRecord(captured_at_ms, "UNKNOWN", 0, "EMPTY", None,
                               None, None, None, 1, "no_text", {})

        session = DesktopObserverSession(
            self.database, capture=lambda: Image.new("RGB", (80, 60)),
            analyzer=analyzer, visual_backend=BrokenBackend())
        session.start(interval_seconds=2)
        frame = self.next_frame(session)
        self.assertEqual(frame["record"].extracted_fields["visual_model"]["status"],
                         "BACKEND_ERROR_LOCAL_OCR_FALLBACK")
        session.stop()
        session.join(5)
        self.assertFalse(session.is_running)
        stored = ObserverStore(self.database).session_frames(session.session_id)[0]
        self.assertIsNone(stored["price"])
        self.assertIn("BACKEND_ERROR_LOCAL_OCR_FALLBACK",
                  stored["extracted_fields_json"])

    def test_slow_visual_inference_drops_old_frames_without_blocking_capture(self):
        captures = []
        dropped = threading.Event()

        def capture():
            captures.append(time.monotonic())
            return Image.new("RGB", (80, 60))

        class SlowBackend:
            status = "SLOW_TEST_BACKEND"
            model_name = "slow-test"

            def analyze(self, _image):
                time.sleep(4.5)
                return VisualPerceptionResult(
                    "UNAVAILABLE_LOCAL_RUNTIME", "slow-test", "slow-test", 4500)

        def analyzer(_image, *, captured_at_ms, **_kwargs):
            return FrameRecord(captured_at_ms, "UNKNOWN", 0, "EMPTY", None,
                               None, None, None, 1, "no_text", {})

        session = DesktopObserverSession(
            self.database, capture=capture, analyzer=analyzer,
            visual_backend=SlowBackend())
        session.start(interval_seconds=2)
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline and session._dropped_frames == 0:
            time.sleep(0.05)
        dropped.set()
        self.assertTrue(dropped.is_set())
        self.assertGreaterEqual(len(captures), 3)
        self.assertGreater(session._dropped_frames, 0)
        session.stop()
        session.join(10)
        self.assertFalse(session.is_running)


if __name__ == "__main__":
    unittest.main()
