# RPIpulse Terminal Feature - Testing Guide

## Prerequisites

1. **SSH Key Setup**: The hub needs an SSH key at `/etc/rpipulse/ssh/id_ed25519`
2. **Node Access**: Target nodes must have the hub's public key in their `authorized_keys`

## Environment Variables

```bash
# Enable terminal feature (required)
export RPIPULSE_ENABLE_TERMINAL=1

# Admin token for authentication (required)
export RPIPULSE_ADMIN_TOKEN="your-secure-token-here"

# SSH key path (optional, defaults to /etc/rpipulse/ssh/id_ed25519)
export RPIPULSE_SSH_KEY_PATH="/etc/rpipulse/ssh/id_ed25519"

# Database path (optional)
export RPIPULSE_DB_PATH="data/rpipulse.sqlite"
```

## Start Server

```bash
cd /root/projects/rpipulse
source .venv/bin/activate
pip install "uvicorn[standard]" asyncssh

RPIPULSE_ENABLE_TERMINAL=1 \
RPIPULSE_ADMIN_TOKEN=6e188e41225f1da22f5f0cb7a81dfda4 \
python -m uvicorn rpipulse.webui.app:app --host 0.0.0.0 --port 8000
```

## API Endpoints

### Public Endpoints (No Auth Required)

| Endpoint | Description |
|----------|GET /api/nodes` | List-------------|
| ` all nodes (no passwords exposed) |
| `GET /terminal` | Terminal UI page |
| `GET /terminal/{node_id}` | Terminal UI with pre-selected node |

### Admin Endpoints (Token Required)

Add `?token=YOUR_ADMIN_TOKEN` to all admin endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/nodes` | GET | List all nodes (admin version) |
| `/api/nodes` | POST | Add new node |
| `/api/nodes/{node_id}` | PUT | Update node |
| `/api/nodes/{node_id}` | DELETE | Delete node |
| `/api/nodes/session-logs` | GET | Get terminal session logs |

### WebSocket Endpoint

| Endpoint | Query Params | Description |
|----------|--------------|-------------|
| `/ws/terminal/{node_id}` | `token` (required) | Terminal WebSocket |

## Testing with curl

```bash
# List nodes (public - no token needed)
curl http://localhost:8000/api/nodes

# Add a node (requires token)
curl -X POST "http://localhost:8000/api/nodes?token=YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id": "pi-office", "name": "Office Pi", "host": "192.168.1.100", "port": 22, "user": "rpipulse"}'

# Get session logs (requires token)
curl "http://localhost:8000/api/nodes/session-logs?token=YOUR_TOKEN"
```

## Testing with WebSocket

Using Python:

```python
import asyncio
import websockets

async def test_terminal():
    token = "YOUR_TOKEN"
    async with websockets.connect(f"ws://localhost:8000/ws/terminal/hub-local?token={token}") as ws:
        # Send command
        await ws.send("echo 'Hello'\n")
        
        # Receive output
        while True:
            msg = await ws.recv()
            print(msg)

asyncio.run(test_terminal())
```

Using wscat (CLI):

```bash
# Install wscat
npm install -g wscat

# Connect (will prompt for token)
wscat -c "ws://localhost:8000/ws/terminal/hub-local?token=YOUR_TOKEN"

# Or with query param
wscat -c "ws://localhost:8000/ws/terminal/hub-local?token=YOUR_TOKEN"
```

## Security Features

1. **Feature Flag**: Terminal is disabled unless `RPIPULSE_ENABLE_TERMINAL=1`
2. **Token Auth**: All admin APIs and WebSocket require valid `RPIPULSE_ADMIN_TOKEN`
3. **No Passwords**: Nodes store only user/host/port - SSH key auth only
4. **Client IP Logging**: Sessions log the client IP address

## Troubleshooting

### "SSH key not found"
- Ensure `/etc/rpipulse/ssh/id_ed25519` exists
- Run: `sudo ssh-keygen -t ed25519 -f /etc/rpipulse/ssh/id_ed25519 -N ""`

### "Permission denied" when connecting
- Add public key to target node's `authorized_keys`:
  ```bash
  cat /etc/rpipulse/ssh/id_ed25519.pub | ssh user@target "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
  ```

### WebSocket connection fails
- Ensure `uvicorn[standard]` is installed (provides WebSocket support)
- Check server logs for error messages

## Production Checklist

- [ ] Generate secure admin token: `openssl rand -hex 32`
- [ ] Add SSH public key to all target nodes
- [ ] Set environment variables in systemd service or container
- [ ] Use HTTPS in production (reverse proxy with TLS termination)
- [ ] Consider rate limiting on the WebSocket endpoint
- [ ] Monitor session logs for suspicious activity
