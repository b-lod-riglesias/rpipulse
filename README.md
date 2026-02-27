# analizador

CLI en Python para registrar observaciones de dispositivos BLE y consultar estadísticas diarias usando SQLite local.

## Requisitos

- Python 3.9+
- Opcional: adaptador Bluetooth LE y permisos del sistema para escaneo

## Instalación

### Desarrollo (editable)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Con soporte BLE real (bleak)

```bash
pip install -e .[ble]
```

## Uso

### Escaneo BLE

```bash
analizador scan --duration 15
```

- Intenta usar `bleak` para descubrir dispositivos BLE durante la duración indicada.
- Guarda una observación en `data/analizador.sqlite` en la tabla `observations`.
- Si `bleak` no está instalado o falla el escaneo, usa fallback seguro que no rompe ejecución (cuenta 0 dispositivos).

### Estadísticas del día

```bash
analizador stats --day today
```

Muestra estadísticas básicas del día actual:
- número de observaciones
- suma total de dispositivos únicos detectados
- promedio de dispositivos únicos por observación
- máximo y mínimo de dispositivos únicos

## Base de datos

Ruta por defecto:

- `data/analizador.sqlite`

Esquema:

- `observations(ts TEXT, unique_devices_count INTEGER, raw_count INTEGER NULL)`

## Tests

```bash
pip install -e .[test]
pytest -q
```

## Limitaciones

- El escaneo BLE depende del hardware, sistema operativo y permisos.
- En algunos entornos (CI, contenedores, servidores sin BT), `bleak` puede no funcionar.
- El fallback mock evita errores fatales pero no produce lecturas reales BLE.
