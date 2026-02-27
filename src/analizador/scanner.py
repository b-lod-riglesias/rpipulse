from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Iterable


@dataclass
class ScanResult:
    unique_count: int
    raw_count: int
    backend: str


def _extract_identifier(device: object) -> str | None:
    address = getattr(device, "address", None)
    if isinstance(address, str) and address:
        return address
    name = getattr(device, "name", None)
    if isinstance(name, str) and name:
        return name
    return None


def _count_unique(devices: Iterable[object]) -> tuple[int, int]:
    materialized = list(devices)
    identifiers = [identifier for d in materialized if (identifier := _extract_identifier(d)) is not None]
    unique_count = len(set(identifiers))
    raw_count = len(materialized)
    return unique_count, raw_count


async def _scan_with_bleak(duration: int) -> ScanResult:
    from bleak import BleakScanner  # type: ignore

    devices = await BleakScanner.discover(timeout=float(duration))
    unique_count, raw_count = _count_unique(devices)
    return ScanResult(unique_count=unique_count, raw_count=raw_count, backend="bleak")


def _scan_fallback(_: int) -> ScanResult:
    return ScanResult(unique_count=0, raw_count=0, backend="fallback")


def scan_devices(duration: int) -> ScanResult:
    try:
        from bleak import BleakScanner  # noqa: F401
    except Exception:
        return _scan_fallback(duration)

    try:
        return asyncio.run(_scan_with_bleak(duration))
    except Exception:
        return _scan_fallback(duration)
