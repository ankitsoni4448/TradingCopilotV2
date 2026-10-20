"""Durable fixed-horizon capture jobs without sleeping worker threads."""
from __future__ import annotations

from contextlib import contextmanager

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


ALLOWED_HORIZONS = (60, 180, 300)


@dataclass(frozen=True)
class CaptureJob:
    id: int
    entry_capture_id: str
    horizon_seconds: int
    due_at_ms: int
    status: str


class OutcomeScheduler:
    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            yield connection
        finally:
            connection.close()

    def _initialize(self):
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS outcome_capture_jobs (
                    id INTEGER PRIMARY KEY,
                    entry_capture_id TEXT NOT NULL,
                    horizon_seconds INTEGER NOT NULL,
                    due_at_ms INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('PENDING','RUNNING','COMPLETED','FAILED')),
                    created_at_ms INTEGER NOT NULL,
                    started_at_ms INTEGER,
                    finished_at_ms INTEGER,
                    outcome_capture_id TEXT,
                    error TEXT,
                    UNIQUE(entry_capture_id, horizon_seconds)
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_outcome_jobs_due
                ON outcome_capture_jobs(status, due_at_ms)
            """)

    def schedule(self, entry_capture_id: str, anchor_ms: int,
                 horizons: tuple[int, ...] = ALLOWED_HORIZONS) -> list[int]:
        if not entry_capture_id.strip() or anchor_ms <= 0:
            raise ValueError("entry_capture_id and a positive anchor_ms are required")
        if not horizons or any(horizon not in ALLOWED_HORIZONS for horizon in horizons):
            raise ValueError("horizons may only contain 60, 180 and 300")
        created_at_ms = int(time.time() * 1000)
        ids = []
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                for horizon in horizons:
                    cursor = connection.execute("""
                        INSERT INTO outcome_capture_jobs
                        (entry_capture_id, horizon_seconds, due_at_ms, status, created_at_ms)
                        VALUES (?, ?, ?, 'PENDING', ?)
                    """, (entry_capture_id, horizon, anchor_ms + horizon * 1000,
                          created_at_ms))
                    ids.append(int(cursor.lastrowid))
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("capture horizons already scheduled") from exc
        return ids

    def claim_due(self, now_ms: int, limit: int = 10) -> list[CaptureJob]:
        if now_ms <= 0 or not 1 <= limit <= 100:
            raise ValueError("invalid now_ms or limit")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute("""
                SELECT id, entry_capture_id, horizon_seconds, due_at_ms, status
                FROM outcome_capture_jobs
                WHERE status = 'PENDING' AND due_at_ms <= ?
                ORDER BY due_at_ms, id LIMIT ?
            """, (now_ms, limit)).fetchall()
            for row in rows:
                connection.execute("""
                    UPDATE outcome_capture_jobs
                    SET status = 'RUNNING', started_at_ms = ?
                    WHERE id = ? AND status = 'PENDING'
                """, (now_ms, row["id"]))
            connection.commit()
        return [CaptureJob(int(row["id"]), row["entry_capture_id"],
                           int(row["horizon_seconds"]), int(row["due_at_ms"]),
                           "RUNNING") for row in rows]

    def complete(self, job_id: int, outcome_capture_id: str, finished_at_ms: int):
        self._finish(job_id, "COMPLETED", finished_at_ms,
                     outcome_capture_id=outcome_capture_id)

    def fail(self, job_id: int, error: str, finished_at_ms: int):
        if not error.strip():
            raise ValueError("failure reason is required")
        self._finish(job_id, "FAILED", finished_at_ms, error=error[:500])

    def _finish(self, job_id: int, status: str, finished_at_ms: int,
                outcome_capture_id: str | None = None, error: str | None = None):
        if finished_at_ms <= 0:
            raise ValueError("finished_at_ms must be positive")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute("""
                UPDATE outcome_capture_jobs
                SET status = ?, finished_at_ms = ?, outcome_capture_id = ?, error = ?
                WHERE id = ? AND status = 'RUNNING'
            """, (status, finished_at_ms, outcome_capture_id, error, job_id))
            if cursor.rowcount != 1:
                connection.rollback()
                raise ValueError("job is not in RUNNING state")
            connection.commit()

    def rows(self) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM outcome_capture_jobs ORDER BY due_at_ms").fetchall()

