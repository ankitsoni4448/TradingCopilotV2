import pandas as pd


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().sort_values("timestamp").reset_index(drop=True)
    df["ema_9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema_21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["macd"] = df["ema_9"] - df["ema_21"]
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    df["rsi_14"] = compute_rsi(df["close"], 14)
    df["atr_14"] = compute_atr(df, 14)
    df["volatility_20"] = df["close"].pct_change().rolling(20).std()
    df["momentum_10"] = df["close"].diff(10)
    # Never back-fill: that would leak future values into earlier rows.
    return df.ffill()


def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window=period, min_periods=period).mean()
    loss = -delta.clip(upper=0).rolling(window=period, min_periods=period).mean()
    rs = gain / (loss + 1e-8)
    rsi = 100 - (100 / (1 + rs))
    # A flat window has neither gains nor losses and is neutral, not bearish.
    rsi = rsi.mask((gain == 0) & (loss == 0), 50.0)
    rsi = rsi.mask((gain > 0) & (loss == 0), 100.0)
    return rsi


def compute_atr(df, period=14):
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    return ranges.max(axis=1).rolling(period, min_periods=period).mean()
