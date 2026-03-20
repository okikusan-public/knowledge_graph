#!/usr/bin/env python3
"""
GDS Leiden Community Detection（プロジェクト共通版）

3-level hierarchy:
  Level 0 (gamma=1.5): Fine-grained
  Level 1 (gamma=0.7): Medium
  Level 2 (gamma=0.3): Coarse

Usage:
  python community_detection.py                    # デフォルトプロジェクト
  python community_detection.py --project project_a   # プロジェクト指定
"""

import sys
import os
import uuid
import argparse
import requests
from neo4j import GraphDatabase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_config


def setup(cfg):
    driver = GraphDatabase.driver(cfg.neo4j_uri, auth=cfg.neo4j_auth)

    def run_query(query, params=None):
        with driver.session() as s:
            return [r.data() for r in s.run(query, params or {})]

    def run_single(query, params=None):
        with driver.session() as s:
            rec = s.run(query, params or {}).single()
            return rec.data() if rec else None

    def run_write(query, params=None):
        with driver.session() as s:
            s.run(query, params or {})

    def get_embedding(text):
        resp = requests.post(cfg.embed_url, json={"inputs": f"passage: {text[:2000]}"})
        resp.raise_for_status()
        return resp.json()["embeddings"][0]

    def get_embeddings_batch(texts):
        prefixed = [f"passage: {t[:2000]}" if t.strip() else "passage: empty" for t in texts]
        resp = requests.post(cfg.embed_url, json={"inputs": prefixed})
        resp.raise_for_status()
        return resp.json()["embeddings"]

    return driver, run_query, run_single, run_write, get_embedding, get_embeddings_batch


# =============================================================================
# Step 1: Graph Projection
# =============================================================================

def create_projection(run_query, run_single, run_write):
    print("=== Step 1: Graph Projection ===")

    existing = run_query("CALL gds.graph.list() YIELD graphName RETURN graphName")
    for g in existing:
        print(f"  Dropping existing projection: {g['graphName']}")
        run_write("CALL gds.graph.drop($name)", {"name": g["graphName"]})

    result = run_single("""
        CALL gds.graph.project(
            'entity-graph',
            'Entity',
            {
                RELATES_TO: {
                    orientation: 'UNDIRECTED',
                    properties: ['weight']
                }
            }
        )
        YIELD graphName, nodeCount, relationshipCount
        RETURN graphName, nodeCount, relationshipCount
    """)
    print(f"  Projected: {result['graphName']} "
          f"({result['nodeCount']} nodes, {result['relationshipCount']} rels)")
    return result


# =============================================================================
# Step 2: Leiden Community Detection (3 levels)
# =============================================================================

def run_leiden(level, gamma, run_single, run_query):
    print(f"\n=== Step 2.{level}: Leiden Level {level} (gamma={gamma}) ===")

    prop_name = f"community_level{level}"

    result = run_single(f"""
        CALL gds.leiden.write('entity-graph', {{
            writeProperty: '{prop_name}',
            gamma: {gamma},
            maxLevels: 10,
            includeIntermediateCommunities: false
        }})
        YIELD communityCount, modularity, ranLevels, didConverge
        RETURN communityCount, modularity, ranLevels, didConverge
    """)
    print(f"  Communities: {result['communityCount']}, "
          f"Modularity: {result['modularity']:.4f}, "
          f"Levels: {result['ranLevels']}, "
          f"Converged: {result['didConverge']}")

    sizes = run_query(f"""
        MATCH (e:Entity)
        WITH e.{prop_name} AS communityId, collect(e.name) AS members
        RETURN communityId, size(members) AS memberCount, members
        ORDER BY memberCount DESC
    """)
    for s in sizes:
        preview = ", ".join(s["members"][:5])
        if len(s["members"]) > 5:
            preview += f" ... (+{len(s['members'])-5})"
        print(f"  Community {s['communityId']}: {s['memberCount']} members [{preview}]")

    return result["communityCount"], sizes


# =============================================================================
# Step 3: Create Community Nodes
# =============================================================================

TYPE_LABELS = {
    "TECHNOLOGY": "技術",
    "REQUIREMENT": "要件",
    "ORGANIZATION": "組織",
    "PERSON": "人物",
    "SCHEDULE": "スケジュール",
    "BUDGET": "予算・数値",
    "RISK": "リスク",
    "PROPOSAL_PATTERN": "提案パターン",
    "EVALUATION_CRITERIA": "評価基準",
    "DELIVERABLE": "成果物",
    "SECURITY": "セキュリティ",
    "DOMAIN": "業務領域",
}


def generate_community_title_and_summary(members, relationships):
    """Generate title and summary from community members and their relationships."""
    by_type = {}
    for m in members:
        t = m.get("type", "OTHER")
        if t not in by_type:
            by_type[t] = []
        by_type[t].append(m["name"])

    type_counts = sorted(by_type.items(), key=lambda x: -len(x[1]))
    dominant_type = type_counts[0][0] if type_counts else "MIXED"
    dominant_names = type_counts[0][1][:3] if type_counts else []

    type_label = TYPE_LABELS.get(dominant_type, dominant_type)

    if len(members) <= 3:
        title = "・".join([m["name"] for m in members[:3]])
    else:
        title = f"{type_label}: {', '.join(dominant_names[:2])}等"

    member_descriptions = []
    for m in members[:10]:
        member_descriptions.append(f"{m['name']}({TYPE_LABELS.get(m.get('type',''), m.get('type',''))})")

    rel_descriptions = []
    for r in relationships[:8]:
        rel_descriptions.append(f"{r['src']}→{r['rel_type']}→{r['tgt']}")

    summary_parts = [f"メンバー: {', '.join(member_descriptions)}"]
    if rel_descriptions:
        summary_parts.append(f"関係: {'; '.join(rel_descriptions)}")

    summary = "。".join(summary_parts)

    if len(title) > 50:
        title = title[:47] + "..."
    if len(summary) > 300:
        summary = summary[:297] + "..."

    return title, summary


def create_community_nodes(level, run_query, run_write, get_embedding):
    print(f"\n=== Step 3.{level}: Creating Community Nodes (Level {level}) ===")

    prop_name = f"community_level{level}"

    communities = run_query(f"""
        MATCH (e:Entity)
        WITH e.{prop_name} AS communityId, collect({{
            id: e.id, name: e.name, type: e.type, description: e.description
        }}) AS members
        RETURN communityId, members
        ORDER BY size(members) DESC
    """)

    for comm in communities:
        comm_id = comm["communityId"]
        members = comm["members"]

        member_ids = [m["id"] for m in members]
        rels = run_query("""
            MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity)
            WHERE a.id IN $ids AND b.id IN $ids
            RETURN a.name AS src, r.type AS rel_type, b.name AS tgt
        """, {"ids": member_ids})

        title, summary = generate_community_title_and_summary(members, rels)

        rel_density = len(rels) / max(len(members), 1)
        rank = min(1.0, (len(members) / 20.0) * 0.5 + (rel_density / 3.0) * 0.5)

        emb = get_embedding(f"{title}。{summary}")

        node_id = str(uuid.uuid4())

        run_write("""
            CREATE (c:Community {
                id: $id, level: $level, title: $title,
                summary: $summary, rank: $rank, embedding: $emb
            })
        """, {
            "id": node_id, "level": level, "title": title,
            "summary": summary, "rank": round(rank, 3), "emb": emb,
        })

        for m in members:
            run_write("""
                MATCH (e:Entity {id: $eid}), (c:Community {id: $cid})
                CREATE (e)-[:BELONGS_TO {level: $level}]->(c)
            """, {"eid": m["id"], "cid": node_id, "level": level})

        print(f"  Community L{level}-{comm_id}: \"{title}\" ({len(members)} members, {len(rels)} rels, rank={rank:.3f})")


# =============================================================================
# Step 4: CHILD_OF Relationships
# =============================================================================

def create_child_of_relationships(run_query, run_write):
    print("\n=== Step 4: Creating CHILD_OF relationships ===")

    for child_level, parent_level in [(0, 1), (1, 2)]:
        child_communities = run_query(f"""
            MATCH (e:Entity)-[:BELONGS_TO {{level: {child_level}}}]->(child:Community {{level: {child_level}}})
            WITH child, collect(e.id) AS memberIds
            RETURN child.id AS childId, memberIds
        """)

        created = 0
        for cc in child_communities:
            child_id = cc["childId"]
            member_ids = cc["memberIds"]

            if not member_ids:
                continue

            parent_counts = run_query(f"""
                MATCH (e:Entity)-[:BELONGS_TO {{level: {parent_level}}}]->(parent:Community {{level: {parent_level}}})
                WHERE e.id IN $memberIds
                WITH parent, count(e) AS overlap
                RETURN parent.id AS parentId, overlap
                ORDER BY overlap DESC
                LIMIT 1
            """, {"memberIds": member_ids})

            if parent_counts:
                parent_id = parent_counts[0]["parentId"]
                run_write("""
                    MATCH (child:Community {id: $childId}), (parent:Community {id: $parentId})
                    CREATE (child)-[:CHILD_OF]->(parent)
                """, {"childId": child_id, "parentId": parent_id})
                created += 1

        print(f"  Level {child_level} -> Level {parent_level}: {created} CHILD_OF relationships")


# =============================================================================
# Step 5: Verification
# =============================================================================

def verify(cfg, run_query, run_single):
    print("\n=== Step 5: Verification ===")

    counts = run_query("""
        MATCH (c:Community)
        RETURN c.level AS level, count(c) AS count
        ORDER BY c.level
    """)
    print("  Community counts:")
    for c in counts:
        print(f"    Level {c['level']}: {c['count']} communities")

    bt = run_single("MATCH ()-[r:BELONGS_TO]->() RETURN count(r) AS count")
    print(f"  BELONGS_TO: {bt['count']} relationships")

    co = run_single("MATCH ()-[r:CHILD_OF]->() RETURN count(r) AS count")
    print(f"  CHILD_OF: {co['count']} relationships")

    for level in [2, 1, 0]:
        print(f"\n  --- Level {level} Communities ---")
        communities = run_query(f"""
            MATCH (e:Entity)-[:BELONGS_TO {{level: {level}}}]->(c:Community {{level: {level}}})
            WITH c, collect(e.name) AS members, count(e) AS memberCount
            RETURN c.title AS title, c.rank AS rank, memberCount, members
            ORDER BY memberCount DESC
        """)
        for comm in communities:
            member_preview = ", ".join(comm["members"][:6])
            if len(comm["members"]) > 6:
                member_preview += f" ... (+{len(comm['members'])-6})"
            print(f"    [{comm['rank']:.3f}] {comm['title']} ({comm['memberCount']} members)")
            print(f"          {member_preview}")

    print("\n  --- Full Graph Stats ---")
    stats = run_query("MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count ORDER BY count DESC")
    for s in stats:
        print(f"    {s['label']}: {s['count']}")

    rels = run_query("MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS count ORDER BY count DESC")
    for r in rels:
        print(f"    {r['type']}: {r['count']}")


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="GDS Leiden Community Detection")
    parser.add_argument("--project", "-p", default=None,
                        help="プロジェクト名 (config.py の PROJECTS キー)")
    args = parser.parse_args()

    cfg = get_config(args.project)
    print("=" * 60)
    print(f"GDS Leiden Community Detection - {cfg.project}")
    print(f"  {cfg}")
    print("=" * 60)

    driver, run_query, run_single, run_write, get_embedding, _ = setup(cfg)

    create_projection(run_query, run_single, run_write)

    for level, gamma in [(0, 1.5), (1, 0.7), (2, 0.3)]:
        run_leiden(level, gamma, run_single, run_query)

    for level in [0, 1, 2]:
        create_community_nodes(level, run_query, run_write, get_embedding)

    create_child_of_relationships(run_query, run_write)

    verify(cfg, run_query, run_single)

    run_write("CALL gds.graph.drop('entity-graph')")
    print("\n  Dropped graph projection.")

    driver.close()
    print("\nCommunity detection complete!")


if __name__ == "__main__":
    main()
