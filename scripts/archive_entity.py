#!/usr/bin/env python3
"""
Archive management for Entity nodes.

Subcommands:
  archive  - Set entity status to 'archived'
  restore  - Restore entity to 'active'
  list     - List archived entities

Usage:
  python archive_entity.py archive "Entity Name" -p <project> [--reason "理由"]
  python archive_entity.py restore "Entity Name" -p <project>
  python archive_entity.py list -p <project>
"""

import sys
import os
import json
import argparse

from neo4j import GraphDatabase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_config


def get_driver(cfg):
    return GraphDatabase.driver(cfg.neo4j_uri, auth=cfg.neo4j_auth)


def archive_entity(cfg, name, reason=None):
    """Archive an entity by setting status to 'archived'."""
    driver = get_driver(cfg)
    try:
        with driver.session() as session:
            result = session.run("""
                MATCH (e:Entity {name: $name})
                WHERE coalesce(e.status, 'active') = 'active'
                SET e.status = 'archived',
                    e.archived_date = datetime(),
                    e.archive_reason = $reason
                RETURN e.name AS name, e.type AS type
            """, name=name, reason=reason)
            record = result.single()
            if not record:
                # Check if already archived
                exists = session.run(
                    "MATCH (e:Entity {name: $name}) RETURN e.status AS status",
                    name=name).single()
                if exists and exists["status"] == "archived":
                    return {"error": "already_archived", "message": f"Entity '{name}' is already archived"}
                return {"error": "not_found", "message": f"Entity '{name}' not found"}
            return {"name": record["name"], "type": record["type"], "status": "archived"}
    finally:
        driver.close()


def restore_entity(cfg, name):
    """Restore an archived entity to active status."""
    driver = get_driver(cfg)
    try:
        with driver.session() as session:
            result = session.run("""
                MATCH (e:Entity {name: $name})
                WHERE e.status = 'archived'
                SET e.status = 'active',
                    e.archived_date = null,
                    e.archive_reason = null
                RETURN e.name AS name, e.type AS type
            """, name=name)
            record = result.single()
            if not record:
                exists = session.run(
                    "MATCH (e:Entity {name: $name}) RETURN e.status AS status",
                    name=name).single()
                if exists:
                    return {"error": "not_archived", "message": f"Entity '{name}' is not archived (status: {exists['status'] or 'active'})"}
                return {"error": "not_found", "message": f"Entity '{name}' not found"}
            return {"name": record["name"], "type": record["type"], "status": "active"}
    finally:
        driver.close()


def list_archived(cfg):
    """List all archived entities."""
    driver = get_driver(cfg)
    try:
        with driver.session() as session:
            result = session.run("""
                MATCH (e:Entity)
                WHERE e.status = 'archived'
                RETURN e.name AS name, e.type AS type,
                       e.description AS description,
                       e.archived_date AS archived_date,
                       e.archive_reason AS reason
                ORDER BY e.archived_date DESC
            """)
            return [dict(r) for r in result]
    finally:
        driver.close()


def main():
    parser = argparse.ArgumentParser(description="Entity archive management")
    parser.add_argument("command", choices=["archive", "restore", "list"])
    parser.add_argument("name", nargs="?", help="Entity name (required for archive/restore)")
    parser.add_argument("--project", "-p", default=None)
    parser.add_argument("--reason", default=None, help="Archive reason (archive only)")
    args = parser.parse_args()

    cfg = get_config(args.project)

    if args.command in ("archive", "restore") and not args.name:
        print("Error: entity name is required for archive/restore", file=sys.stderr)
        sys.exit(1)

    if args.command == "archive":
        result = archive_entity(cfg, args.name, args.reason)
    elif args.command == "restore":
        result = restore_entity(cfg, args.name)
    else:
        result = list_archived(cfg)

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    if isinstance(result, dict) and "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
