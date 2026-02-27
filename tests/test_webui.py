from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from rpipulse.db import init_db, insert_observation
from rpipulse.webui.app import create_app


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _seed(db_path: Path) -> None:
    now = datetime.now(tz=timezone.utc)
    init_db(db_path)
    insert_observation(_ms(now - timedelta(hours=2)), unique_devices_count=3, raw_count=5, db_path=db_path)
    insert_observation(_ms(now - timedelta(hours=1)), unique_devices_count=6, raw_count=8, db_path=db_path)
    insert_observation(_ms(now), unique_devices_count=9, raw_count=12, db_path=db_path)


def test_webui_root_and_health(tmp_path: Path) -> None:
    db_path = tmp_path / "ui.sqlite"
    _seed(db_path)

    client = TestClient(create_app(db_path=db_path))

    index = client.get("/")
    assert index.status_code == 200
    assert "RPIpulse" in index.text

    health = client.get("/api/health")
    assert health.status_code == 200
    payload = health.json()
    assert payload["status"] == "ok"
    assert payload["db_path"] == str(db_path)
    assert payload["observations"] == 3


def test_webui_data_endpoints(tmp_path: Path) -> None:
    db_path = tmp_path / "ui.sqlite"
    _seed(db_path)

    client = TestClient(create_app(db_path=db_path))

    latest = client.get("/api/observations/latest")
    assert latest.status_code == 200
    latest_payload = latest.json()["observation"]
    assert latest_payload["unique_devices_count"] == 9
    assert latest_payload["raw_count"] == 12

    daily = client.get("/api/series/daily", params={"days": 30})
    assert daily.status_code == 200
    assert daily.json()["series"]

    hourly = client.get("/api/series/hourly", params={"days": 7})
    assert hourly.status_code == 200
    assert len(hourly.json()["series"]) >= 1

    kpis = client.get("/api/kpis/now")
    assert kpis.status_code == 200
    kpis_payload = kpis.json()
    assert kpis_payload["latest"]["unique_devices_count"] == 9
    assert kpis_payload["last_24h"]["samples"] == 3
    assert "delta_pct" in kpis_payload["trend"]
