from __future__ import annotations

import importlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from starlette.requests import Request

from rpipulse.db import create_node, init_db, insert_observation
from rpipulse.webui.app import create_app

webui_app_module = importlib.import_module("rpipulse.webui.app")


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


def test_bootstrap_raspberry_creates_node_and_executable(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "ui.sqlite"
    _seed(db_path)
    app = create_app(db_path=db_path)
    route = _route(app, "/api/admin/raspberries/bootstrap", method="POST")

    monkeypatch.setenv("RPIPULSE_ADMIN_TOKEN", "secret-token")
    generated_dir = tmp_path / "generated"
    tool_path = tmp_path / "add_raspberry.sh"
    tool_path.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    tool_path.chmod(0o755)
    monkeypatch.setattr(webui_app_module, "SYNC_SCRIPTS_DIR", generated_dir)
    monkeypatch.setattr(webui_app_module, "ADD_RASPBERRY_TOOL", tool_path)

    payload = route.endpoint(
        payload={
            "name": "Salon Norte",
            "host": "192.168.1.50",
            "bootstrap_user": "pi",
            "ssh_port": 22,
            "http_port": 8000,
        },
        token="secret-token",
    )

    assert payload["node"]["id"] == "salon-norte"
    assert payload["node"]["name"] == "Salon Norte"
    assert payload["node"]["host"] == "192.168.1.50"
    assert payload["node"]["port"] == 8000
    assert payload["node"]["user"] == "rpipulse"

    script_path = Path(payload["bootstrap"]["script_path"])
    assert script_path.exists()
    assert os.access(script_path, os.X_OK)
    script_body = script_path.read_text(encoding="utf-8")
    assert str(tool_path) in script_body
    assert "--host 192.168.1.50" in script_body
    assert "--user pi" in script_body
    assert "--name 'Salon Norte'" in script_body


def test_node_kpis_proxy_allows_without_token(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "ui.sqlite"
    _seed(db_path)
    create_node("n1", "Node 1", "10.0.0.2", 8000, "rpipulse", enabled=True, db_path=db_path)
    app = create_app(db_path=db_path)
    route = _route(app, "/api/nodes/{node_id}/kpis/now")

    # No token required for read-only KPI proxy endpoints
    monkeypatch.delenv("RPIPULSE_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(webui_app_module, "_fetch_node_json", lambda **kwargs: {"ok": True, "node": kwargs["node"]["id"]})

    data = route.endpoint(node_id="n1", token=None)
    assert data["ok"] is True
    assert data["node"] == "n1"
def test_node_kpis_proxy_rejects_wrong_token_when_configured(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "ui.sqlite"
    _seed(db_path)
    create_node("n1", "Node 1", "10.0.0.2", 8000, "rpipulse", enabled=True, db_path=db_path)
    app = create_app(db_path=db_path)
    route = _route(app, "/api/nodes/{node_id}/kpis/now")

    monkeypatch.setenv("RPIPULSE_ADMIN_TOKEN", "secret-token")
    monkeypatch.setattr(webui_app_module, "_fetch_node_json", lambda **kwargs: {"status": "ok"})

    # Wrong token should be rejected
    try:
        route.endpoint(node_id="n1", token="wrong")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 401
    else:
        raise AssertionError("Expected HTTPException 401")

    # Missing token should still be allowed for read-only proxy
    assert route.endpoint(node_id="n1", token=None) == {"status": "ok"}
def test_node_observations_proxy_forwards_limit(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "ui.sqlite"
    _seed(db_path)
    create_node("n1", "Node 1", "10.0.0.2", 8000, "rpipulse", enabled=True, db_path=db_path)
    app = create_app(db_path=db_path)
    route = _route(app, "/api/nodes/{node_id}/observations/recent")

    monkeypatch.delenv("RPIPULSE_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("RPIPULSE_ENABLE_TERMINAL", "1")
    calls: list[dict] = []

    def _fake_fetch(**kwargs):
        calls.append(kwargs)
        return {"items": []}

    monkeypatch.setattr(webui_app_module, "_fetch_node_json", _fake_fetch)

    response = route.endpoint(node_id="n1", token=None, limit=7)
    assert response == {"items": []}
    assert calls[0]["remote_path"] == "/api/observations/recent"
    assert calls[0]["query_params"] == {"limit": 7}


def test_node_proxy_rejects_disabled_node(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "ui.sqlite"
    _seed(db_path)
    create_node("n1", "Node 1", "10.0.0.2", 8000, "rpipulse", enabled=False, db_path=db_path)
    app = create_app(db_path=db_path)
    route = _route(app, "/api/nodes/{node_id}/kpis/now")

    monkeypatch.delenv("RPIPULSE_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("RPIPULSE_ENABLE_TERMINAL", "1")

    try:
        route.endpoint(node_id="n1", token=None)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 403
    else:
        raise AssertionError("Expected HTTPException 403")


def test_node_proxy_series_and_detections_forward_query_params(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "ui.sqlite"
    _seed(db_path)
    create_node("n1", "Node 1", "10.0.0.2", 8000, "rpipulse", enabled=True, db_path=db_path)
    app = create_app(db_path=db_path)

    monkeypatch.delenv("RPIPULSE_ADMIN_TOKEN", raising=False)
    calls: list[dict] = []

    def _fake_fetch(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(webui_app_module, "_fetch_node_json", _fake_fetch)

    _route(app, "/api/nodes/{node_id}/series/hourly").endpoint(node_id="n1", token=None)
    _route(app, "/api/nodes/{node_id}/series/daily").endpoint(node_id="n1", token=None)
    _route(app, "/api/nodes/{node_id}/detections/recent").endpoint(
        node_id="n1", token=None, seconds=120, limit=9
    )
    _route(app, "/api/nodes/{node_id}/detections/top").endpoint(
        node_id="n1", token=None, from_ms=1000, to_ms=5000, limit=11
    )
    _route(app, "/api/nodes/{node_id}/config").endpoint(node_id="n1", token=None)

    assert [call["remote_path"] for call in calls] == [
        "/api/series/hourly",
        "/api/series/daily",
        "/api/detections/recent",
        "/api/detections/top",
        "/api/config",
    ]
    assert calls[2]["query_params"] == {"seconds": 120, "limit": 9}
    assert calls[3]["query_params"] == {"from_ms": 1000, "to_ms": 5000, "limit": 11}


def test_node_config_put_requires_admin_token_and_passthrough_body(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "ui.sqlite"
    _seed(db_path)
    create_node("n1", "Node 1", "10.0.0.2", 8000, "rpipulse", enabled=True, db_path=db_path)
    app = create_app(db_path=db_path)
    route = _route(app, "/api/nodes/{node_id}/config", method="PUT")

    monkeypatch.setenv("RPIPULSE_ADMIN_TOKEN", "secret-token")
    calls: list[dict] = []

    def _fake_put(**kwargs):
        calls.append(kwargs)
        return {"config": kwargs["payload"]}

    monkeypatch.setattr(webui_app_module, "_put_node_json", _fake_put)

    try:
        route.endpoint(node_id="n1", token="wrong", payload={"duration": 25})
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 401
    else:
        raise AssertionError("Expected HTTPException 401")

    response = route.endpoint(node_id="n1", token="secret-token", payload={"duration": 25, "interval": 2})
    assert response == {"config": {"duration": 25, "interval": 2}}
    assert calls[0]["remote_path"] == "/api/config"
    assert calls[0]["payload"] == {"duration": 25, "interval": 2}


def test_aggregate_kpis_sum_mode_and_breakdown(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "ui.sqlite"
    _seed(db_path)
    create_node("n1", "Node 1", "10.0.0.2", 8000, "rpipulse", enabled=True, db_path=db_path)
    create_node("n2", "Node 2", "10.0.0.3", 8000, "rpipulse", enabled=True, db_path=db_path)
    create_node("n3", "Node 3", "10.0.0.4", 8000, "rpipulse", enabled=False, db_path=db_path)
    app = create_app(db_path=db_path)

    calls: list[dict] = []

    def _fake_fetch(**kwargs):
        calls.append(kwargs)
        node_id = kwargs["node"]["id"]
        if kwargs["remote_path"] == "/api/kpis/now" and node_id == "n1":
            return {
                "observation": {"ts_ms": 1000, "unique_devices_count": 5, "raw_count": 20},
                "capacity": 40,
                "status": "quiet",
            }
        if kwargs["remote_path"] == "/api/kpis/now" and node_id == "n2":
            return {
                "observation": {"ts_ms": 1500, "unique_devices_count": 7, "raw_count": 30},
                "capacity": 60,
                "status": "packed",
            }
        raise AssertionError(f"Unexpected fetch call: {kwargs}")

    monkeypatch.setattr(webui_app_module, "_fetch_node_json", _fake_fetch)

    route = _route(app, "/api/aggregate/kpis/now")
    payload = route.endpoint(nodes="n1,n2")

    assert payload["capacity"] == 100
    assert payload["observation"]["unique_devices_count"] == 12
    assert payload["observation"]["raw_count"] == 50
    assert payload["observation"]["load_pct"] == 50.0
    assert payload["status"] == "mixed"
    assert payload["unique"] == {"mode": "sum", "value": 12}
    assert len(payload["breakdown"]) == 2
    assert payload["breakdown"][0]["node_id"] == "n1"
    assert payload["breakdown"][1]["node_id"] == "n2"

    # nodes omitted => all enabled nodes only
    calls.clear()
    all_payload = route.endpoint(nodes=None)
    assert all_payload["node_count"] == 2
    assert sorted(call["node"]["id"] for call in calls) == ["n1", "n2"]


def test_aggregate_series_and_detections_merge(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "ui.sqlite"
    _seed(db_path)
    create_node("n1", "Node 1", "10.0.0.2", 8000, "rpipulse", enabled=True, db_path=db_path)
    create_node("n2", "Node 2", "10.0.0.3", 8000, "rpipulse", enabled=True, db_path=db_path)
    app = create_app(db_path=db_path)

    calls: list[dict] = []

    def _fake_fetch(**kwargs):
        calls.append(kwargs)
        node_id = kwargs["node"]["id"]
        path = kwargs["remote_path"]
        if path == "/api/series/hourly":
            if node_id == "n1":
                return {
                    "items": [
                        {"hour": "2026-03-03 10:00", "hour_start_ms": 10, "peak_unique": 3, "avg_unique": 2.0, "peak_raw": 5, "avg_raw": 4.0},
                        {"hour": "2026-03-03 11:00", "hour_start_ms": 11, "peak_unique": 4, "avg_unique": 3.0, "peak_raw": 6, "avg_raw": 5.0},
                    ]
                }
            return {
                "items": [
                    {"hour": "2026-03-03 11:00", "hour_start_ms": 11, "peak_unique": 7, "avg_unique": 1.0, "peak_raw": 8, "avg_raw": 2.0},
                ]
            }
        if path == "/api/series/daily":
            if node_id == "n1":
                return {
                    "items": [
                        {"day": "2026-03-03", "day_start_ms": 100, "peak_unique": 8, "avg_unique": 3.5, "peak_raw": 12, "avg_raw": 7.0}
                    ]
                }
            return {
                "items": [
                    {"day": "2026-03-03", "day_start_ms": 100, "peak_unique": 2, "avg_unique": 1.5, "peak_raw": 3, "avg_raw": 2.0}
                ]
            }
        if path == "/api/detections/recent":
            if node_id == "n1":
                return {
                    "items": [
                        {"created_at_ms": 3000, "anon_device_id": "d1", "transport": "wifi", "seen_count": 1},
                    ]
                }
            return {
                "items": [
                    {"created_at_ms": 4000, "anon_device_id": "d2", "transport": "ble", "seen_count": 2},
                ]
            }
        if path == "/api/detections/top":
            if node_id == "n1":
                return {
                    "items": [
                        {
                            "anon_device_id": "x",
                            "transport": "wifi",
                            "observations": 2,
                            "seen_total": 5,
                            "avg_rssi_dbm": -60.0,
                            "max_rssi_dbm": -45,
                            "first_seen_ms": 100,
                            "last_seen_ms": 300,
                        }
                    ]
                }
            return {
                "items": [
                    {
                        "anon_device_id": "x",
                        "transport": "wifi",
                        "observations": 3,
                        "seen_total": 8,
                        "avg_rssi_dbm": -50.0,
                        "max_rssi_dbm": -40,
                        "first_seen_ms": 120,
                        "last_seen_ms": 350,
                    },
                    {
                        "anon_device_id": "y",
                        "transport": "ble",
                        "observations": 1,
                        "seen_total": 1,
                        "avg_rssi_dbm": -70.0,
                        "max_rssi_dbm": -70,
                        "first_seen_ms": 200,
                        "last_seen_ms": 210,
                    },
                ]
            }
        raise AssertionError(f"Unexpected fetch call: {kwargs}")

    monkeypatch.setattr(webui_app_module, "_fetch_node_json", _fake_fetch)

    hourly_payload = _route(app, "/api/aggregate/series/hourly").endpoint(nodes="n1,n2")
    assert len(hourly_payload["items"]) == 2
    assert hourly_payload["items"][1]["hour"] == "2026-03-03 11:00"
    assert hourly_payload["items"][1]["peak_unique"] == 11
    assert hourly_payload["items"][1]["avg_unique"] == 4.0
    assert hourly_payload["items"][1]["peak_raw"] == 14
    assert hourly_payload["items"][1]["avg_raw"] == 7.0

    daily_payload = _route(app, "/api/aggregate/series/daily").endpoint(nodes="n1,n2")
    assert daily_payload["items"][0]["peak_unique"] == 10
    assert daily_payload["items"][0]["avg_unique"] == 5.0
    assert daily_payload["items"][0]["peak_raw"] == 15
    assert daily_payload["items"][0]["avg_raw"] == 9.0

    recent_payload = _route(app, "/api/aggregate/detections/recent").endpoint(nodes="n1,n2", seconds=300, limit=5)
    assert [item["node_id"] for item in recent_payload["items"]] == ["n2", "n1"]
    assert calls[-2]["query_params"] == {"seconds": 300, "limit": 5}
    assert calls[-1]["query_params"] == {"seconds": 300, "limit": 5}

    top_payload = _route(app, "/api/aggregate/detections/top").endpoint(nodes="n1,n2", from_ms=1, to_ms=9999, limit=10)
    assert top_payload["items"][0]["anon_device_id"] == "x"
    assert top_payload["items"][0]["transport"] == "wifi"
    assert top_payload["items"][0]["observations"] == 5
    assert top_payload["items"][0]["seen_total"] == 13
    assert any(item["anon_device_id"] == "y" for item in top_payload["items"])

    top_calls = [
        call
        for call in calls
        if call["remote_path"] == "/api/detections/top"
    ]
    assert top_calls[0]["query_params"] == {"from_ms": 1, "to_ms": 9999, "limit": 10}
    assert top_calls[1]["query_params"] == {"from_ms": 1, "to_ms": 9999, "limit": 10}
