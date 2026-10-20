"""Append-only evidence store for target/reference price synchronization."""
from __future__ import annotations

from contextlib import contextmanager

import hashlib
import json
import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Observation:
    source: str
    instrument: str
    observed_at_ms: int
    price: float
    capture_method: str
    screenshot_sha256: str | None = None
    metadata: dict | None = None


@dataclass(frozen=True)
class AlignmentSample:
    anchor_ms: int
    horizon_seconds: int
    target_entry: float
    target_exit: float
    reference_entry: float
    reference_exit: float
    target_direction: str
    reference_direction: str
    agrees: bool | None
    entry_reference_offset_ms: int
    exit_target_offset_ms: int
    exit_reference_offset_ms: int


@dataclass(frozen=True)
class PricePoint:
    source: str
    instrument: str
    observed_at_ms: int
    price: float
    metadata: dict


class ObservationStore:
    """SQLite-backed append-only log. No update/delete methods are exposed."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self):
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY,
                    source TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    observed_at_ms INTEGER NOT NULL,
                    price REAL NOT NULL CHECK(price > 0),
                    capture_method TEXT NOT NULL,
                    screenshot_sha256 TEXT,
                    metadata_json TEXT NOT NULL,
                    inserted_at_ms INTEGER NOT NULL,
                    UNIQUE(source, instrument, observed_at_ms)
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_observation_lookup
                ON observations(source, instrument, observed_at_ms)
            """)

    def append(self, observation: Observation) -> int:
        return self.append_many([observation])[0]

    def append_many(self, observations: list[Observation]) -> list[int]:
        """Insert a capture batch atomically; either every row is saved or none is."""
        if not observations:
            raise ValueError("at least one observation is required")
        for observation in observations:
            self._validate(observation)
        inserted_at_ms = int(time.time() * 1000)
        row_ids: list[int] = []
        try:
            with self._connect() as connection:
                for observation in observations:
                    metadata_json = json.dumps(observation.metadata or {}, sort_keys=True,
                                               separators=(",", ":"))
                    cursor = connection.execute("""
                        INSERT INTO observations
                        (source, instrument, observed_at_ms, price, capture_method,
                         screenshot_sha256, metadata_json, inserted_at_ms)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (observation.source, observation.instrument,
                          observation.observed_at_ms, observation.price,
                          observation.capture_method, observation.screenshot_sha256,
                          metadata_json, inserted_at_ms))
                    row_ids.append(int(cursor.lastrowid))
            return row_ids
        except sqlite3.IntegrityError as exc:
            raise ValueError("duplicate observation timestamp for source/instrument") from exc

    def count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0])

    def nearest(self, source: str, instrument: str, timestamp_ms: int,
                tolerance_ms: int) -> sqlite3.Row | None:
        if tolerance_ms < 0:
            raise ValueError("tolerance_ms must be non-negative")
        with self._connect() as connection:
            return connection.execute("""
                SELECT *, ABS(observed_at_ms - ?) AS offset_abs
                FROM observations
                WHERE source = ? AND instrument = ?
                  AND observed_at_ms BETWEEN ? AND ?
                ORDER BY offset_abs ASC, observed_at_ms ASC
                LIMIT 1
            """, (timestamp_ms, source, instrument,
                  timestamp_ms - tolerance_ms, timestamp_ms + tolerance_ms)).fetchone()

    def at_or_before(self, source: str, instrument: str, timestamp_ms: int,
                     max_age_ms: int) -> PricePoint | None:
        """Return only information available by ``timestamp_ms``.

        This deliberately differs from :meth:`nearest`: predictive features must
        never select a slightly later observation just because it is closer.
        """
        if max_age_ms < 0:
            raise ValueError("max_age_ms must be non-negative")
        with self._connect() as connection:
            row = connection.execute("""
                SELECT * FROM observations
                WHERE source = ? AND instrument = ?
                  AND observed_at_ms BETWEEN ? AND ?
                ORDER BY observed_at_ms DESC
                LIMIT 1
            """, (source, instrument, timestamp_ms - max_age_ms,
                  timestamp_ms)).fetchone()
        return _price_point(row) if row is not None else None

    def price_series(self, source: str, instrument: str, *,
                     start_ms: int | None = None,
                     end_ms: int | None = None) -> tuple[PricePoint, ...]:
        """Read a chronological immutable projection of stored observations."""
        clauses = ["source = ?", "instrument = ?"]
        params: list[object] = [source, instrument]
        if start_ms is not None:
            clauses.append("observed_at_ms >= ?")
            params.append(start_ms)
        if end_ms is not None:
            clauses.append("observed_at_ms <= ?")
            params.append(end_ms)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM observations WHERE " + " AND ".join(clauses) +
                " ORDER BY observed_at_ms", params).fetchall()
        return tuple(_price_point(row) for row in rows)

    def alignment_samples(self, *, target_source: str, target_instrument: str,
                          reference_source: str, reference_instrument: str,
                          horizon_seconds: int, tolerance_ms: int = 1500,
                          start_ms: int | None = None,
                          end_ms: int | None = None) -> list[AlignmentSample]:
        if horizon_seconds not in {60, 180, 300}:
            raise ValueError("horizon_seconds must be 60, 180 or 300")
        clauses = ["source = ?", "instrument = ?"]
        params: list[object] = [target_source, target_instrument]
        if start_ms is not None:
            clauses.append("observed_at_ms >= ?")
            params.append(start_ms)
        if end_ms is not None:
            clauses.append("observed_at_ms <= ?")
            params.append(end_ms)
        with self._connect() as connection:
            anchors = connection.execute(
                "SELECT * FROM observations WHERE " + " AND ".join(clauses) +
                " ORDER BY observed_at_ms", params).fetchall()

        samples: list[AlignmentSample] = []
        horizon_ms = horizon_seconds * 1000
        for anchor in anchors:
            anchor_ms = int(anchor["observed_at_ms"])
            ref_entry = self.nearest(reference_source, reference_instrument,
                                     anchor_ms, tolerance_ms)
            target_exit = self.nearest(target_source, target_instrument,
                                       anchor_ms + horizon_ms, tolerance_ms)
            ref_exit = self.nearest(reference_source, reference_instrument,
                                    anchor_ms + horizon_ms, tolerance_ms)
            if not all((ref_entry, target_exit, ref_exit)):
                continue
            target_direction = _direction(float(anchor["price"]), float(target_exit["price"]))
            reference_direction = _direction(float(ref_entry["price"]), float(ref_exit["price"]))
            agrees = None if "FLAT" in {target_direction, reference_direction} else (
                target_direction == reference_direction)
            samples.append(AlignmentSample(
                anchor_ms=anchor_ms, horizon_seconds=horizon_seconds,
                target_entry=float(anchor["price"]), target_exit=float(target_exit["price"]),
                reference_entry=float(ref_entry["price"]), reference_exit=float(ref_exit["price"]),
                target_direction=target_direction, reference_direction=reference_direction,
                agrees=agrees,
                entry_reference_offset_ms=int(ref_entry["observed_at_ms"]) - anchor_ms,
                exit_target_offset_ms=int(target_exit["observed_at_ms"]) - (anchor_ms + horizon_ms),
                exit_reference_offset_ms=int(ref_exit["observed_at_ms"]) - (anchor_ms + horizon_ms),
            ))
        return samples

    @staticmethod
    def _validate(observation: Observation):
        if not observation.source.strip() or not observation.instrument.strip():
            raise ValueError("source and instrument are required")
        if observation.observed_at_ms <= 0:
            raise ValueError("observed_at_ms must be a positive UTC epoch millisecond")
        if not math.isfinite(observation.price) or observation.price <= 0:
            raise ValueError("price must be finite and positive")
        if not observation.capture_method.strip():
            raise ValueError("capture_method is required")
        digest = observation.screenshot_sha256
        if digest is not None and (len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower())):
            raise ValueError("screenshot_sha256 must be a 64-character hex digest")


def screenshot_digest(image_bytes: bytes) -> str:
    return hashlib.sha256(image_bytes).hexdigest()


def alignment_metrics(samples: list[AlignmentSample], total_anchors: int | None = None) -> dict:
    comparable = [sample for sample in samples if sample.agrees is not None]
    agreements = sum(sample.agrees is True for sample in comparable)
    denominator = len(comparable)
    total = len(samples) if total_anchors is None else total_anchors
    offsets = [abs(offset) for sample in samples for offset in
               (sample.entry_reference_offset_ms, sample.exit_target_offset_ms,
                sample.exit_reference_offset_ms)]
    return {
        "samples": len(samples),
        "comparable_samples": denominator,
        "agreement_rate": agreements / denominator if denominator else None,
        "coverage": len(samples) / total if total else None,
        "max_absolute_timing_offset_ms": max(offsets) if offsets else None,
    }


def _direction(entry: float, exit_: float) -> str:
    tolerance = max(abs(entry), abs(exit_)) * 1e-12
    change = exit_ - entry
    if abs(change) <= tolerance:
        return "FLAT"
    return "UP" if change > 0 else "DOWN"


def _price_point(row: sqlite3.Row) -> PricePoint:
    try:
        metadata = json.loads(row["metadata_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("observation metadata is not valid JSON") from exc
    if not isinstance(metadata, dict):
        raise ValueError("observation metadata must be an object")
    return PricePoint(
        source=str(row["source"]), instrument=str(row["instrument"]),
        observed_at_ms=int(row["observed_at_ms"]), price=float(row["price"]),
        metadata=metadata)
