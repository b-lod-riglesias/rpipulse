from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from rpipulse.db import (
    DAY_MS,
    DEFAULT_DB_PATH,
    daily_peak_and_avg,
    get_connection,
    hourly_peak_and_avg,
    init_db,
    latest_observation,
    latest_observations,
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
    return {**row, "ts_iso": _to_iso(row.get("ts_ms"))}


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


def create_app(db_path: Path | None = None) -> FastAPI:
    app = FastAPI(title="RPIpulse Web UI", version="1.0.0")
    app.state.db_path = resolve_db_path(db_path)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

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

    @app.get("/api/observations/latest")
    def api_observations_latest(limit: int = Query(default=20, ge=1, le=500)) -> dict[str, Any]:
        items = [_with_iso(row) for row in latest_observations(limit=limit, db_path=app.state.db_path)]
        items = [row for row in items if row is not None]
        return {"items": items, "observation": items[0] if items else None}

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

    return app


app = create_app()
