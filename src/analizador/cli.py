from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

from .db import DEFAULT_DB_PATH, insert_observation, stats_for_day
from .scanner import scan_devices


def resolve_db_path() -> Path:
    override = os.environ.get("ANALIZADOR_DB_PATH")
    return Path(override) if override else DEFAULT_DB_PATH


def cmd_scan(args: argparse.Namespace) -> int:
    db_path = resolve_db_path()
    result = scan_devices(duration=args.duration)
    ts = datetime.now().isoformat(timespec="seconds")
    insert_observation(
        ts=ts,
        unique_devices_count=result.unique_count,
        raw_count=result.raw_count,
        db_path=db_path,
    )

    print(f"scan backend={result.backend} duration={args.duration}s")
    print(f"unique_devices={result.unique_count} raw_count={result.raw_count}")
    print(f"saved_at={ts} db={db_path}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    db_path = resolve_db_path()
    if args.day == "today":
        day = datetime.now().date().isoformat()
    else:
        day = args.day

    stats = stats_for_day(day=day, db_path=db_path)
    print(f"day={day}")
    print(f"observations={stats['observations']}")
    print(f"total_unique={stats['total_unique']}")
    print(f"avg_unique={stats['avg_unique']:.2f}")
    print(f"max_unique={stats['max_unique']}")
    print(f"min_unique={stats['min_unique']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="analizador", description="CLI de escaneo BLE y estadísticas")
    sub = parser.add_subparsers(dest="command", required=True)

    scan_parser = sub.add_parser("scan", help="Escanea dispositivos BLE")
    scan_parser.add_argument("--duration", type=int, default=15, help="Duración del escaneo en segundos")
    scan_parser.set_defaults(func=cmd_scan)

    stats_parser = sub.add_parser("stats", help="Muestra estadísticas")
    stats_parser.add_argument("--day", default="today", help="Día en formato YYYY-MM-DD o 'today'")
    stats_parser.set_defaults(func=cmd_stats)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "scan" and args.duration < 1:
        parser.error("--duration debe ser >= 1")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
