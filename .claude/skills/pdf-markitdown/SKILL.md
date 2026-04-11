---
name: pdf-markitdown
description: Convert PDF to structured Markdown via markitdown and ingest into the knowledge graph. Preserves tables, headings, and formatting better than plain-text extraction.
disable-model-invocation: false
argument-hint: [filename.pdf] [--project project_name] [--auto]
allowed-tools: Bash, Read, Write, Glob
---

# PDF Markitdown Ingestion

Convert PDF documents to structured Markdown using Microsoft markitdown (preserving tables, headings, and formatting), then ingest into the knowledge graph with entity extraction.

## Usage

```
/pdf-markitdown report.pdf
/pdf-markitdown report.pdf --project project_a
/pdf-markitdown report.pdf --auto
```

## Mode Selection

Parse arguments from `$ARGUMENTS`:
- `--auto` flag present → **Automated Mode** (skip to Automated Workflow below)
- No `--auto` flag → **Interactive Mode** (default)

---

## Interactive Mode (default)

### 1. Find and Convert PDF

Search for the file in `docs/` (Glob `docs/**/$FILENAME`). If not found, list files in `docs/` and prompt the user.

Convert to structured Markdown:

```bash
python ${CLAUDE_SKILL_DIR}/../../../scripts/pdf_markitdown.py "<file_path>"
```

Capture the output path (last line of stdout). The output is saved as `{stem}_markitdown.md` alongside the input.

### 2. Ingest Markdown into Graph

```bash
python ${CLAUDE_SKILL_DIR}/../../../scripts/auto_ingest.py upsert "<md_file_path>" $1 $2
```

Note the chunk count from the output.

### 3. Extract Entities (Claude Code = Claude)

Read the generated markdown file using the Read tool. Analyze the structured content and extract **entities** and **relationships** as JSON.

**Entity types:**
- PERSON, ORGANIZATION, TECHNOLOGY, REQUIREMENT, SCHEDULE, BUDGET, RISK
- PROPOSAL_PATTERN, EVALUATION_CRITERIA, DELIVERABLE, SECURITY, DOMAIN, CONCEPT

**Extraction rules:**
- Extract concrete, specific entities (not generic terms like "system" or "data")
- Normalize entity names (consistent casing, resolve abbreviations)
- Each entity needs a brief description (1-2 sentences)
- Relationships should capture meaningful connections between entities
- Relationship types should be descriptive verbs/phrases (e.g., "uses", "manages", "depends_on")
- Leverage the structured Markdown (tables, headings) for more precise extraction

### 4. Detect Entity Conflicts

Before saving, check for conflicts with existing entities:

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

For each entity that already exists:
- **No conflict** (additive or equivalent): Proceed. Use the more comprehensive description.
- **Conflict detected** (contradicting facts): Present both to the user and let them decide.

### 5. Save Entities to Graph

```bash
cat <<'ENTITIES_JSON' | python ${CLAUDE_SKILL_DIR}/../../../scripts/save_entities.py --source-path "<md_file_path>" $1 $2
{"entities": [...], "relationships": [...]}
ENTITIES_JSON
```

Note: Community detection and relationship discovery run automatically via the post-entity-save hook.

### 6. Report Results

Report: original PDF filename, markdown output path, chunk count, entity count, relationship count, and any conflicts resolved.

---

## Automated Mode (`--auto`)

Run the full pipeline script for headless/batch processing:

```bash
${CLAUDE_SKILL_DIR}/../../../scripts/ingest_pipeline.sh "<file_path>" $1 $2
```

This executes: markitdown conversion → auto_ingest → entity extraction (via `claude --print`) → save_entities → community detection → relationship discovery.

Report the pipeline output when complete.

---

## Comparison with Other Skills

| Feature | /ingest | /pdf-markitdown | /visual-extract |
|---------|---------|-----------------|-----------------|
| PDF handling | pymupdf (plain text) | markitdown (structured MD) | render → Claude vision |
| Table preservation | No | Yes | Yes (via image) |
| Heading structure | No | Yes | Yes (via image) |
| Diagram extraction | No | No | Yes |
| OCR (scanned PDF) | No | No | Yes |
| Automated mode | No | Yes (`--auto`) | No |
| Best for | General docs | Structured PDFs | Visual/diagram-heavy PDFs |

## Notes

- Requires: `pip install 'markitdown[pdf]'`
- The `_markitdown.md` file is saved alongside the original for reference and re-use
- For scanned PDFs or diagram-heavy documents, use `/visual-extract` instead
- Entity extraction is performed by Claude Code itself (interactive) or `claude --print` (automated)
