# RPIpulse

CLI en Python para escaneo Bluetooth Low Energy (BLE) con BlueZ y cálculo de ocupación BT local usando SQLite.

## Contexto técnico

- Escaneo BLE con `bleak`.
- En Linux, `bleak` usa BlueZ como stack Bluetooth subyacente.
- Las métricas reflejan el entorno de radio BT observado por el adaptador durante cada ventana de escaneo.

## Qué mide (ocupación BT)

RPIpulse guarda observaciones de escaneo en la tabla `observations`:

- `ts_ms` (INTEGER): epoch UTC en milisegundos.
- `unique_devices_count` (INTEGER): cantidad de dispositivos únicos detectados.
- `raw_count` (INTEGER): cantidad cruda de detecciones.

Interpretación práctica de ocupación BT:

- Mayor `unique_devices_count` sugiere mayor diversidad de emisores BLE cercanos.
- Mayor `raw_count` sugiere mayor volumen de actividad/capturas durante el escaneo.

## Métricas

Comandos:

```bash
rpipulse stats --group daily
rpipulse stats --group hourly
```

Buckets en SQLite por división entera sobre `ts_ms`:

- Día: `day_start_ms = (ts_ms/86400000)*86400000`
- Hora: `hour_start_ms = (ts_ms/3600000)*3600000`

Para cada bucket se reporta:

- `peak_unique` = `MAX(unique_devices_count)`
- `avg_unique` = `AVG(unique_devices_count)`
- `peak_raw` = `MAX(raw_count)`
- `avg_raw` = `AVG(raw_count)`

Ejemplo de salida:

- `daily day=2026-02-27 peak_unique=9 avg_unique=4.33 peak_raw=10 avg_raw=5.67`
- `hourly hour=2026-02-27 10:00 peak_unique=3 avg_unique=2.00 peak_raw=5 avg_raw=3.50`

## Instalación

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Con backend BLE real:

```bash
pip install -e .[ble]
```

## Uso

Escaneo:

```bash
rpipulse scan --duration 15
```

Estadísticas:

```bash
rpipulse stats --group daily
rpipulse stats --group hourly
```

Alias de compatibilidad opcional:

```bash
analizador scan --duration 15
analizador stats --group daily
```

## Base de datos

Ruta por defecto:

- `data/rpipulse.sqlite`

Índices:

- `idx_observations_ts_ms`
- `idx_observations_day_start_ms`
- `idx_observations_hour_start_ms`

## Limitaciones

- Sin adaptador BLE o permisos adecuados, el escaneo puede devolver 0 dispositivos.
- En CI/contenedores/hosts sin Bluetooth activo, `bleak` puede no funcionar.
- Las mediciones dependen de hardware, posición, interferencia de radio y ventanas de escaneo.

## DoD

- Proyecto/package/CLI renombrados a `rpipulse`.
- `stats --group daily|hourly` reporta MAX/AVG para `unique_devices_count` y `raw_count`.
- Persistencia en `ts_ms` (epoch ms UTC) con índices de tiempo.
- Compatibilidad de CLI vía alias `analizador`.
- Tests con `pytest` en verde.
