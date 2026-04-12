---
name: youtube-markitdown
description: Convert YouTube video to structured Markdown via markitdown (metadata + transcript) and ingest into the knowledge graph.
disable-model-invocation: false
argument-hint: [YouTube URL] [--project project_name] [--auto] [--lang ja en]
allowed-tools: Bash, Read, Write, Glob
---

# YouTube Markitdown Ingestion

Convert YouTube videos to structured Markdown using Microsoft markitdown (extracting metadata, description, and transcript), then ingest into the knowledge graph with entity extraction.

## Usage

```
/youtube-markitdown https://www.youtube.com/watch?v=VIDEO_ID
/youtube-markitdown https://youtu.be/VIDEO_ID --project project_a
/youtube-markitdown https://www.youtube.com/watch?v=VIDEO_ID --auto
/youtube-markitdown https://www.youtube.com/watch?v=VIDEO_ID --lang en
```

## Mode Selection

Parse arguments from `$ARGUMENTS`:
- `--auto` flag present → **Automated Mode** (skip to Automated Workflow below)
- No `--auto` flag → **Interactive Mode** (default)

---

## Interactive Mode (default)

### 1. Convert YouTube URL

Extract the YouTube URL from arguments. Convert to structured Markdown:

```bash
python ${CLAUDE_SKILL_DIR}/../../../scripts/youtube_markitdown.py "<url>" --lang ja en
```

Capture the output path (last line of stdout). The output is saved as `docs/youtube_{video_id}_markitdown.md`.

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
- YouTube transcripts often contain conversational content — focus on key concepts, people, and technologies mentioned

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

Report: YouTube URL, video title, markdown output path, chunk count, entity count, relationship count, and any conflicts resolved.

---

## Automated Mode (`--auto`)

Run the full pipeline script for headless/batch processing:

```bash
${CLAUDE_SKILL_DIR}/../../../scripts/ingest_pipeline.sh "<url>" $1 $2
```

This executes: YouTube markitdown conversion → auto_ingest → entity extraction (via `claude --print`) → save_entities → community detection → relationship discovery.

Report the pipeline output when complete.

---

## Comparison with Other Skills

| Feature | /ingest | /pdf-markitdown | /youtube-markitdown | /visual-extract |
|---------|---------|-----------------|---------------------|-----------------|
| Input | Files | PDF/DOCX/etc | YouTube URL | PDF |
| Transcript | No | No | Yes (if available) | No |
| Table preservation | No | Yes | No | Yes (via image) |
| Automated mode | No | Yes (`--auto`) | Yes (`--auto`) | No |
| Best for | General docs | Structured PDFs | YouTube videos | Visual/diagram-heavy PDFs |

## Notes

- Requires: `pip install 'markitdown[pdf]'`
- Recommended: `pip install youtube-transcript-api` (for transcript extraction)
- Without `youtube-transcript-api`, only metadata and description are extracted
- Default transcript languages: `["ja", "en"]` — override with `--lang`
- Output saved to `docs/youtube_{video_id}_markitdown.md`
- Supported URL formats: `youtube.com/watch?v=`, `youtu.be/`, `youtube.com/embed/`, `m.youtube.com/watch?v=`
- Entity extraction is performed by Claude Code itself (interactive) or `claude --print` (automated)
