from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from rpipulse.db import (
    DAY_MS,
    DEFAULT_DB_PATH,
    daily_peak_and_avg,
    get_connection,
    get_sensor_config,
    hourly_peak_and_avg,
    init_db,
    latest_detections,
    latest_observation,
    latest_observations,
    purge_old_data,
    top_detections,
    update_sensor_config,
)

WEBUI_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEBUI_DIR / "templates"
STATIC_DIR = WEBUI_DIR / "static"


def resolve_db_path(override: Path | None = None) -> Path:
    if override is not None:
        return override
    env_path = os.environ.get("RPIPULSE_DB_PATH") or os.environ.get("ANALIZADOR_DB_PATH")
    return Path(env_path) if env_path else DEFAULT_DB_PATH


def _to_iso(ts_ms: int | None) -> str | None:
    if ts_ms is None:
        return None
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _with_iso(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    ts_ms = row.get("ts_ms")
    if ts_ms is None:
        ts_ms = row.get("created_at_ms")
    return {**row, "ts_iso": _to_iso(ts_ms)}


def _kpis_now(db_path: Path) -> dict[str, Any]:
    init_db(db_path)
    latest = latest_observation(db_path=db_path)
    hourly = hourly_peak_and_avg(db_path=db_path)
    latest_hourly = hourly[-1] if hourly else None

    capacity = 60
    if latest is None:
        return {
            "observation": None,
            "hourly": latest_hourly,
            "capacity": capacity,
            "status": "unknown",
            "latest": None,
            "last_24h": {"samples": 0, "avg_unique": 0.0, "peak_unique": 0, "avg_raw": 0.0, "peak_raw": 0},
            "trend": {"avg_last_60m": 0.0, "avg_prev_60m": 0.0, "delta_pct": None},
        }

    load_pct = round((latest["unique_devices_count"] / capacity) * 100, 1) if capacity else None
    if load_pct is None:
        status = "unknown"
    elif load_pct < 25:
        status = "quiet"
    elif load_pct < 60:
        status = "moderate"
    elif load_pct < 85:
        status = "busy"
    else:
        status = "packed"

    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    from_24h = now_ms - DAY_MS
    from_120m = now_ms - (2 * 3_600_000)
    from_60m = now_ms - 3_600_000

    with get_connection(db_path) as conn:
        row_24h = conn.execute(
            """
            SELECT
                COUNT(*) AS samples,
                COALESCE(AVG(unique_devices_count), 0) AS avg_unique,
                COALESCE(MAX(unique_devices_count), 0) AS peak_unique,
                COALESCE(AVG(raw_count), 0) AS avg_raw,
                COALESCE(MAX(raw_count), 0) AS peak_raw
            FROM observations
            WHERE ts_ms >= ?
            """,
            (from_24h,),
        ).fetchone()
        row_trend = conn.execute(
            """
            SELECT
                COALESCE(AVG(CASE WHEN ts_ms >= ? THEN unique_devices_count END), 0) AS avg_last_60m,
                COALESCE(AVG(CASE WHEN ts_ms >= ? AND ts_ms < ? THEN unique_devices_count END), 0) AS avg_prev_60m
            FROM observations
            WHERE ts_ms >= ?
            """,
            (from_60m, from_120m, from_60m, from_120m),
        ).fetchone()

    avg_last_60m = float(row_trend[0])
    avg_prev_60m = float(row_trend[1])
    delta_pct = None if avg_prev_60m == 0 else ((avg_last_60m - avg_prev_60m) / avg_prev_60m) * 100

    observation = _with_iso(latest)
    if observation is not None:
        observation["load_pct"] = load_pct

    return {
        "observation": observation,
        "hourly": latest_hourly,
        "capacity": capacity,
        "status": status,
        "latest": observation,
        "last_24h": {
            "samples": int(row_24h[0]),
            "avg_unique": float(row_24h[1]),
            "peak_unique": int(row_24h[2]),
            "avg_raw": float(row_24h[3]),
            "peak_raw": int(row_24h[4]),
        },
        "trend": {
            "avg_last_60m": avg_last_60m,
            "avg_prev_60m": avg_prev_60m,
            "delta_pct": delta_pct,
        },
    }


def _require_node_proxy_access(token: str | None) -> None:
    """Access gate for node-proxy endpoints.

    The dashboard needs to read KPIs from *registered* nodes without forcing an
    admin token (typical internal LAN use-case).

    If an admin token is configured, we still accept it, but we do NOT require
    it for these read-only proxy endpoints.

    Keep stronger auth requirements for truly sensitive features (e.g. terminal).
    """

    admin_token = os.environ.get("RPIPULSE_ADMIN_TOKEN", "")

    # If an admin token is configured and a token is provided but wrong, reject.
    if admin_token and token is not None and token != admin_token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # If token matches (or token omitted), allow read-only proxy.
    return


def _require_admin_token(token: str | None) -> None:
    """Require a valid admin token for mutating node-proxy endpoints."""

    admin_token = os.environ.get("RPIPULSE_ADMIN_TOKEN", "")
    if not admin_token or token != admin_token:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _get_enabled_node(node_id: str, db_path: Path) -> dict[str, Any]:
    from rpipulse.db import get_node

    node = get_node(node_id=node_id, db_path=db_path)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    if not node.get("enabled", False):
        raise HTTPException(status_code=403, detail="Node is disabled")
    return node


def _fetch_node_json(
    *,
    node: dict[str, Any],
    remote_path: str,
    query_params: dict[str, Any] | None = None,
    timeout_s: float = 2.5,
) -> Any:
    url = f"http://{node['host']}:{int(node['port'])}{remote_path}"
    if query_params:
        url = f"{url}?{urllib_parse.urlencode(query_params)}"
    request = urllib_request.Request(url=url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib_request.urlopen(request, timeout=timeout_s) as response:
            payload = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            return json.loads(payload.decode(charset))
    except urllib_error.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Remote node returned HTTP {exc.code}") from exc
    except (urllib_error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="Failed to fetch remote node payload") from exc


def _put_node_json(
    *,
    node: dict[str, Any],
    remote_path: str,
    payload: dict[str, Any],
    timeout_s: float = 2.5,
) -> Any:
    url = f"http://{node['host']}:{int(node['port'])}{remote_path}"
    body = json.dumps(payload).encode("utf-8")
    request = urllib_request.Request(
        url=url,
        data=body,
        method="PUT",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib_request.urlopen(request, timeout=timeout_s) as response:
            data = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            return json.loads(data.decode(charset))
    except urllib_error.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Remote node returned HTTP {exc.code}") from exc
    except (urllib_error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="Failed to update remote node payload") from exc


def create_app(db_path: Path | None = None) -> FastAPI:
    app = FastAPI(title="RPIpulse Web UI", version="1.0.0")
    app.state.db_path = resolve_db_path(db_path)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @app.on_event("startup")
    async def _on_startup() -> None:
        init_db(app.state.db_path)
        purge_old_data(db_path=app.state.db_path)

    @app.get("/", response_class=HTMLResponse)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/dashboard", status_code=302)

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "dashboard.html")

    @app.get("/trends", response_class=HTMLResponse)
    def trends(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "trends.html")

    @app.get("/monitor", response_class=HTMLResponse)
    def monitor(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "monitor.html")

    @app.get("/sensors", response_class=HTMLResponse)
    def sensors(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "sensors.html")
    @app.get("/terminal", response_class=HTMLResponse)
    def terminal_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "terminal.html")

    @app.get("/terminal/{node_id}", response_class=HTMLResponse)
    def terminal_node(request: Request, node_id: str) -> HTMLResponse:
        return templates.TemplateResponse(request, "terminal.html", {"node_id": node_id})

    @app.get("/api/nodes")
    def api_nodes() -> dict[str, Any]:
        """List all nodes (public endpoint for terminal UI)."""
        from rpipulse.db import get_all_nodes
        try:
            nodes = get_all_nodes(db_path=app.state.db_path)
            # Don't expose passwords
            for node in nodes:
                node.pop("password", None)
            return {"nodes": nodes}
        except Exception:
            # If nodes table doesn't exist yet, return empty list
            return {"nodes": []}



    @app.get("/api/observations/latest")
    def api_observations_latest(limit: int = Query(default=20, ge=1, le=500)) -> dict[str, Any]:
        items = [_with_iso(row) for row in latest_observations(limit=limit, db_path=app.state.db_path)]
        items = [row for row in items if row is not None]
        return {"items": items, "observation": items[0] if items else None}

    @app.get("/api/detections/recent")
    def api_detections_recent(
        seconds: int = Query(default=300, ge=1, le=86_400),
        limit: int = Query(default=200, ge=1, le=1_000),
    ) -> dict[str, Any]:
        items = [_with_iso(row) for row in latest_detections(seconds=seconds, limit=limit, db_path=app.state.db_path)]
        items = [row for row in items if row is not None]
        return {"items": items}

    @app.get("/api/detections/top")
    def api_detections_top(
        from_ms: int = Query(...),
        to_ms: int = Query(...),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        if to_ms < from_ms:
            raise HTTPException(status_code=400, detail="to_ms must be >= from_ms")
        items = top_detections(from_ms=from_ms, to_ms=to_ms, limit=limit, db_path=app.state.db_path)
        return {"items": items}

    @app.get("/api/config")
    def api_config_get() -> dict[str, Any]:
        return {"config": get_sensor_config(db_path=app.state.db_path)}

    @app.put("/api/config")
    def api_config_put(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            updates: dict[str, int] = {}
            for key in ("duration", "interval", "rssi_threshold", "retention_days"):
                if key in payload:
                    updates[key] = int(payload[key])
            config = update_sensor_config(updates=updates, db_path=app.state.db_path)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"config": config}

    @app.get("/api/series/daily")
    def api_daily_series() -> dict[str, Any]:
        items = daily_peak_and_avg(db_path=app.state.db_path)
        return {"items": items, "series": items}

    @app.get("/api/series/hourly")
    def api_hourly_series() -> dict[str, Any]:
        items = hourly_peak_and_avg(db_path=app.state.db_path)
        return {"items": items, "series": items}

    @app.get("/api/kpis/now")
    def api_kpis_now() -> dict[str, Any]:
        return _kpis_now(app.state.db_path)

    @app.get("/api/nodes/{node_id}/kpis/now")
    def api_node_kpis_now(
        node_id: str,
        token: str | None = Query(default=None),
    ) -> Any:
        _require_node_proxy_access(token)
        node = _get_enabled_node(node_id=node_id, db_path=app.state.db_path)
        return _fetch_node_json(node=node, remote_path="/api/kpis/now")

    @app.get("/api/nodes/{node_id}/observations/latest")
    def api_node_observations_latest(
        node_id: str,
        token: str | None = Query(default=None),
    ) -> Any:
        _require_node_proxy_access(token)
        node = _get_enabled_node(node_id=node_id, db_path=app.state.db_path)
        return _fetch_node_json(node=node, remote_path="/api/observations/latest")

    @app.get("/api/nodes/{node_id}/observations/recent")
    def api_node_observations_recent(
        node_id: str,
        token: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=500),
    ) -> Any:
        _require_node_proxy_access(token)
        node = _get_enabled_node(node_id=node_id, db_path=app.state.db_path)
        return _fetch_node_json(
            node=node,
            remote_path="/api/observations/recent",
            query_params={"limit": limit},
        )

    @app.get("/api/nodes/{node_id}/series/hourly")
    def api_node_series_hourly(
        node_id: str,
        token: str | None = Query(default=None),
    ) -> Any:
        _require_node_proxy_access(token)
        node = _get_enabled_node(node_id=node_id, db_path=app.state.db_path)
        return _fetch_node_json(node=node, remote_path="/api/series/hourly")

    @app.get("/api/nodes/{node_id}/series/daily")
    def api_node_series_daily(
        node_id: str,
        token: str | None = Query(default=None),
    ) -> Any:
        _require_node_proxy_access(token)
        node = _get_enabled_node(node_id=node_id, db_path=app.state.db_path)
        return _fetch_node_json(node=node, remote_path="/api/series/daily")

    @app.get("/api/nodes/{node_id}/detections/recent")
    def api_node_detections_recent(
        node_id: str,
        token: str | None = Query(default=None),
        seconds: int = Query(default=300, ge=1, le=86_400),
        limit: int = Query(default=200, ge=1, le=1_000),
    ) -> Any:
        _require_node_proxy_access(token)
        node = _get_enabled_node(node_id=node_id, db_path=app.state.db_path)
        return _fetch_node_json(
            node=node,
            remote_path="/api/detections/recent",
            query_params={"seconds": seconds, "limit": limit},
        )

    @app.get("/api/nodes/{node_id}/detections/top")
    def api_node_detections_top(
        node_id: str,
        token: str | None = Query(default=None),
        from_ms: int = Query(...),
        to_ms: int = Query(...),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> Any:
        _require_node_proxy_access(token)
        if to_ms < from_ms:
            raise HTTPException(status_code=400, detail="to_ms must be >= from_ms")
        node = _get_enabled_node(node_id=node_id, db_path=app.state.db_path)
        return _fetch_node_json(
            node=node,
            remote_path="/api/detections/top",
            query_params={"from_ms": from_ms, "to_ms": to_ms, "limit": limit},
        )

    @app.get("/api/nodes/{node_id}/config")
    def api_node_config_get(
        node_id: str,
        token: str | None = Query(default=None),
    ) -> Any:
        _require_node_proxy_access(token)
        node = _get_enabled_node(node_id=node_id, db_path=app.state.db_path)
        return _fetch_node_json(node=node, remote_path="/api/config")

    @app.put("/api/nodes/{node_id}/config")
    def api_node_config_put(
        node_id: str,
        payload: dict[str, Any],
        token: str | None = Query(default=None),
    ) -> Any:
        _require_admin_token(token)
        node = _get_enabled_node(node_id=node_id, db_path=app.state.db_path)
        return _put_node_json(node=node, remote_path="/api/config", payload=payload)

    @app.get("/api/stream/observations")
    async def api_observations_stream(request: Request, interval_s: float = Query(default=3.0, ge=1.0, le=10.0)) -> StreamingResponse:
        async def event_stream() -> Any:
            last_ts_ms: int | None = None
            while True:
                if await request.is_disconnected():
                    break
                obs = latest_observation(db_path=app.state.db_path)
                if obs is not None and obs.get("ts_ms") != last_ts_ms:
                    last_ts_ms = obs.get("ts_ms")
                    payload = _with_iso(obs)
                    yield f"event: observation\ndata: {json.dumps(payload)}\n\n"
                else:
                    yield "event: heartbeat\ndata: {}\n\n"
                await asyncio.sleep(interval_s)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )


    # Include terminal routes when optional terminal dependencies are installed.
    try:
        from rpipulse.terminal import router as terminal_router, create_nodes_router
    except ModuleNotFoundError:
        terminal_router = None
        create_nodes_router = None
    if terminal_router is not None and create_nodes_router is not None:
        app.include_router(terminal_router)
        app.include_router(create_nodes_router())
    return app


app = create_app()
