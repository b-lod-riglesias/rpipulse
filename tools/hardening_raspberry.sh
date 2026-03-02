#!/bin/bash
set -e

SSH_PORT=22
SSH_USER=""
RPI_HOST=""
SSH_KEY_FILE="/etc/rpipulse/ssh/id_ed25519"

while [[ $# -gt 0 ]]; do
  case $1 in
    --host) RPI_HOST="$2"; shift 2 ;;
    --user) SSH_USER="$2"; shift 2 ;;
    --port) SSH_PORT="$2"; shift 2 ;;
    --key-file) SSH_KEY_FILE="$2"; shift 2 ;;
    --help|-h)
      echo "Usage: $0 --host <ip> --user <user> [--port <port>]"
      exit 0 ;;
    *) shift ;;
  esac
done

[[ -z "$RPI_HOST" ]] && { echo "Error: --host required"; exit 1; }
[[ -z "$SSH_USER" ]] && { echo "Error: --user required"; exit 1; }

SSH_OPTS="-p ${SSH_PORT} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ${SSH_KEY_FILE}"

echo "Hardening SSH -> $RPI_HOST"

echo "[1/4] Verifying SSH access..."
ssh $SSH_OPTS ${SSH_USER}@$RPI_HOST "echo OK" || { echo "ERROR: No SSH access"; exit 1; }

echo "[2/4] Backing up sshd_config..."
ssh $SSH_OPTS ${SSH_USER}@$RPI_HOST "sudo cp /etc/ssh/sshd_config /etc/ssh/sshd_config.backup.$(date +%Y%m%d)"

echo "[3/4] Applying hardening..."
ssh $SSH_OPTS ${SSH_USER}@$RPI_HOST "sudo tee /etc/ssh/sshd_config.d/rpipulse-hardening.conf > /dev/null << 'EOF'
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
PermitEmptyPasswords no
Protocol 2
ClientAliveInterval 300
ClientAliveCountMax 2
MaxAuthTries 3
MaxSessions 10


# Allow rpipulse user with key-based auth (for web terminal)
Match User rpipulse
    PasswordAuthentication no
    PubkeyAuthentication yes
    PermitEmptyPasswords no
EOF"

echo "[4/4] Restarting SSH..."
ssh $SSH_OPTS ${SSH_USER}@$RPI_HOST "sudo systemctl restart sshd"

echo "Hardening complete!"
