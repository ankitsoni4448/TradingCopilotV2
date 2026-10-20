import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from collection_audit import CollectionAuditStore, safe_error_code


class CollectionAuditTests(unittest.TestCase):
    def test_health_counts_and_sanitizes_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CollectionAuditStore(Path(directory) / "audit.db")
            store.record(SimpleNamespace(
                status="ACCEPTED", result=SimpleNamespace(capture_id="c1"),
                error=None, attempted_at_ms=1000, duration_ms=80))
            store.record(SimpleNamespace(
                status="REJECTED", result=None,
                error="private/path platform_clock missing",
                attempted_at_ms=2000, duration_ms=12))
            health = store.summary()
            self.assertEqual((health.attempts, health.accepted, health.rejected),
                             (2, 1, 1))
            self.assertEqual(health.rejection_reasons, {"PLATFORM_CLOCK": 1})

    def test_unknown_error_is_not_persisted_verbatim(self):
        self.assertEqual(safe_error_code("secret account text"), "UNCLASSIFIED")
