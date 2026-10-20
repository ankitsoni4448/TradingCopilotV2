import tempfile
import unittest
from pathlib import Path

from capture_service import CaptureCoordinator
from market_data import (
    DEFAULT_REFERENCE_SYMBOLS,
    MarketDataError,
    ReferenceTick,
    fetch_reference_tick,
    fetch_reference_ticks,
)
from observation_store import Observation, ObservationStore


class ReferenceTickTests(unittest.TestCase):
    def test_midpoint_and_latency_are_retained(self):
        times = iter([1_000_000, 1_000_080])
        tick = fetch_reference_tick(
            getter=lambda _url, _timeout: {"symbol": "ETHUSDT", "price": "2500.25"},
            clock_ms=lambda: next(times))
        self.assertEqual(tick.observed_at_ms, 1_000_040)
        self.assertEqual(tick.latency_ms, 80)
        self.assertEqual(tick.price, 2500.25)

    def test_wrong_symbol_is_rejected(self):
        times = iter([1_000_000, 1_000_010])
        with self.assertRaises(MarketDataError):
            fetch_reference_tick(
                getter=lambda _url, _timeout: {"symbol": "BTCUSDT", "price": "2500"},
                clock_ms=lambda: next(times))

    def test_multi_symbol_request_shares_one_midpoint(self):
        times = iter([1_000_000, 1_000_100])
        seen = []
        ticks = fetch_reference_ticks(
            ("BTCUSDT", "ETHUSDT"),
            getter=lambda url, _timeout: seen.append(url) or [
                {"symbol": "BTCUSDT", "price": "60000"},
                {"symbol": "ETHUSDT", "price": "2500"},
            ], clock_ms=lambda: next(times))
        self.assertEqual(len(seen), 1)
        self.assertEqual([tick.symbol for tick in ticks], ["BTCUSDT", "ETHUSDT"])
        self.assertEqual({tick.observed_at_ms for tick in ticks}, {1_000_050})
        self.assertEqual({tick.latency_ms for tick in ticks}, {100})

    def test_multi_symbol_request_rejects_incomplete_payload(self):
        times = iter([1_000_000, 1_000_010])
        with self.assertRaisesRegex(MarketDataError, "missing"):
            fetch_reference_ticks(
                ("BTCUSDT", "ETHUSDT"),
                getter=lambda _url, _timeout: [
                    {"symbol": "BTCUSDT", "price": "60000"}],
                clock_ms=lambda: next(times))

    def test_major_five_share_one_request_and_midpoint(self):
        times = iter([2_000_000, 2_000_120])
        seen = []
        prices = {"BTCUSDT": "60000", "ETHUSDT": "2500",
                  "BNBUSDT": "600", "SOLUSDT": "150",
                  "XRPUSDT": "0.60"}
        ticks = fetch_reference_ticks(
            DEFAULT_REFERENCE_SYMBOLS,
            getter=lambda url, _timeout: seen.append(url) or [
                {"symbol": symbol, "price": prices[symbol]}
                for symbol in reversed(DEFAULT_REFERENCE_SYMBOLS)],
            clock_ms=lambda: next(times))
        self.assertEqual(len(seen), 1)
        self.assertEqual(tuple(tick.symbol for tick in ticks),
                         DEFAULT_REFERENCE_SYMBOLS)
        self.assertEqual({tick.observed_at_ms for tick in ticks}, {2_000_060})
        self.assertEqual({tick.latency_ms for tick in ticks}, {120})


class CaptureCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ObservationStore(Path(self.temp.name) / "capture.db")

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def tick(symbol="ETHUSDT", latency=80, at=1_000_040):
        start = at - latency // 2
        return ReferenceTick("binance_spot_public", symbol, 2500.25, at,
                             start, start + latency)

    def test_capture_is_atomic_and_reports_quality(self):
        coordinator = CaptureCoordinator(
            self.store, reference_fetcher=lambda **_kwargs: self.tick(),
            clock_ms=lambda: 1_000_000, id_factory=lambda: "capture-1")
        result = coordinator.capture(target_instrument="CRYPTO_IDX", target_price=123.45,
                                     target_observed_at_ms=1_000_010)
        self.assertEqual(self.store.count(), 2)
        self.assertEqual(result.quality, "ACCEPTABLE")
        self.assertEqual(result.target_reference_offset_ms, 30)
        self.assertEqual(result.reference_latency_ms, 80)

    def test_high_latency_is_saved_but_marked_low_quality(self):
        coordinator = CaptureCoordinator(
            self.store, reference_fetcher=lambda **_kwargs: self.tick(latency=3000),
            clock_ms=lambda: 1_000_000, id_factory=lambda: "capture-2")
        result = coordinator.capture(target_instrument="CRYPTO_IDX", target_price=123.45,
                                     target_observed_at_ms=1_000_010)
        self.assertEqual(result.quality, "LOW")
        self.assertIn("reference_network_latency", result.quality_reasons)

    def test_duplicate_reference_rolls_back_target_row(self):
        tick = self.tick()
        self.store.append(Observation(tick.source, tick.symbol, tick.observed_at_ms,
                                      tick.price, "preexisting"))
        coordinator = CaptureCoordinator(
            self.store, reference_fetcher=lambda **_kwargs: tick,
            clock_ms=lambda: 1_000_000, id_factory=lambda: "capture-3")
        with self.assertRaises(ValueError):
            coordinator.capture(target_instrument="CRYPTO_IDX", target_price=123.45,
                                target_observed_at_ms=1_000_010)
        self.assertEqual(self.store.count(), 1)

    def test_chart_display_intervals_are_not_treated_as_expiry_horizons(self):
        for index, interval in enumerate(("1h", "3h", "1d"), start=1):
            observed_at = 1_000_000 + index * 10
            coordinator = CaptureCoordinator(
                self.store,
                reference_fetcher=lambda **_kwargs: self.tick(at=observed_at + 30),
                clock_ms=lambda: 1_000_000,
                id_factory=lambda: f"capture-4-{index}")
            result = coordinator.capture(
                target_instrument="CRYPTO_IDX", target_price=123.45,
                target_observed_at_ms=observed_at, payout_ratio=0.83,
                chart_display_interval=interval,
                expiry_at_ms=observed_at + 180_000, amount=100)
            self.assertEqual(result.quality, "ACCEPTABLE")
            with self.store._connect() as connection:
                row = connection.execute(
                    "SELECT metadata_json FROM observations WHERE id = ?",
                    (result.target_observation_id,)).fetchone()
            self.assertIn(f'"chart_display_interval":"{interval}"', row[0])
            self.assertIn(f'"expiry_at_ms":{observed_at + 180_000}', row[0])

    def test_invalid_screen_context_is_rejected_before_fetch(self):
        calls = []
        coordinator = CaptureCoordinator(
            self.store, reference_fetcher=lambda **_kwargs: calls.append(1) or self.tick(),
            clock_ms=lambda: 1_000_000)
        with self.assertRaises(ValueError):
            coordinator.capture(target_instrument="CRYPTO_IDX", target_price=123.45,
                                target_observed_at_ms=1_000_010,
                                payout_ratio=0.83, chart_display_interval="5s",
                                expiry_at_ms=900_000)
        self.assertEqual(calls, [])

    def test_invalid_chart_display_interval_is_rejected_before_fetch(self):
        calls = []
        coordinator = CaptureCoordinator(
            self.store, reference_fetcher=lambda **_kwargs: calls.append(1) or self.tick(),
            clock_ms=lambda: 1_000_000)
        with self.assertRaises(ValueError):
            coordinator.capture(target_instrument="CRYPTO_IDX", target_price=123.45,
                                target_observed_at_ms=1_000_010,
                                chart_display_interval="60m")
        self.assertEqual(calls, [])

    def test_batch_capture_commits_target_and_references_atomically(self):
        coordinator = CaptureCoordinator(
            self.store, clock_ms=lambda: 1_000_000,
            id_factory=lambda: "batch-1")
        ticks = (
            self.tick("BTCUSDT", at=1_000_040),
            self.tick("ETHUSDT", at=1_000_040),
        )
        result = coordinator.capture_batch(
            target_instrument="CRYPTO_IDX", target_price=123.45,
            target_observed_at_ms=1_000_010,
            payout_ratio=0.83,
            majority_up_ratio=0.61, majority_down_ratio=0.39,
            reference_batch_fetcher=lambda **_kwargs: ticks)
        self.assertEqual(self.store.count(), 3)
        self.assertEqual(result.reference_symbols, ("BTCUSDT", "ETHUSDT"))
        self.assertEqual(len(result.reference_observation_ids), 2)
        self.assertEqual(result.max_target_reference_offset_ms, 30)
        self.assertEqual(result.quality, "ACCEPTABLE")
        self.assertEqual(result.payout_ratio, 0.83)
        target = self.store.price_series("binomo_screen", "CRYPTO_IDX")[0]
        self.assertEqual(target.metadata["payout_ratio"], 0.83)
        self.assertEqual(target.metadata["majority_up_ratio"], 0.61)

    def test_batch_symbol_mismatch_writes_nothing(self):
        coordinator = CaptureCoordinator(self.store, clock_ms=lambda: 1_000_000)
        with self.assertRaisesRegex(ValueError, "order/symbol"):
            coordinator.capture_batch(
                target_instrument="CRYPTO_IDX", target_price=123.45,
                target_observed_at_ms=1_000_010,
                reference_symbols=("BTCUSDT", "ETHUSDT"),
                reference_batch_fetcher=lambda **_kwargs: (
                    self.tick("ETHUSDT"), self.tick("BTCUSDT")))
        self.assertEqual(self.store.count(), 0)

    def test_major_five_batch_is_atomic(self):
        coordinator = CaptureCoordinator(
            self.store, clock_ms=lambda: 3_000_000,
            id_factory=lambda: "major-five")
        ticks = tuple(self.tick(symbol, at=3_000_040)
                      for symbol in DEFAULT_REFERENCE_SYMBOLS)
        result = coordinator.capture_batch(
            target_instrument="CRYPTO_IDX", target_price=123.45,
            target_observed_at_ms=3_000_010,
            reference_symbols=DEFAULT_REFERENCE_SYMBOLS,
            reference_batch_fetcher=lambda **_kwargs: ticks)
        self.assertEqual(self.store.count(), 6)
        self.assertEqual(result.reference_symbols, DEFAULT_REFERENCE_SYMBOLS)
        self.assertEqual(len(result.reference_observation_ids), 5)


if __name__ == "__main__":
    unittest.main()
