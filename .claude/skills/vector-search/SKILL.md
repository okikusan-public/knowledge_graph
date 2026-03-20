---
name: vector-search
description: Run vector similarity search on the GraphRAG knowledge graph
disable-model-invocation: false
argument-hint: [query] [--project project_name] [--type chunk|entity|community] [--top-k N]
allowed-tools: Bash, Read
---

# Vector Search

Run vector similarity search against Chunk / Entity / Community nodes stored in the knowledge graph.

## Usage

```
/vector-search "search query"
/vector-search "revenue analysis" --project project_a --type chunk --top-k 10
```

## Workflow

1. Run:

```bash
python ${CLAUDE_SKILL_DIR}/../../../scripts/vector_search.py $ARGUMENTS
```

2. Format and report results (similarity score, text excerpt, source file)

## Defaults

- `--type`: chunk
- `--top-k`: 5
- `--project`: `GRAPHRAG_PROJECT` env var or config.py default
