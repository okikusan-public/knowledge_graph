# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

For detailed usage, setup, and graph schema, see [README.md](README.md).

## Overview

A centralized document knowledge management platform. Ingests documents into a Neo4j knowledge graph (Document → Chunk → Entity → Community) and provides vector search, community detection, and visual content extraction. Fork per project and configure connections via env vars or config.py.

## Commands

```bash
# Start Embedding server + Neo4j
docker compose up -d

# Document ingestion (chunking + embedding only)
python scripts/auto_ingest.py upsert /path/to/file.pdf -p <project>
python scripts/auto_ingest.py delete /path/to/file.pdf -p <project>

# Save entities to graph (JSON via stdin or --json)
echo '{"entities":[...],"relationships":[...]}' | python scripts/save_entities.py --source-path /path/to/file.pdf -p <project>

# Direct knowledge input (Note/WebSource/Conversation)
echo '{"source":{"label":"Note","properties":{"text":"..."}},"entities":[...]}' | python scripts/add_knowledge.py -p <project>

# Standalone entity extraction via Claude API (optional, requires ANTHROPIC_API_KEY)
python scripts/extract_entities.py /path/to/file.pdf -p <project>
python scripts/extract_entities.py --all -p <project>

# Batch-assign embeddings to existing nodes
python scripts/embed_existing.py -p <project>

# Vector search
python scripts/vector_search.py "search query" -p <project> -t chunk -k 5

# Graph search (vector + graph traversal hybrid)
python scripts/graph_search.py "search query" -p <project> -k 3 --json

# Community detection
python scripts/community_detection.py -p <project>

# Render PDF pages to PNG (visual-extract preprocessing)
python scripts/render_pages.py /path/to/file.pdf -o /tmp/output_dir

# Tests
python -m pytest tests/ -v
```

Python deps: `pip install sentence-transformers neo4j requests pymupdf python-docx openpyxl python-pptx anthropic`

## Architecture

- **config.py** — `Config` class and `get_config(project)`. `PROJECTS` dict holds per-project Neo4j connection info; overridable via env vars (`NEO4J_URI`, `GRAPHRAG_PROJECT`, etc.). All scripts select project via `--project`/`-p` flag or `GRAPHRAG_PROJECT` env var
- **embedding/server.py** — FastAPI server loading `intfloat/multilingual-e5-base` (768 dims). `POST /embed` for batch embedding generation. Runs as a Docker container on port 8082, shared across all projects
- **scripts/** — Each script adds the parent directory to `sys.path` and imports `from config import get_config`. Neo4j drivers are created and closed within each script
- **scripts/save_entities.py** — Accepts entity/relationship JSON (via stdin or `--json`) and saves to Neo4j with embeddings. Creates Entity nodes (MERGE by name), MENTIONS, RELATES_TO, and SOURCED_FROM relationships. `query_existing_entities()` enables conflict detection before saving. Used by `/ingest` and `/visual-extract` skills after Claude Code extracts entities
- **scripts/extract_entities.py** — Optional standalone tool using Claude API (tool_use) to extract entities. Requires `ANTHROPIC_API_KEY` env var. Not needed when using Claude Code skills (Claude Code extracts entities itself)
- **scripts/render_pages.py** — Renders each PDF page as PNG (via pymupdf). Preprocessing step for the `/visual-extract` skill. Users must convert PPTX etc. to PDF beforehand
- **scripts/add_knowledge.py** — Direct knowledge input without document files. Creates dynamic source nodes (Note, WebSource, Conversation, or any custom label) with entities and SOURCED_FROM links. Label and property keys are validated (alphanumeric only) to prevent Cypher injection
- **scripts/graph_search.py** — Hybrid search: vector similarity for seed nodes + graph traversal (RELATES_TO, BELONGS_TO, MENTIONS, SOURCED_FROM) for context expansion. Returns structured results with provenance

## Key Conventions (rules to follow when modifying code)

- **Embedding prefix**: Use `"passage: {text}"` for storage and `"query: {text}"` for search (multilingual-e5 requirement). Must be consistent across all scripts
- **Chunking**: Target 512 tokens with 50-token overlap; boundary adjustment at Japanese punctuation and newlines (`CHAR_PER_TOKEN=1.5`)
- **Supported formats**: `.md`, `.txt`, `.csv`, `.pdf`, `.docx`, `.xlsx`, `.pptx`
- **Embedding text**: Truncate at 2000 characters
- **Visual Extract**: Process images one at a time to prevent context overflow. Save results as `{original_name}_visual_extract.md`. Always auto-ingest the result
- **Entity extraction**: When using `/ingest` or `/visual-extract` skills, Claude Code extracts entities itself (no API key needed). `extract_entities.py` remains as an optional standalone tool for non-Claude-Code usage
- **Orphan cleanup**: `auto_ingest.py` automatically removes Entities not referenced by any Chunk (via MENTIONS) after both upsert and delete operations. Entities shared across documents are safe as long as any Chunk still mentions them
- **Dynamic source nodes**: `/add-knowledge` creates source nodes with any label (Note, WebSource, Conversation, etc.). Label and property keys must be alphanumeric (validated to prevent Cypher injection)
- **Adding projects**: Add an entry to the `PROJECTS` dict in `config.py`
