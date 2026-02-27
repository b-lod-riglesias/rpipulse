from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .db import (
    DEFAULT_DB_PATH,
    daily_peak_and_avg,
    get_sensor_config,
    hourly_peak_and_avg,
    insert_observation,
    purge_old_data,
    update_sensor_config,
)
from .scanner import scan_devices


def resolve_db_path() -> Path:
    override = os.environ.get("RPIPULSE_DB_PATH") or os.environ.get("ANALIZADOR_DB_PATH")
    return Path(override) if override else DEFAULT_DB_PATH


def _run_scan_window(duration: int, rssi_threshold: int, db_path: Path) -> int:
    result = scan_devices(duration=duration, rssi_threshold=rssi_threshold)
    detections = list(getattr(result, "detections", []))
    ts_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    insert_observation(
        ts_ms=ts_ms,
        unique_devices_count=result.unique_count,
        raw_count=result.raw_count,
        db_path=db_path,
        detections=[
            {
                "address": detection.address,
                "transport": detection.transport,
                "rssi_dbm": detection.rssi_dbm,
                "seen_count": detection.seen_count,
                "first_offset_ms": detection.first_offset_ms,
                "last_offset_ms": detection.last_offset_ms,
            }
            for detection in detections
        ],
    )

    print(f"scan backend={result.backend} duration={duration}s rssi_threshold={rssi_threshold}")
    print(f"unique_devices={result.unique_count} raw_count={result.raw_count} detections={len(detections)}")
    print(f"saved_at_ms={ts_ms} db={db_path}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    db_path = resolve_db_path()
    config = get_sensor_config(db_path=db_path)
    duration = int(args.duration if args.duration is not None else config["duration"])
    interval = int(args.interval if args.interval is not None else config["interval"])
    rssi_threshold = int(args.rssi_threshold if args.rssi_threshold is not None else config["rssi_threshold"])

    windows = max(1, int(args.windows))
    for index in range(windows):
        _run_scan_window(duration=duration, rssi_threshold=rssi_threshold, db_path=db_path)
        if index < windows - 1:
            time.sleep(max(1, interval))

    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    db_path = resolve_db_path()
    if args.group == "daily":
        for row in daily_peak_and_avg(db_path=db_path):
            print(
                f"daily day={row['day']} "
                f"peak_unique={row['peak_unique']} avg_unique={row['avg_unique']:.2f} "
                f"peak_raw={row['peak_raw']} avg_raw={row['avg_raw']:.2f}"
            )
        return 0

    for row in hourly_peak_and_avg(db_path=db_path):
        print(
            f"hourly hour={row['hour']} "
            f"peak_unique={row['peak_unique']} avg_unique={row['avg_unique']:.2f} "
            f"peak_raw={row['peak_raw']} avg_raw={row['avg_raw']:.2f}"
        )
    return 0


def cmd_config_get(_: argparse.Namespace) -> int:
    db_path = resolve_db_path()
    config = get_sensor_config(db_path=db_path)
    print(
        "config "
        f"duration={config['duration']} "
        f"interval={config['interval']} "
        f"rssi_threshold={config['rssi_threshold']} "
        f"retention_days={config['retention_days']}"
    )
    return 0


def cmd_config_set(args: argparse.Namespace) -> int:
    db_path = resolve_db_path()
    updates = {
        key: value
        for key, value in {
            "duration": args.duration,
            "interval": args.interval,
            "rssi_threshold": args.rssi_threshold,
            "retention_days": args.retention_days,
        }.items()
        if value is not None
    }
    config = update_sensor_config(updates=updates, db_path=db_path)
    print(
        "config_updated "
        f"duration={config['duration']} "
        f"interval={config['interval']} "
        f"rssi_threshold={config['rssi_threshold']} "
        f"retention_days={config['retention_days']}"
    )
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    db_path = resolve_db_path()
    stats = purge_old_data(retention_days=args.retention_days, db_path=db_path)
    print(
        "purge "
        f"retention_days={stats['retention_days']} "
        f"deleted_observations={stats['deleted_observations']} "
        f"deleted_detections={stats['deleted_detections']}"
    )
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("Missing UI dependencies. Install with: pip install -e '.[ui]'") from exc

    from rpipulse.webui.app import create_app

    app = create_app(db_path=resolve_db_path())
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rpipulse", description="RPIpulse CLI de escaneo BLE y estadísticas")
    sub = parser.add_subparsers(dest="command", required=True)

    scan_parser = sub.add_parser("scan", help="Escanea dispositivos BLE")
    scan_parser.add_argument("--duration", type=int, default=None, help="Duración del escaneo en segundos")
    scan_parser.add_argument("--interval", type=int, default=None, help="Pausa entre ventanas en segundos")
    scan_parser.add_argument("--rssi-threshold", type=int, default=None, help="RSSI mínimo para considerar detecciones")
    scan_parser.add_argument("--windows", type=int, default=1, help="Cantidad de ventanas de escaneo a ejecutar")
    scan_parser.set_defaults(func=cmd_scan)

    stats_parser = sub.add_parser("stats", help="Muestra estadísticas")
    stats_parser.add_argument(
        "--group",
        choices=("daily", "hourly"),
        default="daily",
        help="Agrupación temporal para estadísticas",
    )
    stats_parser.set_defaults(func=cmd_stats)

    config_parser = sub.add_parser("config", help="Lee o actualiza configuración persistida del sensor")
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)

    config_get_parser = config_sub.add_parser("get", help="Muestra configuración actual")
    config_get_parser.set_defaults(func=cmd_config_get)

    config_set_parser = config_sub.add_parser("set", help="Actualiza configuración")
    config_set_parser.add_argument("--duration", type=int, default=None)
    config_set_parser.add_argument("--interval", type=int, default=None)
    config_set_parser.add_argument("--rssi-threshold", type=int, default=None)
    config_set_parser.add_argument("--retention-days", type=int, default=None)
    config_set_parser.set_defaults(func=cmd_config_set)

    purge_parser = sub.add_parser("purge", help="Elimina datos fuera de ventana de retención")
    purge_parser.add_argument("--retention-days", type=int, default=None, help="Override de retención para esta ejecución")
    purge_parser.set_defaults(func=cmd_purge)

    ui_parser = sub.add_parser("ui", help="Levanta la Web UI (FastAPI + Uvicorn)")
    ui_parser.add_argument("--host", default="127.0.0.1", help="Host bind (ej: 127.0.0.1 o 0.0.0.0)")
    ui_parser.add_argument("--port", type=int, default=8000, help="Puerto HTTP")
    ui_parser.set_defaults(func=cmd_ui)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "scan":
        if args.duration is not None and args.duration < 1:
            parser.error("--duration debe ser >= 1")
        if args.interval is not None and args.interval < 1:
            parser.error("--interval debe ser >= 1")
        if args.rssi_threshold is not None and not (-127 <= args.rssi_threshold <= 20):
            parser.error("--rssi-threshold debe estar entre -127 y 20")
        if args.windows < 1:
            parser.error("--windows debe ser >= 1")

    if args.command == "config" and args.config_command == "set":
        if args.duration is not None and args.duration < 1:
            parser.error("--duration debe ser >= 1")
        if args.interval is not None and args.interval < 1:
            parser.error("--interval debe ser >= 1")
        if args.rssi_threshold is not None and not (-127 <= args.rssi_threshold <= 20):
            parser.error("--rssi-threshold debe estar entre -127 y 20")
        if args.retention_days is not None and args.retention_days < 1:
            parser.error("--retention-days debe ser >= 1")

    if args.command == "purge" and args.retention_days is not None and args.retention_days < 1:
        parser.error("--retention-days debe ser >= 1")

    if args.command == "ui" and not (1 <= args.port <= 65535):
        parser.error("--port debe estar entre 1 y 65535")

    try:
        return args.func(args)
    except ValueError as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
