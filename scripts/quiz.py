#!/usr/bin/env python3
"""
Spaced repetition quiz system using GraphRAG entities.

Subcommands:
  select  - Select entities due for review, output JSON
  record  - Record quiz result, update entity spaced repetition properties

Usage:
  python quiz.py select -p <project> [-k 5] [--topic "topic"]
  python quiz.py record -p <project> --json '{"entity_name":...,"is_correct":...,"question":...,"user_answer":...,"score":...,"feedback":...}'
  echo '{"entity_name":...}' | python quiz.py record -p <project>
"""

import sys
import os
import json
import uuid
import argparse
import math
from datetime import datetime, timezone

import requests
from neo4j import GraphDatabase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_config


def get_driver(cfg):
    return GraphDatabase.driver(cfg.neo4j_uri, auth=cfg.neo4j_auth)


def get_embedding(cfg, text):
    prefixed = f"query: {text[:2000]}"
    resp = requests.post(cfg.embed_url, json={"inputs": prefixed}, timeout=30)
    resp.raise_for_status()
    return resp.json()["embeddings"][0]


# ---------------------------------------------------------------------------
# Spaced repetition interval calculation (SM-2 inspired)
# ---------------------------------------------------------------------------

def next_interval_days(correct_count, incorrect_count):
    """Calculate next review interval in days.

    Starts at 1 day, doubles with each consecutive correct answer.
    Incorrect answers reset the interval to 1 day.
    """
    if incorrect_count > correct_count:
        return 1.0
    streak = correct_count - incorrect_count
    # Cap at 90 days
    return min(1.0 * (2.0 ** streak), 90.0)


# ---------------------------------------------------------------------------
# select: Pick entities due for quiz
# ---------------------------------------------------------------------------

def select_entities(cfg, k=5, topic=None):
    """Select entities due for spaced repetition review.

    Priority order:
      1. Entities with incorrect answers and overdue for review
      2. Entities never quizzed
      3. Entities overdue for review (correct but interval elapsed)

    If topic is given, use vector search to filter by topic relevance.
    Interleaving: shuffle entity types to mix domains.
    """
    driver = get_driver(cfg)
    topic_emb = get_embedding(cfg, topic) if topic else None

    try:
        with driver.session() as session:
            if topic_emb:
                # Topic mode: use vector similarity to find relevant due entities
                result = session.run("""
                    MATCH (e:Entity)
                    WHERE e.description IS NOT NULL AND e.description <> ''
                      AND e.embedding IS NOT NULL
                      AND coalesce(e.status, 'active') = 'active'
                    WITH e,
                         coalesce(e.correct_count, 0) AS cc,
                         coalesce(e.incorrect_count, 0) AS ic,
                         e.last_quiz_date AS lqd,
                         coalesce(e.quiz_interval_days, 1.0) AS interval_days,
                         gds.similarity.cosine(e.embedding, $topic_emb) AS topic_score
                    WHERE topic_score > 0.80
                    WITH e, cc, ic, lqd, interval_days, topic_score,
                         CASE
                           WHEN lqd IS NULL THEN 9999
                           ELSE duration.between(lqd, datetime()).days +
                                duration.between(lqd, datetime()).hours / 24.0
                         END AS days_since_quiz
                    WHERE days_since_quiz >= interval_days OR lqd IS NULL
                    WITH e, cc, ic, topic_score,
                         CASE
                           WHEN ic > cc THEN 3
                           WHEN lqd IS NULL THEN 2
                           ELSE 1
                         END AS priority
                    ORDER BY priority DESC, topic_score DESC
                    LIMIT $limit
                    OPTIONAL MATCH (e)-[r:RELATES_TO]-(related:Entity)
                    WITH e, cc, ic, priority,
                         [x IN collect(DISTINCT {name: related.name, type: related.type,
                                 rel_type: r.type}) WHERE x.name IS NOT NULL] AS relations
                    OPTIONAL MATCH (e)-[:BELONGS_TO]->(c:Community)
                    WITH e, cc, ic, priority, relations,
                         collect(DISTINCT c.title)[0] AS community
                    RETURN e.name AS name, e.type AS type,
                           e.description AS description,
                           cc AS correct_count, ic AS incorrect_count,
                           priority, relations, community
                """, topic_emb=topic_emb, limit=k * 3)
            else:
                # No topic: select by priority and overdue days
                result = session.run("""
                    MATCH (e:Entity)
                    WHERE e.description IS NOT NULL AND e.description <> ''
                      AND coalesce(e.status, 'active') = 'active'
                    WITH e,
                         coalesce(e.correct_count, 0) AS cc,
                         coalesce(e.incorrect_count, 0) AS ic,
                         e.last_quiz_date AS lqd,
                         coalesce(e.quiz_interval_days, 1.0) AS interval_days
                    WITH e, cc, ic, lqd, interval_days,
                         CASE
                           WHEN lqd IS NULL THEN 9999
                           ELSE duration.between(lqd, datetime()).days +
                                duration.between(lqd, datetime()).hours / 24.0
                         END AS days_since_quiz
                    WHERE days_since_quiz >= interval_days OR lqd IS NULL
                    WITH e, cc, ic, lqd, days_since_quiz,
                         CASE
                           WHEN ic > cc THEN 3
                           WHEN lqd IS NULL THEN 2
                           ELSE 1
                         END AS priority,
                         CASE
                           WHEN lqd IS NULL THEN 9999
                           ELSE days_since_quiz
                         END AS overdue_days
                    ORDER BY priority DESC, overdue_days DESC
                    LIMIT $limit
                    OPTIONAL MATCH (e)-[r:RELATES_TO]-(related:Entity)
                    WITH e, cc, ic, priority,
                         [x IN collect(DISTINCT {name: related.name, type: related.type,
                                 rel_type: r.type}) WHERE x.name IS NOT NULL] AS relations
                    OPTIONAL MATCH (e)-[:BELONGS_TO]->(c:Community)
                    WITH e, cc, ic, priority, relations,
                         collect(DISTINCT c.title)[0] AS community
                    RETURN e.name AS name, e.type AS type,
                           e.description AS description,
                           cc AS correct_count, ic AS incorrect_count,
                           priority, relations, community
                """, limit=k * 3)

            candidates = [dict(r) for r in result]

        if not candidates:
            return []

        # Interleaving: diversify by type
        selected = []
        seen_types = {}
        for c in candidates:
            t = c.get("type", "UNKNOWN")
            count = seen_types.get(t, 0)
            if count < max(2, k // 2):
                selected.append(c)
                seen_types[t] = count + 1
            if len(selected) >= k:
                break

        # If not enough, fill remaining
        if len(selected) < k:
            for c in candidates:
                if c not in selected:
                    selected.append(c)
                if len(selected) >= k:
                    break

        # Format output
        output = []
        for s in selected:
            entry = {
                "name": s["name"],
                "type": s["type"],
                "description": s["description"],
                "correct_count": s["correct_count"],
                "incorrect_count": s["incorrect_count"],
                "community": s["community"],
                "relations": s["relations"][:5],  # limit to avoid huge output
            }
            output.append(entry)

        return output

    finally:
        driver.close()


# ---------------------------------------------------------------------------
# record: Save quiz result
# ---------------------------------------------------------------------------

def record_result(cfg, data):
    """Record a quiz result.

    Creates a QuizResult node and updates entity spaced repetition properties.
    """
    entity_name = data["entity_name"]
    is_correct = data["is_correct"]
    question = data.get("question", "")
    user_answer = data.get("user_answer", "")
    score = data.get("score", 1.0 if is_correct else 0.0)
    feedback = data.get("feedback", "")

    driver = get_driver(cfg)
    try:
        with driver.session() as session:
            # Create QuizResult node
            result_id = str(uuid.uuid4())
            session.run("""
                CREATE (qr:QuizResult {
                    id: $id,
                    entity_name: $entity_name,
                    question: $question,
                    user_answer: $user_answer,
                    is_correct: $is_correct,
                    score: $score,
                    feedback: $feedback,
                    created_at: datetime()
                })
            """, id=result_id, entity_name=entity_name, question=question,
                 user_answer=user_answer, is_correct=is_correct,
                 score=score, feedback=feedback)

            # Link QuizResult to Entity via QUIZ_RESULT_FOR
            session.run("""
                MATCH (e:Entity {name: $name}), (qr:QuizResult {id: $qr_id})
                MERGE (qr)-[:QUIZ_RESULT_FOR]->(e)
            """, name=entity_name, qr_id=result_id)

            # Update entity spaced repetition properties
            result = session.run("""
                MATCH (e:Entity {name: $name})
                SET e.last_quiz_date = datetime(),
                    e.correct_count = coalesce(e.correct_count, 0) + CASE WHEN $correct THEN 1 ELSE 0 END,
                    e.incorrect_count = coalesce(e.incorrect_count, 0) + CASE WHEN $correct THEN 0 ELSE 1 END
                RETURN e.correct_count AS cc, e.incorrect_count AS ic
            """, name=entity_name, correct=is_correct)

            row = result.single()
            if row:
                cc = row["cc"]
                ic = row["ic"]
                interval = next_interval_days(cc, ic)
                session.run("""
                    MATCH (e:Entity {name: $name})
                    SET e.quiz_interval_days = $interval
                """, name=entity_name, interval=interval)
            else:
                interval = 1.0

        return {
            "result_id": result_id,
            "entity_name": entity_name,
            "is_correct": is_correct,
            "next_interval_days": interval,
        }

    finally:
        driver.close()


# ---------------------------------------------------------------------------
# stats: Show quiz statistics
# ---------------------------------------------------------------------------

def get_stats(cfg):
    """Get overall quiz statistics."""
    driver = get_driver(cfg)
    try:
        with driver.session() as session:
            result = session.run("""
                MATCH (e:Entity)
                WHERE e.last_quiz_date IS NOT NULL
                  AND coalesce(e.status, 'active') = 'active'
                WITH e,
                     coalesce(e.correct_count, 0) AS cc,
                     coalesce(e.incorrect_count, 0) AS ic,
                     coalesce(e.quiz_interval_days, 1.0) AS interval_days,
                     duration.between(e.last_quiz_date, datetime()).days +
                     duration.between(e.last_quiz_date, datetime()).hours / 24.0
                       AS days_since
                RETURN
                    count(e) AS total_quizzed,
                    sum(cc) AS total_correct,
                    sum(ic) AS total_incorrect,
                    sum(CASE WHEN days_since >= interval_days THEN 1 ELSE 0 END)
                      AS due_for_review,
                    avg(cc * 1.0 / CASE WHEN cc + ic = 0 THEN 1 ELSE cc + ic END)
                      AS avg_accuracy
            """)
            row = result.single()
            stats = dict(row) if row else {}

            # Count never-quizzed entities
            result2 = session.run("""
                MATCH (e:Entity)
                WHERE e.last_quiz_date IS NULL
                  AND e.description IS NOT NULL AND e.description <> ''
                  AND coalesce(e.status, 'active') = 'active'
                RETURN count(e) AS never_quizzed
            """)
            row2 = result2.single()
            stats["never_quizzed"] = row2["never_quizzed"] if row2 else 0

        return stats

    finally:
        driver.close()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Spaced repetition quiz system")
    parser.add_argument("command", choices=["select", "record", "stats"],
                        help="Subcommand")
    parser.add_argument("--project", "-p", default=None,
                        help="Project name")
    parser.add_argument("-k", type=int, default=5,
                        help="Number of entities to select (select only)")
    parser.add_argument("--topic", default=None,
                        help="Topic filter for entity selection (select only)")
    parser.add_argument("--json", dest="json_str", default=None,
                        help="JSON string for record command")
    args = parser.parse_args()

    cfg = get_config(args.project)
    print(f"  [config] {cfg}", file=sys.stderr)

    if args.command == "select":
        entities = select_entities(cfg, k=args.k, topic=args.topic)
        print(json.dumps(entities, ensure_ascii=False, indent=2))
        print(f"  [done] {len(entities)} entities selected for quiz",
              file=sys.stderr)

    elif args.command == "record":
        if args.json_str:
            data = json.loads(args.json_str)
        else:
            data = json.load(sys.stdin)
        result = record_result(cfg, data)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"  [done] Recorded result for {result['entity_name']} "
              f"(correct={result['is_correct']}, "
              f"next interval={result['next_interval_days']:.1f} days)",
              file=sys.stderr)

    elif args.command == "stats":
        stats = get_stats(cfg)
        print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
