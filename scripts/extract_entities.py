#!/usr/bin/env python3
"""
Entity extraction using Claude API.

Extracts named entities and relationships from document chunks
and creates Entity nodes and relationships in the knowledge graph.

Usage:
  python extract_entities.py /path/to/file.pdf -p <project>
  python extract_entities.py --all -p <project>
"""

import sys
import os
import uuid
import argparse
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_config

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

from neo4j import GraphDatabase

ENTITY_TYPES = [
    "PERSON", "ORGANIZATION", "TECHNOLOGY", "REQUIREMENT",
    "SCHEDULE", "BUDGET", "RISK", "PROPOSAL_PATTERN",
    "EVALUATION_CRITERIA", "DELIVERABLE", "SECURITY", "DOMAIN", "CONCEPT",
]

EXTRACTION_SYSTEM_PROMPT = """\
You are an entity extraction system. Extract named entities and relationships from the provided text chunks.

Entity types:
- PERSON: People, named individuals
- ORGANIZATION: Companies, teams, departments, institutions
- TECHNOLOGY: Technologies, tools, frameworks, programming languages, platforms
- REQUIREMENT: Requirements, specifications, constraints
- SCHEDULE: Dates, deadlines, timelines, milestones
- BUDGET: Financial figures, costs, budgets, numbers
- RISK: Risks, issues, concerns, threats
- PROPOSAL_PATTERN: Proposal patterns, solution approaches
- EVALUATION_CRITERIA: Evaluation criteria, metrics, KPIs
- DELIVERABLE: Deliverables, outputs, artifacts
- SECURITY: Security measures, protocols, compliance
- DOMAIN: Business domains, areas of expertise
- CONCEPT: Abstract concepts, methodologies, processes

Rules:
- Extract concrete, specific entities (not generic terms like "system" or "data")
- Normalize entity names (consistent casing, resolve abbreviations)
- Each entity needs a brief description (1-2 sentences)
- Relationships should capture meaningful connections between extracted entities
- Relationship types should be descriptive verbs/phrases (e.g., "uses", "manages", "depends_on")
"""

CHUNK_BATCH_SIZE = 5


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


def extract_entities_from_chunks(chunks_text, client):
    """Use Claude API to extract entities and relationships from text chunks."""
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"Extract entities and relationships from the following text:\n\n{chunks_text}",
        }],
        tools=[{
            "name": "save_entities",
            "description": "Save extracted entities and relationships",
            "input_schema": {
                "type": "object",
                "properties": {
                    "entities": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Entity name"},
                                "type": {"type": "string", "enum": ENTITY_TYPES},
                                "description": {"type": "string", "description": "Brief description"},
                            },
                            "required": ["name", "type", "description"],
                        },
                    },
                    "relationships": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {"type": "string", "description": "Source entity name"},
                                "target": {"type": "string", "description": "Target entity name"},
                                "type": {"type": "string", "description": "Relationship type (verb/phrase)"},
                                "description": {"type": "string", "description": "Brief description"},
                            },
                            "required": ["source", "target", "type"],
                        },
                    },
                },
                "required": ["entities", "relationships"],
            },
        }],
        tool_choice={"type": "tool", "name": "save_entities"},
    )

    for block in response.content:
        if block.type == "tool_use":
            return block.input
    return {"entities": [], "relationships": []}


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
    entity_texts = [f"{e['name']}。{e.get('description', '')}" for e in unique_entities]
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
                    e.embedding = $embedding,
                    e.status = 'active'
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


def extract_for_document(driver, cfg, source_path, client):
    """Extract entities for all chunks of a document."""
    with driver.session() as session:
        result = session.run("""
            MATCH (d:Document {source_path: $path})-[:HAS_CHUNK]->(c:Chunk)
            RETURN d.id AS doc_id, c.text AS text, c.id AS chunk_id
            ORDER BY c.chunk_index
        """, path=source_path)
        records = list(result)

    if not records:
        print(f"  [skip] No chunks found for: {source_path}", file=sys.stderr)
        return 0, 0

    doc_id = records[0]["doc_id"]
    chunk_texts = [r["text"] for r in records]

    all_entities = []
    all_relationships = []

    for i in range(0, len(chunk_texts), CHUNK_BATCH_SIZE):
        batch = chunk_texts[i:i + CHUNK_BATCH_SIZE]
        combined = "\n\n---\n\n".join(batch)

        print(f"  [extract] Chunks {i + 1}-{min(i + CHUNK_BATCH_SIZE, len(chunk_texts))}"
              f"/{len(chunk_texts)}...", file=sys.stderr)

        result = extract_entities_from_chunks(combined, client)
        all_entities.extend(result.get("entities", []))
        all_relationships.extend(result.get("relationships", []))

    entity_count, rel_count = save_entities_to_graph(
        driver, cfg, doc_id, all_entities, all_relationships,
    )
    return entity_count, rel_count


def main():
    parser = argparse.ArgumentParser(
        description="Extract entities from document chunks using Claude API")
    parser.add_argument("source_path", nargs="?", help="Document source path")
    parser.add_argument("--all", action="store_true", help="Process all documents")
    parser.add_argument("--project", "-p", default=None,
                        help="Project name (key in config.py PROJECTS)")
    args = parser.parse_args()

    if not args.source_path and not args.all:
        parser.error("Either source_path or --all is required")

    if not HAS_ANTHROPIC:
        print("  [error] anthropic package not installed. "
              "Run: pip install anthropic", file=sys.stderr)
        sys.exit(1)

    cfg = get_config(args.project)
    print(f"  [config] {cfg}", file=sys.stderr)

    try:
        client = anthropic.Anthropic()
    except Exception as e:
        print(f"  [error] Failed to initialize Anthropic client: {e}",
              file=sys.stderr)
        print("  [hint] Set ANTHROPIC_API_KEY environment variable",
              file=sys.stderr)
        sys.exit(1)

    driver = get_driver(cfg)
    try:
        if args.all:
            with driver.session() as session:
                result = session.run(
                    "MATCH (d:Document) RETURN d.source_path AS path")
                paths = [r["path"] for r in result]

            total_entities = 0
            total_rels = 0
            for path in paths:
                print(f"\n  Processing: {path}", file=sys.stderr)
                e, r = extract_for_document(driver, cfg, path, client)
                total_entities += e
                total_rels += r

            print(f"\n  [done] Total: {total_entities} entities, "
                  f"{total_rels} relationships", file=sys.stderr)
        else:
            e, r = extract_for_document(driver, cfg, args.source_path, client)
            print(f"  [done] {e} entities, {r} relationships extracted",
                  file=sys.stderr)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
