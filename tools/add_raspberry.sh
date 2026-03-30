#!/bin/bash
#
# add_raspberry.sh - Despliega RPIpulse a una Raspberry Pi
#
# Uso:
#   ./add_raspberry.sh --host <ip> --user <user> [--port <sshport>] --name <alias>
#
# Ejemplo:
#   ./add_raspberry.sh --host 192.168.1.100 --user pi --name salon
#   ./add_raspberry.sh --host 192.168.1.101 --user pi --port 2222 --name cocina
#

set -euo pipefail

# Defaults
SSH_PORT=22
SSH_USER=""
RPI_HOST=""
RPI_NAME=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SSH_KEY_FILE="${RPIPULSE_SSH_KEY_PATH:-/etc/rpipulse/ssh/id_ed25519}"
USE_KEY_AUTH=false
if [[ -f "$SSH_KEY_FILE" ]]; then
  USE_KEY_AUTH=true
fi
RELEASE_NAME="$(date -u +%Y%m%d%H%M%S)"
RELEASE_ARCHIVE="/tmp/rpipulse-release-${RELEASE_NAME}.tar.gz"

# SSH ControlMaster for single connection (avoid antibot/blocking)
# Uses a single persistent SSH connection for all operations
SSH_CONTROL_DIR="/tmp/rpipulse-ssh-$$"
SSH_CONTROL_SOCKET="${SSH_CONTROL_DIR}/socket"
SSH_OPTS_BASE=(
  -p "${SSH_PORT}"
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o ControlMaster=auto
  -o ControlPath="${SSH_CONTROL_SOCKET}"
  -o ControlPersist=300
)
if [[ "$USE_KEY_AUTH" == "true" ]]; then
  SSH_OPTS_BASE+=(-i "${SSH_KEY_FILE}")
fi

# Cleanup function for SSH control socket
cleanup_ssh() {
  if [[ -n "$SSH_CONTROL_SOCKET" ]] && [[ -S "$SSH_CONTROL_SOCKET" ]]; then
    ssh -S "$SSH_CONTROL_SOCKET" -O exit dummy 2>/dev/null || true
  fi
  rm -f "$RELEASE_ARCHIVE" 2>/dev/null || true
  rm -rf "$SSH_CONTROL_DIR" 2>/dev/null || true
}
trap cleanup_ssh EXIT

# Helper function to run SSH commands using the persistent connection
ssh_exec() {
  ssh "${SSH_OPTS_BASE[@]}" "$@"
}

scp_copy() {
  scp "${SSH_OPTS_BASE[@]}" "$@"
}

# Parse args
while [[ $# -gt 0 ]]; do
  case $1 in
    --host)
      RPI_HOST="$2"
      shift 2
      ;;
    --user)
      SSH_USER="$2"
      shift 2
      ;;
    --port)
      SSH_PORT="$2"
      shift 2
      ;;
    --name)
      RPI_NAME="$2"
      shift 2
      ;;
    --help|-h)
      echo "Uso: $0 --host <ip> --user <user> [--port <sshport>] --name <alias>"
      echo ""
      echo "Argumentos:"
      echo "  --host <ip>     IP de la Raspberry Pi"
      echo "  --user <user>   Usuario SSH (ej: pi)"
      echo "  --port <puerto> Puerto SSH opcional (default: 22)"
      echo "  --name <alias>  Nombre/alias para la Raspberry (ej: salon, cocina)"
      echo ""
      echo "El script despliega a:"
      echo "  - /opt/rpipulse/releases/<version>/"
      echo "  - /opt/rpipulse/current -> symlink"
      echo "  - /etc/rpipulse/rpipulse.env (con RPIPULSE_DB_PATH)"
      echo "  - /var/lib/rpipulse/"
      echo "  - Usuario 'rpipulse' con grupo bluetooth"
      echo "  - Servicios: rpipulse-scan.timer, rpipulse-ui.service"
      echo ""
      echo "Notas:"
      echo "  - Usa SSH ControlMaster para evitar bloqueos por conexiones repetidas"
      echo "  - Los servicios usan variables de entorno (no --config flag)"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

# Validate required args
if [[ -z "$RPI_HOST" ]]; then
  echo "Error: --host es obligatorio"
  exit 1
fi
if [[ -z "$SSH_USER" ]]; then
  echo "Error: --user es obligatorio"
  exit 1
fi
if [[ -z "$RPI_NAME" ]]; then
  echo "Error: --name es obligatorio"
  exit 1
fi

echo "=========================================="
echo "  Despliegue RPIpulse -> $RPI_NAME ($RPI_HOST)"
echo "=========================================="
echo ""
echo "  Proyecto local: $PROJECT_ROOT"
if [[ "$USE_KEY_AUTH" == "true" ]]; then
  echo "  SSH key: $SSH_KEY_FILE"
else
  echo "  SSH key: none (using current SSH agent/config)"
fi

# Preparar release local
echo "[0/8] Empaquetando release local..."
tar -C "$PROJECT_ROOT" \
  --exclude=".git" \
  --exclude=".pytest_cache" \
  --exclude="__pycache__" \
  --exclude=".venv" \
  --exclude="data/generated_sync" \
  -czf "$RELEASE_ARCHIVE" \
  pyproject.toml README.md src tools docs
echo "  Release empaquetado en $RELEASE_ARCHIVE"

# Initialize SSH control connection first
echo "[*] Estableciendo conexión SSH persistente..."
ssh_exec -o ConnectTimeout=10 ${SSH_USER}@${RPI_HOST} "echo 'SSH connection ready'" || {
  echo "ERROR: No se pudo establecer conexión SSH"
  exit 1
}

# 1. Crear usuario rpipulse y grupo bluetooth
echo "[1/8] Creando usuario rpipulse y grupo bluetooth..."
ssh_exec ${SSH_USER}@${RPI_HOST} "
  sudo useradd -r -s /bin/bash -m -d /var/lib/rpipulse rpipulse 2>/dev/null || true
  sudo usermod -aG bluetooth rpipulse 2>/dev/null || true
  echo '  Usuario rpipulse creado/configurado'
"

# 1.5. Configurar SSH key para acceso sin password
echo "[1.5/8] Configurando SSH key para acceso sin password..."
if [[ "$USE_KEY_AUTH" == "true" ]] && [[ -f "$SSH_KEY_FILE" ]]; then
  PUB_KEY=$(cat ${SSH_KEY_FILE}.pub)
  ssh_exec ${SSH_USER}@${RPI_HOST} "
    sudo mkdir -p /var/lib/rpipulse/.ssh
    sudo chmod 700 /var/lib/rpipulse/.ssh
    echo '$PUB_KEY' | sudo tee /var/lib/rpipulse/.ssh/authorized_keys > /dev/null
    sudo chmod 600 /var/lib/rpipulse/.ssh/authorized_keys
    sudo chown -R rpipulse:rpipulse /var/lib/rpipulse/.ssh
    echo '  SSH key configurada para usuario rpipulse'
  "
else
  echo "  SKIP: SSH key auth deshabilitado o archivo no encontrado"
fi

# 2. Crear directorios necesarios y archivo de entorno
echo "[2/8] Creando directorios y archivo de entorno..."
ssh_exec ${SSH_USER}@${RPI_HOST} "
  sudo mkdir -p /opt/rpipulse/releases
  sudo mkdir -p /etc/rpipulse
  sudo mkdir -p /var/lib/rpipulse/data
  sudo mkdir -p /var/lib/rpipulse/releases
  sudo chown rpipulse:rpipulse /var/lib/rpipulse
  
  # Create environment file with DB path
  echo 'RPIPULSE_DB_PATH=/var/lib/rpipulse/data/rpipulse.sqlite' | sudo tee /etc/rpipulse/rpipulse.env > /dev/null
  sudo chmod 644 /etc/rpipulse/rpipulse.env
  
  echo '  Directorios y rpipulse.env creados'
"

# 3. Subir y configurar release
echo "[3/8] Subiendo y configurando release..."
scp_copy "$RELEASE_ARCHIVE" ${SSH_USER}@${RPI_HOST}:/tmp/rpipulse-release.tar.gz
ssh_exec ${SSH_USER}@${RPI_HOST} "
  RELEASE_DIR=/opt/rpipulse/releases/${RELEASE_NAME}
  sudo rm -rf \$RELEASE_DIR
  sudo mkdir -p \$RELEASE_DIR
  sudo tar -xzf /tmp/rpipulse-release.tar.gz -C \$RELEASE_DIR
  sudo chown -R rpipulse:rpipulse \$RELEASE_DIR
  sudo rm -f /tmp/rpipulse-release.tar.gz
  if ! command -v python3 >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y python3 python3-venv python3-pip
  fi
  sudo -u rpipulse python3 -m venv \$RELEASE_DIR
  sudo -u rpipulse \$RELEASE_DIR/bin/pip install --upgrade pip wheel setuptools >/dev/null
  sudo -u rpipulse \$RELEASE_DIR/bin/pip install -e \"\$RELEASE_DIR[ble,ui]\" >/dev/null
  sudo ln -sfn \$RELEASE_DIR /opt/rpipulse/current
  if [ ! -f /opt/rpipulse/current/bin/rpipulse ]; then
    echo 'ERROR: /opt/rpipulse/current/bin/rpipulse no existe'
    exit 1
  fi
  echo '  Release configurado: '\$RELEASE_DIR
"

# 4. Instalar units systemd (corregidas - sin --config, usan env vars)
#    Y crear servicio de ensure bluetooth
echo "[4/8] Instalando units systemd y servicio Bluetooth ensure..."
ssh_exec ${SSH_USER}@${RPI_HOST} "
  # Instalar rfkill si no existe
  if ! command -v rfkill &>/dev/null; then
    sudo apt-get update -qq && sudo apt-get install -y rfkill 2>/dev/null || true
  fi

  # Configurar AutoEnable=true en bluetooth main.conf
  if [ -f /etc/bluetooth/main.conf ]; then
    if ! grep -q '^AutoEnable=true' /etc/bluetooth/main.conf; then
      echo 'AutoEnable=true' | sudo tee -a /etc/bluetooth/main.conf > /dev/null
      sudo systemctl restart bluetooth.service 2>/dev/null || true
    fi
  fi

  # Crear servicio oneshot para asegurar bluetooth activo al boot
  sudo tee /etc/systemd/system/rpipulse-bt-ensure.service > /dev/null << 'BTENSUREEOF'
[Unit]
Description=RpiPulse Bluetooth Ensure (power on)
DefaultDependencies=no
After=bluetooth.service
Before=rpipulse-scan.service
Wants=bluetooth.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c 'rfkill unblock bluetooth || true; bluetoothctl power on || true; hciconfig hci0 up 2>/dev/null || true'
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
BTENSUREEOF

  sudo systemctl daemon-reload
  sudo systemctl enable --now rpipulse-bt-ensure.service 2>/dev/null || true
  echo '  Bluetooth ensure service instalado'
"

echo "[4.5/8] Actualizando rpipulse-scan.service con dependencia Bluetooth..."
ssh_exec ${SSH_USER}@${RPI_HOST} "
  # Actualizar rpipulse-scan.service para depender de bt-ensure
  sudo tee /etc/systemd/system/rpipulse-scan.service > /dev/null << 'UNITEOF'
[Unit]
Description=RpiPulse BLE scan (single run)
Wants=rpipulse-bt-ensure.service
After=rpipulse-bt-ensure.service bluetooth.service dbus.service

[Service]
Type=oneshot
User=rpipulse
Group=rpipulse
SupplementaryGroups=bluetooth
StateDirectory=rpipulse
WorkingDirectory=/var/lib/rpipulse
EnvironmentFile=/etc/rpipulse/rpipulse.env

ExecStart=/opt/rpipulse/current/bin/rpipulse scan --windows 1

SuccessExitStatus=0 2
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/rpipulse

[Install]
WantedBy=multi-user.target
UNITEOF

  sudo tee /etc/systemd/system/rpipulse-scan.timer > /dev/null << 'TIMEREOF'
[Unit]
Description=RpiPulse BLE scan timer

[Timer]
OnBootSec=20s
OnUnitActiveSec=10s
AccuracySec=1s
Unit=rpipulse-scan.service
Persistent=true

[Install]
WantedBy=timers.target
TIMEREOF

  sudo tee /etc/systemd/system/rpipulse-ui.service > /dev/null << 'UIEOF'
[Unit]
Description=RpiPulse UI (web)
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=rpipulse
Group=rpipulse
StateDirectory=rpipulse
WorkingDirectory=/var/lib/rpipulse
EnvironmentFile=/etc/rpipulse/rpipulse.env

ExecStart=/opt/rpipulse/current/bin/rpipulse ui --host 0.0.0.0 --port 8000

Restart=on-failure
RestartSec=2s
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/rpipulse

[Install]
WantedBy=multi-user.target
UIEOF

  sudo systemctl daemon-reload
  echo '  Units instaladas'
"

# 5. Habilitar servicios
echo "[5/8] Habilitando servicios..."
ssh_exec ${SSH_USER}@${RPI_HOST} "
  sudo systemctl enable --now rpipulse-scan.timer
  sudo systemctl enable --now rpipulse-ui.service
  echo '  Servicios habilitados'
"

# 6. Verificar estado
echo "[6/8] Verificando servicios..."
ssh_exec ${SSH_USER}@${RPI_HOST} "
  systemctl status rpipulse-scan.timer --no-pager || true
  systemctl status rpipulse-ui.service --no-pager || true
"

# 7. Mostrar URL final
echo "[7/8] Completado!"
echo ""
echo "=========================================="
echo "  RPIpulse desplegado en $RPI_NAME"
echo "=========================================="
echo ""
echo "  URL del dashboard:"
echo "  -> http://${RPI_HOST}:8000"
echo ""
echo "  Comandos en la Pi:"
echo "  - Logs UI:     journalctl -u rpipulse-ui.service -n 50 --no-pager"
echo "  - Logs scan:  journalctl -u rpipulse-scan.service -n 50 --no-pager"
echo "  - Estado:     systemctl status rpipulse-scan.timer rpipulse-ui.service"
echo ""


# 8. Verificar acceso SSH del usuario rpipulse
echo "[8/8] Verificando acceso SSH del usuario rpipulse..."
if ssh_exec rpipulse@${RPI_HOST} "echo OK && whoami && echo \$SHELL" 2>/dev/null; then
  echo "  ✓ Acceso SSH verificado para usuario rpipulse"
else
  echo "  ⚠ Advertencia: No se pudo verificar acceso SSH de rpipulse"
  echo "    ( Puede ser que el servicio SSH necesite reiniciarse en la Pi )"
fi
