"""Local-only demo research API for Trading Copilot V2."""
from __future__ import annotations

import os
from dataclasses import asdict
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from capture_service import CaptureCoordinator
from market_data import MarketDataError, fetch_closed_klines
from observation_store import ObservationStore
from predictor import Predictor


class CaptureRequest(BaseModel):
    target_instrument: str = Field(min_length=1, max_length=64)
    target_price: float = Field(gt=0)
    target_observed_at_ms: int = Field(gt=0)
    reference_symbol: str = Field(default="ETHUSDT", pattern="^(ETHUSDT|BTCUSDT)$")
    screenshot_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    capture_method: str = Field(default="screen_manual", min_length=1, max_length=64)
    payout_ratio: float | None = Field(default=None, gt=0, le=1)
    chart_display_interval: Literal["1s", "5s", "1m", "5m", "15m", "1h", "3h", "1d"] | None = None
    expiry_at_ms: int | None = Field(default=None, gt=0)
    amount: float | None = Field(default=None, gt=0)
    time_to_buy_seconds: int | None = Field(default=None, ge=0)
    until_expiration_seconds: int | None = Field(default=None, ge=0)


def create_app(capture_coordinator: CaptureCoordinator | None = None) -> FastAPI:
    app = FastAPI(title="Trading Copilot V2", version="0.2.0")
    coordinator = capture_coordinator or CaptureCoordinator(
        ObservationStore(os.getenv("COPILOT_DB_PATH", "trading_copilot.db")))

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "demo", "execution": "manual",
                "screen_storage": "disabled"}

    @app.get("/predict")
    def predict(symbol: str = Query(default="ETHUSDT"),
                timeframe: str = Query(default="1m", alias="tf")):
        try:
            snapshot = fetch_closed_klines(symbol=symbol, interval=timeframe)
        except (ValueError, MarketDataError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        result = Predictor(snapshot.symbol).predict(snapshot.candles, snapshot.interval)
        return {"symbol": snapshot.symbol, "timeframe": snapshot.interval,
                "reference_source": snapshot.source,
                "reference_price": float(snapshot.candles.iloc[-1]["close"]),
                "data_age_seconds": round(snapshot.age_seconds, 3),
                "target_instrument_validated": False, "signal": result,
                "warning": "Reference signal only; Binance is not validated against Binomo/Crypto IDX."}

    @app.post("/observations/capture", status_code=201)
    def capture(request: CaptureRequest):
        try:
            result = coordinator.capture(**request.model_dump())
        except MarketDataError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return asdict(result)

    return app


app = create_app()
