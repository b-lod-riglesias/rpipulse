import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from rpipulse.db import (
    DAY_MS,
    anonymize_device_id,
    daily_peak_and_avg,
    get_sensor_config,
    hourly_peak_and_avg,
    init_db,
    insert_observation,
    latest_detections,
    purge_old_data,
    update_sensor_config,
)


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


def test_observations_rebuild_migration_keeps_backup_and_adds_pk(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE observations (
                ts_ms INTEGER,
                unique_devices_count INTEGER,
                raw_count INTEGER
            )
            """
        )
        conn.executemany(
            "INSERT INTO observations (ts_ms, unique_devices_count, raw_count) VALUES (?, ?, ?)",
            [
                (_ms("2026-02-27T10:00:00"), 2, 3),
                (_ms("2026-02-27T09:00:00"), 1, 1),
            ],
        )
        conn.commit()

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "observations_old" in tables

        info = conn.execute("PRAGMA table_info(observations)").fetchall()
        id_row = next(row for row in info if row[1] == "id")
        assert id_row[5] == 1

        rows = conn.execute("SELECT id, ts_ms FROM observations ORDER BY id").fetchall()
        assert len(rows) == 2
        assert rows[0][1] < rows[1][1]


def test_anonymize_device_id_is_deterministic_normalized_and_transport_scoped() -> None:
    secret = b"local-secret"
    first = anonymize_device_id("AA:BB:CC:DD:EE:FF", secret, transport="ble")
    second = anonymize_device_id("aa:bb:cc:dd:ee:ff", secret, transport="ble")
    classic = anonymize_device_id("AA:BB:CC:DD:EE:FF", secret, transport="classic")

    assert first == second
    assert first != classic
    assert len(first) == 64
    assert first.upper() == first


def test_detections_are_persisted_without_plaintext_mac(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "detections.sqlite"
    monkeypatch.setenv("RPIPULSE_ANON_SECRET", "test-secret")
    init_db(db_path)

    insert_observation(
        _ms("2026-02-27T10:00:00"),
        unique_devices_count=1,
        raw_count=2,
        db_path=db_path,
        detections=[
            {
                "address": "AA:BB:CC:DD:EE:FF",
                "transport": "ble",
                "rssi_dbm": -55,
                "seen_count": 2,
                "first_offset_ms": 120,
                "last_offset_ms": 480,
            }
        ],
    )

    detections = latest_detections(seconds=365 * 24 * 3600, db_path=db_path)
    assert len(detections) == 1
    assert detections[0]["rssi_dbm"] == -55
    assert detections[0]["seen_count"] == 2
    assert detections[0]["first_offset_ms"] == 120
    assert detections[0]["last_offset_ms"] == 480

    expected_anon = anonymize_device_id("AA:BB:CC:DD:EE:FF", "test-secret", transport="ble")

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT anon_device_id, transport FROM detections").fetchone()
    assert row is not None
    assert row[0] == expected_anon
    assert row[0] != "AA:BB:CC:DD:EE:FF"
    assert row[1] == "ble"


def test_sensor_config_persistence_schema_and_batch_purge(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "config.sqlite"
    monkeypatch.delenv("RPIPULSE_ANON_SECRET", raising=False)
    init_db(db_path)

    defaults = get_sensor_config(db_path=db_path)
    assert defaults["duration"] == 15
    assert defaults["interval"] == 60

    updated = update_sensor_config(
        {
            "duration": 20,
            "interval": 90,
            "rssi_threshold": -85,
            "retention_days": 2,
        },
        db_path=db_path,
    )
    assert updated["duration"] == 20
    assert updated["interval"] == 90
    assert updated["rssi_threshold"] == -85
    assert updated["retention_days"] == 2

    now_ms = _ms("2026-02-27T10:00:00")
    for idx in range(5):
        insert_observation(now_ms - ((4 + idx) * DAY_MS), 1, 1, db_path=db_path)

    purge_stats = purge_old_data(db_path=db_path, now_ms=now_ms, batch_size=2)
    assert purge_stats["retention_days"] == 2
    assert purge_stats["deleted_observations"] == 5

    with sqlite3.connect(db_path) as conn:
        settings_cols = {row[1] for row in conn.execute("PRAGMA table_info(settings)").fetchall()}
        assert settings_cols == {"key", "value", "updated_at_ms"}


def test_anon_secret_file_created_with_0600_when_env_missing(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "secret.sqlite"
    monkeypatch.delenv("RPIPULSE_ANON_SECRET", raising=False)

    init_db(db_path)
    insert_observation(_ms("2026-02-27T10:00:00"), 1, 1, db_path=db_path)

    secret_path = db_path.parent / ".anon_secret"
    assert secret_path.exists()
    mode = secret_path.stat().st_mode & 0o777
    assert mode == 0o600
    assert secret_path.read_text(encoding="utf-8").strip()

    # keep import used in this module when running isolated tests
    assert os.path.exists(secret_path)
