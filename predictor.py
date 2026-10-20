"""Transparent heuristic baseline, not a trained or calibrated AI model."""
import math
from typing import Optional

import numpy as np
import pandas as pd

from indicators import compute_indicators


class Predictor:
    def __init__(self, symbol: str = "ETHUSDT"):
        self.symbol = symbol

    def score_from_indicators(self, df: pd.DataFrame) -> float:
        if df is None or df.empty:
            return 0.0
        last = compute_indicators(df).iloc[-1]
        price = float(last.get("close", 1.0)) or 1.0
        ema_diff = float(last.get("ema_9", 0.0) - last.get("ema_21", 0.0))
        macd_hist = float(last.get("macd_hist", 0.0))
        rsi = float(last.get("rsi_14", 50.0))
        ema_score = np.tanh(ema_diff / (price + 1e-8)) * 2.0
        macd_score = np.tanh(macd_hist) * 1.5
        rsi_score = (rsi - 50.0) / 50.0
        return float(ema_score + macd_score + rsi_score)

    def predict_from_df(self, df: pd.DataFrame, timeframe: Optional[str] = "1m",
                        no_trade_margin: float = 0.10):
        if df is None or df.empty:
            return self._no_trade(timeframe, "no_data")
        if len(df) < 35:
            return self._no_trade(timeframe, "insufficient_history")
        try:
            score = self.score_from_indicators(df)
            if not math.isfinite(score):
                return self._no_trade(timeframe, "invalid_features")
            directional_score = math.tanh(score)
            strength = abs(directional_score)
            if strength < no_trade_margin:
                signal, reason = "NO_TRADE", "weak_indicator_agreement"
            else:
                signal = "UP" if directional_score > 0 else "DOWN"
                reason = "baseline_indicator_score"
            return {"signal": signal, "score": round(score, 6),
                    "signal_strength": round(strength, 3), "timeframe": timeframe,
                    "reason": reason, "calibrated_probability": None}
        except Exception as exc:
            result = self._no_trade(timeframe, "prediction_error")
            result["error"] = str(exc)
            return result

    def predict(self, df: pd.DataFrame = None, timeframe: Optional[str] = "1m"):
        return self.predict_from_df(df, timeframe)

    @staticmethod
    def _no_trade(timeframe, reason):
        return {"signal": "NO_TRADE", "score": 0.0, "signal_strength": 0.0,
                "timeframe": timeframe, "reason": reason,
                "calibrated_probability": None}

