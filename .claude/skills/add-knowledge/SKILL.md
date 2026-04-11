---
name: add-knowledge
description: Add knowledge directly to the graph from free-form text input without requiring a document file. Supports Note, WebSource, Conversation, or any custom source type.
disable-model-invocation: false
argument-hint: [free-form text or URL]
allowed-tools: Bash, Read
---

# Add Knowledge

Add knowledge directly to the graph without requiring a document file. Claude analyzes the input, determines the appropriate source node type, extracts entities, and saves everything to the graph.

## Usage

```
/add-knowledge "Acme Corp is a software company with 500 employees"
/add-knowledge "https://example.com/about — Acme Corp company overview"
/add-knowledge "Heard from a colleague that Acme Corp's revenue is 10B JPY"
```

## Workflow

### 1. Analyze Input and Determine Source Type

Read the user's input `$ARGUMENTS` and classify into one of these source types:

| Label | When to use | Key Properties |
|-------|------------|----------------|
| Note | Short memo, fact, research note, general knowledge | text, author (optional) |
| WebSource | URL-based information, web reference | url, title, reliability (optional: high/medium/low) |
| Conversation | Info heard from someone, meeting context | text, speaker, date (optional), context (optional) |

**Classification rules:**
- Contains a URL (`http://` or `https://`) → **WebSource**
- Mentions hearing from someone, meeting, discussion → **Conversation** (extract speaker name if present)
- Everything else → **Note**
- If none of the above fit well, propose a new label and confirm with the user

### 2. Extract Entities

Analyze the input text and extract entities and relationships, same as the `/ingest` entity extraction:

**Entity types:** PERSON, ORGANIZATION, TECHNOLOGY, REQUIREMENT, SCHEDULE, BUDGET, RISK, PROPOSAL_PATTERN, EVALUATION_CRITERIA, DELIVERABLE, SECURITY, DOMAIN, CONCEPT

**Extraction rules:**
- Extract concrete, specific entities (not generic terms)
- Normalize entity names (consistent casing, match existing graph entities)
- Each entity needs a brief description (1-2 sentences)

### 3. Check for Conflicts

Before saving, query existing entities for potential conflicts:

```bash
python -c "
import sys, json
sys.path.insert(0, '.')
from neo4j import GraphDatabase
from config import get_config
from scripts.save_entities import query_existing_entities
cfg = get_config()
driver = GraphDatabase.driver(cfg.neo4j_uri, auth=cfg.neo4j_auth)
names = [ENTITY_NAMES_LIST]
result = query_existing_entities(driver, names)
driver.close()
for name, info in result.items():
    print(json.dumps({'name': name, 'type': info['type'], 'description': info['description'], 'sources': info['sources']}, ensure_ascii=False))
"
```

If conflicts exist, present them to the user and resolve before saving.

### 4. Save to Graph

Pipe the structured JSON to `add_knowledge.py`:

```bash
cat <<'KNOWLEDGE_JSON' | python ${CLAUDE_SKILL_DIR}/../../../scripts/add_knowledge.py -p $1
{
  "source": {
    "label": "Note",
    "properties": {
      "text": "the input text",
      "author": "optional author"
    }
  },
  "entities": [
    {"name": "Entity Name", "type": "ORGANIZATION", "description": "Brief description"}
  ],
  "relationships": [
    {"source": "Entity A", "target": "Entity B", "type": "related", "description": "..."}
  ]
}
KNOWLEDGE_JSON
```

**Property examples per type:**

Note:
```json
{"label": "Note", "properties": {"text": "...", "author": "user"}}
```

WebSource:
```json
{"label": "WebSource", "properties": {"url": "https://...", "title": "Page title", "reliability": "high"}}
```

Conversation:
```json
{"label": "Conversation", "properties": {"text": "...", "speaker": "John", "date": "2026-03-20", "context": "Weekly meeting"}}
```

### 5. Report Results

Report: source type created, entity count, relationship count, and any conflicts resolved.
