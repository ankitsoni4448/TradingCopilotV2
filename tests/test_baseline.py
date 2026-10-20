import unittest
from datetime import timedelta
import numpy as np
import pandas as pd

from indicators import compute_indicators
from market_data import fetch_closed_klines
from predictor import Predictor


def candles(count=80, slope=0.25):
    close = 100 + np.arange(count) * slope
    return pd.DataFrame({"timestamp": pd.date_range("2026-01-01", periods=count, freq="min", tz="UTC"),
                         "open": close - 0.05, "high": close + 0.10,
                         "low": close - 0.10, "close": close,
                         "volume": np.full(count, 10.0)})


class IndicatorTests(unittest.TestCase):
    def test_future_row_does_not_change_prior_features(self):
        base = candles()
        before = compute_indicators(base).iloc[-1]
        future_time = base["timestamp"].iloc[-1].to_pydatetime() + timedelta(minutes=1)
        future = candles(1).assign(timestamp=future_time,
                                   open=10000, high=10001, low=9999, close=10000)
        after = compute_indicators(pd.concat([base, future], ignore_index=True)).iloc[-2]
        for column in ("ema_9", "ema_21", "macd_hist", "rsi_14", "atr_14"):
            self.assertAlmostEqual(float(before[column]), float(after[column]), places=10)

    def test_no_trade_has_no_fake_probability(self):
        result = Predictor().predict_from_df(candles(slope=0.0))
        self.assertEqual(result["signal"], "NO_TRADE")
        self.assertIsNone(result["calibrated_probability"])
        self.assertNotIn("confidence", result)

    def test_trending_baseline_can_signal(self):
        self.assertEqual(Predictor().predict_from_df(candles(slope=0.5),
                                                    no_trade_margin=0.01)["signal"], "UP")


class MarketDataTests(unittest.TestCase):
    def test_open_candle_is_excluded(self):
        closed = [0, "1", "2", "0.5", "1.5", "10", 59_999, "0", 1, "0", "0", "0"]
        opened = [1_740_000, "1.5", "2", "1", "1.8", "12", 1_800_001, "0", 1, "0", "0", "0"]
        snapshot = fetch_closed_klines(now_ms=1_800_000, limit=50,
                                       getter=lambda _url, _timeout: [closed, opened])
        self.assertEqual(len(snapshot.candles), 1)
        self.assertEqual(snapshot.latest_close_time_ms, 59_999)


if __name__ == "__main__":
    unittest.main()
