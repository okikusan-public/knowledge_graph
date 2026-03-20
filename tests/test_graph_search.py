#!/usr/bin/env python3
"""graph_search.py integration tests.

Requires:
- Neo4j running on bolt://localhost:7687 (neo4j/changeme)
- Embedding server running on http://localhost:8082
- At least one document ingested with entities
"""

import json
import os
import subprocess
import sys
import time
import unittest
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from neo4j import GraphDatabase
from config import get_config
from graph_search import (
    get_query_embedding,
    vector_search_seeds,
    expand_entities,
    get_entity_chunks,
    get_entity_provenance,
    graph_search,
    format_results,
)
from save_entities import save_entities_to_graph


def _get_driver():
    cfg = get_config("default")
    return GraphDatabase.driver(cfg.neo4j_uri, auth=cfg.neo4j_auth), cfg


def _create_test_document(driver, doc_id, source_path, chunks, cfg):
    """Create a test Document + Chunk nodes with embeddings."""
    from auto_ingest import get_embeddings_batch

    embeddings = get_embeddings_batch(cfg, chunks)
    with driver.session() as s:
        s.run("""
            CREATE (d:Document {
                id: $id, title: $title, source_path: $path,
                file_type: 'md', text_length: 500, chunk_count: $count,
                auto_ingested: true, created_at: datetime()
            })
        """, id=doc_id, title=os.path.basename(source_path),
             path=source_path, count=len(chunks))
        for i, (text, emb) in enumerate(zip(chunks, embeddings)):
            chunk_id = str(uuid.uuid4())
            s.run("""
                MATCH (d:Document {id: $doc_id})
                CREATE (c:Chunk {
                    id: $cid, text: $text, chunk_index: $idx,
                    token_estimate: 50, embedding: $emb
                })
                CREATE (d)-[:HAS_CHUNK]->(c)
            """, doc_id=doc_id, cid=chunk_id, text=text, idx=i, emb=emb)


def _cleanup_test_data(driver, source_paths):
    """Remove test data."""
    with driver.session() as s:
        for path in source_paths:
            s.run("""
                MATCH (d:Document {source_path: $path})
                OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c)
                DETACH DELETE c, d
            """, path=path)
        s.run("""
            MATCH (e:Entity)
            WHERE e.name STARTS WITH 'GS_TEST_'
            DETACH DELETE e
        """)


class TestGetQueryEmbedding(unittest.TestCase):
    """Test embedding generation for queries."""

    @classmethod
    def setUpClass(cls):
        _, cls.cfg = _get_driver()

    def test_returns_vector(self):
        """Query embedding returns a list of floats."""
        vec = get_query_embedding(self.cfg, "test query")
        self.assertIsInstance(vec, list)
        self.assertEqual(len(vec), 768)

    def test_different_queries_different_vectors(self):
        """Different queries produce different embeddings."""
        vec1 = get_query_embedding(self.cfg, "artificial intelligence")
        vec2 = get_query_embedding(self.cfg, "cooking recipes")
        self.assertNotEqual(vec1[:10], vec2[:10])


class TestVectorSearchSeeds(unittest.TestCase):
    """Test vector_search_seeds function."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()

    @classmethod
    def tearDownClass(cls):
        cls.driver.close()

    def test_returns_three_lists(self):
        """Returns entities, chunks, and communities."""
        vec = get_query_embedding(self.cfg, "technology")
        with self.driver.session() as s:
            entities, chunks, communities = vector_search_seeds(s, vec, top_k=2)
        self.assertIsInstance(entities, list)
        self.assertIsInstance(chunks, list)
        self.assertIsInstance(communities, list)

    def test_entities_have_scores(self):
        """Returned entities include similarity scores."""
        vec = get_query_embedding(self.cfg, "technology")
        with self.driver.session() as s:
            entities, _, _ = vector_search_seeds(s, vec, top_k=1)
        if entities:
            self.assertIn("score", entities[0])
            self.assertGreater(entities[0]["score"], 0)

    def test_top_k_limits_results(self):
        """top_k parameter limits number of results."""
        vec = get_query_embedding(self.cfg, "technology")
        with self.driver.session() as s:
            entities, chunks, communities = vector_search_seeds(s, vec, top_k=1)
        self.assertLessEqual(len(entities), 1)
        self.assertLessEqual(len(chunks), 1)
        self.assertLessEqual(len(communities), 1)


class TestExpandEntities(unittest.TestCase):
    """Test graph expansion from seed entities."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()
        cls.doc_id = str(uuid.uuid4())
        cls.source_path = "/tmp/gs_test_expand.md"
        _create_test_document(cls.driver, cls.doc_id, cls.source_path,
                              ["GS_TEST_Hub connects to GS_TEST_Spoke1 and GS_TEST_Spoke2."], cls.cfg)
        entities = [
            {"name": "GS_TEST_Hub", "type": "CONCEPT", "description": "Central node"},
            {"name": "GS_TEST_Spoke1", "type": "TECHNOLOGY", "description": "First connection"},
            {"name": "GS_TEST_Spoke2", "type": "TECHNOLOGY", "description": "Second connection"},
        ]
        relationships = [
            {"source": "GS_TEST_Hub", "target": "GS_TEST_Spoke1",
             "type": "connects", "description": ""},
            {"source": "GS_TEST_Hub", "target": "GS_TEST_Spoke2",
             "type": "connects", "description": ""},
        ]
        save_entities_to_graph(cls.driver, cls.cfg, cls.doc_id, entities, relationships)

    @classmethod
    def tearDownClass(cls):
        _cleanup_test_data(cls.driver, [cls.source_path])
        cls.driver.close()

    def test_finds_related_entities(self):
        """Expanding from hub finds spoke entities."""
        with self.driver.session() as s:
            related, _ = expand_entities(s, ["GS_TEST_Hub"])
        related_names = [r["name"] for r in related]
        self.assertIn("GS_TEST_Spoke1", related_names)
        self.assertIn("GS_TEST_Spoke2", related_names)

    def test_related_includes_rel_type(self):
        """Related entities include the relationship type."""
        with self.driver.session() as s:
            related, _ = expand_entities(s, ["GS_TEST_Hub"])
        for r in related:
            if r["name"].startswith("GS_TEST_"):
                self.assertEqual(r["rel_type"], "connects")

    def test_empty_input_returns_empty(self):
        """Empty entity list returns empty results."""
        with self.driver.session() as s:
            related, communities = expand_entities(s, [])
        self.assertEqual(related, [])
        self.assertEqual(communities, [])

    def test_max_related_limits(self):
        """max_related parameter limits expansion."""
        with self.driver.session() as s:
            related, _ = expand_entities(s, ["GS_TEST_Hub"], max_related=1)
        self.assertLessEqual(len(related), 1)


class TestGetEntityChunks(unittest.TestCase):
    """Test chunk retrieval for entities."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()
        cls.doc_id = str(uuid.uuid4())
        cls.source_path = "/tmp/gs_test_chunks.md"
        _create_test_document(cls.driver, cls.doc_id, cls.source_path, [
            "GS_TEST_ChunkEntity is an important concept in knowledge graphs.",
            "Another chunk without the entity.",
        ], cls.cfg)
        entities = [
            {"name": "GS_TEST_ChunkEntity", "type": "CONCEPT",
             "description": "Entity for chunk test"},
        ]
        save_entities_to_graph(cls.driver, cls.cfg, cls.doc_id, entities, [])

    @classmethod
    def tearDownClass(cls):
        _cleanup_test_data(cls.driver, [cls.source_path])
        cls.driver.close()

    def test_returns_chunks_mentioning_entity(self):
        """Returns chunks that MENTIONS the given entity."""
        with self.driver.session() as s:
            chunks = get_entity_chunks(s, ["GS_TEST_ChunkEntity"])
        self.assertGreaterEqual(len(chunks), 1)
        found = any("GS_TEST_ChunkEntity" in c["text"] for c in chunks)
        self.assertTrue(found)

    def test_chunks_include_document_info(self):
        """Returned chunks include document title and source path."""
        with self.driver.session() as s:
            chunks = get_entity_chunks(s, ["GS_TEST_ChunkEntity"])
        if chunks:
            self.assertIn("document", chunks[0])
            self.assertIn("source_path", chunks[0])

    def test_empty_input_returns_empty(self):
        """Empty entity list returns empty."""
        with self.driver.session() as s:
            chunks = get_entity_chunks(s, [])
        self.assertEqual(chunks, [])


class TestGetEntityProvenance(unittest.TestCase):
    """Test SOURCED_FROM provenance retrieval."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()
        cls.doc_id = str(uuid.uuid4())
        cls.source_path = "/tmp/gs_test_prov.md"
        _create_test_document(cls.driver, cls.doc_id, cls.source_path,
                              ["GS_TEST_ProvEntity originated from this doc."], cls.cfg)
        entities = [
            {"name": "GS_TEST_ProvEntity", "type": "CONCEPT",
             "description": "Provenance test entity"},
        ]
        save_entities_to_graph(cls.driver, cls.cfg, cls.doc_id, entities, [])

    @classmethod
    def tearDownClass(cls):
        _cleanup_test_data(cls.driver, [cls.source_path])
        cls.driver.close()

    def test_returns_source_document(self):
        """Provenance returns the SOURCED_FROM document."""
        with self.driver.session() as s:
            prov = get_entity_provenance(s, ["GS_TEST_ProvEntity"])
        self.assertGreaterEqual(len(prov), 1)
        self.assertEqual(prov[0]["source_path"], self.source_path)

    def test_empty_input_returns_empty(self):
        """Empty entity list returns empty."""
        with self.driver.session() as s:
            prov = get_entity_provenance(s, [])
        self.assertEqual(prov, [])


class TestGraphSearchE2E(unittest.TestCase):
    """End-to-end graph_search function tests."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()

    @classmethod
    def tearDownClass(cls):
        cls.driver.close()

    def test_returns_all_sections(self):
        """graph_search returns all expected result sections."""
        results = graph_search(self.cfg, "technology", top_k=2)
        expected_keys = [
            "seed_entities", "seed_chunks", "seed_communities",
            "related_entities", "entity_communities",
            "context_chunks", "provenance",
        ]
        for key in expected_keys:
            self.assertIn(key, results)

    def test_format_results_produces_string(self):
        """format_results produces a non-empty string."""
        results = graph_search(self.cfg, "technology", top_k=1)
        formatted = format_results(results)
        self.assertIsInstance(formatted, str)
        self.assertGreater(len(formatted), 0)

    def test_json_output_parseable(self):
        """JSON output is valid and parseable."""
        results = graph_search(self.cfg, "technology", top_k=1)
        json_str = json.dumps(results, ensure_ascii=False, default=str)
        parsed = json.loads(json_str)
        self.assertIn("seed_entities", parsed)


class TestGraphSearchCLI(unittest.TestCase):
    """Test graph_search.py CLI."""

    SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "graph_search.py")

    def test_cli_text_output(self):
        """CLI produces text output."""
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "technology", "-k", "1"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("===", result.stdout)

    def test_cli_json_output(self):
        """CLI --json produces valid JSON."""
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "technology", "-k", "1", "--json"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("seed_entities", data)

    def test_cli_top_k_param(self):
        """CLI -k parameter limits results."""
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "technology", "-k", "1", "--json"],
            capture_output=True, text=True,
        )
        data = json.loads(result.stdout)
        self.assertLessEqual(len(data["seed_entities"]), 1)


class TestGraphSearchLoad(unittest.TestCase):
    """Load test: graph search with many entities."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()
        cls.doc_id = str(uuid.uuid4())
        cls.source_path = "/tmp/gs_test_load.md"
        names = [f"GS_TEST_Load_{i}" for i in range(50)]
        _create_test_document(cls.driver, cls.doc_id, cls.source_path,
                              [" ".join(names)], cls.cfg)
        entities = [
            {"name": n, "type": "CONCEPT", "description": f"Load entity {i}"}
            for i, n in enumerate(names)
        ]
        # Chain relationships
        relationships = [
            {"source": names[i], "target": names[i+1],
             "type": "next", "description": ""}
            for i in range(len(names) - 1)
        ]
        save_entities_to_graph(cls.driver, cls.cfg, cls.doc_id, entities, relationships)

    @classmethod
    def tearDownClass(cls):
        _cleanup_test_data(cls.driver, [cls.source_path])
        with cls.driver.session() as s:
            s.run("MATCH (e:Entity) WHERE e.name STARTS WITH 'GS_TEST_Load_' DETACH DELETE e")
        cls.driver.close()

    def test_search_completes_under_5s(self):
        """Graph search with 50 entities completes within 5 seconds."""
        start = time.time()
        results = graph_search(self.cfg, "GS_TEST_Load concept", top_k=5, max_related=20)
        elapsed = time.time() - start
        self.assertLess(elapsed, 5.0, f"Search took {elapsed:.2f}s, expected < 5s")

    def test_expansion_finds_chain(self):
        """Graph expansion through chained RELATES_TO discovers neighbors."""
        results = graph_search(self.cfg, "GS_TEST_Load_0", top_k=3, max_related=10)
        all_names = (
            [e["name"] for e in results["seed_entities"]]
            + [e["name"] for e in results["related_entities"]]
        )
        gs_names = [n for n in all_names if n.startswith("GS_TEST_Load_")]
        # Should find at least a few in the chain
        self.assertGreaterEqual(len(gs_names), 2)


if __name__ == "__main__":
    unittest.main()
