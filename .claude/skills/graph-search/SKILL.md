---
name: graph-search
description: Hybrid search combining vector similarity with graph traversal for multi-hop reasoning over the knowledge graph
disable-model-invocation: false
argument-hint: [query] [--project project_name]
allowed-tools: Bash, Read
---

# Graph Search

Hybrid search that combines vector similarity with graph traversal to discover and synthesize knowledge across the full graph structure.

## Usage

```
/graph-search "What technologies are used in the project?"
/graph-search "generative AI projects" --project project_a
```

## How it differs from /vector-search

- `/vector-search`: Returns top-K similar nodes (isolated matches)
- `/graph-search`: Finds seed nodes via vector search, then traverses RELATES_TO, BELONGS_TO, MENTIONS, and SOURCED_FROM to gather multi-hop context, then synthesizes a coherent answer

## Workflow

### 1. Run Graph Search

```bash
python ${CLAUDE_SKILL_DIR}/../../../scripts/graph_search.py "$QUERY" $1 $2 --json
```

This returns JSON with:
- `seed_entities`: Top entities by vector similarity
- `seed_chunks`: Top chunks by vector similarity
- `seed_communities`: Top communities by vector similarity
- `related_entities`: Entities connected via RELATES_TO (graph expansion)
- `entity_communities`: Communities the entities belong to
- `context_chunks`: Source text chunks mentioning relevant entities
- `provenance`: Which documents contributed each entity

### 2. Read Context Chunks

If the context chunks from Step 1 are truncated or insufficient, read the full text of the most relevant chunks. Use the source_path from provenance to read original documents if needed.

### 3. Synthesize Answer

Using all gathered context (seed nodes, related entities, communities, chunks, provenance), synthesize a comprehensive answer to the user's question:

- Cite specific entities and their relationships
- Reference source documents via provenance
- Use community context to provide broader thematic understanding
- If the graph lacks sufficient information, say so clearly rather than speculating

### 4. Show Sources

At the end, list the source documents and key entities that informed the answer.

## Options

- `--top` / `-k`: Number of seed nodes per type (default: 3)
- `--max-related`: Max related entities to expand (default: 10)
- `--max-chunks`: Max context chunks (default: 10)
- `--project` / `-p`: Project name
