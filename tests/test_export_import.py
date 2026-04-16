#!/usr/bin/env python3
"""export_knowledge.py and import_knowledge.py tests.

These are integration tests that require:
- Neo4j running on bolt://localhost:7689 (neo4j/changeme)
- Embedding server running on http://localhost:8082
"""

import json
import os
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from neo4j import GraphDatabase
from neo4j.time import DateTime as Neo4jDateTime
from config import get_config
from export_knowledge import (
    export_graph,
    resolve_community,
    get_community_scope,
    serialize_value,
    serialize_record,
    FORMAT_VERSION,
)
from import_knowledge import (
    import_graph,
    validate_export,
)


def _get_driver():
    cfg = get_config("default")
    return GraphDatabase.driver(cfg.neo4j_uri, auth=cfg.neo4j_auth), cfg


def _create_test_graph(driver, prefix):
    """Create a small test graph with all node and relationship types.

    Returns dict of created IDs for verification.
    """
    doc_id = str(uuid.uuid4())
    chunk1_id = str(uuid.uuid4())
    chunk2_id = str(uuid.uuid4())
    ent1_name = f"{prefix}_EntityA"
    ent2_name = f"{prefix}_EntityB"

    with driver.session() as s:
        # Document
        s.run("""
            CREATE (d:Document {
                id: $id, title: $title, source_path: $path,
                file_type: 'md', text_length: 200, chunk_count: 2,
                auto_ingested: true, created_at: datetime()
            })
        """, id=doc_id, title=f"{prefix}_doc.md",
             path=f"/tmp/{prefix}_doc.md")

        # Chunks
        s.run("""
            MATCH (d:Document {id: $doc_id})
            CREATE (c1:Chunk {id: $c1_id, text: $text1, chunk_index: 0, token_estimate: 50})
            CREATE (c2:Chunk {id: $c2_id, text: $text2, chunk_index: 1, token_estimate: 50})
            CREATE (d)-[:HAS_CHUNK]->(c1)
            CREATE (d)-[:HAS_CHUNK]->(c2)
            CREATE (c1)-[:NEXT_CHUNK]->(c2)
        """, doc_id=doc_id, c1_id=chunk1_id, c2_id=chunk2_id,
             text1=f"{ent1_name} is a test entity for export/import.",
             text2=f"{ent2_name} relates to {ent1_name}.")

        # Entities
        ent1_id = str(uuid.uuid4())
        ent2_id = str(uuid.uuid4())
        s.run("""
            CREATE (e:Entity {
                id: $id, name: $name, type: 'CONCEPT',
                description: 'Test entity A for export/import',
                status: 'active'
            })
        """, id=ent1_id, name=ent1_name)
        s.run("""
            CREATE (e:Entity {
                id: $id, name: $name, type: 'CONCEPT',
                description: 'Test entity B for export/import',
                status: 'active'
            })
        """, id=ent2_id, name=ent2_name)

        # MENTIONS
        s.run("""
            MATCH (c:Chunk {id: $c_id}), (e:Entity {name: $name})
            CREATE (c)-[:MENTIONS]->(e)
        """, c_id=chunk1_id, name=ent1_name)
        s.run("""
            MATCH (c:Chunk {id: $c_id}), (e:Entity {name: $name})
            CREATE (c)-[:MENTIONS]->(e)
        """, c_id=chunk2_id, name=ent2_name)

        # SOURCED_FROM
        s.run("""
            MATCH (e:Entity {name: $name}), (d:Document {id: $doc_id})
            CREATE (e)-[:SOURCED_FROM {created_at: datetime()}]->(d)
        """, name=ent1_name, doc_id=doc_id)
        s.run("""
            MATCH (e:Entity {name: $name}), (d:Document {id: $doc_id})
            CREATE (e)-[:SOURCED_FROM {created_at: datetime()}]->(d)
        """, name=ent2_name, doc_id=doc_id)

        # RELATES_TO
        s.run("""
            MATCH (a:Entity {name: $src}), (b:Entity {name: $tgt})
            CREATE (a)-[:RELATES_TO {type: 'related', description: 'test relation', weight: 1.0}]->(b)
        """, src=ent1_name, tgt=ent2_name)

    return {
        "doc_id": doc_id, "chunk1_id": chunk1_id, "chunk2_id": chunk2_id,
        "ent1_id": ent1_id, "ent2_id": ent2_id,
        "ent1_name": ent1_name, "ent2_name": ent2_name,
        "source_path": f"/tmp/{prefix}_doc.md",
    }


def _cleanup_test_graph(driver, prefix):
    """Remove all test data by prefix."""
    with driver.session() as s:
        # Delete entities and their relationships
        s.run("""
            MATCH (e:Entity)
            WHERE e.name STARTS WITH $prefix
            DETACH DELETE e
        """, prefix=f"{prefix}_")
        # Delete documents, chunks
        s.run("""
            MATCH (d:Document)
            WHERE d.source_path STARTS WITH $prefix
            OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c)
            DETACH DELETE c, d
        """, prefix=f"/tmp/{prefix}_")


class TestSerializeValue(unittest.TestCase):
    """Test value serialization for export."""

    def test_none(self):
        self.assertIsNone(serialize_value(None))

    def test_string(self):
        self.assertEqual(serialize_value("hello"), "hello")

    def test_int(self):
        self.assertEqual(serialize_value(42), 42)

    def test_list(self):
        self.assertEqual(serialize_value([1, 2, 3]), [1, 2, 3])

    def test_neo4j_datetime(self):
        dt = Neo4jDateTime(2026, 4, 17, 10, 30, 0)
        result = serialize_value(dt)
        self.assertIsInstance(result, str)
        self.assertIn("2026", result)

    def test_serialize_record(self):
        record = {"name": "test", "count": 5, "val": None}
        result = serialize_record(record)
        self.assertEqual(result["name"], "test")
        self.assertEqual(result["count"], 5)
        self.assertIsNone(result["val"])


class TestValidateExport(unittest.TestCase):
    """Test export file validation."""

    def test_valid_export(self):
        data = {"version": "1.0", "nodes": {}, "relationships": {}}
        validate_export(data)  # Should not raise

    def test_missing_version(self):
        with self.assertRaises(SystemExit):
            validate_export({"nodes": {}, "relationships": {}})

    def test_unsupported_version(self):
        with self.assertRaises(SystemExit):
            validate_export({"version": "99.0", "nodes": {}, "relationships": {}})

    def test_missing_nodes(self):
        with self.assertRaises(SystemExit):
            validate_export({"version": "1.0", "relationships": {}})


class TestExportGraph(unittest.TestCase):
    """Test full graph export with real Neo4j."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()
        cls.prefix = "EI_TEST_EXPORT"
        cls.ids = _create_test_graph(cls.driver, cls.prefix)

    @classmethod
    def tearDownClass(cls):
        _cleanup_test_graph(cls.driver, cls.prefix)
        cls.driver.close()

    def test_export_includes_all_node_types(self):
        data = export_graph(self.driver, self.cfg, {}, True)
        self.assertEqual(data["version"], FORMAT_VERSION)
        self.assertIn("Document", data["nodes"])
        self.assertIn("Chunk", data["nodes"])
        self.assertIn("Entity", data["nodes"])

    def test_export_includes_test_data(self):
        data = export_graph(self.driver, self.cfg, {}, True)
        doc_ids = [d["id"] for d in data["nodes"]["Document"]]
        self.assertIn(self.ids["doc_id"], doc_ids)

        entity_names = [e["name"] for e in data["nodes"]["Entity"]]
        self.assertIn(self.ids["ent1_name"], entity_names)
        self.assertIn(self.ids["ent2_name"], entity_names)

    def test_export_includes_relationships(self):
        data = export_graph(self.driver, self.cfg, {}, True)
        self.assertIn("HAS_CHUNK", data["relationships"])
        self.assertIn("NEXT_CHUNK", data["relationships"])
        self.assertIn("MENTIONS", data["relationships"])
        self.assertIn("SOURCED_FROM", data["relationships"])
        self.assertIn("RELATES_TO", data["relationships"])

    def test_export_no_embeddings(self):
        data = export_graph(self.driver, self.cfg, {}, False)
        for doc in data["nodes"]["Document"]:
            self.assertIsNone(doc["embedding"])
        for chunk in data["nodes"]["Chunk"]:
            self.assertIsNone(chunk["embedding"])
        for ent in data["nodes"]["Entity"]:
            self.assertIsNone(ent["embedding"])

    def test_export_metadata(self):
        data = export_graph(self.driver, self.cfg, {}, True)
        meta = data["metadata"]
        self.assertIn("export_date", meta)
        self.assertIn("stats", meta)
        self.assertIn("nodes", meta["stats"])
        self.assertIn("relationships", meta["stats"])
        self.assertEqual(meta["include_embeddings"], True)

    def test_export_source_path_filter(self):
        filters = {"source_path": self.ids["source_path"]}
        data = export_graph(self.driver, self.cfg, filters, False)
        # Should only include our test document
        self.assertEqual(len(data["nodes"]["Document"]), 1)
        self.assertEqual(data["nodes"]["Document"][0]["id"], self.ids["doc_id"])


class TestRoundTrip(unittest.TestCase):
    """Test export → import round trip using minimal hand-built data."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()
        cls.prefix = "EI_TEST_RT"

    @classmethod
    def tearDownClass(cls):
        _cleanup_test_graph(cls.driver, cls.prefix)
        cls.driver.close()

    def _build_minimal_export(self, ids):
        """Build a minimal export dict from known test IDs (no communities/quiz)."""
        return {
            "version": "1.0",
            "metadata": {"export_date": "2026-04-17T00:00:00Z", "project": "default",
                          "include_embeddings": False},
            "nodes": {
                "Document": [{
                    "id": ids["doc_id"], "title": f"{self.prefix}_doc.md",
                    "source_path": ids["source_path"], "file_type": "md",
                    "text_length": 200, "chunk_count": 2, "auto_ingested": True,
                    "created_at": "2026-04-17T00:00:00Z", "embedding": None,
                }],
                "Chunk": [
                    {"id": ids["chunk1_id"],
                     "text": f"{ids['ent1_name']} is a test entity for export/import.",
                     "chunk_index": 0, "token_estimate": 50, "embedding": None},
                    {"id": ids["chunk2_id"],
                     "text": f"{ids['ent2_name']} relates to {ids['ent1_name']}.",
                     "chunk_index": 1, "token_estimate": 50, "embedding": None},
                ],
                "Entity": [
                    {"id": ids["ent1_id"], "name": ids["ent1_name"], "type": "CONCEPT",
                     "description": "Test entity A for export/import", "status": "active",
                     "embedding": None},
                    {"id": ids["ent2_id"], "name": ids["ent2_name"], "type": "CONCEPT",
                     "description": "Test entity B for export/import", "status": "active",
                     "embedding": None},
                ],
            },
            "relationships": {
                "HAS_CHUNK": [
                    {"_start_id": ids["doc_id"], "_end_id": ids["chunk1_id"], "properties": {}},
                    {"_start_id": ids["doc_id"], "_end_id": ids["chunk2_id"], "properties": {}},
                ],
                "NEXT_CHUNK": [
                    {"_start_id": ids["chunk1_id"], "_end_id": ids["chunk2_id"], "properties": {}},
                ],
                "MENTIONS": [
                    {"_start_id": ids["chunk1_id"], "_end_id": ids["ent1_id"], "properties": {}},
                    {"_start_id": ids["chunk2_id"], "_end_id": ids["ent2_id"], "properties": {}},
                ],
                "SOURCED_FROM": [
                    {"_start_id": ids["ent1_id"], "_end_id": ids["doc_id"],
                     "_end_label": "Document", "properties": {"created_at": "2026-04-17T00:00:00Z"}},
                    {"_start_id": ids["ent2_id"], "_end_id": ids["doc_id"],
                     "_end_label": "Document", "properties": {"created_at": "2026-04-17T00:00:00Z"}},
                ],
                "RELATES_TO": [
                    {"_start_id": ids["ent1_id"], "_end_id": ids["ent2_id"],
                     "properties": {"type": "related", "description": "test relation", "weight": 1.0}},
                ],
            },
        }

    def test_round_trip(self):
        """Import, verify, delete, re-import, verify all data is restored."""
        ids = _create_test_graph(self.driver, self.prefix)
        export_data = self._build_minimal_export(ids)

        # Step 1: Delete test data
        _cleanup_test_graph(self.driver, self.prefix)

        # Verify deletion
        with self.driver.session() as s:
            result = s.run(
                "MATCH (d:Document {id: $id}) RETURN count(d) AS cnt",
                id=ids["doc_id"]).single()
            self.assertEqual(result["cnt"], 0)

        # Step 2: Import
        result = import_graph(self.driver, self.cfg, export_data, False, False)
        self.assertEqual(result["nodes"]["Document"], 1)
        self.assertEqual(result["nodes"]["Entity"], 2)

        # Step 3: Verify restoration
        with self.driver.session() as s:
            # Document exists
            doc = s.run(
                "MATCH (d:Document {id: $id}) RETURN d.title AS title",
                id=ids["doc_id"]).single()
            self.assertIsNotNone(doc)

            # Chunks exist with HAS_CHUNK
            chunks = s.run(
                "MATCH (d:Document {id: $id})-[:HAS_CHUNK]->(c) RETURN count(c) AS cnt",
                id=ids["doc_id"]).single()
            self.assertEqual(chunks["cnt"], 2)

            # Entities exist
            ent = s.run(
                "MATCH (e:Entity {name: $name}) RETURN e.type AS type",
                name=ids["ent1_name"]).single()
            self.assertIsNotNone(ent)
            self.assertEqual(ent["type"], "CONCEPT")

            # RELATES_TO exists
            rel = s.run("""
                MATCH (a:Entity {name: $src})-[r:RELATES_TO]->(b:Entity {name: $tgt})
                RETURN r.type AS type, r.weight AS weight
            """, src=ids["ent1_name"], tgt=ids["ent2_name"]).single()
            self.assertIsNotNone(rel)
            self.assertEqual(rel["type"], "related")

            # NEXT_CHUNK exists
            nc = s.run("""
                MATCH (c1:Chunk {id: $c1})-[:NEXT_CHUNK]->(c2:Chunk {id: $c2})
                RETURN count(*) AS cnt
            """, c1=ids["chunk1_id"], c2=ids["chunk2_id"]).single()
            self.assertEqual(nc["cnt"], 1)

            # SOURCED_FROM exists
            sf = s.run("""
                MATCH (e:Entity {name: $name})-[:SOURCED_FROM]->(d:Document {id: $doc_id})
                RETURN count(*) AS cnt
            """, name=ids["ent1_name"], doc_id=ids["doc_id"]).single()
            self.assertEqual(sf["cnt"], 1)


class TestImportDryRun(unittest.TestCase):
    """Test dry-run mode creates nothing."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()
        cls.prefix = "EI_TEST_DRY"

    @classmethod
    def tearDownClass(cls):
        _cleanup_test_graph(cls.driver, cls.prefix)
        cls.driver.close()

    def test_dry_run_no_changes(self):
        doc_id = str(uuid.uuid4())
        data = {
            "version": "1.0",
            "metadata": {},
            "nodes": {
                "Document": [{
                    "id": doc_id, "title": f"{self.prefix}_dry.md",
                    "source_path": f"/tmp/{self.prefix}_dry.md",
                    "file_type": "md", "text_length": 100,
                    "chunk_count": 0, "auto_ingested": True,
                    "created_at": "2026-04-17T00:00:00Z", "embedding": None,
                }],
                "Entity": [{
                    "id": str(uuid.uuid4()), "name": f"{self.prefix}_DryEntity",
                    "type": "CONCEPT", "description": "Should not be created",
                    "status": "active", "embedding": None,
                }],
            },
            "relationships": {},
        }

        result = import_graph(self.driver, self.cfg, data, True, False)
        self.assertEqual(result["nodes"]["Document"], 1)
        self.assertEqual(result["nodes"]["Entity"], 1)

        # Verify nothing was created
        with self.driver.session() as s:
            doc = s.run("MATCH (d:Document {id: $id}) RETURN d", id=doc_id).single()
            self.assertIsNone(doc)
            ent = s.run(
                "MATCH (e:Entity {name: $name}) RETURN e",
                name=f"{self.prefix}_DryEntity").single()
            self.assertIsNone(ent)


class TestImportMerge(unittest.TestCase):
    """Test MERGE behavior when importing into non-empty graph."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()
        cls.prefix = "EI_TEST_MERGE"

    @classmethod
    def tearDownClass(cls):
        _cleanup_test_graph(cls.driver, cls.prefix)
        cls.driver.close()

    def test_entity_merge_keeps_longer_description(self):
        ent_name = f"{self.prefix}_MergeEntity"

        # Create entity with short description
        with self.driver.session() as s:
            s.run("""
                CREATE (e:Entity {
                    id: $id, name: $name, type: 'CONCEPT',
                    description: 'short', status: 'active'
                })
            """, id=str(uuid.uuid4()), name=ent_name)

        # Import with longer description
        data = {
            "version": "1.0",
            "metadata": {},
            "nodes": {
                "Entity": [{
                    "id": str(uuid.uuid4()), "name": ent_name,
                    "type": "TECHNOLOGY", "description": "A much longer description for testing",
                    "status": "active", "embedding": None,
                }],
            },
            "relationships": {},
        }
        import_graph(self.driver, self.cfg, data, False, False)

        # Verify longer description wins, original type preserved
        with self.driver.session() as s:
            ent = s.run(
                "MATCH (e:Entity {name: $name}) RETURN e.description AS desc, e.type AS type",
                name=ent_name).single()
            self.assertEqual(ent["desc"], "A much longer description for testing")
            self.assertEqual(ent["type"], "CONCEPT")  # Original type preserved


class TestExportImportJSON(unittest.TestCase):
    """Test JSON file I/O with a minimal export dict."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()

    @classmethod
    def tearDownClass(cls):
        cls.driver.close()

    def test_export_to_file_and_reimport(self):
        """Write export JSON to temp file, read it back, verify structure."""
        export_data = {
            "version": FORMAT_VERSION,
            "metadata": {"export_date": "2026-04-17T00:00:00Z", "project": "default",
                          "include_embeddings": False},
            "nodes": {
                "Document": [{"id": "test-doc-1", "title": "test.md"}],
                "Entity": [{"id": "test-ent-1", "name": "EI_JSON_TestEntity",
                             "type": "CONCEPT", "description": "For JSON test"}],
            },
            "relationships": {},
        }

        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2, default=str)
            tmp_path = f.name

        try:
            with open(tmp_path, "r", encoding="utf-8") as f:
                reimported = json.load(f)

            self.assertEqual(reimported["version"], FORMAT_VERSION)
            self.assertIn("nodes", reimported)
            self.assertIn("relationships", reimported)
            self.assertIn("metadata", reimported)

            # Verify entity names survived JSON round trip
            entity_names = [e["name"] for e in reimported["nodes"].get("Entity", [])]
            self.assertIn("EI_JSON_TestEntity", entity_names)
        finally:
            os.unlink(tmp_path)


class TestCommunityFilter(unittest.TestCase):
    """Test community-based export filtering with synthetic data."""

    @classmethod
    def setUpClass(cls):
        cls.driver, cls.cfg = _get_driver()
        cls.prefix = "EI_TEST_COMM"
        cls.comm_id = str(uuid.uuid4())
        cls.child_comm_id = str(uuid.uuid4())
        cls.ent_name = f"{cls.prefix}_CommEntity"
        cls.ent_id = str(uuid.uuid4())
        cls.doc_id = str(uuid.uuid4())
        cls.chunk_id = str(uuid.uuid4())

        with cls.driver.session() as s:
            # Create community hierarchy
            s.run("""
                CREATE (c:Community {
                    id: $id, level: 2, title: $title,
                    summary: 'Test community', rank: 0.5
                })
            """, id=cls.comm_id, title=f"{cls.prefix}_TopComm")
            s.run("""
                CREATE (c:Community {
                    id: $id, level: 1, title: $title,
                    summary: 'Test child community', rank: 0.3
                })
            """, id=cls.child_comm_id, title=f"{cls.prefix}_ChildComm")
            s.run("""
                MATCH (child:Community {id: $child_id}),
                      (parent:Community {id: $parent_id})
                CREATE (child)-[:CHILD_OF]->(parent)
            """, child_id=cls.child_comm_id, parent_id=cls.comm_id)

            # Create entity belonging to child community
            s.run("""
                CREATE (e:Entity {
                    id: $id, name: $name, type: 'CONCEPT',
                    description: 'Test entity in community', status: 'active'
                })
            """, id=cls.ent_id, name=cls.ent_name)
            s.run("""
                MATCH (e:Entity {id: $eid}), (c:Community {id: $cid})
                CREATE (e)-[:BELONGS_TO {level: 1}]->(c)
            """, eid=cls.ent_id, cid=cls.child_comm_id)

            # Create document + chunk sourced from entity
            s.run("""
                CREATE (d:Document {
                    id: $id, title: $title, source_path: $path,
                    file_type: 'md', text_length: 100, chunk_count: 1,
                    auto_ingested: true, created_at: datetime()
                })
            """, id=cls.doc_id, title=f"{cls.prefix}_doc.md",
                 path=f"/tmp/{cls.prefix}_doc.md")
            s.run("""
                MATCH (d:Document {id: $doc_id})
                CREATE (c:Chunk {id: $cid, text: $text, chunk_index: 0, token_estimate: 50})
                CREATE (d)-[:HAS_CHUNK]->(c)
            """, doc_id=cls.doc_id, cid=cls.chunk_id,
                 text=f"{cls.ent_name} is in a community.")
            s.run("""
                MATCH (e:Entity {id: $eid}), (d:Document {id: $did})
                CREATE (e)-[:SOURCED_FROM {created_at: datetime()}]->(d)
            """, eid=cls.ent_id, did=cls.doc_id)

    @classmethod
    def tearDownClass(cls):
        with cls.driver.session() as s:
            s.run("MATCH (c:Community) WHERE c.title STARTS WITH $p DETACH DELETE c",
                  p=f"{cls.prefix}_")
            s.run("MATCH (e:Entity) WHERE e.name STARTS WITH $p DETACH DELETE e",
                  p=f"{cls.prefix}_")
            s.run("""
                MATCH (d:Document) WHERE d.source_path STARTS WITH $p
                OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c)
                DETACH DELETE c, d
            """, p=f"/tmp/{cls.prefix}_")
        cls.driver.close()

    def test_resolve_community_by_title(self):
        with self.driver.session() as s:
            cid = resolve_community(s, community_title=f"{self.prefix}_TopComm")
        self.assertEqual(cid, self.comm_id)

    def test_resolve_community_by_id(self):
        with self.driver.session() as s:
            cid = resolve_community(s, community_id=self.comm_id)
        self.assertEqual(cid, self.comm_id)

    def test_resolve_community_not_found(self):
        with self.driver.session() as s:
            with self.assertRaises(SystemExit):
                resolve_community(s, community_id="nonexistent-id")

    def test_community_scope_includes_descendants(self):
        with self.driver.session() as s:
            comm_ids, entity_ids, doc_ids = get_community_scope(s, self.comm_id)
        self.assertIn(self.comm_id, comm_ids)
        self.assertIn(self.child_comm_id, comm_ids)
        self.assertIn(self.ent_id, entity_ids)
        self.assertIn(self.doc_id, doc_ids)

    def test_export_community_filter(self):
        filters = {"community_id": self.comm_id}
        data = export_graph(self.driver, self.cfg, filters, False)

        # Should include our test community and child
        comm_ids_in_export = [c["id"] for c in data["nodes"]["Community"]]
        self.assertIn(self.comm_id, comm_ids_in_export)
        self.assertIn(self.child_comm_id, comm_ids_in_export)

        # Should include our test entity
        ent_names = [e["name"] for e in data["nodes"]["Entity"]]
        self.assertIn(self.ent_name, ent_names)

        # Should include our test document
        doc_ids = [d["id"] for d in data["nodes"]["Document"]]
        self.assertIn(self.doc_id, doc_ids)

        # Metadata should record the filter
        self.assertEqual(data["metadata"]["filters"]["community_id"], self.comm_id)


if __name__ == "__main__":
    unittest.main()
