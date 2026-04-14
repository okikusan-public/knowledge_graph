---
name: agentic-search
description: Autonomous multi-tool search agent that dynamically selects search strategies, evaluates results, and iterates until sufficient context is gathered to answer complex questions
disable-model-invocation: false
argument-hint: [query] [--project project_name]
allowed-tools: Bash, Read
---

# Agentic Search

Autonomous search agent that dynamically selects tools, evaluates results, and iterates to answer complex questions from the knowledge graph.

Unlike `/vector-search` (single vector query) or `/graph-search` (fixed vector + graph pipeline), this skill makes you (Claude Code) the search agent. You decide which tools to use, how many searches to run, whether results are sufficient, and when to stop.

**Important**: This skill is read-only. Never modify the graph during search (no community_detection, no save_entities, no auto_ingest).

## Usage

```
/agentic-search "What technologies are used across all projects and how do they relate?"
/agentic-search "Compare entity A and entity B" --project project_a
/agentic-search "What recent developments relate to topic X?"
```

## Arguments

Parse `$ARGUMENTS` for:
- The search query (quoted string)
- Optional `--project` / `-p` flag for project selection

If a `--project`/`-p` flag was provided, append it to every script command. Example: if the user said `-p my_project`, every command should end with `-p my_project`.

Script base path: `${CLAUDE_SKILL_DIR}/../../../scripts/`

## Available Search Tools

You have the following tools at your disposal. Choose which to use based on the query.

### 1. Vector Search (chunk)

```bash
python ${CLAUDE_SKILL_DIR}/../../../scripts/vector_search.py "$QUERY" -t chunk -k N $1 $2
```

- **Returns**: Text excerpts with similarity scores
- **Best for**: Finding specific passages, facts, quotes
- **Cost**: Free (local embedding)

### 2. Vector Search (entity)

```bash
python ${CLAUDE_SKILL_DIR}/../../../scripts/vector_search.py "$QUERY" -t entity -k N $1 $2
```

- **Returns**: Entity names, types, descriptions with similarity scores
- **Best for**: Finding relevant concepts, people, technologies
- **Cost**: Free

### 3. Vector Search (community)

```bash
python ${CLAUDE_SKILL_DIR}/../../../scripts/vector_search.py "$QUERY" -t community -k N $1 $2
```

- **Returns**: Community titles and summaries
- **Best for**: Understanding thematic clusters, broad topic overview
- **Cost**: Free

### 4. Vector Search (document)

```bash
python ${CLAUDE_SKILL_DIR}/../../../scripts/vector_search.py "$QUERY" -t document -k N $1 $2
```

- **Returns**: Document titles and source paths
- **Best for**: Identifying relevant source documents
- **Cost**: Free

### 5. Graph Search (JSON)

```bash
python ${CLAUDE_SKILL_DIR}/../../../scripts/graph_search.py "$QUERY" -k N --max-related M --max-chunks M --json $1 $2
```

- **Returns**: JSON with seed_entities, seed_chunks, seed_communities, related_entities, entity_communities, context_chunks, provenance
- **Best for**: Multi-hop reasoning, relationship discovery, comprehensive context gathering
- **Cost**: Free

### 6. X Search (external)

```bash
python ${CLAUDE_SKILL_DIR}/../../../scripts/x_search.py "$QUERY" --days N $1 $2
```

- **Returns**: Markdown file with X/Twitter search results (path printed to stdout)
- **Best for**: Real-time information, trends, public discourse
- **Cost**: ~$0.02/call, requires XAI_API_KEY. Use sparingly.
- **Options**: `--web-search` for web results, `--handles user1,user2` for specific accounts

### 7. Direct Cypher Query

For structural or analytical questions, run Cypher directly:

```bash
python -c "
import sys; sys.path.insert(0, '${CLAUDE_SKILL_DIR}/../../..')
from neo4j import GraphDatabase; from config import get_config
cfg = get_config('$PROJECT')  # Replace $PROJECT with the actual project name, or omit argument for default
driver = GraphDatabase.driver(cfg.neo4j_uri, auth=cfg.neo4j_auth)
with driver.session() as s:
    for r in s.run('YOUR_CYPHER_QUERY').data():
        print(r)
driver.close()
"
```

Useful for: counting entities by type, finding shortest paths, listing all relationships for an entity, custom traversals not covered by other tools.

## Search Strategy

You are the search agent. Follow this decision framework:

### Phase 1: Query Analysis

Classify the query:
- **Factual lookup** ("What is X?" where X is a named concept) — Start with vector_search (entity). For passage/quote lookups, prefer vector_search (chunk)
- **Comparison** ("Compare X and Y") — Decompose into sub-queries, search each separately. If comparing across projects, run separate searches with different `-p` flags
- **Relationship exploration** ("How does X relate to Y?") — Start with graph_search --json
- **Thematic overview** ("What do we know about topic X?") — Start with vector_search (community), then graph_search. For overly broad queries ("tell me everything"), start with a Cypher count to understand scope, then use community summaries
- **Temporal/recent** ("What happened recently with X?") — Start with internal graph search; use x_search as fallback if internal results are insufficient
- **External/social media** ("What's on Twitter about X?", explicit X/SNS mention) — Start with x_search directly. Map temporal references: "today"=1, "this week"=7, "this month"=30, "recently"=14 for `--days` parameter
- **Structural** ("How many X exist?", "List all Y") — Use Cypher directly
- **Multi-part** — Decompose into sub-queries and search each

### Phase 2: Execute Initial Search

Run the tool you selected in Phase 1. Start with conservative parameters (e.g., `-k 3`). Do not exceed `-k 10` for vector searches or `--max-related 20` for graph searches.

### Phase 3: Evaluate Results

After each search, evaluate:

**Results are SUFFICIENT when:**
- The results contain information about the **specific concept** the user asked about (not just related concepts)
- The core question can be answered with specific facts from the results
- Key entities and relationships relevant to the query have been identified
- Source provenance exists (you can cite where information came from)
- For relationship queries: both endpoints and connection paths are clear

**Results are INSUFFICIENT when:**
- Results are empty or only tangentially related (similarity scores all below 0.7)
- The query has multiple parts but only some are answered
- Results reference entities or concepts that need further explanation
- You were asked about relationships but only found isolated entities
- Critical context is missing (found the entity but not its source or description)
- **Concept drift**: Results are about related-but-different concepts (e.g., searched for "Agentic RAG" but only found "GraphRAG" and "Agentic AI") — high similarity scores do not mean concept identity

**Concept identity check (CRITICAL):**
After collecting results, verify whether the returned entities/chunks actually describe the queried concept or merely related-but-different concepts. Vector search returns items by semantic *similarity*, not identity. High similarity scores do NOT mean the result is about the same concept (e.g., "Agentic RAG" vs "GraphRAG" vs "Agentic AI" are all semantically close but conceptually distinct). If the graph contains related concepts but NOT the specific concept asked about, treat this as a **knowledge gap** — do not conflate the related concepts with the queried one.

### Phase 4: Iterate (if insufficient)

Choose your next action:
- **Try a different tool** — e.g., vector_search found nothing, try graph_search with broader seeds
- **Reformulate the query** — try synonyms, broader/narrower terms. The graph contains both Japanese and English content, so try the other language
- **Decompose further** — break the remaining unanswered parts into sub-queries
- **Try a different node type** — searched chunks, now try entities or communities
- **Include archived entities** — if a specific entity the user mentioned was not found, retry with `--all` flag to include archived entities
- **Expand parameters** — increase `-k` or `--max-related` (within the limits above)
- **Read source documents** — use provenance paths to read original docs with the Read tool

**Early termination**: If the first 2 searches return zero results across different tools, run a quick Cypher count (`MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count`) to check graph state. If the graph is empty or has no relevant data, inform the user immediately rather than exhausting all 5 rounds.

### Phase 5: Iteration Limits

- **Maximum 5 search tool invocations** per query (Cypher counts and Read tool do not count toward this limit)
- **Maximum 1 x_search call** (cost constraint)
- If after 5 searches results are still insufficient, synthesize the best answer possible and explicitly note the gaps

### Phase 6: Knowledge Gap Protocol

When the graph lacks direct information about the queried concept but contains related concepts:

1. **Acknowledge the gap explicitly**: State that the queried concept (e.g., "Agentic RAG") was not found as a direct entity or in any chunk in the knowledge graph
2. **Use your own knowledge to answer the core question**: You are an LLM with broad training knowledge. When the graph has a gap, provide a correct answer using your own knowledge. Do NOT limit your answer to only what the graph contains. The user asked you a question — answer it
3. **Bridge with graph content**: After answering the core question from your own knowledge, connect it to related concepts that DO exist in the graph (e.g., "The graph does not contain information about Agentic RAG specifically, but it does contain related concepts such as X and Y. Here is how they relate...")
4. **Never conflate**: Do NOT present graph content about concept A as if it answers a question about concept B, even if A and B are semantically similar. "GraphRAG" is not "Agentic RAG." "Agentic AI Pattern" is not "Agentic RAG." Clearly label what each concept is

This protocol applies whenever the concept identity check in Phase 3 reveals that the graph results are about adjacent concepts rather than the queried concept itself.

## Synthesis

After gathering sufficient results, construct your answer:

1. **Answer the actual question first**: Before discussing graph findings, ensure you have directly addressed what the user asked. If the user asked "What is X?", the answer must explain X — not explain Y because Y was found in the graph
2. **Cite sources**: reference document names, entity names, and community context
3. **Show provenance**: mention which documents contributed which facts
4. **Use relationships** to build a narrative, not just list facts
5. **Weave in community summaries** for broader thematic context when relevant
6. **Separate graph knowledge from LLM knowledge**: Clearly distinguish (a) facts sourced from the knowledge graph (with citations) from (b) your own knowledge used to fill gaps. Use phrasing like "According to the knowledge graph: ..." vs "Based on general knowledge: ..."
7. **Respond in the same language** the user used for their query. When quoting entity names or descriptions from the graph, keep them in their original language
8. **Match answer depth** to the complexity of the question — a simple factual query deserves a concise answer, not a multi-paragraph essay

### Source Citation Format

```
Answer text here. [Source: document_name.pdf]
Entity X relates to Entity Y via "relationship_type". [Source: document_a.pdf, document_b.pdf]
```

## Comparison with Other Search Skills

| Feature | /vector-search | /graph-search | /agentic-search |
|---------|---------------|---------------|-----------------|
| Strategy | Fixed: embed + cosine | Fixed: vector seeds + 1-hop expand | Dynamic: Claude decides |
| Tools used | vector_search only | graph_search only | Any combination |
| Iteration | None | None | Up to 5 rounds |
| Query decomposition | No | No | Yes |
| Best for | Quick lookup | Multi-hop context | Complex/ambiguous questions |
| Result evaluation | None | None | Automatic sufficiency check |
| Speed | Fast (single query) | Medium (single pipeline) | Slower (multi-round) |
