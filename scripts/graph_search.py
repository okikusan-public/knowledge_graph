#!/usr/bin/env python3
"""
Graph traversal search: vector search + multi-hop graph expansion.

Finds seed nodes via vector similarity, then expands through graph
relationships (RELATES_TO, BELONGS_TO, MENTIONS, SOURCED_FROM) to
gather rich context for synthesis.

Usage:
  python graph_search.py "search query"
  python graph_search.py "search query" -p project_a
  python graph_search.py "search query" -k 5 --hops 2
"""

import sys
import os
import json
import argparse
import requests
from neo4j import GraphDatabase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_config


def get_query_embedding(cfg, text):
    """Get embedding for a search query."""
    resp = requests.post(cfg.embed_url, json={"inputs": f"query: {text}"}, timeout=30)
    resp.raise_for_status()
    return resp.json()["embeddings"][0]


def vector_search_seeds(session, query_vec, top_k=3):
    """Find seed entities and chunks via vector similarity."""
    # Search entities (exclude archived)
    entities = session.run("""
        MATCH (e:Entity)
        WHERE e.embedding IS NOT NULL
          AND coalesce(e.status, 'active') = 'active'
        WITH e, gds.similarity.cosine(e.embedding, $vec) AS score
        ORDER BY score DESC
        LIMIT $k
        RETURN e.name AS name, e.type AS type, e.description AS description, score
    """, vec=query_vec, k=top_k).data()

    # Search chunks
    chunks = session.run("""
        MATCH (c:Chunk)
        WHERE c.embedding IS NOT NULL
        WITH c, gds.similarity.cosine(c.embedding, $vec) AS score
        ORDER BY score DESC
        LIMIT $k
        RETURN c.id AS id, c.text AS text, score
    """, vec=query_vec, k=top_k).data()

    # Search communities
    communities = session.run("""
        MATCH (comm:Community)
        WHERE comm.embedding IS NOT NULL
        WITH comm, gds.similarity.cosine(comm.embedding, $vec) AS score
        ORDER BY score DESC
        LIMIT $k
        RETURN comm.title AS title, comm.summary AS summary, comm.level AS level, score
    """, vec=query_vec, k=top_k).data()

    return entities, chunks, communities


def expand_entities(session, entity_names, max_related=10):
    """Expand from seed entities via RELATES_TO and BELONGS_TO."""
    if not entity_names:
        return [], []

    # Related entities via RELATES_TO (include archived with status)
    related = session.run("""
        MATCH (e:Entity)-[r:RELATES_TO]-(related:Entity)
        WHERE e.name IN $names AND NOT related.name IN $names
        RETURN DISTINCT related.name AS name, related.type AS type,
               related.description AS description,
               r.type AS rel_type, e.name AS from_entity,
               coalesce(related.status, 'active') AS status
        LIMIT $limit
    """, names=entity_names, limit=max_related).data()

    # Communities containing seed entities
    communities = session.run("""
        MATCH (e:Entity)-[:BELONGS_TO]->(c:Community)
        WHERE e.name IN $names
        RETURN DISTINCT c.title AS title, c.summary AS summary,
               c.level AS level, e.name AS member
        ORDER BY c.level ASC
    """, names=entity_names).data()

    return related, communities


def get_entity_chunks(session, entity_names, max_chunks=10):
    """Get source chunks that mention the given entities."""
    if not entity_names:
        return []
    return session.run("""
        MATCH (e:Entity)<-[:MENTIONS]-(c:Chunk)<-[:HAS_CHUNK]-(d:Document)
        WHERE e.name IN $names
        RETURN DISTINCT c.text AS text, d.title AS document, d.source_path AS source_path,
               collect(DISTINCT e.name) AS entities, c.chunk_index AS chunk_index
        ORDER BY size(collect(DISTINCT e.name)) DESC
        LIMIT $limit
    """, names=entity_names, limit=max_chunks).data()


def get_entity_provenance(session, entity_names):
    """Get source documents for entities."""
    if not entity_names:
        return []
    return session.run("""
        MATCH (e:Entity)-[:SOURCED_FROM]->(d:Document)
        WHERE e.name IN $names
        RETURN e.name AS entity, d.title AS document, d.source_path AS source_path
    """, names=entity_names).data()


def graph_search(cfg, query_text, top_k=3, max_related=10, max_chunks=10):
    """Run vector search + graph traversal and return structured results."""
    query_vec = get_query_embedding(cfg, query_text)
    driver = GraphDatabase.driver(cfg.neo4j_uri, auth=cfg.neo4j_auth)

    with driver.session() as session:
        # Step 1: Vector search for seed nodes
        seed_entities, seed_chunks, seed_communities = vector_search_seeds(
            session, query_vec, top_k)

        # Collect seed entity names
        seed_names = [e["name"] for e in seed_entities]

        # Also extract entity names from top chunks via MENTIONS
        if seed_chunks:
            chunk_ids = [c["id"] for c in seed_chunks]
            chunk_entities = session.run("""
                MATCH (c:Chunk)-[:MENTIONS]->(e:Entity)
                WHERE c.id IN $ids
                  AND coalesce(e.status, 'active') = 'active'
                RETURN DISTINCT e.name AS name
            """, ids=chunk_ids).data()
            for ce in chunk_entities:
                if ce["name"] not in seed_names:
                    seed_names.append(ce["name"])

        # Step 2: Graph expansion from seed entities
        related_entities, entity_communities = expand_entities(
            session, seed_names, max_related)

        # Combine all entity names for chunk retrieval
        all_entity_names = list(set(
            seed_names + [r["name"] for r in related_entities]
        ))

        # Step 3: Get source chunks and provenance
        context_chunks = get_entity_chunks(session, all_entity_names, max_chunks)
        provenance = get_entity_provenance(session, all_entity_names)

    driver.close()

    return {
        "seed_entities": seed_entities,
        "seed_chunks": seed_chunks,
        "seed_communities": seed_communities,
        "related_entities": related_entities,
        "entity_communities": entity_communities,
        "context_chunks": context_chunks,
        "provenance": provenance,
    }


def format_results(results):
    """Format results for human-readable output."""
    lines = []

    # Seed entities
    if results["seed_entities"]:
        lines.append("=== Seed Entities (vector match) ===")
        for e in results["seed_entities"]:
            lines.append(f"  [{e['score']:.4f}] {e['name']} ({e['type']})")
            if e.get("description"):
                lines.append(f"    {e['description'][:150]}")

    # Seed communities
    if results["seed_communities"]:
        lines.append("\n=== Seed Communities (vector match) ===")
        for c in results["seed_communities"]:
            lines.append(f"  [{c['score']:.4f}] L{c['level']} {c['title']}")
            if c.get("summary"):
                lines.append(f"    {c['summary'][:150]}")

    # Related entities (graph expansion)
    if results["related_entities"]:
        lines.append("\n=== Related Entities (graph expansion) ===")
        for r in results["related_entities"]:
            archived_mark = " [archived]" if r.get("status") == "archived" else ""
            lines.append(f"  {r['name']} ({r['type']}){archived_mark} --[{r['rel_type']}]--> {r['from_entity']}")
            if r.get("description"):
                lines.append(f"    {r['description'][:150]}")

    # Entity communities
    if results["entity_communities"]:
        lines.append("\n=== Entity Communities ===")
        seen = set()
        for c in results["entity_communities"]:
            key = c["title"]
            if key not in seen:
                seen.add(key)
                lines.append(f"  L{c['level']} {c['title']}")
                if c.get("summary"):
                    lines.append(f"    {c['summary'][:200]}")

    # Context chunks
    if results["context_chunks"]:
        lines.append("\n=== Context Chunks ===")
        for i, c in enumerate(results["context_chunks"], 1):
            lines.append(f"  [{i}] {c['document']} (chunk #{c.get('chunk_index', '?')})")
            lines.append(f"    entities: {', '.join(c['entities'])}")
            lines.append(f"    {c['text'][:300]}...")

    # Provenance
    if results["provenance"]:
        lines.append("\n=== Provenance ===")
        seen = set()
        for p in results["provenance"]:
            key = (p["entity"], p["document"])
            if key not in seen:
                seen.add(key)
                lines.append(f"  {p['entity']} <- {p['document']}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Graph Traversal Search")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--project", "-p", default=None)
    parser.add_argument("--top", "-k", type=int, default=3,
                        help="Number of seed nodes per type (default: 3)")
    parser.add_argument("--max-related", type=int, default=10,
                        help="Max related entities to expand (default: 10)")
    parser.add_argument("--max-chunks", type=int, default=10,
                        help="Max context chunks to return (default: 10)")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    args = parser.parse_args()

    cfg = get_config(args.project)

    if not args.json:
        print(f"[{cfg.project}] Graph search: \"{args.query}\"")
        print("=" * 60)

    results = graph_search(cfg, args.query, args.top, args.max_related, args.max_chunks)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_results(results))


if __name__ == "__main__":
    main()
