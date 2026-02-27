from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = Path("data/analizador.sqlite")


def _ensure_parent_dir(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    _ensure_parent_dir(db_path)
    return sqlite3.connect(db_path)


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS observations (
                ts TEXT NOT NULL,
                unique_devices_count INTEGER NOT NULL,
                raw_count INTEGER
            )
            """
        )
        conn.commit()


def insert_observation(
    ts: str,
    unique_devices_count: int,
    raw_count: Optional[int] = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO observations (ts, unique_devices_count, raw_count)
            VALUES (?, ?, ?)
            """,
            (ts, unique_devices_count, raw_count),
        )
        conn.commit()


def stats_for_day(day: str, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Optional[float]]:
    init_db(db_path)
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
            WHERE date(ts) = ?
            """,
            (day,),
        ).fetchone()

    return {
        "observations": int(row[0]),
        "total_unique": int(row[1]),
        "avg_unique": float(row[2]),
        "max_unique": int(row[3]),
        "min_unique": int(row[4]),
    }
