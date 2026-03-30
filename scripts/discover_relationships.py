#!/usr/bin/env python3
"""
Auto-discover cross-document RELATES_TO relationships using embedding similarity.

Finds entity pairs from different source documents that have high cosine
similarity and creates RELATES_TO relationships between them.

Usage:
  python discover_relationships.py -p <project> --all
  python discover_relationships.py -p <project> --all --threshold 0.90 --dry-run
  python discover_relationships.py -p <project> --source-entities "Name1,Name2"
  python discover_relationships.py -p <project> --all --max-per-entity 3 --json
"""

import sys
import os
import json
import argparse
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_config

from neo4j import GraphDatabase


def get_driver(cfg):
    return GraphDatabase.driver(cfg.neo4j_uri, auth=cfg.neo4j_auth)


def cosine_similarity(vec_a, vec_b):
    """Compute cosine similarity between two vectors (fallback when GDS is unavailable)."""
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def find_candidates_gds(driver, names, threshold, max_per_entity):
    """Find candidate pairs using GDS cosine similarity (Neo4j built-in)."""
    with driver.session() as session:
        results = session.run("""
            MATCH (src:Entity)
            WHERE src.name IN $names
              AND src.embedding IS NOT NULL
              AND coalesce(src.status, 'active') = 'active'
            WITH src
            MATCH (cand:Entity)
            WHERE cand.name <> src.name
              AND cand.embedding IS NOT NULL
              AND coalesce(cand.status, 'active') = 'active'
              AND NOT (src)-[:RELATES_TO]-(cand)
            WITH src, cand, gds.similarity.cosine(src.embedding, cand.embedding) AS score
            WHERE score >= $threshold
            OPTIONAL MATCH (src)-[:SOURCED_FROM]->(srcDoc)
            OPTIONAL MATCH (cand)-[:SOURCED_FROM]->(candDoc)
            WITH src, cand, score,
                 collect(DISTINCT elementId(srcDoc)) AS srcDocs,
                 collect(DISTINCT elementId(candDoc)) AS candDocs
            WHERE size(candDocs) = 0 OR NOT all(d IN candDocs WHERE d IN srcDocs)
            RETURN src.name AS source_name, src.description AS source_desc,
                   cand.name AS target_name, cand.description AS target_desc, score
            ORDER BY score DESC
        """, names=names, threshold=threshold)
        return [dict(r) for r in results]


def find_candidates_fallback(driver, names, threshold, max_per_entity):
    """Find candidate pairs using Python-side cosine similarity (fallback)."""
    with driver.session() as session:
        # Get source entities with embeddings
        src_results = session.run("""
            MATCH (src:Entity)
            WHERE src.name IN $names
              AND src.embedding IS NOT NULL
              AND coalesce(src.status, 'active') = 'active'
            OPTIONAL MATCH (src)-[:SOURCED_FROM]->(srcDoc)
            RETURN src.name AS name, src.description AS description,
                   src.embedding AS embedding,
                   collect(DISTINCT elementId(srcDoc)) AS doc_ids
        """, names=names).data()

        if not src_results:
            return []

        # Get all candidate entities
        cand_results = session.run("""
            MATCH (cand:Entity)
            WHERE cand.embedding IS NOT NULL
              AND coalesce(cand.status, 'active') = 'active'
              AND NOT cand.name IN $names
            OPTIONAL MATCH (cand)-[:SOURCED_FROM]->(candDoc)
            RETURN cand.name AS name, cand.description AS description,
                   cand.embedding AS embedding,
                   collect(DISTINCT elementId(candDoc)) AS doc_ids
        """, names=names).data()

        if not cand_results:
            return []

        # Get existing RELATES_TO pairs to exclude
        existing_pairs = set()
        existing_results = session.run("""
            MATCH (a:Entity)-[:RELATES_TO]-(b:Entity)
            WHERE a.name IN $names
            RETURN a.name AS a_name, b.name AS b_name
        """, names=names).data()
        for er in existing_results:
            existing_pairs.add((er["a_name"], er["b_name"]))
            existing_pairs.add((er["b_name"], er["a_name"]))

    candidates = []
    for src in src_results:
        for cand in cand_results:
            if src["name"] == cand["name"]:
                continue
            if (src["name"], cand["name"]) in existing_pairs:
                continue

            # Skip if all candidate docs are in source docs (same document)
            cand_docs = cand["doc_ids"]
            src_docs = src["doc_ids"]
            if cand_docs and all(d in src_docs for d in cand_docs):
                continue

            score = cosine_similarity(src["embedding"], cand["embedding"])
            if score >= threshold:
                candidates.append({
                    "source_name": src["name"],
                    "source_desc": src["description"],
                    "target_name": cand["name"],
                    "target_desc": cand["description"],
                    "score": score,
                })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


def find_candidates(driver, names, threshold, max_per_entity):
    """Find candidate entity pairs, trying GDS first, falling back to Python."""
    try:
        candidates = find_candidates_gds(driver, names, threshold, max_per_entity)
    except Exception:
        print("  [warn] GDS cosine function unavailable, using Python fallback",
              file=sys.stderr)
        candidates = find_candidates_fallback(driver, names, threshold, max_per_entity)
    return candidates


def deduplicate_candidates(candidates):
    """Deduplicate pairs: sort names alphabetically, always create A->B direction."""
    seen = set()
    unique = []
    for c in candidates:
        pair = tuple(sorted([c["source_name"], c["target_name"]]))
        if pair not in seen:
            seen.add(pair)
            # Ensure alphabetical direction
            if c["source_name"] <= c["target_name"]:
                unique.append(c)
            else:
                unique.append({
                    "source_name": c["target_name"],
                    "source_desc": c["target_desc"],
                    "target_name": c["source_name"],
                    "target_desc": c["source_desc"],
                    "score": c["score"],
                })
    return unique


def apply_max_per_entity(candidates, max_per_entity):
    """Limit the number of candidates per source entity."""
    counts = {}
    filtered = []
    for c in candidates:
        src = c["source_name"]
        tgt = c["target_name"]
        counts.setdefault(src, 0)
        counts.setdefault(tgt, 0)
        if counts[src] < max_per_entity and counts[tgt] < max_per_entity:
            counts[src] += 1
            counts[tgt] += 1
            filtered.append(c)
    return filtered


def create_relationships(driver, candidates):
    """Create RELATES_TO relationships for candidate pairs."""
    created = 0
    with driver.session() as session:
        for c in candidates:
            desc = (f"Auto-discovered similarity between "
                    f"'{c['source_name']}' and '{c['target_name']}'")
            result = session.run("""
                MATCH (a:Entity {name: $src}), (b:Entity {name: $tgt})
                MERGE (a)-[r:RELATES_TO]->(b)
                ON CREATE SET r.type = 'auto_discovered',
                              r.description = $desc,
                              r.weight = 1.0
                ON MATCH SET r.weight = r.weight + 0.5
                RETURN r
            """, src=c["source_name"], tgt=c["target_name"], desc=desc)
            if result.single():
                created += 1
    return created


def get_all_entity_names(driver):
    """Get all active entity names that have embeddings."""
    with driver.session() as session:
        results = session.run("""
            MATCH (e:Entity)
            WHERE e.embedding IS NOT NULL
              AND coalesce(e.status, 'active') = 'active'
            RETURN e.name AS name
        """).data()
    return [r["name"] for r in results]


def discover_relationships(driver, names, threshold=0.85, max_per_entity=5,
                           dry_run=False, output_json=False):
    """Main discovery logic. Returns list of candidates and count of created relationships."""
    candidates = find_candidates(driver, names, threshold, max_per_entity)
    candidates = deduplicate_candidates(candidates)
    candidates = apply_max_per_entity(candidates, max_per_entity)

    if output_json:
        result = {
            "candidates": [
                {
                    "source": c["source_name"],
                    "target": c["target_name"],
                    "score": round(c["score"], 4),
                }
                for c in candidates
            ],
            "threshold": threshold,
            "dry_run": dry_run,
        }
        if not dry_run:
            created = create_relationships(driver, candidates)
            result["created"] = created
        else:
            result["created"] = 0
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return candidates, result.get("created", 0)

    if dry_run:
        print(f"  [found] {len(candidates)} candidates (threshold: {threshold})",
              file=sys.stderr)
        for c in candidates:
            print(f"  [candidate] {c['source_name']} -> {c['target_name']} "
                  f"(score: {c['score']:.2f})", file=sys.stderr)
        print(f"  [dry-run] {len(candidates)} candidates found, "
              f"no relationships created", file=sys.stderr)
        return candidates, 0
    else:
        print(f"  [found] {len(candidates)} candidates (threshold: {threshold})",
              file=sys.stderr)
        for c in candidates:
            print(f"  [{c['score']:.2f}] {c['source_name']} -> {c['target_name']}",
                  file=sys.stderr)
        created = create_relationships(driver, candidates)
        print(f"  [created] {created} new relationships", file=sys.stderr)
        return candidates, created


def main():
    parser = argparse.ArgumentParser(
        description="Auto-discover cross-document entity relationships")
    parser.add_argument("--project", "-p", default=None,
                        help="Project name (key in config.py PROJECTS)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true",
                       help="Process all active entities with embeddings")
    group.add_argument("--source-entities", default=None,
                       help="Comma-separated entity names to process")
    parser.add_argument("--threshold", type=float, default=0.85,
                        help="Cosine similarity threshold (default: 0.85)")
    parser.add_argument("--max-per-entity", type=int, default=5,
                        help="Max candidates per entity (default: 5)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show candidates without creating relationships")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    args = parser.parse_args()

    cfg = get_config(args.project)

    mode = "all" if args.all else "source-entities"
    dry_label = " (dry-run)" if args.dry_run else ""
    if not args.json:
        print(f"[{cfg.project}] Discovering cross-document relationships{dry_label}...",
              file=sys.stderr)

    driver = get_driver(cfg)
    try:
        if args.all:
            names = get_all_entity_names(driver)
            if not names:
                if not args.json:
                    print("  [skip] No entities with embeddings found",
                          file=sys.stderr)
                else:
                    print(json.dumps({"candidates": [], "created": 0,
                                      "message": "No entities with embeddings"}))
                return
        else:
            names = [n.strip() for n in args.source_entities.split(",") if n.strip()]

        discover_relationships(
            driver, names,
            threshold=args.threshold,
            max_per_entity=args.max_per_entity,
            dry_run=args.dry_run,
            output_json=args.json,
        )
    finally:
        driver.close()


if __name__ == "__main__":
    main()
