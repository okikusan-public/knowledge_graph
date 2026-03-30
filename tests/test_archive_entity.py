#!/usr/bin/env python3
"""archive_entity.py and Entity status/archive integration tests.

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
from archive_entity import archive_entity, restore_entity, list_archived
from save_entities import save_entities_to_graph
from auto_ingest import cleanup_orphan_entities


def _get_driver():
    cfg = get_config("default")
    return GraphDatabase.driver(cfg.neo4j_uri, auth=cfg.neo4j_auth), cfg


def _create_test_entity(driver, name, entity_type="CONCEPT", description="Test entity",
                        status=None, with_embedding=False, cfg=None):
    """Create a test Entity node directly in Neo4j."""
    entity_id = str(uuid.uuid4())
    props = {
        "id": entity_id,
        "name": name,
        "type": entity_type,
        "description": description,
    }
    if status:
        props["status"] = status

    with driver.session() as s:
        if with_embedding and cfg:
            import requests
            resp = requests.post(cfg.embed_url,
                                 json={"inputs": f"passage: {description[:2000]}"},
                                 timeout=30)
            resp.raise_for_status()
            emb = resp.json()["embeddings"][0]
            props["embedding"] = emb
            s.run("""
                CREATE (e:Entity {
                    id: $id, name: $name, type: $type,
                    description: $description, status: $status,
                    embedding: $embedding
                })
            """, id=props["id"], name=props["name"], type=props["type"],
                 description=props["description"],
                 status=props.get("status", "active"),
                 embedding=emb)
        else:
            if status:
                s.run("""
                    CREATE (e:Entity {
                        id: $id, name: $name, type: $type,
                        description: $description, status: $status
                    })
                """, **props)
            else:
                s.run("""
                    CREATE (e:Entity {
                        id: $id, name: $name, type: $type,
                        description: $description
                    })
                """, **props)
    return entity_id


def _create_test_document(driver, doc_id, source_path, chunks, cfg=None):
    """Create a test Document + Chunk nodes, optionally with embeddings."""
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
            if cfg:
                import requests
                resp = requests.post(cfg.embed_url,
                                     json={"inputs": f"passage: {text[:2000]}"},
                                     timeout=30)
                resp.raise_for_status()
                emb = resp.json()["embeddings"][0]
                s.run("""
                    MATCH (d:Document {id: $doc_id})
                    CREATE (c:Chunk {id: $cid, text: $text, chunk_index: $idx,
                                     token_estimate: 50, embedding: $emb})
                    CREATE (d)-[:HAS_CHUNK]->(c)
                """, doc_id=doc_id, cid=chunk_id, text=text, idx=i, emb=emb)
            else:
                s.run("""
                    MATCH (d:Document {id: $doc_id})
                    CREATE (c:Chunk {id: $cid, text: $text, chunk_index: $idx,
                                     token_estimate: 50})
                    CREATE (d)-[:HAS_CHUNK]->(c)
                """, doc_id=doc_id, cid=chunk_id, text=text, idx=i)


def _cleanup_test_entities(driver, prefix="TEST_ARCH_"):
    """Remove all test entities with the given prefix."""
    with driver.session() as s:
        s.run("""
            MATCH (e:Entity)
            WHERE e.name STARTS WITH $prefix
            DETACH DELETE e
        """, prefix=prefix)


def _cleanup_test_documents(driver, source_paths):
    """Remove test documents and their chunks."""
    with driver.session() as s:
        for path in source_paths:
            s.run("""
                MATCH (d:Document {source_path: $path})
                OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c)
                DETACH DELETE c, d
            """, path=path)


# ===========================================================================
# TestArchiveBasic - Test archive_entity.py functions directly
# ===========================================================================

class TestArchiveBasic(unittest.TestCase):
    """Test archive_entity, restore_entity, list_archived functions."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()

    @classmethod
    def tearDownClass(cls):
        _cleanup_test_entities(cls.driver)
        cls.driver.close()

    def setUp(self):
        # Clean up before each test to avoid collisions
        _cleanup_test_entities(self.driver)

    def test_archive_entity(self):
        """Archive an active entity: verify status, archived_date, archive_reason."""
        _create_test_entity(self.driver, "TEST_ARCH_Active", status="active")

        result = archive_entity(self.cfg, "TEST_ARCH_Active", reason="No longer relevant")
        self.assertEqual(result["status"], "archived")
        self.assertEqual(result["name"], "TEST_ARCH_Active")

        # Verify in database
        with self.driver.session() as s:
            record = s.run("""
                MATCH (e:Entity {name: 'TEST_ARCH_Active'})
                RETURN e.status AS status, e.archived_date AS archived_date,
                       e.archive_reason AS reason
            """).single()
            self.assertEqual(record["status"], "archived")
            self.assertIsNotNone(record["archived_date"])
            self.assertEqual(record["reason"], "No longer relevant")

    def test_archive_already_archived(self):
        """Archiving an already-archived entity returns 'already_archived'."""
        _create_test_entity(self.driver, "TEST_ARCH_AlreadyArchived", status="archived")
        # Set archived_date so it looks like a properly archived entity
        with self.driver.session() as s:
            s.run("""
                MATCH (e:Entity {name: 'TEST_ARCH_AlreadyArchived'})
                SET e.archived_date = datetime()
            """)

        result = archive_entity(self.cfg, "TEST_ARCH_AlreadyArchived")
        self.assertIn("error", result)
        self.assertEqual(result["error"], "already_archived")

    def test_archive_nonexistent(self):
        """Archiving a non-existent entity returns 'not_found'."""
        result = archive_entity(self.cfg, "TEST_ARCH_NonExistent_XYZ_999")
        self.assertIn("error", result)
        self.assertEqual(result["error"], "not_found")

    def test_restore_entity(self):
        """Restore an archived entity: verify status='active', properties cleared."""
        _create_test_entity(self.driver, "TEST_ARCH_ToRestore", status="archived")
        with self.driver.session() as s:
            s.run("""
                MATCH (e:Entity {name: 'TEST_ARCH_ToRestore'})
                SET e.archived_date = datetime(), e.archive_reason = 'temporary'
            """)

        result = restore_entity(self.cfg, "TEST_ARCH_ToRestore")
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["name"], "TEST_ARCH_ToRestore")

        # Verify in database
        with self.driver.session() as s:
            record = s.run("""
                MATCH (e:Entity {name: 'TEST_ARCH_ToRestore'})
                RETURN e.status AS status, e.archived_date AS archived_date,
                       e.archive_reason AS reason
            """).single()
            self.assertEqual(record["status"], "active")
            self.assertIsNone(record["archived_date"])
            self.assertIsNone(record["reason"])

    def test_restore_not_archived(self):
        """Restoring an active entity returns 'not_archived'."""
        _create_test_entity(self.driver, "TEST_ARCH_NotArchived", status="active")

        result = restore_entity(self.cfg, "TEST_ARCH_NotArchived")
        self.assertIn("error", result)
        self.assertEqual(result["error"], "not_archived")

    def test_list_archived(self):
        """List returns archived entities with correct fields."""
        _create_test_entity(self.driver, "TEST_ARCH_ListA", status="archived",
                            entity_type="PERSON", description="Archived person")
        _create_test_entity(self.driver, "TEST_ARCH_ListB", status="archived",
                            entity_type="CONCEPT", description="Archived concept")
        with self.driver.session() as s:
            s.run("""
                MATCH (e:Entity)
                WHERE e.name IN ['TEST_ARCH_ListA', 'TEST_ARCH_ListB']
                SET e.archived_date = datetime(), e.archive_reason = 'test reason'
            """)

        results = list_archived(self.cfg)

        # Filter to our test entities only
        test_results = [r for r in results if r["name"].startswith("TEST_ARCH_List")]
        self.assertGreaterEqual(len(test_results), 2)

        names = [r["name"] for r in test_results]
        self.assertIn("TEST_ARCH_ListA", names)
        self.assertIn("TEST_ARCH_ListB", names)

        # Check fields
        for r in test_results:
            self.assertIn("name", r)
            self.assertIn("type", r)
            self.assertIn("description", r)
            self.assertIn("archived_date", r)
            self.assertIn("reason", r)

    def test_list_empty(self):
        """List returns empty when no entities are archived (among test entities)."""
        # No archived entities created in setUp (cleaned up)
        results = list_archived(self.cfg)
        test_results = [r for r in results if r["name"].startswith("TEST_ARCH_")]
        self.assertEqual(len(test_results), 0)


# ===========================================================================
# TestArchiveSearchIntegration - Test search behavior with archived entities
# ===========================================================================

class TestArchiveSearchIntegration(unittest.TestCase):
    """Test that archived entities are excluded from search seeds but visible in traversal."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()
        cls.source_paths = []

        # Create an active entity and an archived entity, both with embeddings
        _create_test_entity(cls.driver, "TEST_ARCH_SearchActive",
                            description="Active quantum computing research entity for search test",
                            status="active", with_embedding=True, cfg=cls.cfg)
        _create_test_entity(cls.driver, "TEST_ARCH_SearchArchived",
                            description="Archived quantum computing research entity for search test",
                            status="archived", with_embedding=True, cfg=cls.cfg)

        # Create two related entities for traversal test
        _create_test_entity(cls.driver, "TEST_ARCH_TraversalHub",
                            description="Hub entity connecting to archived neighbor for traversal test",
                            status="active", with_embedding=True, cfg=cls.cfg)
        _create_test_entity(cls.driver, "TEST_ARCH_TraversalArchived",
                            description="Archived neighbor entity for traversal test",
                            status="archived", with_embedding=True, cfg=cls.cfg)

        # Create RELATES_TO between hub and archived neighbor
        with cls.driver.session() as s:
            s.run("""
                MATCH (a:Entity {name: 'TEST_ARCH_TraversalHub'}),
                      (b:Entity {name: 'TEST_ARCH_TraversalArchived'})
                CREATE (a)-[:RELATES_TO {type: 'connects', description: 'test relation', weight: 1.0}]->(b)
            """)

        # Create a document with chunks for the graph_search tests
        cls.doc_id = str(uuid.uuid4())
        cls.source_path = f"/tmp/test_arch_search_{uuid.uuid4().hex[:8]}.md"
        cls.source_paths.append(cls.source_path)
        _create_test_document(cls.driver, cls.doc_id, cls.source_path,
                              ["TEST_ARCH_TraversalHub connects to TEST_ARCH_TraversalArchived."],
                              cfg=cls.cfg)

        # Create MENTIONS relationships
        with cls.driver.session() as s:
            s.run("""
                MATCH (d:Document {id: $doc_id})-[:HAS_CHUNK]->(c:Chunk)
                MATCH (e:Entity)
                WHERE c.text CONTAINS e.name AND e.name STARTS WITH 'TEST_ARCH_Traversal'
                MERGE (c)-[:MENTIONS]->(e)
            """, doc_id=cls.doc_id)

    @classmethod
    def tearDownClass(cls):
        _cleanup_test_entities(cls.driver)
        _cleanup_test_documents(cls.driver, cls.source_paths)
        cls.driver.close()

    def test_vector_search_excludes_archived(self):
        """vector_search.search() with default params excludes archived entities."""
        from vector_search import search
        results = search(self.cfg, "quantum computing research entity for search test",
                         node_type="entity", top_k=20, include_archived=False)
        result_names = [r["node"].get("name", "") for r in results]
        # Active should be found
        test_names = [n for n in result_names if n.startswith("TEST_ARCH_Search")]
        self.assertIn("TEST_ARCH_SearchActive", test_names)
        self.assertNotIn("TEST_ARCH_SearchArchived", test_names)

    def test_vector_search_includes_with_all(self):
        """vector_search.search() with include_archived=True includes archived entities."""
        from vector_search import search
        results = search(self.cfg, "quantum computing research entity for search test",
                         node_type="entity", top_k=20, include_archived=True)
        result_names = [r["node"].get("name", "") for r in results]
        test_names = [n for n in result_names if n.startswith("TEST_ARCH_Search")]
        self.assertIn("TEST_ARCH_SearchArchived", test_names)

    def test_graph_search_excludes_archived_from_seeds(self):
        """graph_search.vector_search_seeds() excludes archived entities from seed results."""
        from graph_search import get_query_embedding, vector_search_seeds
        vec = get_query_embedding(self.cfg,
                                  "quantum computing research entity for search test")
        with self.driver.session() as s:
            entities, _, _ = vector_search_seeds(s, vec, top_k=20)
        seed_names = [e["name"] for e in entities]
        test_seeds = [n for n in seed_names if n.startswith("TEST_ARCH_Search")]
        self.assertIn("TEST_ARCH_SearchActive", test_seeds)
        self.assertNotIn("TEST_ARCH_SearchArchived", test_seeds)

    def test_graph_search_shows_archived_in_traversal(self):
        """expand_entities() includes archived entities via RELATES_TO with status='archived'."""
        from graph_search import expand_entities
        with self.driver.session() as s:
            related, _ = expand_entities(s, ["TEST_ARCH_TraversalHub"])
        related_names = [r["name"] for r in related]
        self.assertIn("TEST_ARCH_TraversalArchived", related_names)
        # Verify status is reported as 'archived'
        for r in related:
            if r["name"] == "TEST_ARCH_TraversalArchived":
                self.assertEqual(r["status"], "archived")


# ===========================================================================
# TestArchiveQuizIntegration - Test quiz behavior with archived entities
# ===========================================================================

class TestArchiveQuizIntegration(unittest.TestCase):
    """Test that archived entities are excluded from quiz selection."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()
        # Create an archived entity with a description (eligible for quiz normally)
        _create_test_entity(cls.driver, "TEST_ARCH_QuizArchived",
                            description="This is an archived entity that should not appear in quiz",
                            status="archived", with_embedding=True, cfg=cls.cfg)
        # Create an active entity for comparison
        _create_test_entity(cls.driver, "TEST_ARCH_QuizActive",
                            description="This is an active entity that should appear in quiz",
                            status="active", with_embedding=True, cfg=cls.cfg)

    @classmethod
    def tearDownClass(cls):
        _cleanup_test_entities(cls.driver)
        cls.driver.close()

    def test_quiz_excludes_archived(self):
        """select_entities() does not return archived entities."""
        from quiz import select_entities
        # Select a large number to get all eligible entities
        selected = select_entities(self.cfg, k=100)
        selected_names = [s["name"] for s in selected]
        self.assertNotIn("TEST_ARCH_QuizArchived", selected_names)


# ===========================================================================
# TestArchiveOrphanCleanup - Test orphan cleanup behavior with archived entities
# ===========================================================================

class TestArchiveOrphanCleanup(unittest.TestCase):
    """Test that orphan cleanup handles archived entities correctly."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()

    @classmethod
    def tearDownClass(cls):
        _cleanup_test_entities(cls.driver)
        cls.driver.close()

    def setUp(self):
        _cleanup_test_entities(self.driver)

    def test_archived_with_mentions_preserved(self):
        """Archived entity with MENTIONS relationship is NOT cleaned up."""
        doc_id = str(uuid.uuid4())
        source_path = f"/tmp/test_arch_orphan_safe_{uuid.uuid4().hex[:8]}.md"
        _create_test_document(self.driver, doc_id, source_path,
                              ["TEST_ARCH_MentionedArchived is referenced here."])

        # Create entity and save via save_entities_to_graph to get MENTIONS
        entities = [{"name": "TEST_ARCH_MentionedArchived", "type": "CONCEPT",
                     "description": "Archived but mentioned"}]
        save_entities_to_graph(self.driver, self.cfg, doc_id, entities, [])

        # Now archive it
        with self.driver.session() as s:
            s.run("""
                MATCH (e:Entity {name: 'TEST_ARCH_MentionedArchived'})
                SET e.status = 'archived', e.archived_date = datetime()
            """)

        # Run orphan cleanup
        cleanup_orphan_entities(self.driver)

        # Entity should still exist because it has MENTIONS
        with self.driver.session() as s:
            result = s.run(
                "MATCH (e:Entity {name: 'TEST_ARCH_MentionedArchived'}) RETURN count(e) AS c"
            ).single()
            self.assertEqual(result["c"], 1)

        _cleanup_test_documents(self.driver, [source_path])

    def test_archived_orphan_cleaned_up(self):
        """Archived entity with no relationships IS cleaned up."""
        _create_test_entity(self.driver, "TEST_ARCH_OrphanArchived", status="archived")

        cleanup_orphan_entities(self.driver)

        with self.driver.session() as s:
            result = s.run(
                "MATCH (e:Entity {name: 'TEST_ARCH_OrphanArchived'}) RETURN count(e) AS c"
            ).single()
            self.assertEqual(result["c"], 0)


if __name__ == "__main__":
    unittest.main()
