from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Any, Iterable


@dataclass
class DeviceDetection:
    address: str
    transport: str
    rssi_dbm: int | None
    seen_count: int
    first_offset_ms: int
    last_offset_ms: int


@dataclass
class ScanResult:
    unique_count: int
    raw_count: int
    backend: str
    detections: list[DeviceDetection]


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


def _apply_threshold(devices: Iterable[object], rssi_threshold: int | None) -> list[object]:
    if rssi_threshold is None:
        return list(devices)
    filtered: list[object] = []
    for device in devices:
        rssi = getattr(device, "rssi", None)
        if isinstance(rssi, int) and rssi < rssi_threshold:
            continue
        filtered.append(device)
    return filtered


def _as_detection_rows(state: dict[str, dict[str, Any]]) -> list[DeviceDetection]:
    rows: list[DeviceDetection] = []
    for item in state.values():
        rows.append(
            DeviceDetection(
                address=item["address"],
                transport=item["transport"],
                rssi_dbm=item["rssi_dbm"],
                seen_count=item["seen_count"],
                first_offset_ms=item["first_offset_ms"],
                last_offset_ms=item["last_offset_ms"],
            )
        )
    return rows


async def _scan_with_bleak(duration: int, rssi_threshold: int | None) -> ScanResult:
    from bleak import BleakScanner  # type: ignore

    started = monotonic()
    detections: dict[str, dict[str, Any]] = {}
    raw_count = 0

    def on_detect(device: object, adv_data: object) -> None:
        nonlocal raw_count

        address = getattr(device, "address", None)
        if not isinstance(address, str) or not address:
            return

        rssi_val = getattr(adv_data, "rssi", None)
        if rssi_val is None:
            rssi_val = getattr(device, "rssi", None)

        rssi: int | None
        try:
            rssi = None if rssi_val is None else int(rssi_val)
        except (TypeError, ValueError):
            rssi = None

        if rssi_threshold is not None and rssi is not None and rssi < rssi_threshold:
            return

        raw_count += 1
        now_offset_ms = max(0, int((monotonic() - started) * 1000))
        current = detections.get(address)
        if current is None:
            detections[address] = {
                "address": address,
                "transport": "ble",
                "rssi_dbm": rssi,
                "seen_count": 1,
                "first_offset_ms": now_offset_ms,
                "last_offset_ms": now_offset_ms,
            }
            return

        current["seen_count"] += 1
        current["last_offset_ms"] = now_offset_ms
        if rssi is not None:
            prev_rssi = current.get("rssi_dbm")
            if prev_rssi is None or rssi > prev_rssi:
                current["rssi_dbm"] = rssi

    scanner = BleakScanner(detection_callback=on_detect)
    await scanner.start()
    try:
        await asyncio.sleep(float(duration))
    finally:
        await scanner.stop()

    if detections:
        return ScanResult(
            unique_count=len(detections),
            raw_count=raw_count,
            backend="bleak",
            detections=_as_detection_rows(detections),
        )

    devices = await BleakScanner.discover(timeout=float(duration))
    filtered = _apply_threshold(devices, rssi_threshold)
    unique_count, base_raw_count = _count_unique(filtered)
    return ScanResult(unique_count=unique_count, raw_count=base_raw_count, backend="bleak", detections=[])


def _scan_fallback(_: int) -> ScanResult:
    return ScanResult(unique_count=0, raw_count=0, backend="fallback", detections=[])


def scan_devices(duration: int, rssi_threshold: int | None = None) -> ScanResult:
    try:
        from bleak import BleakScanner  # noqa: F401
    except Exception:
        return _scan_fallback(duration)

    try:
        return asyncio.run(_scan_with_bleak(duration, rssi_threshold=rssi_threshold))
    except Exception:
        return _scan_fallback(duration)
