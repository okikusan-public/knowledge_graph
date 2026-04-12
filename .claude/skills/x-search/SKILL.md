---
name: x-search
description: Search X (Twitter) posts via Grok API and ingest results into the knowledge graph.
disable-model-invocation: false
argument-hint: [search query] [--project project_name] [--days 7] [--handles user1,user2] [--web-search]
allowed-tools: Bash, Read, Write, Glob
---

# X (Twitter) Search Ingestion

Search X (Twitter) posts in real-time using xAI's Grok API, save results as structured Markdown, then ingest into the knowledge graph with entity extraction.

## Usage

```
/x-search "AI agent frameworks"
/x-search "product launch" --days 30 --handles elonmusk,openai
/x-search "knowledge graphs" --web-search --project project_a
/x-search "生成AI トレンド" --days 3
```

## Prerequisites

- `pip install openai`
- `XAI_API_KEY` environment variable set (get key at https://console.x.ai/)

---

## Workflow (Interactive Only)

Note: No `--auto` mode is provided due to per-search API costs ($0.015-$0.025/call). All searches require user review before ingestion.

### 1. Execute X Search

Parse `$ARGUMENTS` for the query and optional flags. Run:

```bash
python ${CLAUDE_SKILL_DIR}/../../../scripts/x_search.py "<query>" --days <N> [--handles h1,h2] [--exclude-handles h1,h2] [--web-search] $1 $2
```

Capture the output path (last line of stdout).

### 2. Review Results

Read the generated markdown file using the Read tool. Present a summary to the user:
- Key themes and topics found
- Notable accounts/posts mentioned
- Date range covered
- Number of citations

Ask the user if they want to proceed with ingestion into the knowledge graph.

### 3. Ingest Markdown into Graph

```bash
python ${CLAUDE_SKILL_DIR}/../../../scripts/auto_ingest.py upsert "<md_file_path>" $1 $2
```

Note the chunk count from the output.

### 4. Extract Entities (Claude Code = Claude)

Read the generated markdown file using the Read tool. Analyze the X search results and extract **entities** and **relationships** as JSON.

**Entity types:**
- PERSON, ORGANIZATION, TECHNOLOGY, REQUIREMENT, SCHEDULE, BUDGET, RISK
- PROPOSAL_PATTERN, EVALUATION_CRITERIA, DELIVERABLE, SECURITY, DOMAIN, CONCEPT

**X-search-specific extraction guidance:**
- X posts often reference people (@handles) — extract as PERSON entities
- Company and product mentions — extract as ORGANIZATION or TECHNOLOGY
- Trending topics and hashtags — extract as CONCEPT if substantive
- Avoid extracting generic social media terms (retweet, like, thread)

### 5. Detect Entity Conflicts

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

### 6. Save Entities to Graph

```bash
cat <<'ENTITIES_JSON' | python ${CLAUDE_SKILL_DIR}/../../../scripts/save_entities.py --source-path "<md_file_path>" $1 $2
{"entities": [...], "relationships": [...]}
ENTITIES_JSON
```

Note: Community detection and relationship discovery run automatically via the post-entity-save hook.

### 7. Report Results

Report: search query, date range, markdown output path, chunk count, entity count, relationship count, and any conflicts resolved.

---

## Comparison with Other Skills

| Feature | /x-search | /youtube-markitdown | /pdf-markitdown | /add-knowledge |
|---------|-----------|---------------------|-----------------|----------------|
| Input | Search query | YouTube URL | PDF file | Free text |
| Source | X posts (real-time) | YouTube video | Document file | User input |
| API cost | ~$0.02/search | Free | Free | Free |
| Automated mode | No | Yes (`--auto`) | Yes (`--auto`) | No |
| Best for | Trends, discussions | Video content | Structured docs | Quick notes |

## Notes

- Requires: `pip install openai` and `XAI_API_KEY` environment variable
- Default model: `grok-4-1-fast-non-reasoning` (cheapest: $0.20/M input, $0.50/M output)
- Tool cost: $5 per 1,000 calls (~3-5 calls per search)
- `--web-search` adds web context alongside X results (optional)
- `--handles` and `--exclude-handles` cannot be used together (API constraint)
- Output saved to `docs/x_search_{query}_{date}.md`
