# knowledge_graph

A centralized knowledge management platform that ingests documents and media into a Neo4j knowledge graph for unified search and discovery. Supports multiple file formats (.md, .txt, .csv, .pdf, .docx, .xlsx, .pptx), YouTube videos (metadata + transcript), and provides vector similarity search, community detection, and visual content extraction via Claude's vision capabilities.

## Structure

```
knowledge_graph/
├── config.py                        # Per-project settings (Neo4j connection, etc.)
├── docker-compose.yml               # Embedding server + Neo4j (with GDS plugin)
├── embedding/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── server.py                    # FastAPI + multilingual-e5-base (768 dims)
├── scripts/
│   ├── add_knowledge.py             # Direct knowledge input (Note/WebSource/Conversation)
│   ├── auto_ingest.py               # File → chunks → embeddings → Neo4j
│   ├── community_detection.py       # GDS Leiden 3-level community detection
│   ├── embed_existing.py            # Batch-assign embeddings to existing nodes
│   ├── graph_search.py              # Vector search + graph traversal hybrid
│   ├── archive_entity.py            # Entity archive management (archive/restore/list)
│   ├── quiz.py                      # Spaced repetition quiz system
│   ├── pdf_markitdown.py             # PDF → structured Markdown (via markitdown)
│   ├── youtube_markitdown.py        # YouTube → structured Markdown (metadata + transcript)
│   ├── ingest_pipeline.sh           # Full automation: markitdown → ingest → entities
│   ├── render_pages.py              # PDF → per-page PNG (visual-extract preprocessing)
│   └── vector_search.py             # Vector similarity search CLI
└── tests/
    ├── test_render_pages.py         # Unit tests for render_pages.py
    ├── test_save_entities.py        # Integration tests for entity saving & conflicts
    ├── test_pdf_markitdown.py       # Unit + integration tests for markitdown conversion
    ├── test_youtube_markitdown.py   # Unit + integration tests for YouTube conversion
    └── test_graph_search.py         # Integration tests for graph traversal search
```

## Setup

### 1. Start Embedding Server + Neo4j

```bash
cd ~/knowledge_graph
docker compose up -d
# Health check
curl http://localhost:8082/health
# Neo4j Browser: http://localhost:7474 (neo4j/changeme)
```

Initial startup takes time due to model and plugin downloads.

### 2. Neo4j for additional projects

Each project can run its own Neo4j instance with separate ports and credentials.

docker-compose.yml template:

```yaml
services:
  neo4j:
    image: neo4j:5.26-community
    container_name: <project>-neo4j
    ports:
      - "<browser_port>:7474"
      - "<bolt_port>:7687"
    environment:
      - NEO4J_AUTH=neo4j/<password>
      - NEO4J_PLUGINS=["apoc","graph-data-science"]
      - NEO4J_dbms_security_procedures_unrestricted=apoc.*,gds.*
      - NEO4J_dbms_security_procedures_allowlist=apoc.*,gds.*
    volumes:
      - neo4j_data:/data

volumes:
  neo4j_data:
```

### 3. Python Dependencies

```bash
pip install sentence-transformers neo4j requests pymupdf python-docx openpyxl python-pptx 'markitdown[pdf]' youtube-transcript-api
```

## Usage

All scripts accept `--project` (`-p`) to select a project. Falls back to `GRAPHRAG_PROJECT` env var or the default config.

### Document Ingestion

Supported formats: `.md`, `.txt`, `.csv`, `.pdf`, `.docx`, `.xlsx`, `.pptx`

```bash
# Add or update a document
python scripts/auto_ingest.py upsert /path/to/file.pdf -p <project>

# Remove a document
python scripts/auto_ingest.py delete /path/to/file.pdf -p <project>
```

Processing pipeline:
1. Extract text from the file
2. Split into chunks (512 tokens, 50-token overlap)
3. Generate embeddings (with `passage:` prefix)
4. Create Document + Chunk nodes with HAS_CHUNK / NEXT_CHUNK relationships
5. Auto-create MENTIONS relationships to existing Entities

### File Naming Convention

Include a date or version in the filename to enable version tracking across the knowledge graph. Each file becomes a separate Document node, and Entities link back to their source via SOURCED_FROM — making it possible to trace which version of a document contributed specific knowledge.

```
docs/proposal_v1.pdf        # Version-based
docs/proposal_v2.pdf        # Both coexist as separate Documents
docs/report_2026-03.pdf     # Date-based
docs/report_2026-04.pdf     # Newer version alongside the old
```

When an older version is no longer needed, delete it with `auto_ingest.py delete` — orphaned Entities (those no longer referenced by any Chunk) are automatically cleaned up.

### Batch Embedding for Existing Nodes

Assign embeddings to manually created Chunk/Entity nodes that lack them.

```bash
python scripts/embed_existing.py -p <project>            # Both Chunk + Entity
python scripts/embed_existing.py -p <project> -l Entity  # Entity only
```

### Vector Similarity Search

```bash
python scripts/vector_search.py "search query" -p <project>              # Search Chunks
python scripts/vector_search.py "search query" -p <project> -t entity    # Search Entities
python scripts/vector_search.py "search query" -p <project> -t community # Search Communities
python scripts/vector_search.py "search query" -p <project> -k 10       # Top 10 results
python scripts/vector_search.py "search query" -p <project> -t entity --all  # Include archived
```

### Graph Search (Vector + Graph Traversal)

Hybrid search combining vector similarity with multi-hop graph traversal for richer context retrieval.

```bash
python scripts/graph_search.py "search query" -p <project>              # Default (3 seeds)
python scripts/graph_search.py "search query" -p <project> -k 5         # 5 seed nodes
python scripts/graph_search.py "search query" -p <project> --json       # JSON output
```

Processing pipeline:
1. Vector search finds seed Entities, Chunks, and Communities
2. Graph traversal expands via RELATES_TO, BELONGS_TO, MENTIONS
3. Gathers source Chunks and SOURCED_FROM provenance
4. Claude synthesizes all context into a coherent answer (via `/graph-search` skill)

### Visual Content Extraction (Visual Extract)

Extract semantic information from PDF pages — including diagrams, charts, and embedded text — using Claude's vision capabilities. For Office documents (PPTX, DOCX, etc.), convert to PDF first using Keynote, PowerPoint, or print-to-PDF.

```bash
# Convert PDF pages to PNG (render_pages.py standalone)
python scripts/render_pages.py /path/to/file.pdf -o /tmp/output_dir
python scripts/render_pages.py /path/to/file.pdf --dpi 300

# Run as a Claude Code skill (PNG rendering → vision extraction → markdown output)
/visual-extract report.pdf
/visual-extract report.pdf --project project_a
```

Processing pipeline:
1. Render each PDF page as a PNG image (pymupdf, default 200 dpi)
2. Read each image one at a time via Claude's vision (prevents context overflow)
3. Extract text, diagrams, OCR, and layout info into `{original_name}_visual_extract.md`
4. Optionally ingest into the knowledge graph via `auto_ingest.py`

### PDF Markitdown Conversion

For PDFs with tables, headings, and structured formatting, use markitdown for higher-fidelity text extraction before ingestion.

```bash
# Convert PDF to structured Markdown
python scripts/pdf_markitdown.py /path/to/file.pdf
python scripts/pdf_markitdown.py /path/to/file.pdf -o custom_output.md

# Full automated pipeline (convert + ingest + extract entities + save)
./scripts/ingest_pipeline.sh /path/to/file.pdf -p <project>

# Via Claude Code skill (interactive entity extraction)
/pdf-markitdown report.pdf
/pdf-markitdown report.pdf --auto   # fully automated
```

Requires: `pip install 'markitdown[pdf]'`

### YouTube Markitdown Conversion

Convert YouTube videos to structured Markdown (metadata, description, and transcript) for ingestion into the knowledge graph.

```bash
# Convert YouTube video to Markdown
python scripts/youtube_markitdown.py "https://www.youtube.com/watch?v=VIDEO_ID"
python scripts/youtube_markitdown.py "https://youtu.be/VIDEO_ID" -o custom_output.md
python scripts/youtube_markitdown.py "https://www.youtube.com/watch?v=VIDEO_ID" --lang en

# Full automated pipeline (convert + ingest + extract entities + save)
./scripts/ingest_pipeline.sh "https://www.youtube.com/watch?v=VIDEO_ID" -p <project>

# Via Claude Code skill (interactive entity extraction)
/youtube-markitdown https://www.youtube.com/watch?v=VIDEO_ID
/youtube-markitdown https://www.youtube.com/watch?v=VIDEO_ID --auto
```

Supported URL formats: `youtube.com/watch?v=`, `youtu.be/`, `youtube.com/embed/`, `youtube.com/shorts/`, `youtube.com/live/`, `m.youtube.com/watch?v=`

Output is saved to `docs/youtube_{video_id}_markitdown.md`. Default transcript languages: Japanese, English (`--lang` to override).

Requires: `pip install youtube-transcript-api` (without it, only metadata and description are extracted)

### Direct Knowledge Input

Add knowledge directly to the graph without requiring a document file. Claude determines the appropriate source node type (Note, WebSource, Conversation, or custom) based on the input content.

```bash
# Via Claude Code skill (recommended — Claude auto-classifies the source type)
/add-knowledge "Acme Corp is a software company with 500 employees"
/add-knowledge "https://example.com/about — Acme Corp company overview"
/add-knowledge "Heard from a colleague that Acme Corp's revenue is 10B JPY"

# Via script directly (JSON input)
echo '{"source":{"label":"Note","properties":{"text":"..."}},
  "entities":[{"name":"Acme Corp","type":"ORGANIZATION","description":"..."}],
  "relationships":[]}' | python scripts/add_knowledge.py -p <project>
```

Source types are dynamic — any alphanumeric label can be used. All source nodes get `id`, `created_at`, and `embedding` automatically.

### Spaced Repetition Quiz

Strengthen knowledge retention through retrieval practice with GraphRAG entities. Uses a spaced repetition algorithm (SM-2 inspired) to optimize review timing — correct answers extend the interval, incorrect answers reset it.

```bash
# Select entities due for review
python scripts/quiz.py select -p <project> -k 5

# Select with topic filtering (vector similarity)
python scripts/quiz.py select -p <project> -k 5 --topic "認知科学"

# Record a quiz result
python scripts/quiz.py record -p <project> --json '{
  "entity_name": "Retrieval Practice",
  "is_correct": true,
  "question": "検索練習とは？",
  "user_answer": "思い出す行為が記憶を強化する",
  "score": 1.0,
  "feedback": "正解"
}'

# View quiz statistics
python scripts/quiz.py stats -p <project>

# Run as a Claude Code skill (interactive quiz session)
/quiz
/quiz --topic "認知科学"
/quiz --count 3
```

Selection algorithm:
1. **Priority 3**: Entities with more incorrect than correct answers (struggling)
2. **Priority 2**: Never-quizzed entities (new knowledge)
3. **Priority 1**: Overdue entities (correct but interval elapsed)

Spaced repetition intervals:
- Correct: interval doubles (1 → 2 → 4 → 8 → 16 → ... → max 90 days)
- Incorrect: interval resets to 1 day
- Interleaving: different entity types are mixed to strengthen pattern recognition

### Entity Archive

Archive entities to exclude them from search and quiz while preserving graph relationships.

```bash
# Archive an entity
python scripts/archive_entity.py archive "Entity Name" -p <project>
python scripts/archive_entity.py archive "Entity Name" -p <project> --reason "Completed"

# Restore an archived entity
python scripts/archive_entity.py restore "Entity Name" -p <project>

# List archived entities
python scripts/archive_entity.py list -p <project>
```

Archived entities are excluded from:
- Vector search (default; use `--all` to include)
- Graph search seed selection
- Quiz entity selection

Archived entities remain visible when discovered via graph traversal (RELATES_TO) with an `[archived]` marker.

### Community Detection

Cluster Entities into 3 hierarchy levels using the GDS Leiden algorithm.

```bash
python scripts/community_detection.py -p <project>
```

Prerequisites:
- Neo4j GDS plugin installed
- RELATES_TO relationships exist between Entities
- Embedding server running

Output:
- Level 0 (gamma=1.5): Fine-grained
- Level 1 (gamma=0.7): Medium-grained
- Level 2 (gamma=0.3): Coarse-grained
- Community nodes with title, summary, embedding, and rank
- BELONGS_TO / CHILD_OF relationships

## Graph Schema

### Node Labels

| Label | Description | Key Properties |
|---|---|---|
| Document | Source document | id, title, source_path, file_type, embedding |
| Chunk | Text fragment | id, text, chunk_index, token_estimate, embedding |
| Entity | Extracted entity | id, name, type, description, embedding, status*, archived_date*, archive_reason*, last_quiz_date*, correct_count*, incorrect_count*, quiz_interval_days* |
| Community | Entity cluster | id, level, title, summary, rank, embedding |
| Note | Direct knowledge input (memo, fact) | id, text, author, embedding |
| WebSource | URL-based information | id, url, title, reliability, embedding |
| Conversation | Info from people/meetings | id, text, speaker, date, context, embedding |
| QuizResult | Spaced repetition quiz result | id, entity_name, question, user_answer, is_correct, score, feedback |

### Relationships

| Type | From → To | Description |
|---|---|---|
| HAS_CHUNK | Document → Chunk | Document's text fragments |
| NEXT_CHUNK | Chunk → Chunk | Chunk ordering |
| MENTIONS | Chunk → Entity | Entity mentioned in chunk |
| RELATES_TO | Entity → Entity | Relationship between entities |
| SOURCED_FROM | Entity → Source node | Provenance: which source contributed the entity (Document, Note, WebSource, Conversation) |
| BELONGS_TO | Entity → Community | Community membership |
| CHILD_OF | Community → Community | Hierarchy (fine → coarse) |
| QUIZ_RESULT_FOR | QuizResult → Entity | Links quiz result to the tested entity |

Project-specific relationships can be added as needed.

\* Status/archive properties on Entity are set on creation (`status = 'active'`) and managed via `archive_entity.py`. Quiz properties are added dynamically when the entity is first quizzed.

## Embedding Model

- **Model**: `intfloat/multilingual-e5-base`
- **Dimensions**: 768
- **Prefix convention**:
  - Storage: `"passage: {text}"`
  - Search: `"query: {text}"`

## Adding a New Project

1. Add connection info to the `PROJECTS` dict in `config.py`

```python
PROJECTS = {
    ...
    "new_project": {
        "neo4j_uri": "bolt://localhost:<port>",
        "neo4j_user": "neo4j",
        "neo4j_password": "<password>",
        "embed_url": "http://localhost:8082/embed",
    },
}
```

2. Set up a `docker-compose.yml` in the project repo to run Neo4j (use unique ports)

3. Pass `-p new_project` when running scripts
