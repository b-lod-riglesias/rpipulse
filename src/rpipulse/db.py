from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = Path("data/rpipulse.sqlite")
DAY_MS = 86_400_000
HOUR_MS = 3_600_000


def _ensure_parent_dir(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    _ensure_parent_dir(db_path)
    return sqlite3.connect(db_path)


def _to_epoch_ms(ts_ms: int | str) -> int:
    if isinstance(ts_ms, int):
        return ts_ms

    candidate = ts_ms.replace("Z", "+00:00")
    dt = datetime.fromisoformat(candidate)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return int(dt.timestamp() * 1000)


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS observations (
                ts_ms INTEGER NOT NULL,
                unique_devices_count INTEGER NOT NULL,
                raw_count INTEGER
            )
            """
        )

        columns = {row[1] for row in conn.execute("PRAGMA table_info(observations)").fetchall()}

        if "ts_ms" not in columns:
            conn.execute("ALTER TABLE observations ADD COLUMN ts_ms INTEGER")
            columns.add("ts_ms")

        if "ts" in columns:
            conn.execute(
                """
                UPDATE observations
                SET ts_ms = CAST(strftime('%s', ts) AS INTEGER) * 1000
                WHERE ts_ms IS NULL AND ts IS NOT NULL
                """
            )

        conn.execute("UPDATE observations SET ts_ms = 0 WHERE ts_ms IS NULL")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_observations_ts_ms ON observations(ts_ms)")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_observations_day_start_ms
            ON observations(((ts_ms / 86400000) * 86400000))
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_observations_hour_start_ms
            ON observations(((ts_ms / 3600000) * 3600000))
            """
        )
        conn.commit()


def insert_observation(
    ts_ms: int | str,
    unique_devices_count: int,
    raw_count: Optional[int] = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO observations (ts_ms, unique_devices_count, raw_count)
            VALUES (?, ?, ?)
            """,
            (_to_epoch_ms(ts_ms), unique_devices_count, raw_count),
        )
        conn.commit()


def stats_for_day(day: str, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Optional[float]]:
    init_db(db_path)
    day_start = datetime.combine(date.fromisoformat(day), datetime.min.time(), tzinfo=timezone.utc)
    start_ms = int(day_start.timestamp() * 1000)
    end_ms = start_ms + DAY_MS

    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS observations,
                COALESCE(SUM(unique_devices_count), 0) AS total_unique,
                COALESCE(AVG(unique_devices_count), 0) AS avg_unique,
                COALESCE(MAX(unique_devices_count), 0) AS max_unique,
                COALESCE(MIN(unique_devices_count), 0) AS min_unique
            FROM observations
            WHERE ts_ms >= ? AND ts_ms < ?
            """,
            (start_ms, end_ms),
        ).fetchone()

    return {
        "observations": int(row[0]),
        "total_unique": int(row[1]),
        "avg_unique": float(row[2]),
        "max_unique": int(row[3]),
        "min_unique": int(row[4]),
    }


def daily_peak_and_avg(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, float | str | int]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                ((ts_ms / 86400000) * 86400000) AS day_start_ms,
                COALESCE(MAX(unique_devices_count), 0) AS peak_unique,
                COALESCE(AVG(unique_devices_count), 0) AS avg_unique,
                COALESCE(MAX(raw_count), 0) AS peak_raw,
                COALESCE(AVG(raw_count), 0) AS avg_raw
            FROM observations
            GROUP BY ((ts_ms / 86400000) * 86400000)
            ORDER BY day_start_ms
            """
        ).fetchall()

    return [
        {
            "day": datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
            "day_start_ms": int(row[0]),
            "peak_unique": int(row[1]),
            "avg_unique": float(row[2]),
            "peak_raw": int(row[3]),
            "avg_raw": float(row[4]),
        }
        for row in rows
    ]


def hourly_peak_and_avg(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, float | str | int]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                ((ts_ms / 3600000) * 3600000) AS hour_start_ms,
                COALESCE(MAX(unique_devices_count), 0) AS peak_unique,
                COALESCE(AVG(unique_devices_count), 0) AS avg_unique,
                COALESCE(MAX(raw_count), 0) AS peak_raw,
                COALESCE(AVG(raw_count), 0) AS avg_raw
            FROM observations
            GROUP BY ((ts_ms / 3600000) * 3600000)
            ORDER BY hour_start_ms
            """
        ).fetchall()

    return [
        {
            "hour": datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:00"),
            "hour_start_ms": int(row[0]),
            "peak_unique": int(row[1]),
            "avg_unique": float(row[2]),
            "peak_raw": int(row[3]),
            "avg_raw": float(row[4]),
        }
        for row in rows
    ]
