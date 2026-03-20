#!/usr/bin/env python3
"""
既存ノードにembeddingを一括付与（プロジェクト共通版）
embedding未設定のChunk/Entityに対してバッチ処理でembeddingを生成・格納する。

Usage:
  python embed_existing.py                          # デフォルト
  python embed_existing.py --project project_a        # プロジェクト指定
  python embed_existing.py --project project_a --label Entity  # Entity のみ
"""

import sys
import os
import argparse
import requests
from neo4j import GraphDatabase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_config


BATCH_SIZE = 32


def get_embeddings_batch(cfg, texts):
    prefixed = [f"passage: {t[:2000]}" if t.strip() else "passage: empty" for t in texts]
    resp = requests.post(cfg.embed_url, json={"inputs": prefixed}, timeout=120)
    resp.raise_for_status()
    return resp.json()["embeddings"]


def embed_chunks(driver, cfg):
    """embedding未設定のChunkにembeddingを付与"""
    with driver.session() as session:
        chunks = session.run("""
            MATCH (c:Chunk)
            WHERE c.embedding IS NULL AND c.text IS NOT NULL
            RETURN c.chunk_id AS id, c.id AS uuid_id, c.text AS text
        """).data()

    if not chunks:
        print("  Chunks: all embedded already")
        return 0

    print(f"  Chunks to embed: {len(chunks)}")

    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i:i + BATCH_SIZE]
        texts = [c["text"] for c in batch]
        embeddings = get_embeddings_batch(cfg, texts)

        with driver.session() as session:
            for chunk, emb in zip(batch, embeddings):
                # chunk_id or id でマッチ
                cid = chunk.get("id") or chunk.get("uuid_id")
                if chunk.get("id"):
                    session.run("MATCH (c:Chunk {chunk_id: $id}) SET c.embedding = $emb",
                                id=cid, emb=emb)
                else:
                    session.run("MATCH (c:Chunk {id: $id}) SET c.embedding = $emb",
                                id=cid, emb=emb)

        print(f"    Batch {i // BATCH_SIZE + 1}: {len(batch)} chunks")

    return len(chunks)


def embed_entities(driver, cfg):
    """embedding未設定のEntityにembeddingを付与"""
    with driver.session() as session:
        entities = session.run("""
            MATCH (e:Entity)
            WHERE e.embedding IS NULL AND e.name IS NOT NULL
            RETURN e.name AS name, e.id AS uuid_id, e.description AS desc
        """).data()

    if not entities:
        print("  Entities: all embedded already")
        return 0

    print(f"  Entities to embed: {len(entities)}")

    for i in range(0, len(entities), BATCH_SIZE):
        batch = entities[i:i + BATCH_SIZE]
        texts = [f"{e['name']}。{e.get('desc', '')}" for e in batch]
        embeddings = get_embeddings_batch(cfg, texts)

        with driver.session() as session:
            for ent, emb in zip(batch, embeddings):
                if ent.get("uuid_id"):
                    session.run("MATCH (e:Entity {id: $id}) SET e.embedding = $emb",
                                id=ent["uuid_id"], emb=emb)
                else:
                    session.run("MATCH (e:Entity {name: $name}) SET e.embedding = $emb",
                                name=ent["name"], emb=emb)

        print(f"    Batch {i // BATCH_SIZE + 1}: {len(batch)} entities")

    return len(entities)


def main():
    parser = argparse.ArgumentParser(description="Embed existing nodes")
    parser.add_argument("--project", "-p", default=None)
    parser.add_argument("--label", "-l", default="all",
                        choices=["all", "Chunk", "Entity"])
    args = parser.parse_args()

    cfg = get_config(args.project)
    print(f"=== Embed Existing Nodes ({cfg.project}) ===")
    print(f"  {cfg}")

    driver = GraphDatabase.driver(cfg.neo4j_uri, auth=cfg.neo4j_auth)

    total = 0
    if args.label in ("all", "Chunk"):
        total += embed_chunks(driver, cfg)
    if args.label in ("all", "Entity"):
        total += embed_entities(driver, cfg)

    driver.close()
    print(f"\nDone. Embedded {total} nodes.")


if __name__ == "__main__":
    main()
