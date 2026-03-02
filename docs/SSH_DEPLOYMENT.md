# RPIpulse - Guía de Despliegue Seguro SSH

Este documento describe cómo configurar el acceso SSH seguro desde el hub RPIpulse hacia las Raspberry Pi.

## Índice

1. [Arquitectura](#arquitectura)
2. [Generación de Keys SSH](#generación-de-keys-ssh)
3. [Configuración de Hardening SSH](#configuración-de-hardening-ssh)
4. [Variables de Entorno](#variables-de-entorno)
5. [Uso del Script de Despliegue](#uso-del-script-de-despliegue)
6. [Agregar una Nueva Raspberry](#agregar-una-nueva-raspberry)
7. [Solución de Problemas de Bluetooth](#solución-de-problemas-de-bluetooth)

---

## Arquitectura

```
┌─────────────────┐         SSH (key-only)         ┌─────────────────┐
│   Hub RPIpulse  │ ─────────────────────────────▶│  Raspberry Pi   │
│  (este host)    │    /etc/rpipulse/ssh/id_ed25519│   (objetivo)    │
└─────────────────┘                                  └─────────────────┘
```

- **Hub**: Máquina central que controla el despliegue
- **Raspberry Pi**: Dispositivo objetivo con usuario `rpipulse`

---

## Generación de Keys SSH

Las keys ya están generadas en este host:

```bash
# Key privada (NUNCA compartir)
ls -la /etc/rpipulse/ssh/id_ed25519

# Key pública (para agregar a authorized_keys)
cat /etc/rpipulse/ssh/id_ed25519.pub
```

**Output de la key pública:**
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJ0dmMicpNKtkQou2ZVTDRu0esZuLc0VkuTD0B1p6qGj rpipulse-deployment@hub
```

### Regenerar Keys (si es necesario)

```bash
sudo mkdir -p /etc/rpipulse/ssh
sudo ssh-keygen -t ed25519 -f /etc/rpipulse/ssh/id_ed25519 -N "" -C "rpipulse-deployment@hub"
sudo chmod 700 /etc/rpipulse/ssh
sudo chmod 600 /etc/rpipulse/ssh/id_ed25519
sudo chmod 644 /etc/rpipulse/ssh/id_ed25519.pub
```

---

## Configuración de Hardening SSH

### En el Hub (este host)

No es necesario modificar nada. El script ya usa key authentication.

### En las Raspberry Pi (objetivo)

Se recomienda aplicar las siguientes configuraciones en `/etc/ssh/sshd_config`:

```bash
# /etc/ssh/sshd_config en cada Raspberry Pi

# Deshabilitar password authentication para el usuario rpipulse
Match User rpipulse
    PasswordAuthentication no
    PubkeyAuthentication yes
    PermitEmptyPasswords no

# Deshabilitar root login (recomendado global)
PermitRootLogin no

# Opcional: deshabilitar passwords para todos
PasswordAuthentication no
```

**Aplicar cambios:**
```bash
sudo systemctl restart sshd
```

---

## Variables de Entorno

| Variable | Descripción | Default |
|----------|-------------|---------|
| `RPIPULSE_ENABLE_TERMINAL` | Habilita acceso terminal SSH a las Pi | `false` |
| `RPIPULSE_ADMIN_TOKEN` | Token para autenticación en APIs administrativas | (generado automáticamente) |
| `RPIPULSE_SSH_KEY_PATH` | Ruta a la key SSH privada | `/etc/rpipulse/ssh/id_ed25519` |

### Configurar Variables

```bash
# En el hub
export RPIPULSE_ENABLE_TERMINAL=true
export RPIPULSE_ADMIN_TOKEN="tu-token-seguro-aqui"

# O crear un token aleatorio
export RPIPULSE_ADMIN_TOKEN=$(openssl rand -hex 32)
```

---

## Uso del Script de Despliegue

### Sintaxis

```bash
./tools/add_raspberry.sh --host <IP> --user <usuario> [--port <puerto>] --name <alias>
```

### Ejemplos

```bash
# Desplegar a una Raspberry en la red local
./tools/add_raspberry.sh --host 192.168.1.100 --user pi --name salon

# Desplegar a otra Raspberry con puerto SSH personalizado
./tools/add_raspberry.sh --host 192.168.1.101 --user pi --port 2222 --name cocina

# Desplegar a Raspberry con usuario no estándar
./tools/add_raspberry.sh --host 192.168.1.102 --user admin --name oficina
```

### Lo que hace el script

1. Crea usuario `rpipulse` con grupo `bluetooth` (shell: /bin/bash, SIN sudo, para terminal web)
2. Configura SSH key para acceso sin password
3. Crea directorios necesarios (`/opt/rpipulse`, `/etc/rpipulse`, `/var/lib/rpipulse`)
4. Configura el release actual
5. Instala units systemd (`rpipulse-scan.service`, `rpipulse-scan.timer`, `rpipulse-ui.service`)
6. Habilita y arranca los servicios
7. Muestra URL del dashboard

---

## Agregar una Nueva Raspberry

### Paso 1: Preparar la Raspberry

Asegúrate de tener acceso SSH inicial a la Raspberry (puede ser con password):

```bash
# Desde el hub, verificar conectividad
ssh -p 22 pi@192.168.1.100 "echo 'OK'"
```

### Paso 2: Ejecutar el script de despliegue

```bash
cd /root/projects/rpipulse
./tools/add_raspberry.sh --host 192.168.1.100 --user pi --name mi Raspberry
```

### Paso 3: Verificar

```bash
# Desde el hub, probar acceso sin password
ssh -i /etc/rpipulse/ssh/id_ed25519 rpipulse@192.168.1.100 "hostname"

# Ver servicios en la Raspberry
ssh -i /etc/rpipulse/ssh/id_ed25519 rpipulse@192.168.1.100 "systemctl status rpipulse-ui.service"
```

---

## Acceso Terminal SSH (Web Terminal)

El usuario `rpipulse` está configurado para permitir sesiones SSH interactivas:

| Propiedad | Valor |
|-----------|-------|
| Usuario | `rpipulse` |
| Shell | `/bin/bash` |
| Home | `/var/lib/rpipulse` |
| sudo | **NO** (sin privilegios) |
| Autenticación | SSH key única |

### Probar acceso terminal:

```bash
# Desde el hub
ssh -i /etc/rpipulse/ssh/id_ed25519 rpipulse@<IP_RASPBERRY>
```

El script de despliegue (`add_raspberry.sh`) incluye una verificación automática post-setup que prueba el acceso SSH.


## Solución de Problemas de Bluetooth

### "Active Devices = 0" o Bluetooth apagado

Si el dashboard muestra "Active Devices = 0" y en la Raspberry:

```bash
bluetoothctl show
# Output esperado: Powered: yes

# O si está apagado:
# Powered: no
# PowerState: off-blocked
# hci0 DOWN
```

Ejecuta este bloque de comandos **UNA SOLA VEZ** en la Raspberry (como root o con sudo):

```bash
# 1. Instalar rfkill si no existe
sudo apt-get update -qq && sudo apt-get install -y rfkill

# 2. Habilitar bluetooth automático en main.conf
if ! grep -q '^AutoEnable=true' /etc/bluetooth/main.conf; then
    echo 'AutoEnable=true' | sudo tee -a /etc/bluetooth/main.conf
    sudo systemctl restart bluetooth.service
fi

# 3. Desbloquear y encender bluetooth
sudo rfkill unblock bluetooth
sudo bluetoothctl power on
sudo hciconfig hci0 up

# 4. Verificar estado
bluetoothctl show
```

**Para автоматиizar esto en cada reinicio**, el script `add_raspberry.sh` ya instala el servicio `rpipulse-bt-ensure.service`:

```bash
# Verificar que el servicio está activo
systemctl status rpipulse-bt-ensure.service

# Si no está instalado (Pi antiguas), instalarlo manualmente:
sudo systemctl enable --now rpipulse-bt-ensure.service
```

### Verificar que el scan funciona

```bash
# Forzar un scan manual
sudo -u rpipulse rpipulse scan --windows 1

# Ver logs
journalctl -u rpipulse-scan.service -n 20 --no-pager
```


## Solución de Problemas

### "Permission denied (publickey)"

1. Verificar que la key pública está en `/var/lib/rpipulse/.ssh/authorized_keys`
2. Verificar permisos: `.ssh` debe ser 700, `authorized_keys` debe ser 600
3. Verificar que el usuario `rpipulse` existe

```bash
# En la Raspberry
sudo ls -la /var/lib/rpipulse/.ssh/
sudo cat /var/lib/rpipulse/.ssh/authorized_keys
```

### "Connection refused"

1. Verificar que SSH está corriendo: `systemctl status sshd`
2. Verificar el puerto SSH

### "Timeout connecting"

1. Verificar conectividad de red: `ping 192.168.1.100`
2. Verificar firewall: `sudo ufw status`

---

## Archivos de Referencia

- **Script de despliegue**: `tools/add_raspberry.sh`
- **Keys SSH**: `/etc/rpipulse/ssh/`
- **Configuración**: `/etc/rpipulse/`
- **Datos**: `/var/lib/rpipulse/`
