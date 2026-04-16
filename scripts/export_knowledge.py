#!/usr/bin/env python3
"""
Export knowledge graph data to JSON for backup or cross-project transfer.

Usage:
  python scripts/export_knowledge.py -p <project>
  python scripts/export_knowledge.py -p <project> -o backup.json
  python scripts/export_knowledge.py -p <project> --no-embeddings
  python scripts/export_knowledge.py -p <project> --source-path /path/to/file.pdf
  python scripts/export_knowledge.py -p <project> --entity-type PERSON
"""

import sys
import os
import json
import argparse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_config

from neo4j import GraphDatabase
from neo4j.time import DateTime as Neo4jDateTime

BATCH_SIZE = 1000
FORMAT_VERSION = "1.0"


def get_driver(cfg):
    return GraphDatabase.driver(cfg.neo4j_uri, auth=cfg.neo4j_auth)


def serialize_value(val):
    """Convert Neo4j types to JSON-serializable values."""
    if val is None:
        return None
    if isinstance(val, Neo4jDateTime):
        return val.isoformat()
    if isinstance(val, list):
        return [serialize_value(v) for v in val]
    return val


def serialize_record(record):
    """Serialize all values in a record dict."""
    return {k: serialize_value(v) for k, v in record.items()}


# ── Node Export ──────────────────────────────────────────────────────

def export_documents(session, filters, include_embeddings):
    """Export Document nodes."""
    where_parts = []
    params = {}
    if filters.get("source_path"):
        where_parts.append("d.source_path = $source_path")
        params["source_path"] = filters["source_path"]

    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    emb_expr = "d.embedding" if include_embeddings else "null"
    records = session.run(f"""
        MATCH (d:Document)
        {where_clause}
        RETURN d.id AS id, d.title AS title, d.source_path AS source_path,
               d.file_type AS file_type, d.text_length AS text_length,
               d.chunk_count AS chunk_count, d.auto_ingested AS auto_ingested,
               d.created_at AS created_at, {emb_expr} AS embedding
        ORDER BY d.created_at
    """, **params).data()
    return [serialize_record(r) for r in records]


def export_chunks(session, doc_ids, include_embeddings):
    """Export Chunk nodes, optionally filtered by document IDs."""
    emb_expr = "c.embedding" if include_embeddings else "null"
    if doc_ids is not None:
        records = session.run(f"""
            MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)
            WHERE d.id IN $doc_ids
            RETURN c.id AS id, c.text AS text, c.chunk_index AS chunk_index,
                   c.token_estimate AS token_estimate, {emb_expr} AS embedding
            ORDER BY c.chunk_index
        """, doc_ids=doc_ids).data()
    else:
        records = session.run(f"""
            MATCH (c:Chunk)
            RETURN c.id AS id, c.text AS text, c.chunk_index AS chunk_index,
                   c.token_estimate AS token_estimate, {emb_expr} AS embedding
        """).data()
    return [serialize_record(r) for r in records]


def export_entities(session, filters, include_embeddings):
    """Export Entity nodes."""
    where_parts = []
    params = {}
    if filters.get("entity_type"):
        where_parts.append("e.type = $entity_type")
        params["entity_type"] = filters["entity_type"]
    if filters.get("doc_ids") is not None:
        where_parts.append(
            "EXISTS { MATCH (e)-[:SOURCED_FROM]->(d:Document) WHERE d.id IN $doc_ids }")
        params["doc_ids"] = filters["doc_ids"]

    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    emb_expr = "e.embedding" if include_embeddings else "null"
    records = session.run(f"""
        MATCH (e:Entity)
        {where_clause}
        RETURN e.id AS id, e.name AS name, e.type AS type,
               e.description AS description, e.status AS status,
               e.archived_date AS archived_date, e.archive_reason AS archive_reason,
               e.last_quiz_date AS last_quiz_date,
               e.correct_count AS correct_count, e.incorrect_count AS incorrect_count,
               e.quiz_interval_days AS quiz_interval_days,
               {emb_expr} AS embedding
        ORDER BY e.name
    """, **params).data()
    return [serialize_record(r) for r in records]


def export_communities(session, include_embeddings):
    """Export Community nodes."""
    emb_expr = "c.embedding" if include_embeddings else "null"
    records = session.run(f"""
        MATCH (c:Community)
        RETURN c.id AS id, c.level AS level, c.title AS title,
               c.summary AS summary, c.rank AS rank,
               {emb_expr} AS embedding
        ORDER BY c.level, c.title
    """).data()
    return [serialize_record(r) for r in records]


def export_quiz_results(session):
    """Export QuizResult nodes."""
    records = session.run("""
        MATCH (q:QuizResult)
        RETURN q.id AS id, q.entity_name AS entity_name,
               q.question AS question, q.user_answer AS user_answer,
               q.is_correct AS is_correct, q.score AS score,
               q.feedback AS feedback, q.created_at AS created_at
        ORDER BY q.created_at
    """).data()
    return [serialize_record(r) for r in records]


def discover_source_labels(session):
    """Discover dynamic source node labels (Note, WebSource, Conversation, etc.)."""
    records = session.run("""
        MATCH (e:Entity)-[:SOURCED_FROM]->(src)
        WHERE NOT src:Document
        WITH DISTINCT labels(src) AS lbls
        UNWIND lbls AS label
        RETURN DISTINCT label
        ORDER BY label
    """).data()
    return [r["label"] for r in records]


def export_source_nodes(session, include_embeddings):
    """Export dynamic source nodes (Note, WebSource, Conversation, etc.)."""
    labels = discover_source_labels(session)
    result = {}
    for label in labels:
        if not label.isalnum():
            print(f"  [warn] Skipping non-alphanumeric label: {label}", file=sys.stderr)
            continue
        records = session.run(f"""
            MATCH (n:{label})
            RETURN properties(n) AS props, n.id AS id
            ORDER BY n.created_at
        """).data()
        nodes = []
        for r in records:
            props = serialize_record(r["props"]) if r["props"] else {}
            if not include_embeddings:
                props.pop("embedding", None)
            nodes.append(props)
        if nodes:
            result[label] = nodes
    return result


# ── Relationship Export ──────────────────────────────────────────────

def export_has_chunk(session, doc_ids):
    """Export HAS_CHUNK relationships."""
    if doc_ids is not None:
        records = session.run("""
            MATCH (d:Document)-[r:HAS_CHUNK]->(c:Chunk)
            WHERE d.id IN $doc_ids
            RETURN d.id AS start_id, c.id AS end_id
        """, doc_ids=doc_ids).data()
    else:
        records = session.run("""
            MATCH (d:Document)-[r:HAS_CHUNK]->(c:Chunk)
            RETURN d.id AS start_id, c.id AS end_id
        """).data()
    return [{"_start_id": r["start_id"], "_end_id": r["end_id"], "properties": {}}
            for r in records]


def export_next_chunk(session, doc_ids):
    """Export NEXT_CHUNK relationships."""
    if doc_ids is not None:
        records = session.run("""
            MATCH (d:Document)-[:HAS_CHUNK]->(c1:Chunk)-[r:NEXT_CHUNK]->(c2:Chunk)
            WHERE d.id IN $doc_ids
            RETURN c1.id AS start_id, c2.id AS end_id
        """, doc_ids=doc_ids).data()
    else:
        records = session.run("""
            MATCH (c1:Chunk)-[r:NEXT_CHUNK]->(c2:Chunk)
            RETURN c1.id AS start_id, c2.id AS end_id
        """).data()
    return [{"_start_id": r["start_id"], "_end_id": r["end_id"], "properties": {}}
            for r in records]


def export_mentions(session, doc_ids):
    """Export MENTIONS relationships."""
    if doc_ids is not None:
        records = session.run("""
            MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)-[r:MENTIONS]->(e:Entity)
            WHERE d.id IN $doc_ids
            RETURN c.id AS start_id, e.id AS end_id
        """, doc_ids=doc_ids).data()
    else:
        records = session.run("""
            MATCH (c:Chunk)-[r:MENTIONS]->(e:Entity)
            RETURN c.id AS start_id, e.id AS end_id
        """).data()
    return [{"_start_id": r["start_id"], "_end_id": r["end_id"], "properties": {}}
            for r in records]


def export_sourced_from(session, entity_ids):
    """Export SOURCED_FROM relationships with target label info."""
    if entity_ids is not None:
        records = session.run("""
            MATCH (e:Entity)-[r:SOURCED_FROM]->(src)
            WHERE e.id IN $entity_ids
            RETURN e.id AS start_id, src.id AS end_id,
                   labels(src)[0] AS end_label,
                   r.created_at AS created_at
        """, entity_ids=entity_ids).data()
    else:
        records = session.run("""
            MATCH (e:Entity)-[r:SOURCED_FROM]->(src)
            RETURN e.id AS start_id, src.id AS end_id,
                   labels(src)[0] AS end_label,
                   r.created_at AS created_at
        """).data()
    return [{
        "_start_id": r["start_id"], "_end_id": r["end_id"],
        "_end_label": r["end_label"],
        "properties": {"created_at": serialize_value(r["created_at"])}
    } for r in records]


def export_relates_to(session, entity_ids):
    """Export RELATES_TO relationships."""
    if entity_ids is not None:
        records = session.run("""
            MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity)
            WHERE a.id IN $entity_ids OR b.id IN $entity_ids
            RETURN a.id AS start_id, b.id AS end_id,
                   r.type AS type, r.description AS description, r.weight AS weight
        """, entity_ids=entity_ids).data()
    else:
        records = session.run("""
            MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity)
            RETURN a.id AS start_id, b.id AS end_id,
                   r.type AS type, r.description AS description, r.weight AS weight
        """).data()
    return [{
        "_start_id": r["start_id"], "_end_id": r["end_id"],
        "properties": {
            "type": r["type"], "description": r["description"],
            "weight": r["weight"]
        }
    } for r in records]


def export_belongs_to(session):
    """Export BELONGS_TO relationships."""
    records = session.run("""
        MATCH (e:Entity)-[r:BELONGS_TO]->(c:Community)
        RETURN e.id AS start_id, c.id AS end_id, r.level AS level
    """).data()
    return [{
        "_start_id": r["start_id"], "_end_id": r["end_id"],
        "properties": {"level": r["level"]}
    } for r in records]


def export_child_of(session):
    """Export CHILD_OF relationships."""
    records = session.run("""
        MATCH (c1:Community)-[r:CHILD_OF]->(c2:Community)
        RETURN c1.id AS start_id, c2.id AS end_id
    """).data()
    return [{"_start_id": r["start_id"], "_end_id": r["end_id"], "properties": {}}
            for r in records]


def export_quiz_result_for(session):
    """Export QUIZ_RESULT_FOR relationships."""
    records = session.run("""
        MATCH (q:QuizResult)-[r:QUIZ_RESULT_FOR]->(e:Entity)
        RETURN q.id AS start_id, e.id AS end_id
    """).data()
    return [{"_start_id": r["start_id"], "_end_id": r["end_id"], "properties": {}}
            for r in records]


# ── Main Export ──────────────────────────────────────────────────────

def export_graph(driver, cfg, filters, include_embeddings):
    """Export the full graph to a dict."""
    with driver.session() as session:
        # Determine doc_ids for filtering
        doc_ids = None
        if filters.get("source_path"):
            docs = export_documents(session, filters, include_embeddings)
            doc_ids = [d["id"] for d in docs]
        else:
            docs = export_documents(session, {}, include_embeddings)

        print(f"  [export] {len(docs)} documents", file=sys.stderr)

        chunks = export_chunks(session, doc_ids, include_embeddings)
        print(f"  [export] {len(chunks)} chunks", file=sys.stderr)

        entity_filters = dict(filters)
        if doc_ids is not None:
            entity_filters["doc_ids"] = doc_ids
        entities = export_entities(session, entity_filters, include_embeddings)
        entity_ids = [e["id"] for e in entities] if (doc_ids is not None or filters.get("entity_type")) else None
        print(f"  [export] {len(entities)} entities", file=sys.stderr)

        communities = export_communities(session, include_embeddings)
        print(f"  [export] {len(communities)} communities", file=sys.stderr)

        quiz_results = export_quiz_results(session)
        print(f"  [export] {len(quiz_results)} quiz results", file=sys.stderr)

        source_nodes = export_source_nodes(session, include_embeddings)
        for label, nodes in source_nodes.items():
            print(f"  [export] {len(nodes)} {label} nodes", file=sys.stderr)

        # Relationships
        has_chunk = export_has_chunk(session, doc_ids)
        next_chunk = export_next_chunk(session, doc_ids)
        mentions = export_mentions(session, doc_ids)
        sourced_from = export_sourced_from(session, entity_ids)
        relates_to = export_relates_to(session, entity_ids)
        belongs_to = export_belongs_to(session)
        child_of = export_child_of(session)
        quiz_result_for = export_quiz_result_for(session)

        rel_stats = {
            "HAS_CHUNK": len(has_chunk), "NEXT_CHUNK": len(next_chunk),
            "MENTIONS": len(mentions), "SOURCED_FROM": len(sourced_from),
            "RELATES_TO": len(relates_to), "BELONGS_TO": len(belongs_to),
            "CHILD_OF": len(child_of), "QUIZ_RESULT_FOR": len(quiz_result_for),
        }
        total_rels = sum(rel_stats.values())
        print(f"  [export] {total_rels} relationships total", file=sys.stderr)

    # Build nodes dict
    nodes = {
        "Document": docs,
        "Chunk": chunks,
        "Entity": entities,
        "Community": communities,
        "QuizResult": quiz_results,
    }
    nodes.update(source_nodes)

    # Build stats
    node_stats = {k: len(v) for k, v in nodes.items()}

    return {
        "version": FORMAT_VERSION,
        "metadata": {
            "export_date": datetime.now(timezone.utc).isoformat(),
            "project": cfg.project,
            "include_embeddings": include_embeddings,
            "filters": {
                "source_path": filters.get("source_path"),
                "entity_type": filters.get("entity_type"),
            },
            "stats": {
                "nodes": node_stats,
                "relationships": rel_stats,
            },
        },
        "nodes": nodes,
        "relationships": {
            "HAS_CHUNK": has_chunk,
            "NEXT_CHUNK": next_chunk,
            "MENTIONS": mentions,
            "SOURCED_FROM": sourced_from,
            "RELATES_TO": relates_to,
            "BELONGS_TO": belongs_to,
            "CHILD_OF": child_of,
            "QUIZ_RESULT_FOR": quiz_result_for,
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Export knowledge graph data to JSON")
    parser.add_argument("--project", "-p", default=None,
                        help="Project name (key in config.py PROJECTS)")
    parser.add_argument("--no-embeddings", action="store_true",
                        help="Exclude embedding vectors to reduce file size")
    parser.add_argument("--source-path", default=None,
                        help="Export only data related to this document path")
    parser.add_argument("--entity-type", default=None,
                        help="Export only entities of this type (e.g. PERSON, TECHNOLOGY)")
    parser.add_argument("-o", "--output", default=None,
                        help="Output file path (default: stdout)")
    args = parser.parse_args()

    cfg = get_config(args.project)
    print(f"  [config] {cfg}", file=sys.stderr)

    filters = {}
    if args.source_path:
        filters["source_path"] = args.source_path
    if args.entity_type:
        filters["entity_type"] = args.entity_type

    include_embeddings = not args.no_embeddings

    driver = get_driver(cfg)
    try:
        data = export_graph(driver, cfg, filters, include_embeddings)
    finally:
        driver.close()

    json_str = json.dumps(data, ensure_ascii=False, indent=2, default=str)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(json_str)
        print(f"  [done] Exported to {args.output}", file=sys.stderr)
    else:
        print(json_str)
        print("  [done] Exported to stdout", file=sys.stderr)


if __name__ == "__main__":
    main()
