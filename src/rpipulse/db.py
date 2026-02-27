from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

DEFAULT_DB_PATH = Path("data/rpipulse.sqlite")
DAY_MS = 86_400_000
HOUR_MS = 3_600_000
ANON_SECRET_FILENAME = ".anon_secret"

SENSOR_CONFIG_DEFAULTS = {
    "duration": 15,
    "interval": 60,
    "rssi_threshold": -100,
    "retention_days": 30,
}

SENSOR_CONFIG_LIMITS = {
    "duration": (1, 300),
    "interval": (1, 3_600),
    "rssi_threshold": (-127, 20),
    "retention_days": (1, 3_650),
}


def _ensure_parent_dir(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    _ensure_parent_dir(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _to_epoch_ms(ts_ms: int | str) -> int:
    if isinstance(ts_ms, int):
        return ts_ms

    candidate = ts_ms.replace("Z", "+00:00")
    dt = datetime.fromisoformat(candidate)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return int(dt.timestamp() * 1000)


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
    return row is not None


def _table_info(conn: sqlite3.Connection, name: str) -> list[sqlite3.Row]:
    return conn.execute(f"PRAGMA table_info({name})").fetchall()


def _table_columns(conn: sqlite3.Connection, name: str) -> set[str]:
    return {str(row[1]) for row in _table_info(conn, name)}


def _ensure_observations_schema(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "observations"):
        conn.execute(
            """
            CREATE TABLE observations (
                id INTEGER PRIMARY KEY,
                ts_ms INTEGER NOT NULL,
                unique_devices_count INTEGER NOT NULL,
                raw_count INTEGER
            )
            """
        )
        return

    info = _table_info(conn, "observations")
    has_id_pk = any(str(row[1]) == "id" and int(row[5]) == 1 for row in info)
    if has_id_pk:
        return

    # Reed-aligned rebuild path for observations with no PK.
    if _table_exists(conn, "observations_old"):
        conn.execute(f"ALTER TABLE observations_old RENAME TO observations_old_{_now_ms()}")
    conn.execute("ALTER TABLE observations RENAME TO observations_old")
    conn.execute(
        """
        CREATE TABLE observations (
            id INTEGER PRIMARY KEY,
            ts_ms INTEGER NOT NULL,
            unique_devices_count INTEGER NOT NULL,
            raw_count INTEGER
        )
        """
    )

    old_cols = _table_columns(conn, "observations_old")
    if "ts_ms" in old_cols:
        ts_expr = "COALESCE(ts_ms, 0)"
    elif "ts" in old_cols:
        ts_expr = "COALESCE(CAST(strftime('%s', ts) AS INTEGER) * 1000, 0)"
    else:
        ts_expr = "0"

    unique_expr = "COALESCE(unique_devices_count, 0)" if "unique_devices_count" in old_cols else "0"
    raw_expr = "raw_count" if "raw_count" in old_cols else "NULL"

    conn.execute(
        f"""
        INSERT INTO observations (ts_ms, unique_devices_count, raw_count)
        SELECT {ts_expr}, {unique_expr}, {raw_expr}
        FROM observations_old
        ORDER BY {ts_expr}
        """
    )


def _ensure_detections_schema(conn: sqlite3.Connection) -> None:
    required_columns = {
        "observation_id",
        "anon_device_id",
        "transport",
        "rssi_dbm",
        "seen_count",
        "first_offset_ms",
        "last_offset_ms",
        "created_at_ms",
    }

    if _table_exists(conn, "detections"):
        cols = _table_columns(conn, "detections")
        if required_columns.issubset(cols):
            return

        if _table_exists(conn, "detections_old"):
            conn.execute(f"ALTER TABLE detections_old RENAME TO detections_old_{_now_ms()}")
        conn.execute("ALTER TABLE detections RENAME TO detections_old")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS detections (
            observation_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
            anon_device_id TEXT NOT NULL,
            transport TEXT NOT NULL CHECK (transport IN ('ble', 'classic')),
            rssi_dbm INTEGER,
            seen_count INTEGER NOT NULL,
            first_offset_ms INTEGER NOT NULL,
            last_offset_ms INTEGER NOT NULL,
            created_at_ms INTEGER NOT NULL,
            UNIQUE(observation_id, anon_device_id, transport)
        )
        """
    )

    if not _table_exists(conn, "detections_old"):
        return

    old_cols = _table_columns(conn, "detections_old")
    if {"observation_id", "anon_device_id"}.issubset(old_cols):
        select_cols: list[str] = [
            "observation_id",
            "anon_device_id",
            "'ble' AS transport",
            "rssi AS rssi_dbm" if "rssi" in old_cols else "NULL AS rssi_dbm",
            "COALESCE(seen_count, 1) AS seen_count",
            "0 AS first_offset_ms",
            "0 AS last_offset_ms",
            "COALESCE(ts_ms, 0) AS created_at_ms" if "ts_ms" in old_cols else "0 AS created_at_ms",
        ]
        conn.execute(
            f"""
            INSERT INTO detections (
                observation_id,
                anon_device_id,
                transport,
                rssi_dbm,
                seen_count,
                first_offset_ms,
                last_offset_ms,
                created_at_ms
            )
            SELECT {", ".join(select_cols)}
            FROM detections_old
            """
        )


def _ensure_settings_schema(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "settings"):
        conn.execute(
            """
            CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at_ms INTEGER NOT NULL
            )
            """
        )
        return

    cols = _table_columns(conn, "settings")
    if {"key", "value", "updated_at_ms"}.issubset(cols):
        return

    if _table_exists(conn, "settings_old"):
        conn.execute(f"ALTER TABLE settings_old RENAME TO settings_old_{_now_ms()}")
    conn.execute("ALTER TABLE settings RENAME TO settings_old")
    conn.execute(
        """
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at_ms INTEGER NOT NULL
        )
        """
    )

    old_cols = _table_columns(conn, "settings_old")
    if {"key", "value"}.issubset(old_cols):
        updated_expr = "updated_ts_ms" if "updated_ts_ms" in old_cols else str(_now_ms())
        conn.execute(
            f"""
            INSERT INTO settings (key, value, updated_at_ms)
            SELECT key, value, COALESCE({updated_expr}, {_now_ms()})
            FROM settings_old
            """
        )


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    with get_connection(db_path) as conn:
        _ensure_observations_schema(conn)
        _ensure_detections_schema(conn)
        _ensure_settings_schema(conn)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_observations_ts_ms ON observations(ts_ms)")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_observations_day_start_ms
            ON observations(((ts_ms / 86400000) * 86400000))
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_observations_hour_start_ms
            ON observations(((ts_ms / 3600000) * 3600000))
            """
        )

        conn.execute("CREATE INDEX IF NOT EXISTS idx_detections_created_at_ms ON detections(created_at_ms)")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_detections_anon_device_created
            ON detections(anon_device_id, created_at_ms)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_detections_hour_start_ms
            ON detections(((created_at_ms / 3600000) * 3600000))
            """
        )

        _ensure_config_defaults(conn)
        conn.commit()


def _ensure_config_defaults(conn: sqlite3.Connection) -> None:
    now_ms = _now_ms()
    for key, value in SENSOR_CONFIG_DEFAULTS.items():
        conn.execute(
            """
            INSERT INTO settings (key, value, updated_at_ms)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO NOTHING
            """,
            (key, str(value), now_ms),
        )


def _get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return str(row[0])


def _set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings (key, value, updated_at_ms)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at_ms = excluded.updated_at_ms
        """,
        (key, value, _now_ms()),
    )


def _anon_secret_path(db_path: Path) -> Path:
    return db_path.parent / ANON_SECRET_FILENAME


def _load_or_create_anon_secret(db_path: Path) -> bytes:
    env_secret = os.environ.get("RPIPULSE_ANON_SECRET")
    if env_secret:
        return env_secret.encode("utf-8")

    secret_path = _anon_secret_path(db_path)
    _ensure_parent_dir(db_path)

    if secret_path.exists():
        secret_path.chmod(0o600)
        value = secret_path.read_text(encoding="utf-8").strip()
        if value:
            return value.encode("utf-8")

    generated = secrets.token_hex(32)
    secret_path.write_text(generated, encoding="utf-8")
    secret_path.chmod(0o600)
    return generated.encode("utf-8")


def normalize_mac(address: str) -> str:
    return address.strip().lower()


def anonymize_device_id(address: str, secret: bytes | str, transport: str = "ble") -> str:
    normalized = normalize_mac(address)
    key = secret if isinstance(secret, bytes) else secret.encode("utf-8")
    payload = f"{transport}:{normalized}".encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest().upper()


def _insert_detections(
    conn: sqlite3.Connection,
    observation_id: int,
    created_at_ms: int,
    detections: list[dict[str, Any]],
    secret: bytes,
) -> None:
    if not detections:
        return

    for detection in detections:
        address = detection.get("address")
        if not isinstance(address, str) or not address.strip():
            continue

        transport_raw = detection.get("transport", "ble")
        transport = str(transport_raw).lower().strip()
        if transport not in {"ble", "classic"}:
            continue

        anon_device_id = anonymize_device_id(address=address, secret=secret, transport=transport)

        rssi_dbm = detection.get("rssi_dbm", detection.get("rssi"))
        if rssi_dbm is not None:
            try:
                rssi_dbm = int(rssi_dbm)
            except (TypeError, ValueError):
                rssi_dbm = None

        seen_count = detection.get("seen_count", 1)
        try:
            seen_count = max(1, int(seen_count))
        except (TypeError, ValueError):
            seen_count = 1

        first_offset_ms = detection.get("first_offset_ms")
        last_offset_ms = detection.get("last_offset_ms")
        if first_offset_ms is None or last_offset_ms is None:
            offsets = detection.get("offsets_ms")
            if isinstance(offsets, list) and offsets:
                parsed_offsets: list[int] = []
                for offset in offsets:
                    try:
                        parsed_offsets.append(max(0, int(offset)))
                    except (TypeError, ValueError):
                        continue
                if parsed_offsets:
                    first_offset_ms = min(parsed_offsets)
                    last_offset_ms = max(parsed_offsets)

        try:
            first_offset_ms = max(0, int(first_offset_ms))
        except (TypeError, ValueError):
            first_offset_ms = 0

        try:
            last_offset_ms = max(first_offset_ms, int(last_offset_ms))
        except (TypeError, ValueError):
            last_offset_ms = first_offset_ms

        conn.execute(
            """
            INSERT INTO detections (
                observation_id,
                anon_device_id,
                transport,
                rssi_dbm,
                seen_count,
                first_offset_ms,
                last_offset_ms,
                created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(observation_id, anon_device_id, transport) DO UPDATE SET
                rssi_dbm = excluded.rssi_dbm,
                seen_count = excluded.seen_count,
                first_offset_ms = excluded.first_offset_ms,
                last_offset_ms = excluded.last_offset_ms,
                created_at_ms = excluded.created_at_ms
            """,
            (
                observation_id,
                anon_device_id,
                transport,
                rssi_dbm,
                seen_count,
                first_offset_ms,
                last_offset_ms,
                created_at_ms,
            ),
        )


def insert_observation(
    ts_ms: int | str,
    unique_devices_count: int,
    raw_count: Optional[int] = None,
    db_path: Path = DEFAULT_DB_PATH,
    detections: list[dict[str, Any]] | None = None,
) -> None:
    init_db(db_path)
    epoch_ms = _to_epoch_ms(ts_ms)
    secret = _load_or_create_anon_secret(db_path)

    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO observations (ts_ms, unique_devices_count, raw_count)
            VALUES (?, ?, ?)
            """,
            (epoch_ms, unique_devices_count, raw_count),
        )
        observation_id = int(cursor.lastrowid)
        _insert_detections(
            conn,
            observation_id=observation_id,
            created_at_ms=epoch_ms,
            detections=detections or [],
            secret=secret,
        )
        conn.commit()


def get_sensor_config(db_path: Path = DEFAULT_DB_PATH) -> dict[str, int]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        out: dict[str, int] = {}
        for key, default_value in SENSOR_CONFIG_DEFAULTS.items():
            raw = _get_setting(conn, key)
            if raw is None:
                out[key] = default_value
                continue
            try:
                out[key] = int(raw)
            except (TypeError, ValueError):
                out[key] = default_value

        return out


def update_sensor_config(
    updates: dict[str, int],
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, int]:
    if not updates:
        return get_sensor_config(db_path=db_path)

    unknown_keys = sorted(set(updates) - set(SENSOR_CONFIG_DEFAULTS))
    if unknown_keys:
        raise ValueError(f"Unknown config keys: {', '.join(unknown_keys)}")

    normalized: dict[str, int] = {}
    for key, value in updates.items():
        lo, hi = SENSOR_CONFIG_LIMITS[key]
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid integer for {key}") from exc
        if parsed < lo or parsed > hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
        normalized[key] = parsed

    init_db(db_path)
    with get_connection(db_path) as conn:
        for key, value in normalized.items():
            _set_setting(conn, key, str(value))
        conn.commit()

    return get_sensor_config(db_path=db_path)


def latest_detections(seconds: int = 300, limit: int = 200, db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    init_db(db_path)
    window_ms = max(1, int(seconds)) * 1000
    max_items = max(1, min(1000, int(limit)))
    min_ts_ms = _now_ms() - window_ms

    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                created_at_ms,
                anon_device_id,
                transport,
                rssi_dbm,
                seen_count,
                first_offset_ms,
                last_offset_ms
            FROM detections
            WHERE created_at_ms >= ?
            ORDER BY created_at_ms DESC, seen_count DESC
            LIMIT ?
            """,
            (min_ts_ms, max_items),
        ).fetchall()

    output: list[dict[str, Any]] = []
    for row in rows:
        output.append(
            {
                "created_at_ms": int(row[0]),
                "ts_ms": int(row[0]),
                "anon_device_id": str(row[1]),
                "transport": str(row[2]),
                "rssi_dbm": None if row[3] is None else int(row[3]),
                "rssi": None if row[3] is None else int(row[3]),
                "seen_count": int(row[4]),
                "first_offset_ms": int(row[5]),
                "last_offset_ms": int(row[6]),
                "offsets_ms": [int(row[5]), int(row[6])],
            }
        )
    return output


def top_detections(
    from_ms: int,
    to_ms: int,
    limit: int = 50,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    init_db(db_path)
    start_ms = int(from_ms)
    end_ms = int(to_ms)
    max_items = max(1, min(500, int(limit)))

    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                anon_device_id,
                transport,
                COUNT(*) AS observations,
                COALESCE(SUM(seen_count), 0) AS seen_total,
                COALESCE(AVG(rssi_dbm), 0) AS avg_rssi_dbm,
                COALESCE(MAX(rssi_dbm), 0) AS max_rssi_dbm,
                MIN(created_at_ms) AS first_seen_ms,
                MAX(created_at_ms) AS last_seen_ms
            FROM detections
            WHERE created_at_ms >= ? AND created_at_ms <= ?
            GROUP BY anon_device_id, transport
            ORDER BY observations DESC, seen_total DESC, last_seen_ms DESC
            LIMIT ?
            """,
            (start_ms, end_ms, max_items),
        ).fetchall()

    return [
        {
            "anon_device_id": str(row[0]),
            "transport": str(row[1]),
            "observations": int(row[2]),
            "seen_total": int(row[3]),
            "avg_rssi_dbm": float(row[4]),
            "max_rssi_dbm": int(row[5]),
            "first_seen_ms": int(row[6]),
            "last_seen_ms": int(row[7]),
        }
        for row in rows
    ]


def purge_old_data(
    retention_days: int | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    now_ms: int | None = None,
    batch_size: int = 500,
) -> dict[str, int]:
    init_db(db_path)
    if retention_days is None:
        retention_days = get_sensor_config(db_path=db_path)["retention_days"]

    horizon_days = max(1, int(retention_days))
    current_ms = _now_ms() if now_ms is None else int(now_ms)
    min_ts_ms = current_ms - (horizon_days * DAY_MS)
    page = max(1, int(batch_size))

    deleted_detections = 0
    deleted_observations = 0

    with get_connection(db_path) as conn:
        while True:
            count = conn.execute(
                """
                DELETE FROM detections
                WHERE rowid IN (
                    SELECT rowid FROM detections
                    WHERE created_at_ms < ?
                    LIMIT ?
                )
                """,
                (min_ts_ms, page),
            ).rowcount
            conn.commit()
            chunk = max(0, int(count))
            deleted_detections += chunk
            if chunk < page:
                break

        while True:
            count = conn.execute(
                """
                DELETE FROM observations
                WHERE rowid IN (
                    SELECT rowid FROM observations
                    WHERE ts_ms < ?
                    LIMIT ?
                )
                """,
                (min_ts_ms, page),
            ).rowcount
            conn.commit()
            chunk = max(0, int(count))
            deleted_observations += chunk
            if chunk < page:
                break

    return {
        "retention_days": horizon_days,
        "deleted_observations": deleted_observations,
        "deleted_detections": deleted_detections,
        "purge_before_ts_ms": min_ts_ms,
    }


def stats_for_day(day: str, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Optional[float]]:
    init_db(db_path)
    day_start = datetime.combine(date.fromisoformat(day), datetime.min.time(), tzinfo=timezone.utc)
    start_ms = int(day_start.timestamp() * 1000)
    end_ms = start_ms + DAY_MS

    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS observations,
                COALESCE(SUM(unique_devices_count), 0) AS total_unique,
                COALESCE(AVG(unique_devices_count), 0) AS avg_unique,
                COALESCE(MAX(unique_devices_count), 0) AS max_unique,
                COALESCE(MIN(unique_devices_count), 0) AS min_unique
            FROM observations
            WHERE ts_ms >= ? AND ts_ms < ?
            """,
            (start_ms, end_ms),
        ).fetchone()

    return {
        "observations": int(row[0]),
        "total_unique": int(row[1]),
        "avg_unique": float(row[2]),
        "max_unique": int(row[3]),
        "min_unique": int(row[4]),
    }


def daily_peak_and_avg(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, float | str | int]]:
    init_db(db_path)
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
            GROUP BY ((ts_ms / 86400000) * 86400000)
            ORDER BY day_start_ms
            """
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


def hourly_peak_and_avg(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, float | str | int]]:
    init_db(db_path)
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
            GROUP BY ((ts_ms / 3600000) * 3600000)
            ORDER BY hour_start_ms
            """
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


def latest_observation(db_path: Path = DEFAULT_DB_PATH) -> Optional[dict[str, int]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT ts_ms, unique_devices_count, COALESCE(raw_count, 0) AS raw_count
            FROM observations
            ORDER BY ts_ms DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    return {
        "ts_ms": int(row[0]),
        "unique_devices_count": int(row[1]),
        "raw_count": int(row[2]),
    }


def latest_observations(limit: int = 20, db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, int]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT ts_ms, unique_devices_count, COALESCE(raw_count, 0) AS raw_count
            FROM observations
            ORDER BY ts_ms DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    return [
        {
            "ts_ms": int(row[0]),
            "unique_devices_count": int(row[1]),
            "raw_count": int(row[2]),
        }
        for row in rows
    ]
