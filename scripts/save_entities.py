#!/usr/bin/env python3
"""
Save extracted entities and relationships to the Neo4j knowledge graph.

Accepts JSON via stdin or --json argument. Designed to be called by
Claude Code skills after entity extraction.

Usage:
  echo '{"entities":[...],"relationships":[...]}' | python save_entities.py --source-path /path/to/file.pdf -p project
  python save_entities.py --source-path /path/to/file.pdf -p project --json '{"entities":[...],"relationships":[...]}'

JSON format:
  {
    "entities": [
      {"name": "Entity Name", "type": "PERSON", "description": "Brief description"}
    ],
    "relationships": [
      {"source": "Entity A", "target": "Entity B", "type": "uses", "description": "..."}
    ]
  }

Entity types: PERSON, ORGANIZATION, TECHNOLOGY, REQUIREMENT, SCHEDULE, BUDGET,
              RISK, PROPOSAL_PATTERN, EVALUATION_CRITERIA, DELIVERABLE, SECURITY,
              DOMAIN, CONCEPT
"""

import sys
import os
import json
import uuid
import argparse
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_config

from neo4j import GraphDatabase


def get_driver(cfg):
    return GraphDatabase.driver(cfg.neo4j_uri, auth=cfg.neo4j_auth)


def get_embeddings_batch(cfg, texts):
    """Get embeddings for a batch of texts."""
    if not texts:
        return []
    prefixed = [f"passage: {t[:2000]}" if t.strip() else "passage: empty" for t in texts]
    resp = requests.post(cfg.embed_url, json={"inputs": prefixed}, timeout=60)
    resp.raise_for_status()
    return resp.json()["embeddings"]


def query_existing_entities(driver, names):
    """Query existing entities by name. Returns dict of name -> {type, description, sources}."""
    if not names:
        return {}
    with driver.session() as session:
        result = session.run("""
            MATCH (e:Entity)
            WHERE e.name IN $names
            OPTIONAL MATCH (e)-[:SOURCED_FROM]->(d:Document)
            RETURN e.name AS name, e.type AS type, e.description AS description,
                   collect(DISTINCT d.source_path) AS sources
        """, names=list(names))
        return {r["name"]: {"type": r["type"], "description": r["description"],
                            "sources": r["sources"]} for r in result}


def save_entities_to_graph(driver, cfg, doc_id, entities, relationships):
    """Save extracted entities and relationships to Neo4j."""
    seen_names = set()
    unique_entities = []
    for ent in entities:
        name = ent["name"].strip()
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        unique_entities.append(ent)

    if not unique_entities:
        return 0, 0

    # Get embeddings for all entities
    entity_texts = [f"{e['name']}. {e.get('description', '')}" for e in unique_entities]
    embeddings = get_embeddings_batch(cfg, entity_texts)

    with driver.session() as session:
        # Create/merge Entity nodes
        for ent, emb in zip(unique_entities, embeddings):
            session.run("""
                MERGE (e:Entity {name: $name})
                ON CREATE SET
                    e.id = $id,
                    e.type = $type,
                    e.description = $description,
                    e.embedding = $embedding
                ON MATCH SET
                    e.type = CASE WHEN e.type IS NULL THEN $type ELSE e.type END,
                    e.description = CASE WHEN e.description IS NULL
                                         OR size(e.description) < size($description)
                                    THEN $description ELSE e.description END,
                    e.embedding = CASE WHEN e.embedding IS NULL THEN $embedding
                                  ELSE e.embedding END
            """, name=ent["name"].strip(), id=str(uuid.uuid4()),
                 type=ent["type"], description=ent.get("description", ""),
                 embedding=emb)

        # Create SOURCED_FROM relationships (Entity → Document)
        session.run("""
            MATCH (d:Document {id: $doc_id})
            MATCH (e:Entity)
            WHERE e.name IN $names
            MERGE (e)-[r:SOURCED_FROM]->(d)
            ON CREATE SET r.created_at = datetime()
        """, doc_id=doc_id, names=list(seen_names))

        # Create MENTIONS relationships (Chunk → Entity)
        session.run("""
            MATCH (d:Document {id: $doc_id})-[:HAS_CHUNK]->(c:Chunk)
            MATCH (e:Entity)
            WHERE e.name IN $names AND c.text CONTAINS e.name
            MERGE (c)-[:MENTIONS]->(e)
        """, doc_id=doc_id, names=list(seen_names))

        # Create RELATES_TO relationships (Entity ↔ Entity)
        rel_count = 0
        for rel in relationships:
            src = rel["source"].strip()
            tgt = rel["target"].strip()
            if src in seen_names and tgt in seen_names and src != tgt:
                session.run("""
                    MATCH (a:Entity {name: $src}), (b:Entity {name: $tgt})
                    MERGE (a)-[r:RELATES_TO]->(b)
                    ON CREATE SET r.type = $type, r.description = $desc, r.weight = 1.0
                    ON MATCH SET r.weight = r.weight + 0.5
                """, src=src, tgt=tgt, type=rel.get("type", "related"),
                     desc=rel.get("description", ""))
                rel_count += 1

    return len(unique_entities), rel_count


def main():
    parser = argparse.ArgumentParser(
        description="Save extracted entities and relationships to Neo4j")
    parser.add_argument("--source-path", required=True,
                        help="Document source path (to find doc in Neo4j)")
    parser.add_argument("--project", "-p", default=None,
                        help="Project name (key in config.py PROJECTS)")
    parser.add_argument("--json", dest="json_str", default=None,
                        help="JSON string with entities and relationships")
    args = parser.parse_args()

    # Read JSON from --json arg or stdin
    if args.json_str:
        data = json.loads(args.json_str)
    else:
        data = json.load(sys.stdin)

    entities = data.get("entities", [])
    relationships = data.get("relationships", [])

    if not entities:
        print("  [skip] No entities to save", file=sys.stderr)
        return

    cfg = get_config(args.project)
    print(f"  [config] {cfg}", file=sys.stderr)

    driver = get_driver(cfg)
    try:
        # Find document ID
        with driver.session() as session:
            result = session.run(
                "MATCH (d:Document {source_path: $path}) RETURN d.id AS doc_id",
                path=args.source_path)
            record = result.single()

        if not record:
            print(f"  [error] Document not found: {args.source_path}", file=sys.stderr)
            sys.exit(1)

        doc_id = record["doc_id"]
        e_count, r_count = save_entities_to_graph(
            driver, cfg, doc_id, entities, relationships)
        print(f"  [done] {e_count} entities, {r_count} relationships saved",
              file=sys.stderr)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
