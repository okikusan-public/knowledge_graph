#!/usr/bin/env python3
"""save_entities.py and entity conflict detection tests.

These are integration tests that require:
- Neo4j running on bolt://localhost:7687 (neo4j/changeme)
- Embedding server running on http://localhost:8082
"""

import json
import os
import subprocess
import sys
import unittest
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from neo4j import GraphDatabase
from config import get_config
from save_entities import (
    query_existing_entities,
    save_entities_to_graph,
    get_embeddings_batch,
)
from auto_ingest import cleanup_orphan_entities


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


def _cleanup_test_data(driver, source_paths):
    """Remove test documents and their chunks."""
    with driver.session() as s:
        for path in source_paths:
            s.run("""
                MATCH (d:Document {source_path: $path})
                OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c)
                DETACH DELETE c, d
            """, path=path)
        # Clean up test entities
        s.run("""
            MATCH (e:Entity)
            WHERE e.name STARTS WITH 'TEST_'
            DETACH DELETE e
        """)


class TestQueryExistingEntities(unittest.TestCase):
    """Test query_existing_entities function."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()
        cls.doc_id = str(uuid.uuid4())
        cls.source_path = "/tmp/test_query_entities.md"
        _create_test_document(cls.driver, cls.doc_id, cls.source_path,
                              ["TEST_Alpha is a company. TEST_Beta is a person."])
        # Insert test entities
        entities = [
            {"name": "TEST_Alpha", "type": "ORGANIZATION",
             "description": "A test organization with 50 employees"},
            {"name": "TEST_Beta", "type": "PERSON",
             "description": "A test person who works at TEST_Alpha"},
        ]
        save_entities_to_graph(cls.driver, cls.cfg, cls.doc_id, entities, [])

    @classmethod
    def tearDownClass(cls):
        _cleanup_test_data(cls.driver, [cls.source_path])
        cls.driver.close()

    def test_query_returns_existing_entities(self):
        """Querying existing entity names returns their info."""
        result = query_existing_entities(self.driver, ["TEST_Alpha", "TEST_Beta"])
        self.assertIn("TEST_Alpha", result)
        self.assertIn("TEST_Beta", result)
        self.assertEqual(result["TEST_Alpha"]["type"], "ORGANIZATION")
        self.assertEqual(result["TEST_Beta"]["type"], "PERSON")

    def test_query_returns_descriptions(self):
        """Returned entities include descriptions."""
        result = query_existing_entities(self.driver, ["TEST_Alpha"])
        self.assertIn("50 employees", result["TEST_Alpha"]["description"])

    def test_query_returns_sources(self):
        """Returned entities include SOURCED_FROM document paths."""
        result = query_existing_entities(self.driver, ["TEST_Alpha"])
        self.assertIn(self.source_path, result["TEST_Alpha"]["sources"])

    def test_query_nonexistent_returns_empty(self):
        """Querying non-existent entity names returns empty dict."""
        result = query_existing_entities(self.driver, ["NONEXISTENT_XYZ"])
        self.assertEqual(result, {})

    def test_query_empty_list(self):
        """Querying with empty list returns empty dict."""
        result = query_existing_entities(self.driver, [])
        self.assertEqual(result, {})


class TestEntityConflict(unittest.TestCase):
    """Test entity conflict scenarios."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()

    @classmethod
    def tearDownClass(cls):
        cls.driver.close()

    def setUp(self):
        self.doc_id_1 = str(uuid.uuid4())
        self.doc_id_2 = str(uuid.uuid4())
        self.path_1 = f"/tmp/test_conflict_1_{uuid.uuid4().hex[:8]}.md"
        self.path_2 = f"/tmp/test_conflict_2_{uuid.uuid4().hex[:8]}.md"
        _create_test_document(self.driver, self.doc_id_1, self.path_1,
                              ["TEST_Gamma Corp is a technology company."])
        _create_test_document(self.driver, self.doc_id_2, self.path_2,
                              ["TEST_Gamma Corp is a major enterprise."])

    def tearDown(self):
        _cleanup_test_data(self.driver, [self.path_1, self.path_2])

    def test_first_insert_creates_entity(self):
        """First entity insertion creates the node."""
        entities = [{"name": "TEST_Gamma Corp", "type": "ORGANIZATION",
                     "description": "A tech company with 100 employees"}]
        e_count, _ = save_entities_to_graph(
            self.driver, self.cfg, self.doc_id_1, entities, [])
        self.assertEqual(e_count, 1)

        result = query_existing_entities(self.driver, ["TEST_Gamma Corp"])
        self.assertEqual(result["TEST_Gamma Corp"]["description"],
                         "A tech company with 100 employees")

    def test_longer_description_wins_on_merge(self):
        """On MERGE, the longer description replaces the shorter one."""
        # First insert: short description
        entities_1 = [{"name": "TEST_Gamma Corp", "type": "ORGANIZATION",
                       "description": "A tech company"}]
        save_entities_to_graph(self.driver, self.cfg, self.doc_id_1, entities_1, [])

        # Second insert: longer description
        entities_2 = [{"name": "TEST_Gamma Corp", "type": "ORGANIZATION",
                       "description": "A major technology company with 500 employees and offices worldwide"}]
        save_entities_to_graph(self.driver, self.cfg, self.doc_id_2, entities_2, [])

        result = query_existing_entities(self.driver, ["TEST_Gamma Corp"])
        self.assertIn("500 employees", result["TEST_Gamma Corp"]["description"])

    def test_sourced_from_tracks_both_documents(self):
        """SOURCED_FROM relationship tracks all contributing documents."""
        entities = [{"name": "TEST_Gamma Corp", "type": "ORGANIZATION",
                     "description": "A company"}]
        save_entities_to_graph(self.driver, self.cfg, self.doc_id_1, entities, [])
        save_entities_to_graph(self.driver, self.cfg, self.doc_id_2, entities, [])

        result = query_existing_entities(self.driver, ["TEST_Gamma Corp"])
        sources = result["TEST_Gamma Corp"]["sources"]
        self.assertIn(self.path_1, sources)
        self.assertIn(self.path_2, sources)

    def test_conflicting_descriptions_detectable(self):
        """Conflicting descriptions can be detected by querying before save."""
        # Insert initial entity
        entities_1 = [{"name": "TEST_Gamma Corp", "type": "ORGANIZATION",
                       "description": "A company with 100 employees founded in 2010"}]
        save_entities_to_graph(self.driver, self.cfg, self.doc_id_1, entities_1, [])

        # Query existing before second save
        existing = query_existing_entities(self.driver, ["TEST_Gamma Corp"])
        self.assertIn("TEST_Gamma Corp", existing)

        # New conflicting data
        new_desc = "A company with 500 employees founded in 2015"

        # Conflict detection: both exist and descriptions differ
        old_desc = existing["TEST_Gamma Corp"]["description"]
        self.assertNotEqual(old_desc, new_desc)
        self.assertIn("100", old_desc)
        self.assertIn("500", new_desc)


class TestEntityRelationships(unittest.TestCase):
    """Test MENTIONS, RELATES_TO, SOURCED_FROM relationships."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()
        cls.doc_id = str(uuid.uuid4())
        cls.source_path = f"/tmp/test_rels_{uuid.uuid4().hex[:8]}.md"
        _create_test_document(cls.driver, cls.doc_id, cls.source_path,
                              ["TEST_Delta uses TEST_Epsilon for data processing."])

    @classmethod
    def tearDownClass(cls):
        _cleanup_test_data(cls.driver, [cls.source_path])
        cls.driver.close()

    def test_mentions_created(self):
        """MENTIONS relationships are created between Chunks and Entities."""
        entities = [
            {"name": "TEST_Delta", "type": "TECHNOLOGY", "description": "A tool"},
            {"name": "TEST_Epsilon", "type": "TECHNOLOGY", "description": "A platform"},
        ]
        save_entities_to_graph(self.driver, self.cfg, self.doc_id, entities, [])

        with self.driver.session() as s:
            result = s.run("""
                MATCH (c:Chunk)-[:MENTIONS]->(e:Entity)
                WHERE e.name IN ['TEST_Delta', 'TEST_Epsilon']
                RETURN collect(e.name) AS names
            """).single()
            self.assertIn("TEST_Delta", result["names"])
            self.assertIn("TEST_Epsilon", result["names"])

    def test_relates_to_created(self):
        """RELATES_TO relationships are created between Entities."""
        entities = [
            {"name": "TEST_Delta", "type": "TECHNOLOGY", "description": "A tool"},
            {"name": "TEST_Epsilon", "type": "TECHNOLOGY", "description": "A platform"},
        ]
        relationships = [
            {"source": "TEST_Delta", "target": "TEST_Epsilon",
             "type": "uses", "description": "Delta uses Epsilon"},
        ]
        save_entities_to_graph(
            self.driver, self.cfg, self.doc_id, entities, relationships)

        with self.driver.session() as s:
            result = s.run("""
                MATCH (a:Entity {name: 'TEST_Delta'})-[r:RELATES_TO]->(b:Entity {name: 'TEST_Epsilon'})
                RETURN r.type AS type, r.weight AS weight
            """).single()
            self.assertIsNotNone(result)
            self.assertEqual(result["type"], "uses")

    def test_relates_to_weight_increments(self):
        """RELATES_TO weight increments on duplicate relationship."""
        entities = [
            {"name": "TEST_Delta", "type": "TECHNOLOGY", "description": "A tool"},
            {"name": "TEST_Epsilon", "type": "TECHNOLOGY", "description": "A platform"},
        ]
        rels = [{"source": "TEST_Delta", "target": "TEST_Epsilon",
                 "type": "uses", "description": ""}]
        save_entities_to_graph(self.driver, self.cfg, self.doc_id, entities, rels)
        save_entities_to_graph(self.driver, self.cfg, self.doc_id, entities, rels)

        with self.driver.session() as s:
            result = s.run("""
                MATCH (a:Entity {name: 'TEST_Delta'})-[r:RELATES_TO]->(b:Entity {name: 'TEST_Epsilon'})
                RETURN r.weight AS weight
            """).single()
            self.assertGreater(result["weight"], 1.0)


class TestOrphanCleanup(unittest.TestCase):
    """Test orphan entity cleanup after document deletion."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()

    @classmethod
    def tearDownClass(cls):
        cls.driver.close()

    def test_orphaned_entity_removed(self):
        """Entity with no MENTIONS is removed by cleanup."""
        # Create a standalone entity (no MENTIONS)
        with self.driver.session() as s:
            s.run("""
                CREATE (e:Entity {id: $id, name: 'TEST_Orphan', type: 'CONCEPT',
                        description: 'Should be cleaned up'})
            """, id=str(uuid.uuid4()))

        deleted = cleanup_orphan_entities(self.driver)
        self.assertGreaterEqual(deleted, 1)

        with self.driver.session() as s:
            result = s.run(
                "MATCH (e:Entity {name: 'TEST_Orphan'}) RETURN count(e) AS c"
            ).single()
            self.assertEqual(result["c"], 0)

    def test_referenced_entity_not_removed(self):
        """Entity with MENTIONS relationship is NOT removed by cleanup."""
        doc_id = str(uuid.uuid4())
        path = f"/tmp/test_orphan_safe_{uuid.uuid4().hex[:8]}.md"
        _create_test_document(self.driver, doc_id, path,
                              ["TEST_Safe entity should survive cleanup."])
        entities = [{"name": "TEST_Safe", "type": "CONCEPT",
                     "description": "Should survive"}]
        save_entities_to_graph(self.driver, self.cfg, doc_id, entities, [])

        cleanup_orphan_entities(self.driver)

        with self.driver.session() as s:
            result = s.run(
                "MATCH (e:Entity {name: 'TEST_Safe'}) RETURN count(e) AS c"
            ).single()
            self.assertEqual(result["c"], 1)

        _cleanup_test_data(self.driver, [path])


class TestSaveEntitiesCLI(unittest.TestCase):
    """Test save_entities.py CLI via subprocess."""

    SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "save_entities.py")

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()
        cls.doc_id = str(uuid.uuid4())
        cls.source_path = f"/tmp/test_cli_{uuid.uuid4().hex[:8]}.md"
        _create_test_document(cls.driver, cls.doc_id, cls.source_path,
                              ["TEST_CLI_Entity is mentioned here."])

    @classmethod
    def tearDownClass(cls):
        _cleanup_test_data(cls.driver, [cls.source_path])
        cls.driver.close()

    def test_cli_stdin(self):
        """CLI accepts JSON via stdin and saves entities."""
        data = json.dumps({
            "entities": [
                {"name": "TEST_CLI_Entity", "type": "CONCEPT",
                 "description": "Created via CLI stdin"}
            ],
            "relationships": []
        })
        result = subprocess.run(
            [sys.executable, self.SCRIPT,
             "--source-path", self.source_path],
            input=data, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("1 entities", result.stderr)

    def test_cli_json_arg(self):
        """CLI accepts JSON via --json argument."""
        data = json.dumps({
            "entities": [
                {"name": "TEST_CLI_Entity", "type": "CONCEPT",
                 "description": "Created via CLI --json argument test"}
            ],
            "relationships": []
        })
        result = subprocess.run(
            [sys.executable, self.SCRIPT,
             "--source-path", self.source_path, "--json", data],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)

    def test_cli_nonexistent_doc_fails(self):
        """CLI fails when source document doesn't exist in Neo4j."""
        data = json.dumps({
            "entities": [
                {"name": "TEST_Fail", "type": "CONCEPT", "description": "x"}
            ],
            "relationships": []
        })
        result = subprocess.run(
            [sys.executable, self.SCRIPT,
             "--source-path", "/nonexistent/path.md", "--json", data],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)


class TestLoadEntities(unittest.TestCase):
    """Load test: bulk entity insertion."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()
        cls.doc_id = str(uuid.uuid4())
        cls.source_path = f"/tmp/test_load_{uuid.uuid4().hex[:8]}.md"
        # Create document with chunk text that mentions all test entities
        entity_names = [f"TEST_Load_{i}" for i in range(100)]
        chunk_text = " ".join(entity_names)
        _create_test_document(cls.driver, cls.doc_id, cls.source_path, [chunk_text])

    @classmethod
    def tearDownClass(cls):
        _cleanup_test_data(cls.driver, [cls.source_path])
        # Clean up load test entities
        with cls.driver.session() as s:
            s.run("MATCH (e:Entity) WHERE e.name STARTS WITH 'TEST_Load_' DETACH DELETE e")
        cls.driver.close()

    def test_bulk_100_entities(self):
        """100 entities can be saved in a single call."""
        entities = [
            {"name": f"TEST_Load_{i}", "type": "CONCEPT",
             "description": f"Load test entity number {i}"}
            for i in range(100)
        ]
        relationships = [
            {"source": f"TEST_Load_{i}", "target": f"TEST_Load_{i+1}",
             "type": "next", "description": ""}
            for i in range(99)
        ]
        e_count, r_count = save_entities_to_graph(
            self.driver, self.cfg, self.doc_id, entities, relationships)
        self.assertEqual(e_count, 100)
        self.assertEqual(r_count, 99)

    def test_bulk_entities_have_embeddings(self):
        """All bulk-inserted entities have embeddings."""
        # Entities should already exist from previous test
        with self.driver.session() as s:
            result = s.run("""
                MATCH (e:Entity)
                WHERE e.name STARTS WITH 'TEST_Load_'
                  AND e.embedding IS NULL
                RETURN count(e) AS missing
            """).single()
            self.assertEqual(result["missing"], 0)

    def test_duplicate_entities_deduplicated(self):
        """Duplicate entity names in a single call are deduplicated."""
        entities = [
            {"name": "TEST_Load_Dup", "type": "CONCEPT", "description": "First"},
            {"name": "TEST_Load_Dup", "type": "CONCEPT", "description": "Second"},
            {"name": "TEST_Load_Dup", "type": "CONCEPT", "description": "Third"},
        ]
        e_count, _ = save_entities_to_graph(
            self.driver, self.cfg, self.doc_id, entities, [])
        self.assertEqual(e_count, 1)


if __name__ == "__main__":
    unittest.main()
