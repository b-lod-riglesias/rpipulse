from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
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
REPO_ROOT = WEBUI_DIR.parents[2]
SYNC_SCRIPTS_DIR = REPO_ROOT / "data" / "generated_sync"
SYNC_LINKS_DIR = SYNC_SCRIPTS_DIR / "links"
ADD_RASPBERRY_TOOL = REPO_ROOT / "tools" / "add_raspberry.sh"


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


def _status_from_load_pct(load_pct: float | None) -> str:
    if load_pct is None:
        return "unknown"
    if load_pct < 25:
        return "quiet"
    if load_pct < 60:
        return "moderate"
    if load_pct < 85:
        return "busy"
    return "packed"


def _slugify_node_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return normalized.strip("-")


def _build_sync_script(
    *,
    node_name: str,
    host: str,
    bootstrap_user: str,
    ssh_port: int,
) -> str:
    tool_path = shlex.quote(str(ADD_RASPBERRY_TOOL))
    host_q = shlex.quote(host)
    user_q = shlex.quote(bootstrap_user)
    port_q = shlex.quote(str(ssh_port))
    name_q = shlex.quote(node_name)
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            f"exec {tool_path} --host {host_q} --user {user_q} --port {port_q} --name {name_q}",
            "",
        ]
    )


def _create_sync_script(
    *,
    node_id: str,
    node_name: str,
    host: str,
    bootstrap_user: str,
    ssh_port: int,
) -> Path:
    if not ADD_RASPBERRY_TOOL.exists():
        raise FileNotFoundError(f"Missing deployment tool: {ADD_RASPBERRY_TOOL}")

    SYNC_SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    script_path = SYNC_SCRIPTS_DIR / f"sync_{node_id}.sh"
    script_path.write_text(
        _build_sync_script(
            node_name=node_name,
            host=host,
            bootstrap_user=bootstrap_user,
            ssh_port=ssh_port,
        ),
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    return script_path


def _create_short_bootstrap_link(
    *,
    node_id: str,
    script_path: Path,
) -> str:
    SYNC_LINKS_DIR.mkdir(parents=True, exist_ok=True)
    for _ in range(20):
        code = secrets.token_urlsafe(4).replace("_", "").replace("-", "")[:6].lower()
        if len(code) < 6:
            continue
        link_path = SYNC_LINKS_DIR / f"{code}.json"
        if link_path.exists():
            continue
        link_path.write_text(
            json.dumps({"node_id": node_id, "script_path": str(script_path)}, ensure_ascii=True),
            encoding="utf-8",
        )
        return code
    raise RuntimeError("Could not allocate bootstrap link")


def _read_short_bootstrap_link(code: str) -> dict[str, Any]:
    link_path = SYNC_LINKS_DIR / f"{code}.json"
    if not link_path.exists():
        raise FileNotFoundError(code)
    payload = json.loads(link_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid bootstrap link payload")
    return payload


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("items", "series", "detections"):
            items = payload.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _parse_nodes_csv(nodes_csv: str | None) -> list[str]:
    if not nodes_csv:
        return []
    seen: set[str] = set()
    output: list[str] = []
    for raw_node_id in nodes_csv.split(","):
        node_id = raw_node_id.strip()
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        output.append(node_id)
    return output


def _resolve_aggregate_nodes(nodes_csv: str | None, db_path: Path) -> list[dict[str, Any]]:
    from rpipulse.db import get_all_nodes, get_node

    requested = _parse_nodes_csv(nodes_csv)
    all_nodes = get_all_nodes(db_path=db_path)
    enabled_nodes = [node for node in all_nodes if node.get("enabled", False)]
    enabled_by_id = {str(node["id"]): node for node in enabled_nodes}

    if not requested:
        return enabled_nodes

    resolved: list[dict[str, Any]] = []
    for node_id in requested:
        node = enabled_by_id.get(node_id)
        if node is not None:
            resolved.append(node)
            continue

        existing = get_node(node_id=node_id, db_path=db_path)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")
        raise HTTPException(status_code=403, detail=f"Node '{node_id}' is disabled")

    return resolved


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
    status = _status_from_load_pct(load_pct)

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

    @app.post("/api/admin/raspberries/bootstrap")
    def api_bootstrap_raspberry(
        payload: dict[str, Any],
        request: Request,
        token: str | None = Query(default=None),
    ) -> dict[str, Any]:
        _require_admin_token(token)

        from rpipulse.db import create_node, get_node, update_node

        name = str(payload.get("name") or "").strip()
        host = str(payload.get("host") or "").strip()
        bootstrap_user = str(payload.get("bootstrap_user") or "").strip()
        node_user = str(payload.get("node_user") or "rpipulse").strip() or "rpipulse"
        raw_node_id = str(payload.get("id") or name).strip()
        node_id = _slugify_node_id(raw_node_id)
        enabled = bool(payload.get("enabled", True))

        if not name or not host or not bootstrap_user:
            raise HTTPException(status_code=400, detail="Missing required fields: name, host, bootstrap_user")
        if not node_id:
            raise HTTPException(status_code=400, detail="Could not derive a valid node id")

        try:
            ssh_port = int(payload.get("ssh_port", 22))
            http_port = int(payload.get("http_port", 8000))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="ssh_port and http_port must be integers") from exc

        if not (1 <= ssh_port <= 65535):
            raise HTTPException(status_code=400, detail="ssh_port must be between 1 and 65535")
        if not (1 <= http_port <= 65535):
            raise HTTPException(status_code=400, detail="http_port must be between 1 and 65535")

        existing = get_node(node_id=node_id, db_path=app.state.db_path)
        if existing is None:
            node = create_node(
                node_id=node_id,
                name=name,
                host=host,
                port=http_port,
                user=node_user,
                enabled=enabled,
                db_path=app.state.db_path,
            )
        else:
            node = update_node(
                node_id=node_id,
                name=name,
                host=host,
                port=http_port,
                user=node_user,
                enabled=enabled,
                db_path=app.state.db_path,
            )
            if node is None:
                raise HTTPException(status_code=404, detail="Node not found")

        try:
            script_path = _create_sync_script(
                node_id=node_id,
                node_name=name,
                host=host,
                bootstrap_user=bootstrap_user,
                ssh_port=ssh_port,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        node.pop("password", None)
        script_filename = script_path.name
        script_body = script_path.read_text(encoding="utf-8")
        short_code = _create_short_bootstrap_link(node_id=node_id, script_path=script_path)
        base_url = str(request.base_url).rstrip("/")
        short_url = f"{base_url}/r/{short_code}"
        return {
            "node": node,
            "bootstrap": {
                "short_code": short_code,
                "short_url": short_url,
                "shell_command": f"curl -fsSL {shlex.quote(short_url)} | bash",
                "script_filename": script_filename,
                "script_body": script_body,
                "script_path": str(script_path),
                "command": str(script_path),
                "bootstrap_user": bootstrap_user,
                "ssh_port": ssh_port,
                "http_port": http_port,
            },
        }

    @app.get("/r/{code}")
    def api_bootstrap_short_link(code: str, download: int = Query(default=0)) -> PlainTextResponse:
        try:
            payload = _read_short_bootstrap_link(code)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Bootstrap link not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        script_path = Path(str(payload.get("script_path") or ""))
        if not script_path.exists():
            raise HTTPException(status_code=404, detail="Bootstrap script not found")

        content = script_path.read_text(encoding="utf-8")
        headers: dict[str, str] = {}
        if download:
            headers["Content-Disposition"] = f'attachment; filename="{script_path.name}"'
        return PlainTextResponse(content, media_type="text/x-shellscript; charset=utf-8", headers=headers)

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

    @app.get("/api/aggregate/kpis/now")
    def api_aggregate_kpis_now(nodes: str | None = Query(default=None)) -> dict[str, Any]:
        target_nodes = _resolve_aggregate_nodes(nodes_csv=nodes, db_path=app.state.db_path)

        sum_unique = 0
        sum_raw = 0
        sum_capacity = 0
        latest_ts_ms: int | None = None
        node_statuses: set[str] = set()
        breakdown: list[dict[str, Any]] = []

        for node in target_nodes:
            payload = _fetch_node_json(node=node, remote_path="/api/kpis/now")
            if not isinstance(payload, dict):
                payload = {}

            node_observation = payload.get("observation")
            if not isinstance(node_observation, dict):
                node_observation = None

            unique_count = _as_int(node_observation.get("unique_devices_count") if node_observation else 0)
            raw_count = _as_int(node_observation.get("raw_count") if node_observation else 0)
            capacity = max(0, _as_int(payload.get("capacity"), default=0))

            sum_unique += unique_count
            sum_raw += raw_count
            sum_capacity += capacity

            node_status = str(payload.get("status") or "unknown")
            if node_status:
                node_statuses.add(node_status)

            node_ts_ms = _as_int(node_observation.get("ts_ms") if node_observation else None, default=0)
            if node_ts_ms > 0:
                latest_ts_ms = node_ts_ms if latest_ts_ms is None else max(latest_ts_ms, node_ts_ms)

            node_load_pct = round((raw_count / capacity) * 100, 1) if capacity > 0 else None
            breakdown.append(
                {
                    "node_id": str(node["id"]),
                    "name": node.get("name"),
                    "capacity": capacity,
                    "status": node_status,
                    "observation": {
                        "ts_ms": node_ts_ms or None,
                        "ts_iso": _to_iso(node_ts_ms) if node_ts_ms > 0 else None,
                        "unique_devices_count": unique_count,
                        "raw_count": raw_count,
                        "load_pct": node_load_pct,
                    },
                }
            )

        load_pct = round((sum_raw / sum_capacity) * 100, 1) if sum_capacity > 0 else None
        derived_status = _status_from_load_pct(load_pct)
        status = "mixed" if len(node_statuses) > 1 else derived_status

        observation = {
            "ts_ms": latest_ts_ms,
            "ts_iso": _to_iso(latest_ts_ms),
            "unique_devices_count": sum_unique,
            "raw_count": sum_raw,
            "load_pct": load_pct,
        }

        return {
            "observation": observation,
            "latest": observation,
            "capacity": sum_capacity,
            "status": status,
            "node_count": len(target_nodes),
            "unique": {"mode": "sum", "value": sum_unique},
            "breakdown": breakdown,
        }

    @app.get("/api/aggregate/series/hourly")
    def api_aggregate_series_hourly(nodes: str | None = Query(default=None)) -> dict[str, Any]:
        target_nodes = _resolve_aggregate_nodes(nodes_csv=nodes, db_path=app.state.db_path)
        merged: dict[str, dict[str, Any]] = {}

        for node in target_nodes:
            payload = _fetch_node_json(node=node, remote_path="/api/series/hourly")
            for item in _extract_items(payload):
                key = str(item.get("hour") or "")
                if not key:
                    continue
                current = merged.get(key)
                if current is None:
                    current = {
                        "hour": key,
                        "hour_start_ms": _as_int(item.get("hour_start_ms"), default=0),
                        "peak_unique": 0,
                        "avg_unique": 0.0,
                        "peak_raw": 0,
                        "avg_raw": 0.0,
                    }
                    merged[key] = current
                current["peak_unique"] += _as_int(item.get("peak_unique"), default=0)
                current["avg_unique"] += _as_float(item.get("avg_unique"), default=0.0)
                current["peak_raw"] += _as_int(item.get("peak_raw"), default=0)
                current["avg_raw"] += _as_float(item.get("avg_raw"), default=0.0)

        items = sorted(merged.values(), key=lambda row: _as_int(row.get("hour_start_ms"), default=0))
        return {"items": items, "series": items}

    @app.get("/api/aggregate/series/daily")
    def api_aggregate_series_daily(nodes: str | None = Query(default=None)) -> dict[str, Any]:
        target_nodes = _resolve_aggregate_nodes(nodes_csv=nodes, db_path=app.state.db_path)
        merged: dict[str, dict[str, Any]] = {}

        for node in target_nodes:
            payload = _fetch_node_json(node=node, remote_path="/api/series/daily")
            for item in _extract_items(payload):
                key = str(item.get("day") or "")
                if not key:
                    continue
                current = merged.get(key)
                if current is None:
                    current = {
                        "day": key,
                        "day_start_ms": _as_int(item.get("day_start_ms"), default=0),
                        "peak_unique": 0,
                        "avg_unique": 0.0,
                        "peak_raw": 0,
                        "avg_raw": 0.0,
                    }
                    merged[key] = current
                current["peak_unique"] += _as_int(item.get("peak_unique"), default=0)
                current["avg_unique"] += _as_float(item.get("avg_unique"), default=0.0)
                current["peak_raw"] += _as_int(item.get("peak_raw"), default=0)
                current["avg_raw"] += _as_float(item.get("avg_raw"), default=0.0)

        items = sorted(merged.values(), key=lambda row: _as_int(row.get("day_start_ms"), default=0))
        return {"items": items, "series": items}

    @app.get("/api/aggregate/detections/recent")
    def api_aggregate_detections_recent(
        nodes: str | None = Query(default=None),
        seconds: int = Query(default=300, ge=1, le=86_400),
        limit: int = Query(default=200, ge=1, le=1_000),
    ) -> dict[str, Any]:
        target_nodes = _resolve_aggregate_nodes(nodes_csv=nodes, db_path=app.state.db_path)
        merged_items: list[dict[str, Any]] = []

        for node in target_nodes:
            payload = _fetch_node_json(
                node=node,
                remote_path="/api/detections/recent",
                query_params={"seconds": seconds, "limit": limit},
            )
            for item in _extract_items(payload):
                merged_items.append({**item, "node_id": str(node["id"])})

        merged_items.sort(
            key=lambda row: (
                _as_int(row.get("created_at_ms") or row.get("ts_ms"), default=0),
                _as_int(row.get("seen_count"), default=0),
            ),
            reverse=True,
        )
        return {"items": merged_items[:limit]}

    @app.get("/api/aggregate/detections/top")
    def api_aggregate_detections_top(
        nodes: str | None = Query(default=None),
        from_ms: int = Query(...),
        to_ms: int = Query(...),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        if to_ms < from_ms:
            raise HTTPException(status_code=400, detail="to_ms must be >= from_ms")

        target_nodes = _resolve_aggregate_nodes(nodes_csv=nodes, db_path=app.state.db_path)
        merged: dict[tuple[str, str], dict[str, Any]] = {}

        for node in target_nodes:
            payload = _fetch_node_json(
                node=node,
                remote_path="/api/detections/top",
                query_params={"from_ms": from_ms, "to_ms": to_ms, "limit": limit},
            )
            for item in _extract_items(payload):
                anon_device_id = str(item.get("anon_device_id") or "")
                transport = str(item.get("transport") or "")
                if not anon_device_id or not transport:
                    continue

                observations = _as_int(item.get("observations"), default=0)
                seen_total = _as_int(item.get("seen_total"), default=0)
                avg_rssi_dbm = _as_float(item.get("avg_rssi_dbm"), default=0.0)
                max_rssi_dbm = _as_int(item.get("max_rssi_dbm"), default=0)
                first_seen_ms = _as_int(item.get("first_seen_ms"), default=0)
                last_seen_ms = _as_int(item.get("last_seen_ms"), default=0)

                key = (anon_device_id, transport)
                current = merged.get(key)
                if current is None:
                    merged[key] = {
                        "anon_device_id": anon_device_id,
                        "transport": transport,
                        "observations": observations,
                        "seen_total": seen_total,
                        "avg_rssi_dbm": avg_rssi_dbm,
                        "max_rssi_dbm": max_rssi_dbm,
                        "first_seen_ms": first_seen_ms,
                        "last_seen_ms": last_seen_ms,
                        "_rssi_weight": max(0, observations),
                    }
                    continue

                prev_weight = _as_int(current.get("_rssi_weight"), default=0)
                obs_weight = max(0, observations)
                next_weight = prev_weight + obs_weight
                if next_weight > 0:
                    current["avg_rssi_dbm"] = (
                        (_as_float(current.get("avg_rssi_dbm"), default=0.0) * prev_weight)
                        + (avg_rssi_dbm * obs_weight)
                    ) / next_weight
                current["_rssi_weight"] = next_weight
                current["observations"] = _as_int(current.get("observations"), default=0) + observations
                current["seen_total"] = _as_int(current.get("seen_total"), default=0) + seen_total
                current["max_rssi_dbm"] = max(_as_int(current.get("max_rssi_dbm"), default=0), max_rssi_dbm)
                current_first = _as_int(current.get("first_seen_ms"), default=0)
                if first_seen_ms > 0:
                    current["first_seen_ms"] = first_seen_ms if current_first <= 0 else min(current_first, first_seen_ms)
                current["last_seen_ms"] = max(_as_int(current.get("last_seen_ms"), default=0), last_seen_ms)

        items: list[dict[str, Any]] = []
        for entry in merged.values():
            items.append({k: v for k, v in entry.items() if k != "_rssi_weight"})

        items.sort(
            key=lambda row: (
                _as_int(row.get("observations"), default=0),
                _as_int(row.get("seen_total"), default=0),
                _as_int(row.get("last_seen_ms"), default=0),
            ),
            reverse=True,
        )
        return {"items": items[:limit]}

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
