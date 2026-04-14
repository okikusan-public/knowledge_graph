#!/usr/bin/env python3
"""lint_graph.py unit + integration tests.

Unit tests (TestClustering, TestStalePatterns): No external dependencies.
Integration tests: Require Neo4j on bolt://localhost:7687 and embedding
server on http://localhost:8082.
"""

import os
import sys
import json
import unittest
import subprocess
import uuid
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from neo4j import GraphDatabase
from config import get_config
from lint_graph import (
    _cluster_duplicates,
    cosine_similarity,
    STALE_PATTERN,
    check_duplicates,
    check_orphans,
    check_stale,
)

TEST_PREFIX = "LINT_TEST_"
SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "lint_graph.py")


def _get_driver():
    cfg = get_config()
    return GraphDatabase.driver(cfg.neo4j_uri, auth=cfg.neo4j_auth), cfg


def _get_embedding(cfg, text):
    """Get embedding from the embedding server."""
    resp = requests.post(
        cfg.embed_url,
        json={"inputs": [f"passage: {text}"]},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["embeddings"][0]


def _create_entity(session, name, entity_type, description, embedding=None,
                   status="active"):
    session.run(
        """
        CREATE (e:Entity {
            id: $id, name: $name, type: $type,
            description: $description, status: $status,
            embedding: $embedding
        })
        """,
        id=str(uuid.uuid4()), name=name, type=entity_type,
        description=description, status=status, embedding=embedding,
    )


def _create_document(session, title, source_path, created_at=None):
    params = {
        "id": str(uuid.uuid4()),
        "title": title,
        "source_path": source_path,
    }
    if created_at:
        session.run(
            """
            CREATE (d:Document {
                id: $id, title: $title, source_path: $source_path,
                created_at: datetime($created_at)
            })
            """,
            created_at=created_at, **params,
        )
    else:
        session.run(
            """
            CREATE (d:Document {
                id: $id, title: $title, source_path: $source_path,
                created_at: datetime()
            })
            """,
            **params,
        )


def _cleanup(driver):
    with driver.session() as session:
        session.run(
            "MATCH (n) WHERE n.name STARTS WITH $prefix DETACH DELETE n",
            prefix=TEST_PREFIX,
        )
        session.run(
            "MATCH (n) WHERE n.title STARTS WITH $prefix DETACH DELETE n",
            prefix=TEST_PREFIX,
        )


# =========================================================================
# Unit Tests (no Neo4j needed)
# =========================================================================

class TestClustering(unittest.TestCase):
    """Test union-find clustering for duplicate groups."""

    def test_single_pair_forms_cluster(self):
        pairs = [{"name_a": "A", "name_b": "B", "similarity": 0.96}]
        clusters = _cluster_duplicates(pairs)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(sorted(clusters[0]), ["A", "B"])

    def test_transitive_pairs_form_single_cluster(self):
        pairs = [
            {"name_a": "A", "name_b": "B", "similarity": 0.96},
            {"name_a": "B", "name_b": "C", "similarity": 0.97},
        ]
        clusters = _cluster_duplicates(pairs)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(sorted(clusters[0]), ["A", "B", "C"])

    def test_disjoint_pairs_form_separate_clusters(self):
        pairs = [
            {"name_a": "A", "name_b": "B", "similarity": 0.96},
            {"name_a": "C", "name_b": "D", "similarity": 0.97},
        ]
        clusters = _cluster_duplicates(pairs)
        self.assertEqual(len(clusters), 2)
        cluster_sets = [sorted(c) for c in clusters]
        self.assertIn(["A", "B"], cluster_sets)
        self.assertIn(["C", "D"], cluster_sets)

    def test_empty_pairs(self):
        clusters = _cluster_duplicates([])
        self.assertEqual(clusters, [])


class TestStalePatterns(unittest.TestCase):
    """Test time-dependent expression regex matching."""

    def test_matches_japanese_patterns(self):
        for text in ["最新のフレームワーク", "現在のバージョン", "2025年時点",
                     "最近の更新", "現時点での状態"]:
            with self.subTest(text=text):
                self.assertTrue(STALE_PATTERN.search(text),
                                f"Should match: {text}")

    def test_matches_english_patterns(self):
        for text in ["the latest version", "currently active",
                     "as of 2025", "recent update", "up-to-date info"]:
            with self.subTest(text=text):
                self.assertTrue(STALE_PATTERN.search(text),
                                f"Should match: {text}")

    def test_no_match_on_plain_text(self):
        for text in ["machine learning framework",
                     "ニューラルネットワークの設計",
                     "Python programming language"]:
            with self.subTest(text=text):
                self.assertIsNone(STALE_PATTERN.search(text),
                                  f"Should not match: {text}")


class TestCosineSimilarity(unittest.TestCase):

    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        self.assertAlmostEqual(cosine_similarity(v, v), 1.0, places=5)

    def test_orthogonal_vectors(self):
        self.assertAlmostEqual(
            cosine_similarity([1, 0, 0], [0, 1, 0]), 0.0, places=5)

    def test_zero_vector(self):
        self.assertEqual(cosine_similarity([0, 0], [1, 2]), 0.0)


# =========================================================================
# Integration Tests (require Neo4j + Embedding server)
# =========================================================================

class TestCheckDuplicates(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()
        _cleanup(cls.driver)

        # Create two near-duplicate entities
        emb_a = _get_embedding(cls.cfg,
                               "machine learning framework for neural networks")
        emb_b = _get_embedding(cls.cfg,
                               "machine learning framework for neural network models")
        # Create one different entity
        emb_c = _get_embedding(cls.cfg,
                               "relational database management system for SQL queries")

        with cls.driver.session() as s:
            _create_entity(s, f"{TEST_PREFIX}DupA", "TECHNOLOGY",
                           "ML framework for neural networks", emb_a)
            _create_entity(s, f"{TEST_PREFIX}DupB", "TECHNOLOGY",
                           "ML framework for neural network models - extended",
                           emb_b)
            _create_entity(s, f"{TEST_PREFIX}NoDup", "TECHNOLOGY",
                           "RDBMS for SQL", emb_c)

    @classmethod
    def tearDownClass(cls):
        _cleanup(cls.driver)
        cls.driver.close()

    def test_finds_near_duplicates(self):
        result = check_duplicates(self.driver, threshold=0.90)
        all_dup_names = set()
        for g in result["groups"]:
            for e in g["entities"]:
                all_dup_names.add(e["name"])
        self.assertIn(f"{TEST_PREFIX}DupA", all_dup_names)
        self.assertIn(f"{TEST_PREFIX}DupB", all_dup_names)

    def test_excludes_different_entities(self):
        result = check_duplicates(self.driver, threshold=0.95)
        all_dup_names = set()
        for g in result["groups"]:
            for e in g["entities"]:
                all_dup_names.add(e["name"])
        self.assertNotIn(f"{TEST_PREFIX}NoDup", all_dup_names)

    def test_high_threshold_no_matches(self):
        result = check_duplicates(self.driver, threshold=0.999)
        # Filter to only our test entities
        test_groups = [
            g for g in result["groups"]
            if any(e["name"].startswith(TEST_PREFIX) for e in g["entities"])
        ]
        self.assertEqual(len(test_groups), 0)

    def test_dry_run_no_changes(self):
        result = check_duplicates(self.driver, threshold=0.90,
                                  fix=True, dry_run=True)
        # Entities should still exist
        with self.driver.session() as s:
            count = s.run(
                "MATCH (e:Entity) WHERE e.name STARTS WITH $prefix "
                "RETURN count(e) AS c", prefix=TEST_PREFIX
            ).single()["c"]
        self.assertEqual(count, 3)


class TestCheckOrphans(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()
        _cleanup(cls.driver)

        with cls.driver.session() as s:
            # Orphan: no RELATES_TO, no BELONGS_TO
            _create_entity(s, f"{TEST_PREFIX}Orphan1", "CONCEPT",
                           "An isolated concept")
            # Connected: has RELATES_TO
            _create_entity(s, f"{TEST_PREFIX}Connected1", "CONCEPT",
                           "A connected concept")
            _create_entity(s, f"{TEST_PREFIX}Connected2", "CONCEPT",
                           "Another connected concept")
            s.run("""
                MATCH (a:Entity {name: $a}), (b:Entity {name: $b})
                CREATE (a)-[:RELATES_TO {type: 'test'}]->(b)
            """, a=f"{TEST_PREFIX}Connected1",
               b=f"{TEST_PREFIX}Connected2")

    @classmethod
    def tearDownClass(cls):
        _cleanup(cls.driver)
        cls.driver.close()

    def test_detects_orphans(self):
        result = check_orphans(self.driver)
        orphan_names = [o["name"] for o in result["orphans"]]
        self.assertIn(f"{TEST_PREFIX}Orphan1", orphan_names)

    def test_excludes_connected(self):
        result = check_orphans(self.driver)
        orphan_names = [o["name"] for o in result["orphans"]]
        self.assertNotIn(f"{TEST_PREFIX}Connected1", orphan_names)
        self.assertNotIn(f"{TEST_PREFIX}Connected2", orphan_names)

    def test_fix_archives_orphans(self):
        # Create a temporary orphan for fix test
        with self.driver.session() as s:
            _create_entity(s, f"{TEST_PREFIX}FixOrphan", "CONCEPT",
                           "Will be archived")
        result = check_orphans(self.driver, fix=True)
        # Check it was archived
        with self.driver.session() as s:
            status = s.run(
                "MATCH (e:Entity {name: $name}) RETURN e.status AS status",
                name=f"{TEST_PREFIX}FixOrphan"
            ).single()["status"]
        self.assertEqual(status, "archived")

    def test_dry_run_no_archive(self):
        # Create a temporary orphan
        with self.driver.session() as s:
            _create_entity(s, f"{TEST_PREFIX}DryOrphan", "CONCEPT",
                           "Should remain active")
        check_orphans(self.driver, fix=True, dry_run=True)
        with self.driver.session() as s:
            status = s.run(
                "MATCH (e:Entity {name: $name}) RETURN e.status AS status",
                name=f"{TEST_PREFIX}DryOrphan"
            ).single()["status"]
        self.assertEqual(status, "active")


class TestCheckStale(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()
        _cleanup(cls.driver)

        with cls.driver.session() as s:
            # Stale: old document + time-dependent description
            _create_entity(s, f"{TEST_PREFIX}StaleJA", "CONCEPT",
                           "最新のフレームワークを使用")
            _create_document(s, f"{TEST_PREFIX}OldDoc",
                             "/tmp/old_doc.md", "2024-01-01T00:00:00Z")
            s.run("""
                MATCH (e:Entity {name: $ename}),
                      (d:Document {title: $dtitle})
                CREATE (e)-[:SOURCED_FROM]->(d)
            """, ename=f"{TEST_PREFIX}StaleJA",
               dtitle=f"{TEST_PREFIX}OldDoc")

            # Not stale: recent document
            _create_entity(s, f"{TEST_PREFIX}Fresh", "CONCEPT",
                           "最新のフレームワーク")
            _create_document(s, f"{TEST_PREFIX}NewDoc",
                             "/tmp/new_doc.md")
            s.run("""
                MATCH (e:Entity {name: $ename}),
                      (d:Document {title: $dtitle})
                CREATE (e)-[:SOURCED_FROM]->(d)
            """, ename=f"{TEST_PREFIX}Fresh",
               dtitle=f"{TEST_PREFIX}NewDoc")

            # Not stale: old document but no time-dependent language
            _create_entity(s, f"{TEST_PREFIX}OldPlain", "CONCEPT",
                           "Python programming language")
            s.run("""
                MATCH (e:Entity {name: $ename}),
                      (d:Document {title: $dtitle})
                CREATE (e)-[:SOURCED_FROM]->(d)
            """, ename=f"{TEST_PREFIX}OldPlain",
               dtitle=f"{TEST_PREFIX}OldDoc")

    @classmethod
    def tearDownClass(cls):
        _cleanup(cls.driver)
        cls.driver.close()

    def test_detects_stale_japanese(self):
        result = check_stale(self.driver, stale_days=180)
        stale_names = [f["name"] for f in result["stale"]]
        self.assertIn(f"{TEST_PREFIX}StaleJA", stale_names)

    def test_excludes_recent(self):
        result = check_stale(self.driver, stale_days=180)
        stale_names = [f["name"] for f in result["stale"]]
        self.assertNotIn(f"{TEST_PREFIX}Fresh", stale_names)

    def test_excludes_non_temporal(self):
        result = check_stale(self.driver, stale_days=180)
        stale_names = [f["name"] for f in result["stale"]]
        self.assertNotIn(f"{TEST_PREFIX}OldPlain", stale_names)


class TestCLI(unittest.TestCase):
    """Test CLI execution via subprocess."""

    def test_all_json_output(self):
        result = subprocess.run(
            [sys.executable, SCRIPT, "all", "--json"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("duplicates", data)
        self.assertIn("orphans", data)
        self.assertIn("stale", data)

    def test_duplicates_json(self):
        result = subprocess.run(
            [sys.executable, SCRIPT, "duplicates", "--json"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("groups", data)
        self.assertIn("threshold", data)

    def test_orphans_json(self):
        result = subprocess.run(
            [sys.executable, SCRIPT, "orphans", "--json"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("orphans", data)

    def test_stale_json(self):
        result = subprocess.run(
            [sys.executable, SCRIPT, "stale", "--json"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("stale", data)


if __name__ == "__main__":
    unittest.main()
