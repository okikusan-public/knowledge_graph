#!/usr/bin/env python3
"""
Knowledge graph quality linter.

Detects duplicates (near-duplicate entities by embedding similarity),
orphans (structurally isolated entities), and stale entities (old source
with time-dependent language).

Usage:
  python lint_graph.py duplicates -p <project>
  python lint_graph.py duplicates -p <project> --threshold 0.90 --fix --dry-run
  python lint_graph.py orphans -p <project> --min-age 7
  python lint_graph.py stale -p <project> --stale-days 90
  python lint_graph.py all -p <project> --json
"""

import sys
import os
import json
import re
import math
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_config
from neo4j import GraphDatabase


def get_driver(cfg):
    return GraphDatabase.driver(cfg.neo4j_uri, auth=cfg.neo4j_auth)


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def cosine_similarity(vec_a, vec_b):
    """Compute cosine similarity between two vectors."""
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _cluster_duplicates(pairs):
    """Group duplicate pairs into connected clusters using union-find."""
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for p in pairs:
        union(p["name_a"], p["name_b"])

    all_names = set()
    for p in pairs:
        all_names.add(p["name_a"])
        all_names.add(p["name_b"])

    clusters = {}
    for name in all_names:
        root = find(name)
        clusters.setdefault(root, set()).add(name)

    return [sorted(members) for members in clusters.values()]


# Stale detection patterns
STALE_PATTERNS_JA = [r"最新", r"現在", r"今", r"時点", r"最近", r"現時点"]
STALE_PATTERNS_EN = [
    r"\blatest\b", r"\bcurrent(?:ly)?\b", r"\bnow\b", r"\bas\s+of\b",
    r"\brecent(?:ly)?\b", r"\bup-to-date\b",
]
STALE_PATTERN = re.compile(
    "|".join(STALE_PATTERNS_JA + STALE_PATTERNS_EN), re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Subcommand: duplicates
# ---------------------------------------------------------------------------

def check_duplicates(driver, threshold=0.95, fix=False, dry_run=False):
    """Detect near-duplicate entities by embedding cosine similarity."""
    pairs = []

    # Try GDS first, fall back to Python
    try:
        pairs = _duplicates_gds(driver, threshold)
    except Exception:
        print("  [info] GDS unavailable, using Python fallback", file=sys.stderr)
        pairs = _duplicates_python(driver, threshold)

    if not pairs:
        print("  [done] No duplicates found", file=sys.stderr)
        return {"groups": [], "threshold": threshold,
                "total_groups": 0, "total_duplicate_entities": 0}

    clusters = _cluster_duplicates(pairs)

    # Build entity info lookup
    entity_info = {}
    with driver.session() as session:
        for record in session.run(
            "MATCH (e:Entity) WHERE coalesce(e.status, 'active') = 'active' "
            "RETURN e.name AS name, e.type AS type, e.description AS description"
        ):
            entity_info[record["name"]] = {
                "name": record["name"],
                "type": record["type"],
                "description": record["description"],
            }

    # Build pair lookup
    pair_lookup = {}
    for p in pairs:
        pair_lookup[(p["name_a"], p["name_b"])] = p["similarity"]
        pair_lookup[(p["name_b"], p["name_a"])] = p["similarity"]

    groups = []
    for cluster in clusters:
        entities = [entity_info.get(n, {"name": n, "type": None, "description": None})
                    for n in cluster]
        cluster_pairs = []
        for i, a in enumerate(cluster):
            for b in cluster[i + 1:]:
                sim = pair_lookup.get((a, b))
                if sim is not None:
                    cluster_pairs.append(
                        {"entity_a": a, "entity_b": b, "similarity": sim})
        groups.append({"entities": entities, "pairs": cluster_pairs})

    # Report
    total_entities = sum(len(g["entities"]) for g in groups)
    for g in groups:
        names = [e["name"] for e in g["entities"]]
        best_sim = max((p["similarity"] for p in g["pairs"]), default=0)
        print(f"  [dup] {names} (best: {best_sim:.4f})", file=sys.stderr)

    print(f"  [done] {len(groups)} duplicate groups, "
          f"{total_entities} entities", file=sys.stderr)

    # Fix mode
    merged = 0
    if fix:
        for g in groups:
            merged += _merge_duplicate_group(driver, g, dry_run)
        action = "would merge" if dry_run else "merged"
        print(f"  [{action}] {merged} entities removed", file=sys.stderr)

    return {
        "groups": groups,
        "threshold": threshold,
        "total_groups": len(groups),
        "total_duplicate_entities": total_entities,
        "merged": merged if fix else None,
        "dry_run": dry_run if fix else None,
    }


def _duplicates_gds(driver, threshold):
    """Detect duplicates using GDS cosine similarity."""
    pairs = []
    with driver.session() as session:
        result = session.run("""
            MATCH (a:Entity), (b:Entity)
            WHERE a.embedding IS NOT NULL AND b.embedding IS NOT NULL
              AND coalesce(a.status, 'active') = 'active'
              AND coalesce(b.status, 'active') = 'active'
              AND elementId(a) < elementId(b)
            WITH a, b, gds.similarity.cosine(a.embedding, b.embedding) AS sim
            WHERE sim >= $threshold
            RETURN a.name AS name_a, b.name AS name_b, sim AS similarity
            ORDER BY sim DESC
        """, threshold=threshold)
        for record in result:
            pairs.append({
                "name_a": record["name_a"],
                "name_b": record["name_b"],
                "similarity": record["similarity"],
            })
    return pairs


def _duplicates_python(driver, threshold):
    """Detect duplicates using Python cosine similarity (fallback)."""
    entities = []
    with driver.session() as session:
        for record in session.run(
            "MATCH (e:Entity) "
            "WHERE e.embedding IS NOT NULL "
            "  AND coalesce(e.status, 'active') = 'active' "
            "RETURN e.name AS name, e.embedding AS embedding"
        ):
            entities.append({
                "name": record["name"],
                "embedding": record["embedding"],
            })

    pairs = []
    for i in range(len(entities)):
        for j in range(i + 1, len(entities)):
            sim = cosine_similarity(entities[i]["embedding"],
                                    entities[j]["embedding"])
            if sim >= threshold:
                pairs.append({
                    "name_a": entities[i]["name"],
                    "name_b": entities[j]["name"],
                    "similarity": sim,
                })
    pairs.sort(key=lambda p: p["similarity"], reverse=True)
    return pairs


def _merge_duplicate_group(driver, group, dry_run=False):
    """Merge a group of duplicate entities, keeping the one with longest description."""
    entities = group["entities"]
    # Pick keeper: longest description
    keeper = max(entities,
                 key=lambda e: len(e.get("description") or ""))
    others = [e for e in entities if e["name"] != keeper["name"]]

    if dry_run:
        for o in others:
            print(f"    [dry-run] would merge '{o['name']}' "
                  f"into '{keeper['name']}'", file=sys.stderr)
        return len(others)

    with driver.session() as session:
        for o in others:
            old_name = o["name"]
            keeper_name = keeper["name"]

            # Reassign RELATES_TO (outgoing)
            session.run("""
                MATCH (old:Entity {name: $old_name})-[r:RELATES_TO]->(other:Entity)
                WHERE other.name <> $keeper_name
                WITH old, other, r, r.type AS rtype,
                     r.description AS rdesc, r.weight AS rweight
                DELETE r
                WITH old, other, rtype, rdesc, rweight
                MATCH (keeper:Entity {name: $keeper_name})
                MERGE (keeper)-[nr:RELATES_TO]->(other)
                ON CREATE SET nr.type = rtype, nr.description = rdesc,
                              nr.weight = coalesce(rweight, 1.0)
                ON MATCH SET nr.weight = nr.weight + coalesce(rweight, 0.5)
            """, old_name=old_name, keeper_name=keeper_name)

            # Reassign RELATES_TO (incoming)
            session.run("""
                MATCH (other:Entity)-[r:RELATES_TO]->(old:Entity {name: $old_name})
                WHERE other.name <> $keeper_name
                WITH old, other, r, r.type AS rtype,
                     r.description AS rdesc, r.weight AS rweight
                DELETE r
                WITH old, other, rtype, rdesc, rweight
                MATCH (keeper:Entity {name: $keeper_name})
                MERGE (other)-[nr:RELATES_TO]->(keeper)
                ON CREATE SET nr.type = rtype, nr.description = rdesc,
                              nr.weight = coalesce(rweight, 1.0)
                ON MATCH SET nr.weight = nr.weight + coalesce(rweight, 0.5)
            """, old_name=old_name, keeper_name=keeper_name)

            # Reassign SOURCED_FROM
            session.run("""
                MATCH (old:Entity {name: $old_name})-[r:SOURCED_FROM]->(d)
                MATCH (keeper:Entity {name: $keeper_name})
                MERGE (keeper)-[:SOURCED_FROM]->(d)
                DELETE r
            """, old_name=old_name, keeper_name=keeper_name)

            # Reassign BELONGS_TO
            session.run("""
                MATCH (old:Entity {name: $old_name})-[r:BELONGS_TO]->(c:Community)
                MATCH (keeper:Entity {name: $keeper_name})
                MERGE (keeper)-[:BELONGS_TO]->(c)
                DELETE r
            """, old_name=old_name, keeper_name=keeper_name)

            # Reassign incoming MENTIONS
            session.run("""
                MATCH (chunk:Chunk)-[r:MENTIONS]->(old:Entity {name: $old_name})
                MATCH (keeper:Entity {name: $keeper_name})
                MERGE (chunk)-[:MENTIONS]->(keeper)
                DELETE r
            """, old_name=old_name, keeper_name=keeper_name)

            # Update keeper description if old has longer
            session.run("""
                MATCH (keeper:Entity {name: $keeper_name})
                WHERE size(coalesce(keeper.description, '')) < size($old_desc)
                SET keeper.description = $old_desc
            """, keeper_name=keeper_name,
               old_desc=o.get("description") or "")

            # Delete old entity
            session.run("""
                MATCH (old:Entity {name: $old_name})
                DETACH DELETE old
            """, old_name=old_name)

            print(f"    [merged] '{old_name}' into '{keeper_name}'",
                  file=sys.stderr)

    return len(others)


# ---------------------------------------------------------------------------
# Subcommand: orphans
# ---------------------------------------------------------------------------

def check_orphans(driver, min_age_days=0, fix=False, dry_run=False):
    """Detect structurally isolated entities (no RELATES_TO, no BELONGS_TO)."""
    with driver.session() as session:
        if min_age_days > 0:
            result = session.run("""
                MATCH (e:Entity)
                WHERE coalesce(e.status, 'active') = 'active'
                  AND NOT (e)-[:RELATES_TO]-()
                  AND NOT (e)-[:BELONGS_TO]->()
                OPTIONAL MATCH (e)-[:SOURCED_FROM]->(d)
                WITH e, collect(DISTINCT d.source_path) AS sources,
                     min(d.created_at) AS earliest
                WHERE earliest IS NULL
                   OR earliest < datetime() - duration({days: $min_age})
                RETURN e.name AS name, e.type AS type,
                       e.description AS description, sources,
                       earliest AS earliest_source_date
                ORDER BY e.name
            """, min_age=min_age_days)
        else:
            result = session.run("""
                MATCH (e:Entity)
                WHERE coalesce(e.status, 'active') = 'active'
                  AND NOT (e)-[:RELATES_TO]-()
                  AND NOT (e)-[:BELONGS_TO]->()
                OPTIONAL MATCH (e)-[:SOURCED_FROM]->(d)
                WITH e, collect(DISTINCT d.source_path) AS sources,
                     min(d.created_at) AS earliest
                RETURN e.name AS name, e.type AS type,
                       e.description AS description, sources,
                       earliest AS earliest_source_date
                ORDER BY e.name
            """)

        orphans = []
        for record in result:
            orphans.append({
                "name": record["name"],
                "type": record["type"],
                "description": record["description"],
                "sources": record["sources"],
                "earliest_source_date": record["earliest_source_date"],
            })

    if not orphans:
        print("  [done] No orphans found", file=sys.stderr)
    else:
        for o in orphans:
            print(f"  [orphan] {o['name']} ({o['type']})", file=sys.stderr)
        print(f"  [done] {len(orphans)} orphans found", file=sys.stderr)

    # Fix mode
    archived = 0
    if fix and orphans:
        for o in orphans:
            if dry_run:
                print(f"    [dry-run] would archive '{o['name']}'",
                      file=sys.stderr)
            else:
                _archive_orphan(driver, o["name"])
                print(f"    [archived] '{o['name']}'", file=sys.stderr)
            archived += 1
        action = "would archive" if dry_run else "archived"
        print(f"  [{action}] {archived} orphans", file=sys.stderr)

    return {
        "orphans": orphans,
        "total": len(orphans),
        "min_age_days": min_age_days,
        "archived": archived if fix else None,
        "dry_run": dry_run if fix else None,
    }


def _archive_orphan(driver, name):
    """Archive an orphan entity."""
    with driver.session() as session:
        session.run("""
            MATCH (e:Entity {name: $name})
            WHERE coalesce(e.status, 'active') = 'active'
            SET e.status = 'archived',
                e.archived_date = datetime(),
                e.archive_reason = 'lint_graph: structurally isolated orphan'
        """, name=name)


# ---------------------------------------------------------------------------
# Subcommand: stale
# ---------------------------------------------------------------------------

def check_stale(driver, stale_days=180):
    """Detect entities with old sources and time-dependent descriptions."""
    with driver.session() as session:
        result = session.run("""
            MATCH (e:Entity)-[:SOURCED_FROM]->(d)
            WHERE coalesce(e.status, 'active') = 'active'
              AND d.created_at IS NOT NULL
              AND d.created_at < datetime() - duration({days: $stale_days})
            RETURN DISTINCT e.name AS name, e.type AS type,
                   e.description AS description,
                   d.title AS doc_title, d.source_path AS doc_path,
                   d.created_at AS doc_date
            ORDER BY d.created_at ASC
        """, stale_days=stale_days)

        findings = []
        for record in result:
            desc = record["description"] or ""
            matches = STALE_PATTERN.findall(desc)
            if matches:
                findings.append({
                    "name": record["name"],
                    "type": record["type"],
                    "description": desc,
                    "doc_title": record["doc_title"],
                    "doc_path": record["doc_path"],
                    "doc_date": record["doc_date"],
                    "matched_expressions": sorted(set(matches)),
                })

    if not findings:
        print("  [done] No stale entities found", file=sys.stderr)
    else:
        for f in findings:
            print(f"  [stale] {f['name']} — matched: "
                  f"{f['matched_expressions']}", file=sys.stderr)
        print(f"  [done] {len(findings)} stale entities found", file=sys.stderr)

    return {
        "stale": findings,
        "total": len(findings),
        "stale_days": stale_days,
    }


# ---------------------------------------------------------------------------
# Subcommand: all
# ---------------------------------------------------------------------------

def run_all(driver, threshold=0.95, min_age_days=0, stale_days=180,
            fix=False, dry_run=False):
    """Run all lint checks sequentially."""
    print("=== Duplicates ===", file=sys.stderr)
    duplicates = check_duplicates(driver, threshold, fix, dry_run)

    print("\n=== Orphans ===", file=sys.stderr)
    orphans = check_orphans(driver, min_age_days, fix, dry_run)

    print("\n=== Stale ===", file=sys.stderr)
    stale = check_stale(driver, stale_days)

    return {
        "duplicates": duplicates,
        "orphans": orphans,
        "stale": stale,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Knowledge graph quality linter")
    parser.add_argument("command",
                        choices=["duplicates", "orphans", "stale", "all"])
    parser.add_argument("--project", "-p", default=None,
                        help="Project name")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON to stdout")
    parser.add_argument("--threshold", type=float, default=0.95,
                        help="Cosine similarity threshold for duplicates "
                             "(default: 0.95)")
    parser.add_argument("--fix", action="store_true",
                        help="Apply fixes (merge duplicates, archive orphans)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what --fix would do without doing it")
    parser.add_argument("--min-age", type=int, default=0,
                        help="Minimum entity age in days for orphan check "
                             "(default: 0)")
    parser.add_argument("--stale-days", type=int, default=180,
                        help="Days threshold for stale detection "
                             "(default: 180)")
    args = parser.parse_args()

    cfg = get_config(args.project)
    print(f"[{cfg.project}] Running lint: {args.command}...",
          file=sys.stderr)

    driver = get_driver(cfg)
    try:
        if args.command == "duplicates":
            result = check_duplicates(
                driver, args.threshold, args.fix, args.dry_run)
        elif args.command == "orphans":
            result = check_orphans(
                driver, args.min_age, args.fix, args.dry_run)
        elif args.command == "stale":
            result = check_stale(driver, args.stale_days)
        elif args.command == "all":
            result = run_all(
                driver, args.threshold, args.min_age,
                args.stale_days, args.fix, args.dry_run)

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2,
                             default=str))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
