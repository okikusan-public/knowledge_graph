#!/usr/bin/env python3
"""
ベクトル類似度検索（プロジェクト共通版）

Usage:
  python vector_search.py "search query"                            # デフォルト
  python vector_search.py "search query" --project project_a       # プロジェクト指定
  python vector_search.py "search query" --type chunk --top 5      # Chunkから5件
  python vector_search.py "search query" --type entity             # Entityから検索
  python vector_search.py "search query" --type community          # Communityから検索
"""

import sys
import os
import argparse
import requests
from neo4j import GraphDatabase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_config


def search(cfg, query_text, node_type="chunk", top_k=5):
    """ベクトル類似度検索"""
    # query用プレフィックス
    resp = requests.post(cfg.embed_url, json={"inputs": f"query: {query_text}"}, timeout=30)
    resp.raise_for_status()
    query_vec = resp.json()["embeddings"][0]

    driver = GraphDatabase.driver(cfg.neo4j_uri, auth=cfg.neo4j_auth)

    label_map = {
        "chunk": "Chunk",
        "entity": "Entity",
        "community": "Community",
        "document": "Document",
    }
    label = label_map.get(node_type.lower(), "Chunk")

    with driver.session() as session:
        # vector indexがある場合はそちらを使う
        try:
            index_name = f"{node_type.lower()}_embeddings"
            results = session.run(f"""
                CALL db.index.vector.queryNodes('{index_name}', $k, $vec)
                YIELD node, score
                RETURN node, score
            """, k=top_k, vec=query_vec).data()
        except Exception:
            # vector indexがない場合はbrute force cosine similarity
            results = session.run(f"""
                MATCH (n:{label})
                WHERE n.embedding IS NOT NULL
                WITH n, gds.similarity.cosine(n.embedding, $vec) AS score
                ORDER BY score DESC
                LIMIT $k
                RETURN n AS node, score
            """, vec=query_vec, k=top_k).data()

    driver.close()
    return results


def main():
    parser = argparse.ArgumentParser(description="GraphRAG Vector Search")
    parser.add_argument("query", help="検索クエリ")
    parser.add_argument("--project", "-p", default=None)
    parser.add_argument("--type", "-t", default="chunk",
                        choices=["chunk", "entity", "community", "document"])
    parser.add_argument("--top", "-k", type=int, default=5)
    args = parser.parse_args()

    cfg = get_config(args.project)
    print(f"[{cfg.project}] Searching {args.type}s for: \"{args.query}\"")
    print("-" * 60)

    results = search(cfg, args.query, args.type, args.top)

    for i, r in enumerate(results, 1):
        node = r["node"]
        score = r["score"]

        if args.type == "chunk":
            text = node.get("text", "")[:200]
            print(f"{i}. [{score:.4f}] {node.get('chunk_id', node.get('id', ''))}")
            print(f"   {text}...")
        elif args.type == "entity":
            print(f"{i}. [{score:.4f}] {node.get('name', '')}")
            print(f"   {node.get('description', '')[:200]}")
        elif args.type == "community":
            print(f"{i}. [{score:.4f}] L{node.get('level', '?')} {node.get('title', '')}")
            print(f"   {node.get('summary', '')[:200]}")
        elif args.type == "document":
            print(f"{i}. [{score:.4f}] {node.get('title', '')}")
            print(f"   {node.get('source_path', '')}")
        print()


if __name__ == "__main__":
    main()
