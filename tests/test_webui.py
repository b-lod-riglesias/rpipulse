from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from starlette.requests import Request

from rpipulse.db import init_db, insert_observation
from rpipulse.webui.app import create_app


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _seed(db_path: Path) -> None:
    now = datetime.now(tz=timezone.utc)
    init_db(db_path)
    insert_observation(
        _ms(now - timedelta(hours=2)),
        unique_devices_count=3,
        raw_count=5,
        db_path=db_path,
        detections=[{"address": "AA:AA:AA:AA:AA:01", "rssi": -70, "seen_count": 2, "offsets_ms": [100, 200]}],
    )
    insert_observation(
        _ms(now - timedelta(hours=1)),
        unique_devices_count=6,
        raw_count=8,
        db_path=db_path,
        detections=[{"address": "AA:AA:AA:AA:AA:02", "rssi": -60, "seen_count": 1, "offsets_ms": [300]}],
    )
    insert_observation(
        _ms(now),
        unique_devices_count=9,
        raw_count=12,
        db_path=db_path,
        detections=[
            {"address": "AA:AA:AA:AA:AA:01", "rssi": -50, "seen_count": 3, "offsets_ms": [120, 240, 360]},
            {"address": "AA:AA:AA:AA:AA:03", "rssi": -65, "seen_count": 1, "offsets_ms": [90]},
        ],
    )


def _route(app, path: str, method: str = "GET"):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", {method}):
            return route
    raise AssertionError(f"Route not found: {path}")


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "client": ("test", 123),
            "server": ("test", 80),
            "scheme": "http",
            "http_version": "1.1",
        }
    )


def test_webui_routes_and_redirect(tmp_path: Path) -> None:
    db_path = tmp_path / "ui.sqlite"
    _seed(db_path)
    app = create_app(db_path=db_path)

    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/" in paths
    assert "/dashboard" in paths
    assert "/trends" in paths
    assert "/monitor" in paths
    assert "/sensors" in paths

    root_response = _route(app, "/").endpoint()
    assert root_response.status_code == 302
    assert root_response.headers["location"] == "/dashboard"

    dashboard_response = _route(app, "/dashboard").endpoint(_request("/dashboard"))
    assert dashboard_response.status_code == 200
    assert dashboard_response.template.name == "dashboard.html"

    trends_response = _route(app, "/trends").endpoint(_request("/trends"))
    assert trends_response.status_code == 200
    assert trends_response.template.name == "trends.html"

    monitor_response = _route(app, "/monitor").endpoint(_request("/monitor"))
    assert monitor_response.status_code == 200
    assert monitor_response.template.name == "monitor.html"

    sensors_response = _route(app, "/sensors").endpoint(_request("/sensors"))
    assert sensors_response.status_code == 200
    assert sensors_response.template.name == "sensors.html"


def test_webui_data_endpoints(tmp_path: Path) -> None:
    db_path = tmp_path / "ui.sqlite"
    _seed(db_path)
    app = create_app(db_path=db_path)

    latest_payload = _route(app, "/api/observations/latest").endpoint(limit=2)
    assert len(latest_payload["items"]) == 2
    assert latest_payload["items"][0]["unique_devices_count"] == 9

    daily_payload = _route(app, "/api/series/daily").endpoint()
    assert daily_payload["items"]

    hourly_payload = _route(app, "/api/series/hourly").endpoint()
    assert hourly_payload["items"]

    kpis_payload = _route(app, "/api/kpis/now").endpoint()
    assert kpis_payload["observation"]["unique_devices_count"] == 9
    assert "status" in kpis_payload
    assert "capacity" in kpis_payload


def test_webui_detection_and_config_endpoints(tmp_path: Path) -> None:
    db_path = tmp_path / "ui.sqlite"
    _seed(db_path)
    app = create_app(db_path=db_path)
    recent_payload = _route(app, "/api/detections/recent").endpoint(seconds=24 * 3600, limit=20)
    assert len(recent_payload["items"]) >= 3
    assert all("anon_device_id" in item for item in recent_payload["items"])

    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    top_payload = _route(app, "/api/detections/top").endpoint(from_ms=now_ms - (24 * 3600 * 1000), to_ms=now_ms, limit=20)
    assert top_payload["items"]
    assert top_payload["items"][0]["observations"] >= 1

    config_before = _route(app, "/api/config", method="GET").endpoint()
    assert config_before["config"]["duration"] == 15

    config_after = _route(app, "/api/config", method="PUT").endpoint(payload={"duration": 21, "retention_days": 11})
    assert config_after["config"]["duration"] == 21
    assert config_after["config"]["retention_days"] == 11
