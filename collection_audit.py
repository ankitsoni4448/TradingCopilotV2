"""Append-only collection health records containing no screen pixels."""
from __future__ import annotations

from contextlib import contextmanager

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CollectionHealth:
    attempts: int
    accepted: int
    rejected: int
    acceptance_rate: float | None
    rejection_reasons: dict[str, int]
    latest_attempt_at_ms: int | None


def safe_error_code(message: str | None) -> str | None:
    if not message:
        return None
    value = message.lower()
    mappings = (
        ("price_tag", "PRICE_TAG"), ("price_ocr", "PRICE_OCR"),
        ("collector_config", "CONFIG"), ("config", "CONFIG"),
        ("display_size", "DISPLAY_SIZE"),
        ("ocr_engine", "OCR_ENGINE"), ("tesseract", "OCR_ENGINE"),
        ("platform_clock", "PLATFORM_CLOCK"),
        ("selected_expiry", "SELECTED_EXPIRY"),
        ("stale", "STALE_FRAME"), ("date_mismatch", "STALE_FRAME"),
        ("failed to fetch", "NETWORK_FAILURE"),
        ("network", "NETWORK_FAILURE"), ("reference", "REFERENCE_FEED"),
        ("duplicate", "DUPLICATE_TIMESTAMP"),
        ("image", "IMAGE_PROVIDER"), ("tesseract", "OCR_ENGINE"),
    )
    return next((code for needle, code in mappings if needle in value),
                "UNCLASSIFIED")


class CollectionAuditStore:
    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS collection_attempts (
                    id INTEGER PRIMARY KEY,
                    attempted_at_ms INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('ACCEPTED','REJECTED')),
                    capture_id TEXT,
                    error_code TEXT,
                    duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
                    CHECK((status='ACCEPTED' AND capture_id IS NOT NULL AND error_code IS NULL)
                       OR (status='REJECTED' AND capture_id IS NULL AND error_code IS NOT NULL))
                )
            """)

    def record(self, attempt) -> int:
        result = getattr(attempt, "result", None)
        values = (int(attempt.attempted_at_ms), str(attempt.status),
                  getattr(result, "capture_id", None) if result else None,
                  safe_error_code(attempt.error), int(attempt.duration_ms))
        if values[0] <= 0 or values[4] < 0:
            raise ValueError("invalid_collection_attempt_timing")
        with self._connect() as connection:
            cursor = connection.execute("""
                INSERT INTO collection_attempts
                (attempted_at_ms,status,capture_id,error_code,duration_ms)
                VALUES (?,?,?,?,?)
            """, values)
            return int(cursor.lastrowid)

    def summary(self) -> CollectionHealth:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status,error_code,attempted_at_ms FROM collection_attempts "
                "ORDER BY attempted_at_ms").fetchall()
        accepted = sum(row["status"] == "ACCEPTED" for row in rows)
        reasons: dict[str, int] = {}
        for row in rows:
            if row["error_code"]:
                reasons[row["error_code"]] = reasons.get(row["error_code"], 0) + 1
        return CollectionHealth(
            len(rows), accepted, len(rows) - accepted,
            accepted / len(rows) if rows else None, reasons,
            int(rows[-1]["attempted_at_ms"]) if rows else None)
