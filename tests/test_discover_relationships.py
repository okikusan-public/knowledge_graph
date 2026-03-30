#!/usr/bin/env python3
"""discover_relationships.py integration tests.

These are integration tests that require:
- Neo4j running on bolt://localhost:7689 (neo4j/changeme)
- Embedding server running on http://localhost:8082
"""

import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from neo4j import GraphDatabase
from config import get_config
from save_entities import save_entities_to_graph
from discover_relationships import (
    find_candidates,
    deduplicate_candidates,
    apply_max_per_entity,
    create_relationships,
    discover_relationships,
    get_all_entity_names,
)


def _get_driver():
    cfg = get_config("default")
    return GraphDatabase.driver(cfg.neo4j_uri, auth=cfg.neo4j_auth), cfg


def _create_test_document(driver, doc_id, source_path, chunks):
    """Create a test Document + Chunk nodes."""
    with driver.session() as s:
        s.run("""
            CREATE (d:Document {
                id: $id, title: $title, source_path: $path,
                file_type: 'md', text_length: 100, chunk_count: $count,
                auto_ingested: true, created_at: datetime()
            })
        """, id=doc_id, title=os.path.basename(source_path),
             path=source_path, count=len(chunks))
        for i, text in enumerate(chunks):
            chunk_id = str(uuid.uuid4())
            s.run("""
                MATCH (d:Document {id: $doc_id})
                CREATE (c:Chunk {id: $cid, text: $text, chunk_index: $idx, token_estimate: 50})
                CREATE (d)-[:HAS_CHUNK]->(c)
            """, doc_id=doc_id, cid=chunk_id, text=text, idx=i)


def _cleanup_test_data(driver):
    """Remove all TEST_DISC_ entities and test documents."""
    with driver.session() as s:
        s.run("""
            MATCH (e:Entity)
            WHERE e.name STARTS WITH 'TEST_DISC_'
            DETACH DELETE e
        """)
        s.run("""
            MATCH (d:Document)
            WHERE d.source_path STARTS WITH '/tmp/test_disc_'
            OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c)
            DETACH DELETE c, d
        """)


class TestDiscoverRelationships(unittest.TestCase):
    """Integration tests for cross-document relationship discovery."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()
        _cleanup_test_data(cls.driver)

        # Create two documents with entities
        cls.doc_id_1 = str(uuid.uuid4())
        cls.doc_id_2 = str(uuid.uuid4())
        cls.path_1 = f"/tmp/test_disc_doc1_{uuid.uuid4().hex[:8]}.md"
        cls.path_2 = f"/tmp/test_disc_doc2_{uuid.uuid4().hex[:8]}.md"

        _create_test_document(cls.driver, cls.doc_id_1, cls.path_1,
                              ["TEST_DISC_Alpha is a machine learning framework. "
                               "TEST_DISC_Beta is a deep learning library."])
        _create_test_document(cls.driver, cls.doc_id_2, cls.path_2,
                              ["TEST_DISC_Gamma is a neural network toolkit. "
                               "TEST_DISC_Delta is a database system."])

        # Save entities to doc1 - similar topics (ML/DL)
        entities_1 = [
            {"name": "TEST_DISC_Alpha", "type": "TECHNOLOGY",
             "description": "A machine learning framework for training neural networks"},
            {"name": "TEST_DISC_Beta", "type": "TECHNOLOGY",
             "description": "A deep learning library for building neural network models"},
        ]
        save_entities_to_graph(cls.driver, cls.cfg, cls.doc_id_1, entities_1, [])

        # Save entities to doc2 - Gamma similar to doc1, Delta is different
        entities_2 = [
            {"name": "TEST_DISC_Gamma", "type": "TECHNOLOGY",
             "description": "A neural network toolkit for training deep learning models"},
            {"name": "TEST_DISC_Delta", "type": "TECHNOLOGY",
             "description": "A relational database management system for SQL queries"},
        ]
        save_entities_to_graph(cls.driver, cls.cfg, cls.doc_id_2, entities_2, [])

    @classmethod
    def tearDownClass(cls):
        _cleanup_test_data(cls.driver)
        cls.driver.close()

    def _clear_auto_discovered_rels(self):
        """Remove auto_discovered relationships between test entities."""
        with self.driver.session() as s:
            s.run("""
                MATCH (a:Entity)-[r:RELATES_TO]-(b:Entity)
                WHERE a.name STARTS WITH 'TEST_DISC_'
                  AND b.name STARTS WITH 'TEST_DISC_'
                  AND r.type = 'auto_discovered'
                DELETE r
            """)

    def test_finds_similar_entities_across_documents(self):
        """Entities from different documents with high similarity are found."""
        self._clear_auto_discovered_rels()
        names = ["TEST_DISC_Alpha", "TEST_DISC_Beta"]
        candidates = find_candidates(self.driver, names, threshold=0.50,
                                     max_per_entity=10)
        # Alpha/Beta (ML/DL) should find Gamma (neural nets) as similar
        target_names = [c["target_name"] for c in candidates]
        self.assertIn("TEST_DISC_Gamma", target_names,
                      "Gamma (neural network toolkit) should be similar to Alpha/Beta (ML/DL)")

    def test_skips_same_document_entities(self):
        """Entities from the same document are not suggested as candidates."""
        self._clear_auto_discovered_rels()
        names = ["TEST_DISC_Alpha"]
        candidates = find_candidates(self.driver, names, threshold=0.50,
                                     max_per_entity=10)
        # Beta is from the same doc as Alpha, should not appear
        target_names = [c["target_name"] for c in candidates]
        self.assertNotIn("TEST_DISC_Beta", target_names,
                         "Beta is from same document as Alpha, should be excluded")

    def test_skips_existing_relationships(self):
        """Already related entities are not suggested again."""
        self._clear_auto_discovered_rels()
        # Create an explicit relationship Alpha -> Gamma
        with self.driver.session() as s:
            s.run("""
                MATCH (a:Entity {name: 'TEST_DISC_Alpha'}),
                      (b:Entity {name: 'TEST_DISC_Gamma'})
                MERGE (a)-[r:RELATES_TO]->(b)
                ON CREATE SET r.type = 'manual', r.weight = 1.0
            """)
        try:
            names = ["TEST_DISC_Alpha"]
            candidates = find_candidates(self.driver, names, threshold=0.50,
                                         max_per_entity=10)
            target_names = [c["target_name"] for c in candidates]
            self.assertNotIn("TEST_DISC_Gamma", target_names,
                             "Already related entities should be excluded")
        finally:
            with self.driver.session() as s:
                s.run("""
                    MATCH (a:Entity {name: 'TEST_DISC_Alpha'})-[r:RELATES_TO]->(b:Entity {name: 'TEST_DISC_Gamma'})
                    WHERE r.type = 'manual'
                    DELETE r
                """)

    def test_respects_threshold(self):
        """Very high threshold excludes lower-similarity pairs."""
        self._clear_auto_discovered_rels()
        names = ["TEST_DISC_Alpha", "TEST_DISC_Beta",
                 "TEST_DISC_Gamma", "TEST_DISC_Delta"]
        # With a very high threshold, should find fewer (or no) candidates
        candidates_high = find_candidates(self.driver, names, threshold=0.99,
                                          max_per_entity=10)
        candidates_low = find_candidates(self.driver, names, threshold=0.50,
                                         max_per_entity=10)
        self.assertGreaterEqual(len(candidates_low), len(candidates_high),
                                "Lower threshold should yield equal or more candidates")

    def test_skips_archived_entities(self):
        """Archived entities are excluded from discovery."""
        self._clear_auto_discovered_rels()
        # Create an archived entity
        with self.driver.session() as s:
            s.run("""
                MERGE (e:Entity {name: 'TEST_DISC_Archived'})
                ON CREATE SET e.id = $id, e.type = 'TECHNOLOGY',
                    e.description = 'An archived machine learning framework',
                    e.status = 'archived'
            """, id=str(uuid.uuid4()))
            # Give it an embedding
            from save_entities import get_embeddings_batch
            embs = get_embeddings_batch(self.cfg,
                                        ["TEST_DISC_Archived. An archived machine learning framework"])
            s.run("""
                MATCH (e:Entity {name: 'TEST_DISC_Archived'})
                SET e.embedding = $emb
            """, emb=embs[0])

        try:
            names = ["TEST_DISC_Alpha"]
            candidates = find_candidates(self.driver, names, threshold=0.50,
                                         max_per_entity=10)
            target_names = [c["target_name"] for c in candidates]
            self.assertNotIn("TEST_DISC_Archived", target_names,
                             "Archived entities should be excluded")
        finally:
            with self.driver.session() as s:
                s.run("MATCH (e:Entity {name: 'TEST_DISC_Archived'}) DETACH DELETE e")

    def test_no_duplicates_on_rerun(self):
        """Running discovery twice does not create duplicate relationships."""
        self._clear_auto_discovered_rels()
        names = ["TEST_DISC_Alpha", "TEST_DISC_Beta",
                 "TEST_DISC_Gamma", "TEST_DISC_Delta"]

        # First run
        candidates1, created1 = discover_relationships(
            self.driver, names, threshold=0.50, max_per_entity=10)

        # Second run - should find no new candidates (existing rels are excluded)
        candidates2 = find_candidates(self.driver, names, threshold=0.50,
                                      max_per_entity=10)
        candidates2 = deduplicate_candidates(candidates2)

        # All pairs from first run should now be excluded
        first_pairs = {(c["source_name"], c["target_name"]) for c in candidates1}
        second_pairs = {(c["source_name"], c["target_name"]) for c in candidates2}
        overlap = first_pairs & second_pairs
        self.assertEqual(len(overlap), 0,
                         "Re-run should not find pairs that already have relationships")

        self._clear_auto_discovered_rels()

    def test_dry_run_no_creation(self):
        """Dry run shows candidates but does not create relationships."""
        self._clear_auto_discovered_rels()
        names = ["TEST_DISC_Alpha", "TEST_DISC_Beta",
                 "TEST_DISC_Gamma", "TEST_DISC_Delta"]

        candidates, created = discover_relationships(
            self.driver, names, threshold=0.50, max_per_entity=10,
            dry_run=True)

        self.assertEqual(created, 0, "Dry run should not create relationships")

        # Verify nothing was created
        with self.driver.session() as s:
            result = s.run("""
                MATCH (a:Entity)-[r:RELATES_TO {type: 'auto_discovered'}]-(b:Entity)
                WHERE a.name STARTS WITH 'TEST_DISC_'
                RETURN count(r) AS cnt
            """).single()
            self.assertEqual(result["cnt"], 0,
                             "No auto_discovered relationships should exist after dry run")

    def test_all_mode(self):
        """--all mode processes all active entities with embeddings."""
        self._clear_auto_discovered_rels()
        all_names = get_all_entity_names(self.driver)
        # Our test entities should be included
        test_names = [n for n in all_names if n.startswith("TEST_DISC_")]
        self.assertGreaterEqual(len(test_names), 4,
                                "All 4 test entities should be found in --all mode")

    def test_source_entities_mode(self):
        """--source-entities mode only processes specified entities."""
        self._clear_auto_discovered_rels()
        names = ["TEST_DISC_Alpha"]
        candidates = find_candidates(self.driver, names, threshold=0.50,
                                     max_per_entity=10)
        # Only Alpha should appear as source
        source_names = {c["source_name"] for c in candidates}
        self.assertEqual(source_names, {"TEST_DISC_Alpha"},
                         "Only specified entity should be source")


class TestDeduplication(unittest.TestCase):
    """Test candidate deduplication logic."""

    def test_dedup_reverses_pair_alphabetically(self):
        """Pairs are ordered alphabetically: B->A becomes A->B."""
        candidates = [
            {"source_name": "Zebra", "source_desc": "Z desc",
             "target_name": "Alpha", "target_desc": "A desc", "score": 0.9},
        ]
        result = deduplicate_candidates(candidates)
        self.assertEqual(result[0]["source_name"], "Alpha")
        self.assertEqual(result[0]["target_name"], "Zebra")

    def test_dedup_removes_reverse_duplicates(self):
        """A->B and B->A are deduplicated to one entry."""
        candidates = [
            {"source_name": "Alpha", "source_desc": "", "target_name": "Beta",
             "target_desc": "", "score": 0.9},
            {"source_name": "Beta", "source_desc": "", "target_name": "Alpha",
             "target_desc": "", "score": 0.88},
        ]
        result = deduplicate_candidates(candidates)
        self.assertEqual(len(result), 1)
        # Should keep the first occurrence (higher score)
        self.assertEqual(result[0]["score"], 0.9)

    def test_dedup_preserves_unique_pairs(self):
        """Different pairs are preserved."""
        candidates = [
            {"source_name": "A", "source_desc": "", "target_name": "B",
             "target_desc": "", "score": 0.9},
            {"source_name": "C", "source_desc": "", "target_name": "D",
             "target_desc": "", "score": 0.85},
        ]
        result = deduplicate_candidates(candidates)
        self.assertEqual(len(result), 2)


class TestApplyMaxPerEntity(unittest.TestCase):
    """Test max_per_entity filtering."""

    def test_limits_candidates_per_entity(self):
        """Each entity appears at most max_per_entity times."""
        candidates = [
            {"source_name": "A", "target_name": f"T{i}", "score": 0.9 - i * 0.01}
            for i in range(10)
        ]
        result = apply_max_per_entity(candidates, max_per_entity=3)
        a_count = sum(1 for c in result
                      if c["source_name"] == "A" or c["target_name"] == "A")
        self.assertLessEqual(a_count, 3)


if __name__ == "__main__":
    unittest.main()
