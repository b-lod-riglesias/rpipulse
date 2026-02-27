from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from .db import DEFAULT_DB_PATH, daily_peak_and_avg, hourly_peak_and_avg, insert_observation
from .scanner import scan_devices


def resolve_db_path() -> Path:
    override = os.environ.get("RPIPULSE_DB_PATH") or os.environ.get("ANALIZADOR_DB_PATH")
    return Path(override) if override else DEFAULT_DB_PATH


def cmd_scan(args: argparse.Namespace) -> int:
    db_path = resolve_db_path()
    result = scan_devices(duration=args.duration)
    ts_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    insert_observation(
        ts_ms=ts_ms,
        unique_devices_count=result.unique_count,
        raw_count=result.raw_count,
        db_path=db_path,
    )

    print(f"scan backend={result.backend} duration={args.duration}s")
    print(f"unique_devices={result.unique_count} raw_count={result.raw_count}")
    print(f"saved_at_ms={ts_ms} db={db_path}")
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
    scan_parser.add_argument("--duration", type=int, default=15, help="Duración del escaneo en segundos")
    scan_parser.set_defaults(func=cmd_scan)

    stats_parser = sub.add_parser("stats", help="Muestra estadísticas")
    stats_parser.add_argument(
        "--group",
        choices=("daily", "hourly"),
        default="daily",
        help="Agrupación temporal para estadísticas",
    )
    stats_parser.set_defaults(func=cmd_stats)

    ui_parser = sub.add_parser("ui", help="Levanta la Web UI (FastAPI + Uvicorn)")
    ui_parser.add_argument("--host", default="127.0.0.1", help="Host bind (ej: 127.0.0.1 o 0.0.0.0)")
    ui_parser.add_argument("--port", type=int, default=8000, help="Puerto HTTP")
    ui_parser.set_defaults(func=cmd_ui)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "scan" and args.duration < 1:
        parser.error("--duration debe ser >= 1")
    if args.command == "ui" and not (1 <= args.port <= 65535):
        parser.error("--port debe estar entre 1 y 65535")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
