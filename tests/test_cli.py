from pathlib import Path

from analizador import cli


def test_scan_stores_observation(monkeypatch, tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "cli.sqlite"
    monkeypatch.setenv("ANALIZADOR_DB_PATH", str(db_path))

    class DummyResult:
        unique_count = 7
        raw_count = 8
        backend = "dummy"

    monkeypatch.setattr(cli, "scan_devices", lambda duration: DummyResult())

    code = cli.main(["scan", "--duration", "15"])
    out = capsys.readouterr().out

    assert code == 0
    assert "backend=dummy" in out

    stats_code = cli.main(["stats", "--day", "today"])
    out2 = capsys.readouterr().out
    assert stats_code == 0
    assert "observations=1" in out2


def test_scan_duration_validation() -> None:
    try:
        cli.main(["scan", "--duration", "0"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected SystemExit for invalid duration")
