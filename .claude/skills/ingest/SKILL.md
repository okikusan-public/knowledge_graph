---
name: ingest
description: Ingest files from docs/ directory into the GraphRAG knowledge graph. Supported formats: .md, .txt, .csv, .pdf, .docx, .xlsx, .pptx
disable-model-invocation: false
argument-hint: [filename] [--project project_name]
allowed-tools: Bash, Read, Write, Glob
---

# File Ingestion

Ingest (upsert) files from the `docs/` directory into the GraphRAG knowledge graph with automatic entity extraction.

## Usage

```
/ingest report.pdf
/ingest report.docx --project project_a
/ingest *.pptx
```

When only a filename is given, it searches under `docs/`. Wildcard patterns ingest all matching files sequentially.

## Workflow

### 1. Find and Ingest File

Search for file `$0` in `docs/` (Glob `docs/**/$0`). If not found, list supported files in `docs/` and prompt the user to select.

Run for each file:

```bash
python ${CLAUDE_SKILL_DIR}/../../../scripts/auto_ingest.py upsert "<file_path>" $1 $2
```

Note the chunk count from the output.

### 2. Extract Entities (Claude Code = Claude)

Read the file content using the Read tool. For binary formats (.docx, .xlsx, .pptx) that Read cannot handle, extract text via Bash:

```bash
python -c "import sys; sys.path.insert(0, '.'); from scripts.auto_ingest import extract_text; t = extract_text('<file_path>'); print(t if t else '')"
```

Analyze the document content and extract **entities** and **relationships** as structured JSON.

**Entity types:**
- PERSON — People, named individuals
- ORGANIZATION — Companies, teams, departments, institutions
- TECHNOLOGY — Technologies, tools, frameworks, platforms
- REQUIREMENT — Requirements, specifications, constraints
- SCHEDULE — Dates, deadlines, timelines, milestones
- BUDGET — Financial figures, costs, budgets
- RISK — Risks, issues, concerns, threats
- PROPOSAL_PATTERN — Proposal patterns, solution approaches
- EVALUATION_CRITERIA — Evaluation criteria, metrics, KPIs
- DELIVERABLE — Deliverables, outputs, artifacts
- SECURITY — Security measures, protocols, compliance
- DOMAIN — Business domains, areas of expertise
- CONCEPT — Abstract concepts, methodologies, processes

**Extraction rules:**
- Extract concrete, specific entities (not generic terms like "system" or "data")
- Normalize entity names (consistent casing, resolve abbreviations)
- Each entity needs a brief description (1-2 sentences)
- Relationships should capture meaningful connections between entities
- Relationship types should be descriptive verbs/phrases (e.g., "uses", "manages", "depends_on")

### 3. Detect Entity Conflicts

Before saving, check for conflicts with existing entities. Query the graph for entities that share names with the extracted ones:

```bash
python -c "
import sys, json
sys.path.insert(0, '.')
from neo4j import GraphDatabase
from config import get_config
from scripts.save_entities import query_existing_entities
cfg = get_config()
driver = GraphDatabase.driver(cfg.neo4j_uri, auth=cfg.neo4j_auth)
names = [COMMA_SEPARATED_ENTITY_NAMES]
result = query_existing_entities(driver, names)
driver.close()
for name, info in result.items():
    print(json.dumps({'name': name, 'type': info['type'], 'description': info['description'], 'sources': info['sources']}, ensure_ascii=False))
"
```

For each entity that already exists in the graph, compare the **existing description** with the **new description**:

- **No conflict** (additive or equivalent info): Proceed automatically. Use the more comprehensive description.
- **Conflict detected** (contradicting facts like different numbers, dates, or statuses): Present both to the user:
  ```
  Entity "Company X" conflict:
    Existing (from doc_a.pdf): "Company X has 100 employees"
    New (from current doc):    "Company X has grown to 500 employees"
  Options: [keep existing / use new / merge]
  ```
- Let the user decide, or draft a merged description for approval.
- If the new description is clearly more recent/accurate and the existing one is outdated, suggest using the new one.

If there are no conflicts (common case), skip straight to saving.

### 4. Save Entities to Graph

Pipe the extracted JSON to save_entities.py:

```bash
cat <<'ENTITIES_JSON' | python ${CLAUDE_SKILL_DIR}/../../../scripts/save_entities.py --source-path "<file_path>" $1 $2
{"entities": [...], "relationships": [...]}
ENTITIES_JSON
```

JSON format:
```json
{
  "entities": [
    {"name": "Entity Name", "type": "PERSON", "description": "Brief description"}
  ],
  "relationships": [
    {"source": "Entity A", "target": "Entity B", "type": "uses", "description": "A uses B"}
  ]
}
```

### 5. Run Community Detection

If entities were extracted (count > 0), run community detection to create/update Community nodes:

```bash
python ${CLAUDE_SKILL_DIR}/../../../scripts/community_detection.py $1 $2
```

This uses the GDS Leiden algorithm to create 3-level hierarchical communities with BELONGS_TO and CHILD_OF relationships.

### 6. Report Results

Report: filename, chunk count, entity count, relationship count, community count, and any errors.

## Supported Formats

| Format | Library |
|--------|---------|
| .md, .txt, .csv | Standard library |
| .pdf | pymupdf (fitz) |
| .docx | python-docx |
| .xlsx | openpyxl |
| .pptx | python-pptx |

## Delete Workflow

When deleting a document (e.g., `/ingest --delete report.pdf`):

### 1. Delete Document and Chunks

```bash
python ${CLAUDE_SKILL_DIR}/../../../scripts/auto_ingest.py delete "<file_path>" $1 $2
```

This automatically cleans up orphaned Entities (those no longer MENTIONS-ed by any Chunk).

### 2. Run Community Detection

Rebuild communities after deletion:

```bash
python ${CLAUDE_SKILL_DIR}/../../../scripts/community_detection.py $1 $2
```

### 3. Report Results

Report: filename, deleted chunk count, orphaned entities removed, and updated community count.

## Notes

- Files must be placed in the `docs/` directory before ingestion
- Ensure the Embedding server is running (`docker compose up -d`)
- Ensure Neo4j is running for the target project
- Re-ingesting the same file path automatically upserts (deletes old data, then re-inserts)
- Orphaned Entities (not referenced by any Chunk) are automatically cleaned up on both upsert and delete
- Entity extraction is performed by Claude Code itself — no ANTHROPIC_API_KEY needed
