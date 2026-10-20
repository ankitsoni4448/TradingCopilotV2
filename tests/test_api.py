import unittest

from fastapi.testclient import TestClient

from api import create_app
from capture_service import CaptureResult


class FakeCoordinator:
    def __init__(self):
        self.last = None

    def capture(self, **kwargs):
        self.last = kwargs
        return CaptureResult("test-id", 1, 2, kwargs["target_observed_at_ms"],
                             kwargs["target_observed_at_ms"] + 20, 20, 0, 40,
                             "ACCEPTABLE", ())


class CaptureApiTests(unittest.TestCase):
    def test_health_discloses_demo_manual_mode(self):
        client = TestClient(create_app(FakeCoordinator()))
        payload = client.get("/health").json()
        self.assertEqual(payload["mode"], "demo")
        self.assertEqual(payload["execution"], "manual")
        self.assertEqual(payload["screen_storage"], "disabled")

    def test_capture_accepts_fields_but_no_raw_screenshot(self):
        coordinator = FakeCoordinator()
        client = TestClient(create_app(coordinator))
        response = client.post("/observations/capture", json={
            "target_instrument": "CRYPTO_IDX", "target_price": 123.45,
            "target_observed_at_ms": 1_000_000,
            "reference_symbol": "ETHUSDT",
            "screenshot_sha256": "a" * 64,
            "payout_ratio": 0.83, "chart_display_interval": "1h",
            "expiry_at_ms": 1_060_000, "amount": 100,
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["quality"], "ACCEPTABLE")
        self.assertNotIn("screenshot", response.json())
        self.assertEqual(coordinator.last["screenshot_sha256"], "a" * 64)
        self.assertEqual(coordinator.last["payout_ratio"], 0.83)
        self.assertEqual(coordinator.last["chart_display_interval"], "1h")

    def test_capture_rejects_invalid_price_and_symbol(self):
        client = TestClient(create_app(FakeCoordinator()))
        response = client.post("/observations/capture", json={
            "target_instrument": "CRYPTO_IDX", "target_price": -1,
            "target_observed_at_ms": 1_000_000,
            "reference_symbol": "UNKNOWN",
        })
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
