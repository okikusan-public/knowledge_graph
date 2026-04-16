#!/usr/bin/env python3
"""
Import knowledge graph data from JSON exported by export_knowledge.py.

Usage:
  python scripts/import_knowledge.py backup.json -p <project>
  python scripts/import_knowledge.py backup.json -p <project> --dry-run
  python scripts/import_knowledge.py backup.json -p <project> --regenerate-embeddings
  cat backup.json | python scripts/import_knowledge.py - -p <project>
"""

import sys
import os
import json
import argparse
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_config

from neo4j import GraphDatabase

SUPPORTED_VERSIONS = {"1.0"}
EMBED_BATCH_SIZE = 32


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


def validate_export(data):
    """Validate the export file structure."""
    version = data.get("version")
    if not version:
        print("  [error] Missing version field", file=sys.stderr)
        sys.exit(1)
    if version not in SUPPORTED_VERSIONS:
        print(f"  [error] Unsupported format version: {version} "
              f"(supported: {', '.join(SUPPORTED_VERSIONS)})", file=sys.stderr)
        sys.exit(1)
    if "nodes" not in data or "relationships" not in data:
        print("  [error] Missing nodes or relationships section", file=sys.stderr)
        sys.exit(1)


# ── Node Import ──────────────────────────────────────────────────────

def import_documents(session, documents, dry_run):
    """Import Document nodes using MERGE on id."""
    count = 0
    for doc in documents:
        if dry_run:
            count += 1
            continue
        session.run("""
            MERGE (d:Document {id: $id})
            ON CREATE SET
                d.title = $title, d.source_path = $source_path,
                d.file_type = $file_type, d.text_length = $text_length,
                d.chunk_count = $chunk_count, d.auto_ingested = $auto_ingested,
                d.created_at = CASE WHEN $created_at IS NOT NULL
                               THEN datetime($created_at) ELSE datetime() END,
                d.embedding = $embedding
            ON MATCH SET
                d.title = coalesce($title, d.title),
                d.source_path = coalesce($source_path, d.source_path),
                d.embedding = CASE WHEN d.embedding IS NULL THEN $embedding
                              ELSE d.embedding END
        """,
            id=doc["id"], title=doc.get("title"), source_path=doc.get("source_path"),
            file_type=doc.get("file_type"), text_length=doc.get("text_length"),
            chunk_count=doc.get("chunk_count"), auto_ingested=doc.get("auto_ingested"),
            created_at=doc.get("created_at"), embedding=doc.get("embedding"))
        count += 1
    return count


def import_chunks(session, chunks, dry_run):
    """Import Chunk nodes using MERGE on id."""
    count = 0
    for chunk in chunks:
        if dry_run:
            count += 1
            continue
        session.run("""
            MERGE (c:Chunk {id: $id})
            ON CREATE SET
                c.text = $text, c.chunk_index = $chunk_index,
                c.token_estimate = $token_estimate, c.embedding = $embedding
            ON MATCH SET
                c.text = coalesce($text, c.text),
                c.embedding = CASE WHEN c.embedding IS NULL THEN $embedding
                              ELSE c.embedding END
        """,
            id=chunk["id"], text=chunk.get("text"),
            chunk_index=chunk.get("chunk_index"),
            token_estimate=chunk.get("token_estimate"),
            embedding=chunk.get("embedding"))
        count += 1
    return count


def import_entities(session, entities, dry_run):
    """Import Entity nodes using MERGE on name (consistent with save_entities.py).

    Returns (count, id_remap) where id_remap maps exported id -> actual id in graph.
    This is needed because MERGE on name may match existing entities with different ids.
    """
    count = 0
    id_remap = {}
    for ent in entities:
        if dry_run:
            count += 1
            id_remap[ent["id"]] = ent["id"]
            continue
        result = session.run("""
            MERGE (e:Entity {name: $name})
            ON CREATE SET
                e.id = $id, e.type = $type, e.description = $description,
                e.embedding = $embedding, e.status = coalesce($status, 'active'),
                e.archived_date = $archived_date, e.archive_reason = $archive_reason,
                e.last_quiz_date = $last_quiz_date,
                e.correct_count = $correct_count,
                e.incorrect_count = $incorrect_count,
                e.quiz_interval_days = $quiz_interval_days
            ON MATCH SET
                e.type = CASE WHEN e.type IS NULL THEN $type ELSE e.type END,
                e.description = CASE WHEN e.description IS NULL
                                     OR size(e.description) < size($description)
                                THEN $description ELSE e.description END,
                e.embedding = CASE WHEN e.embedding IS NULL THEN $embedding
                              ELSE e.embedding END,
                e.status = CASE WHEN e.status IS NULL
                           THEN coalesce($status, 'active') ELSE e.status END
            RETURN e.id AS actual_id
        """,
            id=ent["id"], name=ent["name"], type=ent.get("type"),
            description=ent.get("description", ""),
            embedding=ent.get("embedding"),
            status=ent.get("status"),
            archived_date=ent.get("archived_date"),
            archive_reason=ent.get("archive_reason"),
            last_quiz_date=ent.get("last_quiz_date"),
            correct_count=ent.get("correct_count"),
            incorrect_count=ent.get("incorrect_count"),
            quiz_interval_days=ent.get("quiz_interval_days"))
        record = result.single()
        actual_id = record["actual_id"] if record else ent["id"]
        id_remap[ent["id"]] = actual_id
        count += 1
    return count, id_remap


def import_communities(session, communities, dry_run):
    """Import Community nodes using MERGE on id."""
    count = 0
    for comm in communities:
        if dry_run:
            count += 1
            continue
        session.run("""
            MERGE (c:Community {id: $id})
            ON CREATE SET
                c.level = $level, c.title = $title,
                c.summary = $summary, c.rank = $rank,
                c.embedding = $embedding
            ON MATCH SET
                c.title = coalesce($title, c.title),
                c.summary = coalesce($summary, c.summary),
                c.embedding = CASE WHEN c.embedding IS NULL THEN $embedding
                              ELSE c.embedding END
        """,
            id=comm["id"], level=comm.get("level"), title=comm.get("title"),
            summary=comm.get("summary"), rank=comm.get("rank"),
            embedding=comm.get("embedding"))
        count += 1
    return count


def import_quiz_results(session, quiz_results, dry_run):
    """Import QuizResult nodes using MERGE on id."""
    count = 0
    for qr in quiz_results:
        if dry_run:
            count += 1
            continue
        session.run("""
            MERGE (q:QuizResult {id: $id})
            ON CREATE SET
                q.entity_name = $entity_name, q.question = $question,
                q.user_answer = $user_answer, q.is_correct = $is_correct,
                q.score = $score, q.feedback = $feedback,
                q.created_at = CASE WHEN $created_at IS NOT NULL
                               THEN datetime($created_at) ELSE datetime() END
        """,
            id=qr["id"], entity_name=qr.get("entity_name"),
            question=qr.get("question"), user_answer=qr.get("user_answer"),
            is_correct=qr.get("is_correct"), score=qr.get("score"),
            feedback=qr.get("feedback"), created_at=qr.get("created_at"))
        count += 1
    return count


def import_source_nodes(session, label, nodes, dry_run):
    """Import dynamic source nodes (Note, WebSource, Conversation, etc.)."""
    if not label.isalnum():
        print(f"  [warn] Skipping non-alphanumeric label: {label}", file=sys.stderr)
        return 0

    count = 0
    for node in nodes:
        if dry_run:
            count += 1
            continue
        node_id = node.get("id")
        if not node_id:
            continue

        # Build SET clause dynamically
        set_parts = []
        params = {"id": node_id}
        for key, value in node.items():
            if key == "id":
                continue
            if value is not None:
                if not key.replace("_", "").isalnum():
                    continue
                param_name = f"p_{key}"
                if key == "created_at":
                    set_parts.append(f"n.{key} = datetime(${param_name})")
                else:
                    set_parts.append(f"n.{key} = ${param_name}")
                params[param_name] = value

        on_create = f", {', '.join(set_parts)}" if set_parts else ""
        session.run(
            f"MERGE (n:{label} {{id: $id}}) ON CREATE SET n.id = $id{on_create}",
            **params)
        count += 1
    return count


# ── Relationship Import ──────────────────────────────────────────────

def import_simple_rel(session, rels, rel_type, start_label, end_label, dry_run):
    """Import simple relationships (no properties beyond optional ones)."""
    count = 0
    for rel in rels:
        if dry_run:
            count += 1
            continue
        session.run(f"""
            MATCH (a:{start_label} {{id: $start_id}})
            MATCH (b:{end_label} {{id: $end_id}})
            MERGE (a)-[r:{rel_type}]->(b)
        """, start_id=rel["_start_id"], end_id=rel["_end_id"])
        count += 1
    return count


def import_sourced_from(session, rels, dry_run):
    """Import SOURCED_FROM relationships with dynamic end label."""
    count = 0
    for rel in rels:
        if dry_run:
            count += 1
            continue
        end_label = rel.get("_end_label", "Document")
        if not end_label.isalnum():
            continue
        props = rel.get("properties", {})
        created_at = props.get("created_at")
        session.run(f"""
            MATCH (e:Entity {{id: $start_id}})
            MATCH (src:{end_label} {{id: $end_id}})
            MERGE (e)-[r:SOURCED_FROM]->(src)
            ON CREATE SET r.created_at = CASE WHEN $created_at IS NOT NULL
                                         THEN datetime($created_at) ELSE datetime() END
        """, start_id=rel["_start_id"], end_id=rel["_end_id"],
            created_at=created_at)
        count += 1
    return count


def import_relates_to(session, rels, dry_run):
    """Import RELATES_TO relationships with properties."""
    count = 0
    for rel in rels:
        if dry_run:
            count += 1
            continue
        props = rel.get("properties", {})
        session.run("""
            MATCH (a:Entity {id: $start_id})
            MATCH (b:Entity {id: $end_id})
            MERGE (a)-[r:RELATES_TO]->(b)
            ON CREATE SET r.type = $type, r.description = $description,
                          r.weight = $weight
        """, start_id=rel["_start_id"], end_id=rel["_end_id"],
            type=props.get("type"), description=props.get("description"),
            weight=props.get("weight", 1.0))
        count += 1
    return count


def import_belongs_to(session, rels, dry_run):
    """Import BELONGS_TO relationships with level property."""
    count = 0
    for rel in rels:
        if dry_run:
            count += 1
            continue
        props = rel.get("properties", {})
        session.run("""
            MATCH (e:Entity {id: $start_id})
            MATCH (c:Community {id: $end_id})
            MERGE (e)-[r:BELONGS_TO]->(c)
            ON CREATE SET r.level = $level
        """, start_id=rel["_start_id"], end_id=rel["_end_id"],
            level=props.get("level"))
        count += 1
    return count


# ── Embedding Regeneration ───────────────────────────────────────────

def regenerate_embeddings(driver, cfg):
    """Regenerate missing embeddings for all node types."""
    print("  [embed] Regenerating missing embeddings...", file=sys.stderr)

    with driver.session() as session:
        # Entities
        entities = session.run("""
            MATCH (e:Entity)
            WHERE e.embedding IS NULL AND e.name IS NOT NULL
            RETURN e.id AS id, e.name AS name, e.description AS description
        """).data()
        if entities:
            texts = [f"{e['name']}. {e.get('description') or ''}" for e in entities]
            for i in range(0, len(texts), EMBED_BATCH_SIZE):
                batch_texts = texts[i:i + EMBED_BATCH_SIZE]
                batch_entities = entities[i:i + EMBED_BATCH_SIZE]
                embeddings = get_embeddings_batch(cfg, batch_texts)
                for ent, emb in zip(batch_entities, embeddings):
                    session.run("MATCH (e:Entity {id: $id}) SET e.embedding = $emb",
                                id=ent["id"], emb=emb)
            print(f"  [embed] {len(entities)} entities", file=sys.stderr)

        # Chunks
        chunks = session.run("""
            MATCH (c:Chunk)
            WHERE c.embedding IS NULL AND c.text IS NOT NULL
            RETURN c.id AS id, c.text AS text
        """).data()
        if chunks:
            texts = [c["text"] for c in chunks]
            for i in range(0, len(texts), EMBED_BATCH_SIZE):
                batch_texts = texts[i:i + EMBED_BATCH_SIZE]
                batch_chunks = chunks[i:i + EMBED_BATCH_SIZE]
                embeddings = get_embeddings_batch(cfg, batch_texts)
                for chunk, emb in zip(batch_chunks, embeddings):
                    session.run("MATCH (c:Chunk {id: $id}) SET c.embedding = $emb",
                                id=chunk["id"], emb=emb)
            print(f"  [embed] {len(chunks)} chunks", file=sys.stderr)

        # Documents
        docs = session.run("""
            MATCH (d:Document)
            WHERE d.embedding IS NULL AND d.title IS NOT NULL
            RETURN d.id AS id, d.title AS title
        """).data()
        if docs:
            texts = [d["title"] for d in docs]
            for i in range(0, len(texts), EMBED_BATCH_SIZE):
                batch_texts = texts[i:i + EMBED_BATCH_SIZE]
                batch_docs = docs[i:i + EMBED_BATCH_SIZE]
                embeddings = get_embeddings_batch(cfg, batch_texts)
                for doc, emb in zip(batch_docs, embeddings):
                    session.run("MATCH (d:Document {id: $id}) SET d.embedding = $emb",
                                id=doc["id"], emb=emb)
            print(f"  [embed] {len(docs)} documents", file=sys.stderr)

        # Communities
        comms = session.run("""
            MATCH (c:Community)
            WHERE c.embedding IS NULL AND c.title IS NOT NULL
            RETURN c.id AS id, c.title AS title, c.summary AS summary
        """).data()
        if comms:
            texts = [f"{c['title']}. {c.get('summary') or ''}" for c in comms]
            for i in range(0, len(texts), EMBED_BATCH_SIZE):
                batch_texts = texts[i:i + EMBED_BATCH_SIZE]
                batch_comms = comms[i:i + EMBED_BATCH_SIZE]
                embeddings = get_embeddings_batch(cfg, batch_texts)
                for comm, emb in zip(batch_comms, embeddings):
                    session.run("MATCH (c:Community {id: $id}) SET c.embedding = $emb",
                                id=comm["id"], emb=emb)
            print(f"  [embed] {len(comms)} communities", file=sys.stderr)


# ── Main Import ──────────────────────────────────────────────────────

def import_graph(driver, cfg, data, dry_run, regenerate):
    """Import graph data from export dict."""
    validate_export(data)

    nodes = data.get("nodes", {})
    relationships = data.get("relationships", {})
    prefix = "[dry-run] " if dry_run else ""

    with driver.session() as session:
        # Phase 1: Import nodes (order matters for referential integrity)
        # 1. Documents
        doc_count = import_documents(session, nodes.get("Document", []), dry_run)
        print(f"  {prefix}[import] {doc_count} documents", file=sys.stderr)

        # 2. Dynamic source nodes
        known_node_types = {"Document", "Chunk", "Entity", "Community", "QuizResult"}
        source_total = 0
        for label, label_nodes in nodes.items():
            if label in known_node_types:
                continue
            count = import_source_nodes(session, label, label_nodes, dry_run)
            source_total += count
            print(f"  {prefix}[import] {count} {label} nodes", file=sys.stderr)

        # 3. Chunks
        chunk_count = import_chunks(session, nodes.get("Chunk", []), dry_run)
        print(f"  {prefix}[import] {chunk_count} chunks", file=sys.stderr)

        # 4. Entities (returns id_remap for relationship resolution)
        entity_count, entity_id_remap = import_entities(
            session, nodes.get("Entity", []), dry_run)
        print(f"  {prefix}[import] {entity_count} entities", file=sys.stderr)

        # 5. Communities
        comm_count = import_communities(session, nodes.get("Community", []), dry_run)
        print(f"  {prefix}[import] {comm_count} communities", file=sys.stderr)

        # 6. QuizResults
        qr_count = import_quiz_results(session, nodes.get("QuizResult", []), dry_run)
        print(f"  {prefix}[import] {qr_count} quiz results", file=sys.stderr)

        # Phase 2: Import relationships
        # Remap entity IDs in relationships that reference entities
        def remap_entity_id(rels, key):
            """Remap entity IDs using the entity_id_remap from MERGE on name."""
            for rel in rels:
                if rel[key] in entity_id_remap:
                    rel[key] = entity_id_remap[rel[key]]
            return rels

        mentions_rels = relationships.get("MENTIONS", [])
        remap_entity_id(mentions_rels, "_end_id")

        sourced_rels = relationships.get("SOURCED_FROM", [])
        remap_entity_id(sourced_rels, "_start_id")

        relates_rels = relationships.get("RELATES_TO", [])
        remap_entity_id(relates_rels, "_start_id")
        remap_entity_id(relates_rels, "_end_id")

        belongs_rels = relationships.get("BELONGS_TO", [])
        remap_entity_id(belongs_rels, "_start_id")

        qrf_rels = relationships.get("QUIZ_RESULT_FOR", [])
        remap_entity_id(qrf_rels, "_end_id")

        # 7. HAS_CHUNK
        hc = import_simple_rel(session, relationships.get("HAS_CHUNK", []),
                               "HAS_CHUNK", "Document", "Chunk", dry_run)
        # 8. NEXT_CHUNK
        nc = import_simple_rel(session, relationships.get("NEXT_CHUNK", []),
                               "NEXT_CHUNK", "Chunk", "Chunk", dry_run)
        # 9. MENTIONS
        mn = import_simple_rel(session, mentions_rels,
                               "MENTIONS", "Chunk", "Entity", dry_run)
        # 10. SOURCED_FROM
        sf = import_sourced_from(session, sourced_rels, dry_run)
        # 11. RELATES_TO
        rt = import_relates_to(session, relates_rels, dry_run)
        # 12. BELONGS_TO
        bt = import_belongs_to(session, belongs_rels, dry_run)
        # 13. CHILD_OF
        co = import_simple_rel(session, relationships.get("CHILD_OF", []),
                               "CHILD_OF", "Community", "Community", dry_run)
        # 14. QUIZ_RESULT_FOR
        qrf = import_simple_rel(session, qrf_rels,
                                "QUIZ_RESULT_FOR", "QuizResult", "Entity", dry_run)

        total_rels = hc + nc + mn + sf + rt + bt + co + qrf
        print(f"  {prefix}[import] {total_rels} relationships total", file=sys.stderr)

    # Phase 3: Regenerate embeddings if requested
    if regenerate and not dry_run:
        regenerate_embeddings(driver, cfg)

    return {
        "nodes": {
            "Document": doc_count, "Chunk": chunk_count, "Entity": entity_count,
            "Community": comm_count, "QuizResult": qr_count, "source_nodes": source_total,
        },
        "relationships": total_rels,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Import knowledge graph data from JSON")
    parser.add_argument("input", help="Input JSON file path (or - for stdin)")
    parser.add_argument("--project", "-p", default=None,
                        help="Project name (key in config.py PROJECTS)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be imported without writing")
    parser.add_argument("--regenerate-embeddings", action="store_true",
                        help="Regenerate missing embeddings via embedding server")
    args = parser.parse_args()

    # Read input
    if args.input == "-":
        data = json.load(sys.stdin)
    else:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)

    cfg = get_config(args.project)
    print(f"  [config] {cfg}", file=sys.stderr)

    meta = data.get("metadata", {})
    print(f"  [info] Export date: {meta.get('export_date', 'unknown')}", file=sys.stderr)
    print(f"  [info] Source project: {meta.get('project', 'unknown')}", file=sys.stderr)
    print(f"  [info] Embeddings included: {meta.get('include_embeddings', 'unknown')}",
          file=sys.stderr)

    driver = get_driver(cfg)
    try:
        result = import_graph(driver, cfg, data, args.dry_run, args.regenerate_embeddings)
        if args.dry_run:
            print("  [done] Dry run complete (no changes made)", file=sys.stderr)
        else:
            print("  [done] Import complete", file=sys.stderr)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
