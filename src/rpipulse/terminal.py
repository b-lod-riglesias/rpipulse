"""
Terminal WebSocket module for RPIpulse.

Provides SSH terminal access to nodes via WebSocket.
Security: Requires RPIPULSE_ENABLE_TERMINAL=1 and valid RPIPULSE_ADMIN_TOKEN.
Uses SSH key-based authentication from /etc/rpipulse/ssh/id_ed25519
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import asyncssh
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.requests import Request

from rpipulse.db import (
    DEFAULT_DB_PATH,
    get_node,
    init_nodes_db,
    update_node_last_seen,
)

# Configure logging
logger = logging.getLogger("rpipulse.terminal")

# Security settings
ENABLE_TERMINAL = os.environ.get("RPIPULSE_ENABLE_TERMINAL", "0") == "1"
ADMIN_TOKEN = os.environ.get("RPIPULSE_ADMIN_TOKEN", "")
SSH_KEY_PATH = os.environ.get("RPIPULSE_SSH_KEY_PATH", "/etc/rpipulse/ssh/id_ed25519")

# Validate SSH key path
SSH_KEY_FILE = Path(SSH_KEY_PATH)
if not SSH_KEY_FILE.exists():
    logger.warning(f"SSH key not found at {SSH_KEY_PATH}. Terminal feature will not work.")

# Session logging storage (in production, consider using a proper DB table)
terminal_sessions: list[dict[str, Any]] = []

# Router for terminal endpoints
router = APIRouter(prefix="", tags=["terminal"])


def verify_admin_token(token: Optional[str]) -> bool:
    """Verify admin token for terminal access."""
    if not ENABLE_TERMINAL:
        return False
    if not ADMIN_TOKEN:
        logger.warning("Terminal access attempted but RPIPULSE_ADMIN_TOKEN not set")
        return False
    return token == ADMIN_TOKEN


def get_client_ip(request: Request) -> str:
    """Extract client IP from request."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def log_session(
    node_id: str,
    client_ip: str,
    event: str,
    bytes_transferred: int = 0,
) -> None:
    """Log terminal session events."""
    session_entry = {
        "node_id": node_id,
        "client_ip": client_ip,
        "event": event,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "bytes": bytes_transferred,
    }
    terminal_sessions.append(session_entry)
    logger.info(f"Terminal session: node={node_id}, client={client_ip}, event={event}, bytes={bytes_transferred}")


async def ssh_terminal_handler(
    websocket: WebSocket,
    node_id: str,
    client_ip: str,
    db_path: Any,
) -> None:
    """Handle SSH terminal session via WebSocket using key-based auth."""
    node = get_node(node_id, db_path)
    
    if not node:
        await websocket.send_text("ERROR: Node not found")
        await websocket.close()
        return

    if not node.get("enabled", True):
        await websocket.send_text("ERROR: Node is disabled")
        await websocket.close()
        return

    # Validate SSH key exists
    if not SSH_KEY_FILE.exists():
        await websocket.send_text("ERROR: SSH key not configured on hub")
        await websocket.close()
        return

    # Update last_seen
    update_node_last_seen(node_id, db_path)
    
    host = node["host"]
    port = node.get("port", 22)
    user = node["user"]
    
    log_session(node_id, client_ip, "start")
    bytes_sent = 0
    bytes_received = 0

    # Connect to SSH with key-only authentication
    ssh_conn: Optional[asyncssh.SSHClientConnection] = None
    process: Optional[asyncssh.SSHClientProcess] = None
    
    try:
        # Create SSH connection with key-based authentication
        ssh_conn = await asyncssh.connect(
            host=host,
            port=port,
            username=user,
            client_keys=[str(SSH_KEY_FILE)],
            known_hosts=None,  # In production, use proper known_hosts
            server_host_key_algs=["ssh-rsa", "rsa-sha2-256", "rsa-sha2-512", "ssh-ed25519"],
        )
        
        # Open pseudo-terminal using the new asyncssh 2.x API
        # Use request_pty=True and term_type instead of term=
        process = await ssh_conn.create_process(
            command="bash",
            stdin=asyncssh.PIPE,
            stdout=asyncssh.PIPE,
            stderr=asyncssh.PIPE,
            request_pty=True,
            term_type="xterm-256color",
        )
        
        # Start reader tasks
        async def read_stdout():
            nonlocal bytes_sent
            try:
                while True:
                    data = await process.stdout.read(1024)
                    if not data:
                        break
                    await websocket.send_text(data)
                    bytes_sent += len(data)
            except Exception:
                pass

        async def read_stderr():
            nonlocal bytes_sent
            try:
                while True:
                    data = await process.stderr.read(1024)
                    if not data:
                        break
                    await websocket.send_text(data)
                    bytes_sent += len(data)
            except Exception:
                pass

        # Start readers
        readers = asyncio.gather(
            read_stdout(),
            read_stderr(),
        )

        # Forward WebSocket to SSH
        try:
            while True:
                data = await websocket.receive_text()
                bytes_received += len(data)
                if process.stdin:
                    process.stdin.write(data)
        except WebSocketDisconnect:
            pass

        # Wait for readers to finish
        await readers

    except asyncssh.DisconnectError as e:
        await websocket.send_text(f"\r\n[Disconnected: {e}]\r\n")
        log_session(node_id, client_ip, "end", bytes_sent + bytes_received)
    except Exception as e:
        logger.error(f"Terminal error: {e}")
        await websocket.send_text(f"\r\n[Error: {e}]\r\n")
        log_session(node_id, client_ip, "error", bytes_sent + bytes_received)
    finally:
        if process:
            process.close()
        if ssh_conn:
            ssh_conn.close()
        log_session(node_id, client_ip, "end", bytes_sent + bytes_received)


def get_websocket_client_ip(websocket: WebSocket) -> str:
    """Extract client IP from WebSocket scope."""
    try:
        scope = websocket.scope
        if scope:
            # Check for X-Forwarded-For header first
            headers = scope.get("headers", [])
            for name, value in headers:
                if name == b"x-forwarded-for":
                    return value.decode("utf-8").split(",")[0].strip()
            # Try to get client from scope
            client = scope.get("client")
            if client:
                return client[0]  # (host, port) tuple
    except Exception as e:
        logger.warning(f"Could not get client IP: {e}")
    return "unknown"


@router.websocket("/ws/terminal/{node_id}")
async def websocket_terminal(
    websocket: WebSocket,
    node_id: str,
    token: Optional[str] = Query(None),
    db_path: Any = DEFAULT_DB_PATH,
) -> None:
    """WebSocket endpoint for terminal access to a node."""
    # Check feature flag
    if not ENABLE_TERMINAL:
        await websocket.close(code=4003, reason="Terminal feature disabled")
        return

    # Check SSH key exists
    if not SSH_KEY_FILE.exists():
        logger.error(f"SSH key not found at {SSH_KEY_PATH}")
        await websocket.close(code=4004, reason="SSH key not configured")
        return

    # Check admin token
    if not verify_admin_token(token):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # Get client IP from websocket scope
    client_ip = get_websocket_client_ip(websocket)
    
    # Accept connection
    await websocket.accept()
    
    log_session(node_id, client_ip, "connecting")
    
    # Handle the terminal session
    try:
        await ssh_terminal_handler(websocket, node_id, client_ip, db_path)
    except Exception as e:
        logger.error(f"Terminal session error: {e}")
        log_session(node_id, client_ip, "error")


# Add API endpoints for nodes management
def create_nodes_router() -> APIRouter:
    """Create router for nodes API endpoints."""
    from rpipulse.db import create_node, delete_node, get_all_nodes, update_node
    
    nodes_router = APIRouter(prefix="/api/nodes", tags=["nodes"])
    
    @nodes_router.get("")
    async def list_nodes(
        token: Optional[str] = Query(None),
        db_path: Any = DEFAULT_DB_PATH,
    ) -> dict[str, Any]:
        """List all registered nodes."""
        if not verify_admin_token(token):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        nodes = get_all_nodes(db_path)
        # Don't expose passwords (should not exist, but safety first)
        for node in nodes:
            node.pop("password", None)
        return {"nodes": nodes}
    
    @nodes_router.post("")
    async def add_node(
        request: Request,
        token: Optional[str] = Query(None),
        db_path: Any = DEFAULT_DB_PATH,
    ) -> dict[str, Any]:
        """Add a new node (user/host/port only - no password storage)."""
        if not verify_admin_token(token):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        body = await request.json()
        
        node_id = body.get("id")
        name = body.get("name")
        host = body.get("host")
        port = body.get("port", 22)
        user = body.get("user")
        # Password is intentionally NOT accepted - use SSH key instead
        enabled = body.get("enabled", True)
        
        if not all([node_id, name, host, user]):
            raise HTTPException(status_code=400, detail="Missing required fields: id, name, host, user")
        
        node = create_node(
            node_id=node_id,
            name=name,
            host=host,
            port=port,
            user=user,
            password=None,  # SSH key auth - no password stored
            enabled=enabled,
            db_path=db_path,
        )
        
        # Don't expose password in response (should be None)
        node.pop("password", None)
        return {"node": node}
    
    @nodes_router.put("/{node_id}")
    async def update_node_endpoint(
        request: Request,
        node_id: str,
        token: Optional[str] = Query(None),
        db_path: Any = DEFAULT_DB_PATH,
    ) -> dict[str, Any]:
        """Update an existing node."""
        if not verify_admin_token(token):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        body = await request.json()
        
        node = update_node(
            node_id=node_id,
            name=body.get("name"),
            host=body.get("host"),
            port=body.get("port"),
            user=body.get("user"),
            password=None,  # SSH key auth - no password updates
            enabled=body.get("enabled"),
            db_path=db_path,
        )
        
        if not node:
            raise HTTPException(status_code=404, detail="Node not found")
        
        # Don't expose password in response
        node.pop("password", None)
        return {"node": node}
    
    @nodes_router.delete("/{node_id}")
    async def delete_node_endpoint(
        node_id: str,
        token: Optional[str] = Query(None),
        db_path: Any = DEFAULT_DB_PATH,
    ) -> dict[str, Any]:
        """Delete a node."""
        if not verify_admin_token(token):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        success = delete_node(node_id, db_path)
        
        if not success:
            raise HTTPException(status_code=404, detail="Node not found")
        
        return {"success": True, "node_id": node_id}
    
    @nodes_router.get("/session-logs")
    async def get_session_logs(
        token: Optional[str] = Query(None),
    ) -> dict[str, Any]:
        """Get terminal session logs (admin only)."""
        if not verify_admin_token(token):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        return {"sessions": terminal_sessions}
    
    return nodes_router
