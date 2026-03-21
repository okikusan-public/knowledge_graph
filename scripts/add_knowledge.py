#!/usr/bin/env python3
"""
Add knowledge directly to the graph without requiring a document file.

Creates a source node (Note, WebSource, Conversation, or any label),
extracts entities, and links them via SOURCED_FROM.

Usage:
  echo '{"source": {...}, "entities": [...], "relationships": [...]}' | python add_knowledge.py
  python add_knowledge.py --json '{"source": {...}, "entities": [...], "relationships": [...]}'

JSON format:
  {
    "source": {
      "label": "Note",
      "properties": {
        "text": "Acme Corp is a software company with 500 employees",
        "author": "user"
      }
    },
    "entities": [
      {"name": "Acme Corp", "type": "ORGANIZATION", "description": "A software company"}
    ],
    "relationships": [
      {"source": "Entity A", "target": "Entity B", "type": "related", "description": "..."}
    ]
  }

Source labels: Note, WebSource, Conversation (or any custom label).
All source nodes get: id, created_at, embedding.
"""

import sys
import os
import json
import uuid
import argparse
import requests
from neo4j import GraphDatabase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_config


def get_driver(cfg):
    return GraphDatabase.driver(cfg.neo4j_uri, auth=cfg.neo4j_auth)


def get_embedding(cfg, text):
    """Get embedding for a single text."""
    prefixed = f"passage: {text[:2000]}"
    resp = requests.post(cfg.embed_url, json={"inputs": prefixed}, timeout=30)
    resp.raise_for_status()
    return resp.json()["embeddings"][0]


def get_embeddings_batch(cfg, texts):
    """Get embeddings for a batch of texts."""
    if not texts:
        return []
    prefixed = [f"passage: {t[:2000]}" if t.strip() else "passage: empty" for t in texts]
    resp = requests.post(cfg.embed_url, json={"inputs": prefixed}, timeout=60)
    resp.raise_for_status()
    return resp.json()["embeddings"]


def create_source_node(driver, cfg, label, properties):
    """Create a source node with the given label and properties."""
    if not label.isalnum():
        raise ValueError(f"Invalid label (alphanumeric only): {label}")

    node_id = str(uuid.uuid4())

    # Build embedding from text content
    text_for_embedding = properties.get("text", "") or properties.get("title", "") or ""
    if properties.get("url"):
        text_for_embedding += f" {properties['url']}"
    embedding = get_embedding(cfg, text_for_embedding) if text_for_embedding.strip() else None

    # Build SET clause dynamically from properties
    set_parts = ["n.id = $id", "n.created_at = datetime()"]
    params = {"id": node_id}

    for key, value in properties.items():
        if value is not None:
            if not key.replace("_", "").isalnum():
                raise ValueError(f"Invalid property key: {key}")
            param_name = f"prop_{key}"
            set_parts.append(f"n.{key} = ${param_name}")
            params[param_name] = value

    if embedding:
        set_parts.append("n.embedding = $embedding")
        params["embedding"] = embedding

    set_clause = ", ".join(set_parts)

    with driver.session() as session:
        session.run(f"CREATE (n:{label}) SET {set_clause}", **params)

    return node_id


def save_entities_and_link(driver, cfg, source_id, source_label, entities, relationships):
    """Save entities and create SOURCED_FROM links to the source node."""
    if not entities:
        return 0, 0

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

    # Get embeddings for entities
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

        # Create SOURCED_FROM relationships (Entity → source node)
        session.run(f"""
            MATCH (src:{source_label} {{id: $source_id}})
            MATCH (e:Entity)
            WHERE e.name IN $names
            MERGE (e)-[r:SOURCED_FROM]->(src)
            ON CREATE SET r.created_at = datetime()
        """, source_id=source_id, names=list(seen_names))

        # Create RELATES_TO relationships (allow linking to existing entities in graph)
        rel_count = 0
        for rel in relationships:
            src = rel["source"].strip()
            tgt = rel["target"].strip()
            if src and tgt and src != tgt:
                result = session.run("""
                    MATCH (a:Entity {name: $src}), (b:Entity {name: $tgt})
                    MERGE (a)-[r:RELATES_TO]->(b)
                    ON CREATE SET r.type = $type, r.description = $desc, r.weight = 1.0
                    ON MATCH SET r.weight = r.weight + 0.5
                    RETURN a.name
                """, src=src, tgt=tgt, type=rel.get("type", "related"),
                     desc=rel.get("description", ""))
                if result.single():
                    rel_count += 1

    return len(unique_entities), rel_count


def main():
    parser = argparse.ArgumentParser(description="Add knowledge directly to the graph")
    parser.add_argument("--project", "-p", default=None,
                        help="Project name (key in config.py PROJECTS)")
    parser.add_argument("--json", dest="json_str", default=None,
                        help="JSON string with source, entities, and relationships")
    args = parser.parse_args()

    # Read JSON from --json arg or stdin
    if args.json_str:
        data = json.loads(args.json_str)
    else:
        data = json.load(sys.stdin)

    source = data.get("source", {})
    label = source.get("label", "Note")
    properties = source.get("properties", {})
    entities = data.get("entities", [])
    relationships = data.get("relationships", [])

    if not properties:
        print("  [error] No source properties provided", file=sys.stderr)
        sys.exit(1)

    cfg = get_config(args.project)
    print(f"  [config] {cfg}", file=sys.stderr)

    driver = get_driver(cfg)
    try:
        # Create source node
        source_id = create_source_node(driver, cfg, label, properties)
        print(f"  [created] {label} node (id: {source_id[:8]}...)", file=sys.stderr)

        # Save entities and link
        e_count, r_count = save_entities_and_link(
            driver, cfg, source_id, label, entities, relationships)
        print(f"  [done] {e_count} entities, {r_count} relationships saved",
              file=sys.stderr)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
