from pathlib import Path

from rpipulse import cli


def test_scan_stores_observation_and_prints_daily_hourly_stats(monkeypatch, tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "cli.sqlite"
    monkeypatch.setenv("RPIPULSE_DB_PATH", str(db_path))

    class DummyResult:
        unique_count = 7
        raw_count = 8
        backend = "dummy"

    monkeypatch.setattr(cli, "scan_devices", lambda duration, rssi_threshold=None: DummyResult())

    code = cli.main(["scan", "--duration", "15"])
    out_scan = capsys.readouterr().out

    assert code == 0
    assert "backend=dummy" in out_scan
    assert "unique_devices=7" in out_scan
    assert "raw_count=8" in out_scan
    assert "detections=0" in out_scan
    assert "saved_at_ms=" in out_scan

    daily_code = cli.main(["stats", "--group", "daily"])
    out_daily = capsys.readouterr().out
    assert daily_code == 0
    assert "daily day=" in out_daily
    assert "peak_unique=7" in out_daily
    assert "avg_unique=7.00" in out_daily
    assert "peak_raw=8" in out_daily
    assert "avg_raw=8.00" in out_daily

    hourly_code = cli.main(["stats", "--group", "hourly"])
    out_hourly = capsys.readouterr().out
    assert hourly_code == 0
    assert "hourly hour=" in out_hourly
    assert "peak_unique=7" in out_hourly
    assert "avg_unique=7.00" in out_hourly
    assert "peak_raw=8" in out_hourly
    assert "avg_raw=8.00" in out_hourly


def test_scan_duration_validation() -> None:
    try:
        cli.main(["scan", "--duration", "0"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected SystemExit for invalid duration")


def test_config_and_purge_commands(monkeypatch, tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "cli.sqlite"
    monkeypatch.setenv("RPIPULSE_DB_PATH", str(db_path))

    set_code = cli.main(
        [
            "config",
            "set",
            "--duration",
            "20",
            "--interval",
            "90",
            "--rssi-threshold",
            "-80",
            "--retention-days",
            "10",
        ]
    )
    set_out = capsys.readouterr().out
    assert set_code == 0
    assert "config_updated" in set_out
    assert "duration=20" in set_out
    assert "retention_days=10" in set_out

    get_code = cli.main(["config", "get"])
    get_out = capsys.readouterr().out
    assert get_code == 0
    assert "config duration=20 interval=90 rssi_threshold=-80 retention_days=10" in get_out

    purge_code = cli.main(["purge", "--retention-days", "7"])
    purge_out = capsys.readouterr().out
    assert purge_code == 0
    assert "purge retention_days=7" in purge_out
