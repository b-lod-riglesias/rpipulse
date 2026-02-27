from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from rpipulse.db import DAY_MS, HOUR_MS, DEFAULT_DB_PATH, get_connection, init_db

WEBUI_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEBUI_DIR / "templates"
STATIC_DIR = WEBUI_DIR / "static"


def resolve_db_path(override: Path | None = None) -> Path:
    if override is not None:
        return override
    env_path = os.environ.get("RPIPULSE_DB_PATH") or os.environ.get("ANALIZADOR_DB_PATH")
    return Path(env_path) if env_path else DEFAULT_DB_PATH


def _to_iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _latest_observation(db_path: Path) -> dict[str, Any] | None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT ts_ms, unique_devices_count, raw_count
            FROM observations
            ORDER BY ts_ms DESC
            LIMIT 1
            """
        ).fetchone()

    if row is None:
        return None

    return {
        "ts_ms": int(row[0]),
        "timestamp": _to_iso(int(row[0])),
        "unique_devices_count": int(row[1]),
        "raw_count": 0 if row[2] is None else int(row[2]),
    }


def _series_daily(db_path: Path, days: int) -> list[dict[str, Any]]:
    init_db(db_path)
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    from_ms = now_ms - (days * DAY_MS)

    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                ((ts_ms / 86400000) * 86400000) AS day_start_ms,
                COALESCE(MAX(unique_devices_count), 0) AS peak_unique,
                COALESCE(AVG(unique_devices_count), 0) AS avg_unique,
                COALESCE(MAX(raw_count), 0) AS peak_raw,
                COALESCE(AVG(raw_count), 0) AS avg_raw
            FROM observations
            WHERE ts_ms >= ?
            GROUP BY ((ts_ms / 86400000) * 86400000)
            ORDER BY day_start_ms ASC
            """,
            (from_ms,),
        ).fetchall()

    return [
        {
            "day": datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
            "day_start_ms": int(row[0]),
            "peak_unique": int(row[1]),
            "avg_unique": float(row[2]),
            "peak_raw": int(row[3]),
            "avg_raw": float(row[4]),
        }
        for row in rows
    ]


def _series_hourly(db_path: Path, days: int) -> list[dict[str, Any]]:
    init_db(db_path)
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    from_ms = now_ms - (days * DAY_MS)

    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                ((ts_ms / 3600000) * 3600000) AS hour_start_ms,
                COALESCE(MAX(unique_devices_count), 0) AS peak_unique,
                COALESCE(AVG(unique_devices_count), 0) AS avg_unique,
                COALESCE(MAX(raw_count), 0) AS peak_raw,
                COALESCE(AVG(raw_count), 0) AS avg_raw
            FROM observations
            WHERE ts_ms >= ?
            GROUP BY ((ts_ms / 3600000) * 3600000)
            ORDER BY hour_start_ms ASC
            """,
            (from_ms,),
        ).fetchall()

    return [
        {
            "hour": datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:00"),
            "hour_start_ms": int(row[0]),
            "peak_unique": int(row[1]),
            "avg_unique": float(row[2]),
            "peak_raw": int(row[3]),
            "avg_raw": float(row[4]),
        }
        for row in rows
    ]


def _kpis_now(db_path: Path) -> dict[str, Any]:
    init_db(db_path)
    latest = _latest_observation(db_path)
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    from_24h = now_ms - (24 * HOUR_MS)
    from_60m = now_ms - HOUR_MS
    from_120m = now_ms - (2 * HOUR_MS)

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

    return {
        "latest": latest,
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
    app = FastAPI(title="RPIpulse Web UI", version="0.1.0")
    app.state.db_path = resolve_db_path(db_path)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "index.html")

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        db_path_value: Path = app.state.db_path
        init_db(db_path_value)
        with get_connection(db_path_value) as conn:
            observations = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]

        return {
            "status": "ok",
            "db_path": str(db_path_value),
            "db_exists": db_path_value.exists(),
            "observations": int(observations),
            "utc_now": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        }

    @app.get("/api/observations/latest")
    def api_latest_observation() -> dict[str, Any]:
        return {"observation": _latest_observation(app.state.db_path)}

    @app.get("/api/series/daily")
    def api_daily_series(days: int = Query(default=30, ge=1, le=365)) -> dict[str, Any]:
        return {"days": days, "series": _series_daily(app.state.db_path, days)}

    @app.get("/api/series/hourly")
    def api_hourly_series(days: int = Query(default=7, ge=1, le=60)) -> dict[str, Any]:
        return {"days": days, "series": _series_hourly(app.state.db_path, days)}

    @app.get("/api/kpis/now")
    def api_kpis_now() -> dict[str, Any]:
        return _kpis_now(app.state.db_path)

    @app.get("/api/stream/observations")
    async def api_observations_stream(request: Request, interval_s: float = Query(default=3.0, ge=2.0, le=5.0)) -> StreamingResponse:
        async def event_stream() -> Any:
            while True:
                if await request.is_disconnected():
                    break
                payload = {"observation": _latest_observation(app.state.db_path)}
                yield f"event: observation\ndata: {json.dumps(payload)}\n\n"
                await asyncio.sleep(interval_s)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    return app


app = create_app()
