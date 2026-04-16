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

# Agentic search (autonomous multi-tool search — Claude Code dynamically selects tools)
# Use via /agentic-search skill (not a standalone script)

# Graph quality linting
python scripts/lint_graph.py duplicates -p <project> --threshold 0.95
python scripts/lint_graph.py duplicates -p <project> --fix --dry-run
python scripts/lint_graph.py orphans -p <project> --min-age 7
python scripts/lint_graph.py stale -p <project> --stale-days 90
python scripts/lint_graph.py all -p <project> --json

# Knowledge export/import (backup, cross-project transfer)
python scripts/export_knowledge.py -p <project> -o backup.json
python scripts/export_knowledge.py -p <project> --no-embeddings -o backup_light.json
python scripts/export_knowledge.py -p <project> --source-path /path/to/file.pdf -o partial.json
python scripts/export_knowledge.py -p <project> --entity-type PERSON -o people.json
python scripts/export_knowledge.py -p <project> --community "タイトル" -o community.json
python scripts/export_knowledge.py -p <project> --community-id <uuid> -o community.json
python scripts/import_knowledge.py backup.json -p <project>
python scripts/import_knowledge.py backup.json -p <project> --dry-run
python scripts/import_knowledge.py backup.json -p <project> --regenerate-embeddings

# Community detection
python scripts/community_detection.py -p <project>

# Cross-document relationship discovery
python scripts/discover_relationships.py -p <project> --all
python scripts/discover_relationships.py -p <project> --all --threshold 0.90 --dry-run

# Render PDF pages to PNG (visual-extract preprocessing)
python scripts/render_pages.py /path/to/file.pdf -o /tmp/output_dir

# PDF to structured Markdown (preserves tables, headings)
python scripts/pdf_markitdown.py /path/to/file.pdf
python scripts/pdf_markitdown.py /path/to/file.pdf -o output.md

# YouTube to structured Markdown (metadata + transcript)
python scripts/youtube_markitdown.py "https://www.youtube.com/watch?v=VIDEO_ID"
python scripts/youtube_markitdown.py "https://youtu.be/VIDEO_ID" -o output.md --lang ja en

# X (Twitter) search via Grok API (requires XAI_API_KEY)
python scripts/x_search.py "search query" --days 7
python scripts/x_search.py "search query" --handles user1,user2 -o output.md
python scripts/x_search.py "search query" --web-search

# Full ingestion pipeline (markitdown → ingest → entity extraction → save)
./scripts/ingest_pipeline.sh /path/to/file.pdf -p <project>
./scripts/ingest_pipeline.sh "https://www.youtube.com/watch?v=VIDEO_ID" -p <project>

# Spaced repetition quiz
python scripts/quiz.py select -p <project> -k 5 --topic "topic"
python scripts/quiz.py record -p <project> --json '{"entity_name":"...","is_correct":true,"question":"...","user_answer":"...","score":1.0,"feedback":"..."}'
python scripts/quiz.py stats -p <project>

# Entity archive management
python scripts/archive_entity.py archive "Entity Name" -p <project>
python scripts/archive_entity.py archive "Entity Name" -p <project> --reason "Completed"
python scripts/archive_entity.py restore "Entity Name" -p <project>
python scripts/archive_entity.py list -p <project>

# Tests
python -m pytest tests/ -v
```

Python deps: `pip install sentence-transformers neo4j requests pymupdf python-docx openpyxl python-pptx anthropic 'markitdown[pdf]' youtube-transcript-api openai`

## Architecture

- **config.py** — `Config` class and `get_config(project)`. `PROJECTS` dict holds per-project Neo4j connection info; overridable via env vars (`NEO4J_URI`, `GRAPHRAG_PROJECT`, etc.). All scripts select project via `--project`/`-p` flag or `GRAPHRAG_PROJECT` env var
- **embedding/server.py** — FastAPI server loading `intfloat/multilingual-e5-base` (768 dims). `POST /embed` for batch embedding generation. Runs as a Docker container on port 8082, shared across all projects
- **scripts/** — Each script adds the parent directory to `sys.path` and imports `from config import get_config`. Neo4j drivers are created and closed within each script
- **scripts/save_entities.py** — Accepts entity/relationship JSON (via stdin or `--json`) and saves to Neo4j with embeddings. Creates Entity nodes (MERGE by name), MENTIONS, RELATES_TO, and SOURCED_FROM relationships. `query_existing_entities()` enables conflict detection before saving. Used by `/ingest` and `/visual-extract` skills after Claude Code extracts entities
- **scripts/extract_entities.py** — Optional standalone tool using Claude API (tool_use) to extract entities. Requires `ANTHROPIC_API_KEY` env var. Not needed when using Claude Code skills (Claude Code extracts entities itself)
- **scripts/render_pages.py** — Renders each PDF page as PNG (via pymupdf). Preprocessing step for the `/visual-extract` skill. Users must convert PPTX etc. to PDF beforehand
- **scripts/add_knowledge.py** — Direct knowledge input without document files. Creates dynamic source nodes (Note, WebSource, Conversation, or any custom label) with entities and SOURCED_FROM links. Label and property keys are validated (alphanumeric only) to prevent Cypher injection
- **scripts/export_knowledge.py** — Exports all graph data (nodes + relationships) to versioned JSON (v1.0). Supports `--no-embeddings` to reduce file size, `--source-path` for document-scoped export, `--entity-type` for type filtering. Output to file (`-o`) or stdout
- **scripts/import_knowledge.py** — Imports graph data from export JSON. Uses MERGE for idempotent writes (Entity merges on `name`, others on `id`). Handles entity ID remapping when merging into non-empty graphs. Supports `--dry-run` and `--regenerate-embeddings` (batch 32 via embedding server)
- **scripts/graph_search.py** — Hybrid search: vector similarity for seed nodes + graph traversal (RELATES_TO, BELONGS_TO, MENTIONS, SOURCED_FROM) for context expansion. Returns structured results with provenance
- **`.claude/skills/agentic-search/SKILL.md`** — Autonomous search agent skill. Unlike `/vector-search` (single vector query) and `/graph-search` (fixed pipeline), `/agentic-search` lets Claude Code dynamically choose search tools (vector_search, graph_search, x_search, Cypher), decompose complex queries, evaluate result sufficiency, and iterate up to 5 rounds before synthesizing a cited answer
- **scripts/quiz.py** — Spaced repetition quiz system. `select` picks entities due for review (prioritizes incorrect/overdue/never-quizzed; supports topic filtering via vector similarity). `record` saves QuizResult nodes and updates Entity spaced repetition properties (last_quiz_date, correct_count, incorrect_count, quiz_interval_days). `stats` shows overall quiz statistics. Interval doubles on correct (max 90 days), resets to 1 day on incorrect
- **scripts/archive_entity.py** — Entity archive management. `archive` sets status to archived, `restore` restores to active, `list` shows all archived entities. Archived entities are excluded from search seeds and quiz but visible during graph traversal with `[archived]` mark
- **scripts/lint_graph.py** — Knowledge graph quality linter. `duplicates` detects near-duplicate entities via embedding cosine similarity (GDS preferred, Python fallback). `orphans` finds structurally isolated entities (no RELATES_TO/BELONGS_TO). `stale` flags entities with old source documents and time-dependent language (Japanese/English). `all` runs all checks. Supports `--fix` (merge duplicates, archive orphans) and `--dry-run`
- **scripts/discover_relationships.py** — Auto-discovers RELATES_TO relationships between entities from different documents using embedding cosine similarity. Threshold configurable (default 0.85). Runs automatically after entity save via hook
- **scripts/pdf_markitdown.py** — Converts PDF (or DOCX/PPTX/XLSX) to structured Markdown using Microsoft markitdown. Preserves tables, headings, and formatting that plain-text extraction loses. Outputs `{stem}_markitdown.md`
- **scripts/youtube_markitdown.py** — Converts YouTube video to structured Markdown using markitdown. Extracts metadata (title, keywords, runtime), description, and transcript (if `youtube-transcript-api` installed). Outputs `docs/youtube_{video_id}_markitdown.md`. Supports multiple URL formats (youtube.com, youtu.be, embed, mobile)
- **scripts/x_search.py** — Searches X (Twitter) posts via xAI's Grok API using the OpenAI SDK. Supports date range filtering (`--days`), handle filtering (`--handles`/`--exclude-handles`), optional web search (`--web-search`), and model selection. Outputs structured Markdown to `docs/x_search_{query}_{date}.md`. Requires `XAI_API_KEY` env var and `openai` package
- **scripts/ingest_pipeline.sh** — Fully automated pipeline: markitdown conversion → auto_ingest (chunk + embed) → entity extraction (via `claude --print`) → save_entities → community detection. Accepts both file paths and YouTube URLs. For headless/batch ingestion

## Key Conventions (rules to follow when modifying code)

- **Embedding prefix**: Use `"passage: {text}"` for storage and `"query: {text}"` for search (multilingual-e5 requirement). Must be consistent across all scripts
- **Chunking**: Target 512 tokens with 50-token overlap; boundary adjustment at Japanese punctuation and newlines (`CHAR_PER_TOKEN=1.5`)
- **Supported formats**: `.md`, `.txt`, `.csv`, `.pdf`, `.docx`, `.xlsx`, `.pptx`
- **Embedding text**: Truncate at 2000 characters
- **Visual Extract**: Process images one at a time to prevent context overflow. Save results as `{original_name}_visual_extract.md`. Always auto-ingest the result
- **Entity extraction**: When using `/ingest` or `/visual-extract` skills, Claude Code extracts entities itself (no API key needed). `extract_entities.py` remains as an optional standalone tool for non-Claude-Code usage
- **Orphan cleanup**: `auto_ingest.py` automatically removes Entities not referenced by any Chunk (via MENTIONS) after both upsert and delete operations. Entities shared across documents are safe as long as any Chunk still mentions them
- **Dynamic source nodes**: `/add-knowledge` creates source nodes with any label (Note, WebSource, Conversation, etc.). Label and property keys must be alphanumeric (validated to prevent Cypher injection)
- **Quiz system**: QuizResult nodes link to Entity via QUIZ_RESULT_FOR. Entity spaced repetition properties: `last_quiz_date`, `correct_count`, `incorrect_count`, `quiz_interval_days`. Topic filtering uses vector similarity threshold > 0.80
- **Entity status**: Entities have `status` property (`active`/`archived`). Default is `active`. Archived entities are excluded from search seeds and quiz selection but visible during graph traversal (marked `[archived]`). Use `archive_entity.py` for archive/restore operations. `coalesce(e.status, 'active')` handles backward compatibility with entities that lack the property
- **Cross-document relationship discovery**: `discover_relationships.py` creates RELATES_TO with `type = "auto_discovered"`. Uses MERGE to prevent duplicates. Only considers entity pairs from different source documents. Respects archived entity status. Default similarity threshold is 0.85
- **Markitdown conversion**: Output files follow `{original_stem}_markitdown.md` naming convention (parallels `_visual_extract.md`). Use for PDFs with tables, structured headings, or complex formatting. The `/pdf-markitdown` skill defaults to interactive entity extraction; pass `--auto` for fully automated pipeline via `ingest_pipeline.sh`
- **YouTube markitdown**: Output files follow `docs/youtube_{video_id}_markitdown.md` naming convention. All YouTube URL formats are normalized to `https://www.youtube.com/watch?v={id}` before conversion (required by markitdown's YouTubeConverter). Default transcript languages are `["ja", "en"]`. Without `youtube-transcript-api`, only metadata and description are extracted (no transcript). The `/youtube-markitdown` skill follows the same Interactive/Automated pattern as `/pdf-markitdown`
- **X search**: Output files follow `docs/x_search_{sanitized_query}_{date}.md` naming convention. Uses the `openai` package with custom `base_url="https://api.x.ai/v1"` (OpenAI SDK compatible). Default model is `grok-4-1-fast-non-reasoning` (cheapest). `web_search` tool is opt-in via `--web-search` flag. No automated pipeline mode due to per-search API costs (~$0.02/call). The `/x-search` skill is always interactive with user review before ingestion
- **Graph linting**: `lint_graph.py` checks graph quality. `duplicates` threshold default is 0.95 (stricter than discover_relationships' 0.85). Orphan detection checks RELATES_TO/BELONGS_TO isolation (not MENTIONS/SOURCED_FROM isolation used by auto_ingest cleanup). Stale detection requires both old source AND time-dependent language. `--fix` for duplicates merges into the entity with the longest description; `--fix` for orphans archives (not deletes)
- **Agentic search**: `/agentic-search` is a meta-skill that orchestrates existing search tools. It never modifies the graph (read-only). Maximum 5 search invocations per query. `x_search` is only used when the user explicitly asks about recent/real-time information or when internal results are clearly insufficient for a time-sensitive query. All answers include source citations. Responds in the same language as the user's query
- **Knowledge export/import**: Export format is versioned JSON (v1.0) with metadata header, nodes section (keyed by label), and relationships section (keyed by type). Entity import uses `MERGE on name` (consistent with save_entities.py) with ID remapping for relationships. Other nodes use `MERGE on id`. `--no-embeddings` reduces file size ~90%. Communities and QuizResults are always exported in full (not filtered by `--source-path`). Dynamic source node labels are validated with `isalnum()` on both export and import
- **Adding projects**: Add an entry to the `PROJECTS` dict in `config.py`
