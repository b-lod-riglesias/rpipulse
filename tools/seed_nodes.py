#!/usr/bin/env python3
"""
Seed script to add initial nodes to the database.

Usage:
    python -m tools.seed_nodes

Environment variables:
    RPIPULSE_DB_PATH: Path to SQLite database (default: data/rpipulse.sqlite)
    RPIPULSE_SEED_NODES: JSON array of nodes to seed
"""

import json
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rpipulse.db import create_node, get_all_nodes, init_nodes_db


def load_nodes_from_env() -> list[dict]:
    """Load nodes from RPIPULSE_SEED_NODES env var."""
    env_nodes = os.environ.get("RPIPULSE_SEED_NODES")
    if env_nodes:
        try:
            return json.loads(env_nodes)
        except json.JSONDecodeError as e:
            print(f"Error parsing RPIPULSE_SEED_NODES: {e}")
            return []
    return []


def main() -> int:
    db_path = Path(os.environ.get("RPIPULSE_DB_PATH", "data/rpipulse.sqlite"))
    
    # Initialize the nodes table
    init_nodes_db(db_path)
    
    # Get existing nodes
    existing = get_all_nodes(db_path)
    existing_ids = {n["id"] for n in existing}
    
    print(f"Existing nodes: {len(existing)}")
    
    # Load nodes from env or use defaults
    nodes_to_add = load_nodes_from_env()
    
    # Default seed nodes
    if not nodes_to_add:
        nodes_to_add = [
            {
                "id": "rpi-radio",
                "name": "rpiRadio-03",
                "host": "10.20.5.62",
                "port": 8000,
                "user": "rpipulse",
                "enabled": True,
            }
        ]
    
    added = 0
    for node_data in nodes_to_add:
        node_id = node_data.get("id")
        if not node_id:
            print("Skipping node without id")
            continue
            
        if node_id in existing_ids:
            print(f"Node '{node_id}' already exists, skipping")
            continue
        
        create_node(
            node_id=node_id,
            name=node_data.get("name", node_id),
            host=node_data.get("host", "127.0.0.1"),
            port=node_data.get("port", 22),
            user=node_data.get("user", "pi"),
            password=node_data.get("password"),
            enabled=node_data.get("enabled", True),
            db_path=db_path,
        )
        print(f"Added node: {node_id} ({node_data.get('name', 'N/A')})")
        added += 1
    
    # Show final state
    all_nodes = get_all_nodes(db_path)
    print(f"\nTotal nodes in database: {len(all_nodes)}")
    for node in all_nodes:
        print(f"  - {node['id']}: {node['name']} ({node['host']}) [enabled={node['enabled']}]")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
