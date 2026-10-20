"""Public, read-only reference-market data client.

Binance is a reference feed only. It must not be assumed to match
Binomo/Crypto IDX until timestamp-aligned observations have been measured.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

BINANCE_PUBLIC_BASE = "https://data-api.binance.vision"
BINANCE_KLINES_URL = f"{BINANCE_PUBLIC_BASE}/api/v3/klines"
BINANCE_TICKER_URL = f"{BINANCE_PUBLIC_BASE}/api/v3/ticker/price"
DEFAULT_REFERENCE_SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT")
ALLOWED_SYMBOLS = frozenset(DEFAULT_REFERENCE_SYMBOLS)
ALLOWED_INTERVALS = {"1m", "3m", "5m", "15m", "1h"}


class MarketDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class MarketSnapshot:
    candles: pd.DataFrame
    source: str
    symbol: str
    interval: str
    fetched_at_ms: int
    latest_close_time_ms: int

    @property
    def age_seconds(self) -> float:
        return max(0.0, (self.fetched_at_ms - self.latest_close_time_ms) / 1000)


@dataclass(frozen=True)
class ReferenceTick:
    source: str
    symbol: str
    price: float
    observed_at_ms: int
    requested_at_ms: int
    received_at_ms: int

    @property
    def latency_ms(self) -> int:
        return self.received_at_ms - self.requested_at_ms


def _http_get_json(url: str, timeout: float = 8.0):
    request = Request(url, headers={"User-Agent": "TradingCopilotV2/0.1"})
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise MarketDataError(f"market data HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def fetch_reference_tick(symbol: str = "ETHUSDT", *,
                         getter: Callable[[str, float], object] = _http_get_json,
                         clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
                         timeout: float = 8.0) -> ReferenceTick:
    """Fetch a ticker and retain local request latency as timestamp uncertainty."""
    symbol = symbol.upper()
    if symbol not in ALLOWED_SYMBOLS:
        raise ValueError(f"unsupported symbol: {symbol}")
    requested_at_ms = int(clock_ms())
    query = urlencode({"symbol": symbol})
    try:
        payload = getter(f"{BINANCE_TICKER_URL}?{query}", timeout)
    except (OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise MarketDataError(f"failed to fetch reference tick: {exc}") from exc
    received_at_ms = int(clock_ms())
    if received_at_ms < requested_at_ms:
        raise MarketDataError("local clock moved backwards during reference request")
    if not isinstance(payload, dict) or payload.get("symbol") != symbol:
        raise MarketDataError("reference ticker returned an unexpected symbol")
    try:
        price = float(payload["price"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MarketDataError("reference ticker returned an invalid price") from exc
    if not math.isfinite(price) or price <= 0:
        raise MarketDataError("reference ticker price must be finite and positive")
    midpoint_ms = requested_at_ms + (received_at_ms - requested_at_ms) // 2
    return ReferenceTick("binance_spot_public", symbol, price, midpoint_ms,
                         requested_at_ms, received_at_ms)


def fetch_reference_ticks(
    symbols: tuple[str, ...] = DEFAULT_REFERENCE_SYMBOLS, *,
    getter: Callable[[str, float], object] = _http_get_json,
    clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    timeout: float = 8.0,
) -> tuple[ReferenceTick, ...]:
    """Fetch several reference prices in one request and one latency window."""
    normalized = tuple(symbol.upper() for symbol in symbols)
    if not normalized or len(set(normalized)) != len(normalized):
        raise ValueError("reference symbols must be non-empty and unique")
    unsupported = set(normalized) - ALLOWED_SYMBOLS
    if unsupported:
        raise ValueError(f"unsupported symbols: {', '.join(sorted(unsupported))}")
    requested_at_ms = int(clock_ms())
    query = urlencode({"symbols": json.dumps(normalized, separators=(",", ":"))})
    try:
        payload = getter(f"{BINANCE_TICKER_URL}?{query}", timeout)
    except (OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise MarketDataError(f"failed to fetch reference ticks: {exc}") from exc
    received_at_ms = int(clock_ms())
    if received_at_ms < requested_at_ms:
        raise MarketDataError("local clock moved backwards during reference request")
    if not isinstance(payload, list):
        raise MarketDataError("reference tickers returned an invalid payload")
    rows = {row.get("symbol"): row for row in payload if isinstance(row, dict)}
    if set(rows) != set(normalized):
        raise MarketDataError("reference tickers returned missing or unexpected symbols")
    midpoint_ms = requested_at_ms + (received_at_ms - requested_at_ms) // 2
    ticks = []
    for symbol in normalized:
        try:
            price = float(rows[symbol]["price"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketDataError("reference ticker returned an invalid price") from exc
        if not math.isfinite(price) or price <= 0:
            raise MarketDataError("reference ticker price must be finite and positive")
        ticks.append(ReferenceTick(
            "binance_spot_public", symbol, price, midpoint_ms,
            requested_at_ms, received_at_ms))
    return tuple(ticks)


def fetch_closed_klines(
    symbol: str = "ETHUSDT",
    interval: str = "1m",
    limit: int = 250,
    *,
    now_ms: int | None = None,
    getter: Callable[[str, float], object] = _http_get_json,
    timeout: float = 8.0,
) -> MarketSnapshot:
    symbol = symbol.upper()
    if symbol not in ALLOWED_SYMBOLS:
        raise ValueError(f"unsupported symbol: {symbol}")
    if interval not in ALLOWED_INTERVALS:
        raise ValueError(f"unsupported interval: {interval}")
    if not 50 <= limit <= 1000:
        raise ValueError("limit must be between 50 and 1000")

    fetched_at_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    query = urlencode({"symbol": symbol, "interval": interval, "limit": limit})
    try:
        rows = getter(f"{BINANCE_KLINES_URL}?{query}", timeout)
    except (OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise MarketDataError(f"failed to fetch reference data: {exc}") from exc
    if not isinstance(rows, list) or not rows:
        raise MarketDataError("reference feed returned no candles")

    columns = ["open_time", "open", "high", "low", "close", "volume",
               "close_time", "quote_volume", "trade_count", "taker_base_volume",
               "taker_quote_volume", "ignore"]
    frame = pd.DataFrame(rows, columns=columns)
    frame = frame[frame["close_time"].astype("int64") < fetched_at_ms].copy()
    if frame.empty:
        raise MarketDataError("reference feed has no closed candles")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame["timestamp"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
    frame = frame[["timestamp", "open", "high", "low", "close", "volume", "close_time"]]
    frame = frame.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    return MarketSnapshot(frame, "binance_spot_public", symbol, interval,
                          fetched_at_ms, int(frame.iloc[-1]["close_time"]))
