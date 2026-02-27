import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from rpipulse.db import daily_peak_and_avg, hourly_peak_and_avg, init_db, insert_observation


def _ms(iso_ts: str) -> int:
    dt = datetime.fromisoformat(iso_ts).replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def test_daily_and_hourly_peak_and_avg(tmp_path: Path) -> None:
    db_path = tmp_path / "test.sqlite"
    init_db(db_path)

    insert_observation(_ms("2026-02-27T10:00:00"), 3, 5, db_path=db_path)
    insert_observation(_ms("2026-02-27T10:30:00"), 1, 2, db_path=db_path)
    insert_observation(_ms("2026-02-27T11:00:00"), 9, 9, db_path=db_path)
    insert_observation(_ms("2026-02-26T10:00:00"), 4, 4, db_path=db_path)

    daily = daily_peak_and_avg(db_path=db_path)
    assert daily == [
        {
            "day": "2026-02-26",
            "day_start_ms": _ms("2026-02-26T00:00:00"),
            "peak_unique": 4,
            "avg_unique": 4.0,
            "peak_raw": 4,
            "avg_raw": 4.0,
        },
        {
            "day": "2026-02-27",
            "day_start_ms": _ms("2026-02-27T00:00:00"),
            "peak_unique": 9,
            "avg_unique": 4.333333333333333,
            "peak_raw": 9,
            "avg_raw": 5.333333333333333,
        },
    ]

    hourly = hourly_peak_and_avg(db_path=db_path)
    assert hourly == [
        {
            "hour": "2026-02-26 10:00",
            "hour_start_ms": _ms("2026-02-26T10:00:00"),
            "peak_unique": 4,
            "avg_unique": 4.0,
            "peak_raw": 4,
            "avg_raw": 4.0,
        },
        {
            "hour": "2026-02-27 10:00",
            "hour_start_ms": _ms("2026-02-27T10:00:00"),
            "peak_unique": 3,
            "avg_unique": 2.0,
            "peak_raw": 5,
            "avg_raw": 3.5,
        },
        {
            "hour": "2026-02-27 11:00",
            "hour_start_ms": _ms("2026-02-27T11:00:00"),
            "peak_unique": 9,
            "avg_unique": 9.0,
            "peak_raw": 9,
            "avg_raw": 9.0,
        },
    ]


def test_legacy_ts_column_is_migrated(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE observations (
                ts TEXT NOT NULL,
                unique_devices_count INTEGER NOT NULL,
                raw_count INTEGER
            )
            """
        )
        conn.execute(
            "INSERT INTO observations (ts, unique_devices_count, raw_count) VALUES (?, ?, ?)",
            ("2026-02-27T10:00:00", 6, 7),
        )
        conn.commit()

    rows = daily_peak_and_avg(db_path=db_path)
    assert rows[0]["day"] == "2026-02-27"
    assert rows[0]["peak_unique"] == 6
    assert rows[0]["avg_unique"] == 6.0
    assert rows[0]["peak_raw"] == 7
    assert rows[0]["avg_raw"] == 7.0
