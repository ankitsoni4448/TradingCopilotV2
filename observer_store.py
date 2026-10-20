"""Additive SQLite storage and local reports for desktop observation sessions."""
from __future__ import annotations

import json
import html
import math
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from screen_observer import FrameRecord


class ObserverStore:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=30000")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS observer_sessions (
                    session_id TEXT PRIMARY KEY,
                    started_at_ms INTEGER NOT NULL,
                    ended_at_ms INTEGER,
                    interval_seconds INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    report_path TEXT
                );
                CREATE TABLE IF NOT EXISTS screen_observations (
                    observation_id INTEGER PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES observer_sessions(session_id),
                    captured_at_ms INTEGER NOT NULL,
                    classification TEXT NOT NULL CHECK(classification IN
                        ('BINOMO_MARKET','OTHER_MARKET','NON_MARKET','UNKNOWN')),
                    confidence REAL NOT NULL,
                    extraction_status TEXT NOT NULL,
                    application_context TEXT,
                    asset TEXT,
                    price REAL,
                    price_source TEXT,
                    processing_ms INTEGER NOT NULL,
                    error_reason TEXT,
                    extracted_fields_json TEXT NOT NULL,
                    UNIQUE(session_id, captured_at_ms)
                );
                CREATE INDEX IF NOT EXISTS idx_screen_observations_session_time
                    ON screen_observations(session_id, captured_at_ms);
                CREATE TABLE IF NOT EXISTS observer_market_events (
                    event_id INTEGER PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES observer_sessions(session_id),
                    observation_id INTEGER NOT NULL REFERENCES screen_observations(observation_id),
                    captured_at_ms INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    detail_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS manual_demo_trades (
                    trade_id INTEGER PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES observer_sessions(session_id),
                    asset TEXT NOT NULL,
                    direction TEXT NOT NULL CHECK(direction IN ('UP','DOWN')),
                    entry_time_ms INTEGER NOT NULL,
                    duration_seconds INTEGER NOT NULL,
                    stake REAL NOT NULL,
                    entry_price REAL,
                    outcome TEXT NOT NULL CHECK(outcome IN
                        ('OPEN','WIN','LOSS','PUSH','UNKNOWN')),
                    nearby_observation_ids_json TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );
            """)

    @staticmethod
    def new_session_id() -> str:
        return uuid.uuid4().hex

    def start_session(self, session_id: str, interval_seconds: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO observer_sessions "
                "(session_id,started_at_ms,interval_seconds,status) VALUES (?,?,?,'RUNNING')",
                (session_id, _now_ms(), interval_seconds))

    def append_frame(self, session_id: str, record: FrameRecord) -> int:
        with self._connect() as connection:
            cursor = connection.execute("""
                INSERT INTO screen_observations
                (session_id,captured_at_ms,classification,confidence,
                 extraction_status,application_context,asset,price,price_source,
                 processing_ms,error_reason,extracted_fields_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (session_id, record.captured_at_ms, record.classification,
                  record.confidence, record.extraction_status,
                  record.application_context, record.asset, record.price,
                  record.price_source, record.processing_ms, record.error_reason,
                  json.dumps(record.extracted_fields, sort_keys=True,
                             separators=(",", ":"))))
            observation_id = int(cursor.lastrowid)
            for event in record.extracted_fields.get("temporal_events", []):
                if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                    continue
                connection.execute("""
                    INSERT INTO observer_market_events
                    (session_id,observation_id,captured_at_ms,event_type,detail_json)
                    VALUES (?,?,?,?,?)
                """, (session_id, observation_id, record.captured_at_ms,
                      event["type"], json.dumps(event, sort_keys=True)))
            return observation_id

    def counts(self, session_id: str) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT classification, extraction_status, error_reason,
                       COUNT(*) AS count
                FROM screen_observations WHERE session_id=?
                GROUP BY classification, extraction_status, error_reason
            """, (session_id,)).fetchall()
        result = {"total": 0, "market": 0, "non_market": 0,
                  "errors": 0, "valid_prices": 0}
        for row in rows:
            count = int(row["count"])
            result["total"] += count
            if row["classification"] in {"BINOMO_MARKET", "OTHER_MARKET"}:
                result["market"] += count
            if row["classification"] == "NON_MARKET":
                result["non_market"] += count
            if (row["error_reason"] and
                    row["error_reason"] not in {
                        "privacy_excluded_application", "temporal_price_pending"}):
                result["errors"] += count
        with self._connect() as connection:
            result["valid_prices"] = int(connection.execute(
                "SELECT COUNT(*) FROM screen_observations "
                "WHERE session_id=? AND price IS NOT NULL", (session_id,)
                ).fetchone()[0])
        return result

    def session_frames(self, session_id: str) -> tuple[sqlite3.Row, ...]:
        with self._connect() as connection:
            return tuple(connection.execute(
                "SELECT * FROM screen_observations WHERE session_id=? "
                "ORDER BY captured_at_ms", (session_id,)).fetchall())

    def add_trade(self, session_id: str, *, asset: str, direction: str,
                  entry_time_ms: int, duration_seconds: int, stake: float,
                  entry_price: float | None, outcome: str) -> int:
        _validate_trade(asset, direction, entry_time_ms, duration_seconds,
                       stake, entry_price, outcome)
        with self._connect() as connection:
            nearby = connection.execute(
                "SELECT observation_id FROM screen_observations "
                "WHERE session_id=? AND captured_at_ms BETWEEN ? AND ? "
                "ORDER BY ABS(captured_at_ms-?) LIMIT 30",
                (session_id, entry_time_ms - 30_000,
                 entry_time_ms + duration_seconds * 1000 + 30_000,
                 entry_time_ms)).fetchall()
            now = _now_ms()
            cursor = connection.execute("""
                INSERT INTO manual_demo_trades
                (session_id,asset,direction,entry_time_ms,duration_seconds,stake,
                 entry_price,outcome,nearby_observation_ids_json,created_at_ms,updated_at_ms)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (session_id, asset.strip().upper(), direction, entry_time_ms,
                  duration_seconds, stake, entry_price, outcome,
                  json.dumps([int(row[0]) for row in nearby]), now, now))
            return int(cursor.lastrowid)

    def update_trade(self, trade_id: int, *, asset: str, direction: str,
                     entry_time_ms: int, duration_seconds: int, stake: float,
                     entry_price: float | None, outcome: str) -> None:
        _validate_trade(asset, direction, entry_time_ms, duration_seconds,
                       stake, entry_price, outcome)
        with self._connect() as connection:
            trade = connection.execute(
                "SELECT session_id FROM manual_demo_trades WHERE trade_id=?",
                (trade_id,)).fetchone()
            if trade is None:
                raise ValueError("manual_trade_not_found")
            nearby = connection.execute(
                "SELECT observation_id FROM screen_observations WHERE session_id=? "
                "AND captured_at_ms BETWEEN ? AND ? ORDER BY ABS(captured_at_ms-?) LIMIT 30",
                (trade["session_id"], entry_time_ms - 30_000,
                 entry_time_ms + duration_seconds * 1000 + 30_000,
                 entry_time_ms)).fetchall()
            connection.execute("""
                UPDATE manual_demo_trades SET asset=?,direction=?,entry_time_ms=?,
                duration_seconds=?,stake=?,entry_price=?,outcome=?,
                nearby_observation_ids_json=?,updated_at_ms=? WHERE trade_id=?
            """, (asset.strip().upper(), direction, entry_time_ms,
                  duration_seconds, stake, entry_price, outcome,
                  json.dumps([int(row[0]) for row in nearby]), _now_ms(), trade_id))

    def trades(self, session_id: str) -> tuple[dict, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM manual_demo_trades WHERE session_id=? ORDER BY entry_time_ms",
                (session_id,)).fetchall()
        return tuple({**dict(row), "nearby_observation_ids": json.loads(
            row["nearby_observation_ids_json"])} for row in rows)

    def generate_report(self, session_id: str, *, finish: bool = False) -> Path:
        frames = self.session_frames(session_id)
        session = self._session(session_id)
        times = [int(row["captured_at_ms"]) for row in frames]
        priced_rows = [row for row in frames if row["price"] is not None]
        assets = {row["asset"] for row in priced_rows if row["asset"]}
        prices = ([float(row["price"]) for row in priced_rows
               if row["asset"] in assets] if len(assets) == 1 else [])
        interval_ms = int(session["interval_seconds"]) * 1000
        gaps = [right - left for left, right in zip(times, times[1:])
                if right - left > interval_ms * 1.5]
        market = sum(row["classification"] in {"BINOMO_MARKET", "OTHER_MARKET"}
                 for row in frames)
        trades = [_calculate_trade_outcome(
            trade, priced_rows, max_gap_ms=interval_ms * 2)
              for trade in self.trades(session_id)]
        with self._connect() as connection:
            events = [dict(row) for row in connection.execute(
                "SELECT captured_at_ms,event_type,detail_json "
                "FROM observer_market_events WHERE session_id=? "
                "ORDER BY captured_at_ms,event_id", (session_id,)).fetchall()]
        for event in events:
            event["details"] = json.loads(event.pop("detail_json"))
        frame_fields = [json.loads(row["extracted_fields_json"]) for row in frames]
        model_statuses: dict[str, int] = {}
        inference_latencies = []
        for fields in frame_fields:
            provenance = fields.get("visual_model", {})
            if isinstance(provenance, dict):
                status = provenance.get("status")
                if isinstance(status, str):
                    model_statuses[status] = model_statuses.get(status, 0) + 1
                latency = provenance.get("latency_ms")
                if isinstance(latency, (int, float)) and math.isfinite(latency):
                    inference_latencies.append(float(latency))
        from observer_ml import evaluate_baseline
        ml_readiness = evaluate_baseline(
            self.database_path, horizon_seconds=60, minimum_each=100)
        report = {
            "session_id": session_id,
            "started_at_ms": int(session["started_at_ms"]),
            "ended_at_ms": _now_ms() if finish else session["ended_at_ms"],
            "interval_seconds": int(session["interval_seconds"]),
            "total_frames_captured": len(frames),
            "market_frames": market,
            "non_market_frames": sum(row["classification"] == "NON_MARKET"
                                      for row in frames),
            "unknown_frames": sum(row["classification"] == "UNKNOWN"
                                   for row in frames),
            "valid_price_observations": len(priced_rows),
            "ocr_failures": sum(
                (row["error_reason"] or "").startswith("ocr_") or
                row["error_reason"] == "price_context_unverified"
                for row in frames),
            "price_region_rejections": sum(
                (row["error_reason"] or "").startswith("price_region_")
                for row in frames),
            "capture_errors": sum(row["error_reason"] == "CAPTURE_ERROR"
                                   for row in frames),
            "time_gap_count": len(gaps),
            "maximum_gap_ms": max(gaps, default=0),
            "price_movement": ({
                "first": prices[0], "last": prices[-1],
                "absolute_change": prices[-1] - prices[0],
                "percent_change": ((prices[-1] / prices[0] - 1) * 100)
                if prices[0] else None,
            } if len(prices) >= 2 else None),
            "manual_demo_trades": trades,
            "market_event_timeline": events,
            "visual_backend_status_counts": model_statuses,
            "visual_inference_latency_ms": {
                "maximum": max(inference_latencies, default=None),
                "median": (sorted(inference_latencies)[len(inference_latencies) // 2]
                           if inference_latencies else None),
            },
            "latest_extraction_diagnostics": ({
                key: frame_fields[-1].get(key) for key in (
                    "frame_dimensions", "detected_market_application",
                    "chart_candidate_region", "price_candidate_region",
                    "ocr_engine_status", "price_candidate_count",
                    "price_rejection_reason", "temporal_validation_reason",
                    "visual_model", "dropped_stale_frames")
            } if frame_fields else {}),
            "data_quality": ("NO_VERIFIED_PRICE_DATA" if not priced_rows else
                             "SPARSE" if len(prices) < max(3, len(frames) // 4)
                             else "OBSERVED_NOT_SIGNAL_VALIDATED"),
            "missing_for_reliable_signals": [
                "verified prices with source context", "validated asset identity",
                "out-of-sample tested signal model", "execution and payout evidence"],
            "paper_trade_status": "INSUFFICIENT VERIFIED MARKET DATA",
            "decision": {"action": "NO_TRADE",
                         "reason": "no_out_of_sample_approved_model"},
            "ml_readiness": ml_readiness,
            "screenshots_saved": False,
            "ocr_transcripts_persisted": False,
        }
        base = self.database_path.parent / f"observer-session-{session_id}"
        json_path = base.with_suffix(".json")
        report_path = base.with_suffix(".html")
        json_path.write_text(json.dumps(report, indent=2, sort_keys=True),
                             encoding="utf-8")
        _write_html_report(report_path, report, frames)
        with self._connect() as connection:
            if finish:
                connection.execute(
                    "UPDATE observer_sessions SET ended_at_ms=?,status='STOPPED',"
                    "report_path=? WHERE session_id=?",
                    (report["ended_at_ms"], str(report_path), session_id))
            else:
                connection.execute(
                    "UPDATE observer_sessions SET report_path=? WHERE session_id=?",
                    (str(report_path), session_id))
        return report_path

    def session_report_path(self, session_id: str) -> Path | None:
        row = self._session(session_id)
        path = row["report_path"]
        return Path(path) if path else None

    def _session(self, session_id: str) -> sqlite3.Row:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM observer_sessions WHERE session_id=?",
                (session_id,)).fetchone()
        if row is None:
            raise ValueError("observer_session_not_found")
        return row


def parse_entry_time(value: str) -> int:
    parsed = datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return round(parsed.timestamp() * 1000)


def _validate_trade(asset: str, direction: str, entry_time_ms: int,
                    duration_seconds: int, stake: float,
                    entry_price: float | None, outcome: str) -> None:
    if not asset.strip():
        raise ValueError("manual_trade_asset_required")
    if direction not in {"UP", "DOWN"}:
        raise ValueError("manual_trade_direction_invalid")
    if entry_time_ms <= 0 or duration_seconds <= 0:
        raise ValueError("manual_trade_timing_invalid")
    if not math.isfinite(stake) or stake <= 0:
        raise ValueError("manual_trade_stake_invalid")
    if entry_price is not None and (
            not math.isfinite(entry_price) or entry_price <= 0):
        raise ValueError("manual_trade_entry_price_invalid")
    if outcome not in {"OPEN", "WIN", "LOSS", "PUSH", "UNKNOWN"}:
        raise ValueError("manual_trade_outcome_invalid")


def _now_ms() -> int:
    return round(datetime.now(timezone.utc).timestamp() * 1000)


def _write_html_report(path: Path, report: dict,
                       frames: tuple[sqlite3.Row, ...]) -> None:
    start = min((int(row["captured_at_ms"]) for row in frames), default=0)
    end = max((int(row["captured_at_ms"]) for row in frames), default=start + 1)
    duration = max(1, end - start)
    colors = {"BINOMO_MARKET": "#18794e", "OTHER_MARKET": "#2563eb",
              "NON_MARKET": "#a1a1aa", "UNKNOWN": "#dc2626"}
    marks = []
    price_points: list[str] = []
    prices = [row for row in frames if row["price"] is not None]
    values = [float(row["price"]) for row in prices]
    low, high = (min(values), max(values)) if values else (0.0, 1.0)
    spread = max(1e-12, high - low)
    for row in frames:
        x = (int(row["captured_at_ms"]) - start) / duration * 1000
        color = colors.get(row["classification"], "#71717a")
        marks.append(f'<circle cx="{x:.2f}" cy="20" r="3" fill="{color}" />')
        if row["price"] is not None and len(values) > 1:
            y = 75 - (float(row["price"]) - low) / spread * 55
            price_points.append(f"{x:.2f},{y:.2f}")
    line = (f'<polyline points="{" ".join(price_points)}" fill="none" '
            'stroke="#0f766e" stroke-width="2" />') if len(price_points) > 1 else ""
    timeline = "".join(marks) + line
    event_items = []
    for event in report["market_event_timeline"]:
        event_time = datetime.fromtimestamp(
            event["captured_at_ms"] / 1000).astimezone().isoformat(timespec="seconds")
        event_items.append(
            f"<li>{html.escape(event_time)}: "
            f"{html.escape(event['event_type'])}</li>")
    events = "".join(event_items)
    document = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Observer session report</title>
<style>body{{font:15px Segoe UI,sans-serif;max-width:900px;margin:32px auto;padding:0 18px;color:#202124}}
svg{{width:100%;height:110px;background:#f5f7f8;border:1px solid #ddd}}
table{{border-collapse:collapse}}td,th{{padding:6px 12px;border-bottom:1px solid #ddd;text-align:left}}</style></head>
<body><h1>Desktop observer session</h1><p>Session {html.escape(report['session_id'])}</p>
<table><tr><th>Frames</th><td>{report['total_frames_captured']}</td></tr>
<tr><th>Market frames</th><td>{report['market_frames']}</td></tr>
<tr><th>Validated prices</th><td>{report['valid_price_observations']}</td></tr>
<tr><th>Data quality</th><td>{html.escape(report['data_quality'])}</td></tr>
<tr><th>ML readiness</th><td>{html.escape(report['ml_readiness']['status'])}</td></tr>
<tr><th>Decision</th><td>NO_TRADE</td></tr></table>
<h2>Session timeline</h2><svg viewBox="0 0 1000 100" role="img" aria-label="Frame classes and validated price movement">{timeline}</svg>
<p>Dots: green Binomo, blue other market, grey non-market, red unknown. Teal line: temporally validated same-asset prices.</p>
<h2>Market events</h2><ul>{events or '<li>No market events</li>'}</ul>
<p>Raw screenshots and unrelated OCR transcripts were not persisted. Demo trades are user-entered only.</p>
</body></html>"""
    path.write_text(document, encoding="utf-8")


def _calculate_trade_outcome(trade: dict, price_rows: list[sqlite3.Row], *,
                             max_gap_ms: int) -> dict:
    asset_rows = [row for row in price_rows if row["asset"] == trade["asset"]]
    entry_ms = int(trade["entry_time_ms"])
    settlement_ms = entry_ms + int(trade["duration_seconds"]) * 1000
    entry_rows = [row for row in asset_rows
                  if entry_ms - max_gap_ms <= int(row["captured_at_ms"]) <= entry_ms]
    exit_rows = [row for row in asset_rows
                 if settlement_ms - max_gap_ms <= int(row["captured_at_ms"])
                 <= settlement_ms]
    calculated = "OUTCOME_UNAVAILABLE"
    if entry_rows and exit_rows:
        entry = (float(trade["entry_price"]) if trade["entry_price"] is not None
                 else float(entry_rows[-1]["price"]))
        exit_price = float(exit_rows[-1]["price"])
        entry_source = ("MANUAL_ENTRY_PRICE" if trade["entry_price"] is not None
                        else "VALIDATED_OBSERVATION")
        if entry > 0:
            if abs(exit_price - entry) <= max(entry, exit_price) * 1e-12:
                calculated = "PUSH"
            elif ((exit_price > entry) == (trade["direction"] == "UP")):
                calculated = "WIN"
            else:
                calculated = "LOSS"
        else:
            entry_source = "INVALID_ENTRY_PRICE"
    else:
        entry_source = "VALIDATED_PRICE_EVIDENCE_MISSING"
    return {**trade, "calculated_outcome": calculated,
            "outcome_entry_source": entry_source}
