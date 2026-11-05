from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

from models import Tour


class Storage:
    """Small SQLite repository for response caching and price history."""

    def __init__(self, database: Path) -> None:
        self.database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS response_cache (
                    cache_key TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    tour_key TEXT NOT NULL,
                    source TEXT NOT NULL,
                    hotel_name TEXT NOT NULL,
                    departure_date TEXT NOT NULL,
                    nights INTEGER NOT NULL,
                    price_total REAL NOT NULL,
                    price_per_person REAL NOT NULL,
                    url TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    UNIQUE(run_id, tour_key)
                );
                CREATE INDEX IF NOT EXISTS idx_price_history_lookup
                    ON price_history(tour_key, observed_at);
                """
            )

    def get_cached(self, cache_key: str, ttl_seconds: int) -> str | None:
        if ttl_seconds <= 0:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT created_at, body FROM response_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if row is None:
            return None
        if time.time() - float(row["created_at"]) > ttl_seconds:
            return None
        return str(row["body"])

    def set_cached(self, cache_key: str, body: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO response_cache(cache_key, created_at, body)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET created_at = excluded.created_at, body = excluded.body
                """,
                (cache_key, time.time(), body),
            )

    def cleanup_cache(self, ttl_seconds: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM response_cache WHERE created_at < ?",
                (time.time() - ttl_seconds,),
            )

    def previous_prices(self, tour_keys: Iterable[str], before: datetime) -> dict[str, float]:
        keys = sorted(set(tour_keys))
        if not keys:
            return {}
        result: dict[str, float] = {}
        before_text = before.isoformat()
        with self._connect() as connection:
            for offset in range(0, len(keys), 400):
                chunk = keys[offset : offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""
                    SELECT current.tour_key, current.price_total
                    FROM price_history AS current
                    JOIN (
                        SELECT tour_key, MAX(observed_at) AS latest
                        FROM price_history
                        WHERE observed_at < ? AND tour_key IN ({placeholders})
                        GROUP BY tour_key
                    ) AS previous
                    ON current.tour_key = previous.tour_key AND current.observed_at = previous.latest
                    WHERE current.observed_at < ?
                    """,
                    [before_text, *chunk, before_text],
                ).fetchall()
                for row in rows:
                    key = str(row["tour_key"])
                    result[key] = min(result.get(key, float("inf")), float(row["price_total"]))
        return result

    def save_price_history(self, tours: Iterable[Tour], run_id: str, observed_at: datetime) -> None:
        rows = [
            (
                run_id,
                tour.dedup_key,
                tour.source,
                tour.hotel_name,
                tour.departure_date.isoformat(),
                tour.nights,
                tour.price_total,
                tour.price_per_person,
                tour.url,
                observed_at.isoformat(),
            )
            for tour in tours
            if tour.dedup_key
        ]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO price_history(
                    run_id, tour_key, source, hotel_name, departure_date, nights,
                    price_total, price_per_person, url, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, tour_key) DO UPDATE SET
                    source = excluded.source,
                    price_total = excluded.price_total,
                    price_per_person = excluded.price_per_person,
                    url = excluded.url,
                    observed_at = excluded.observed_at
                """,
                rows,
            )
