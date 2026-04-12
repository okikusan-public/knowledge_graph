#!/bin/bash
# Full ingestion pipeline: markitdown → auto_ingest → entity extraction → save_entities
#
# Usage:
#   ./scripts/ingest_pipeline.sh <input.pdf> [-p project]
#   ./scripts/ingest_pipeline.sh "https://www.youtube.com/watch?v=ID" [-p project]
#
# Prerequisites:
#   - markitdown[pdf] installed: pip install 'markitdown[pdf]'
#   - claude CLI installed and authenticated
#   - Embedding server running (docker compose up -d)
#   - Neo4j running for the target project
#   - (YouTube) youtube-transcript-api recommended: pip install youtube-transcript-api
#
# Steps:
#   1. Convert input (PDF/YouTube) to structured Markdown via markitdown
#   2. Upsert the Markdown into the knowledge graph (chunk + embed)
#   3. Extract entities using claude --print (stdin)
#   4. Save entities to the graph
#   5. Run community detection + relationship discovery

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Parse arguments ---
INPUT_FILE=""
PROJECT_ARGS=()

while [[ $# -gt 0 ]]; do
    case $1 in
        -p|--project)
            PROJECT_ARGS=(-p "$2")
            shift 2
            ;;
        *)
            if [[ -z "$INPUT_FILE" ]]; then
                INPUT_FILE="$1"
            fi
            shift
            ;;
    esac
done

if [[ -z "$INPUT_FILE" ]]; then
    echo "Usage: $0 <input file or YouTube URL> [-p project]" >&2
    echo "" >&2
    echo "Supported: PDF, DOCX, PPTX, XLSX, YouTube URLs" >&2
    exit 1
fi

# --- Validate prerequisites ---
if ! command -v claude >/dev/null 2>&1; then
    echo "  [error] claude CLI not found. Install: https://claude.ai/download" >&2
    exit 1
fi

# Detect YouTube URL or resolve file path
IS_YOUTUBE_URL=false
if [[ "$INPUT_FILE" =~ ^https?://(www\.youtube\.com|youtube\.com|m\.youtube\.com|youtu\.be)/ ]]; then
    IS_YOUTUBE_URL=true
elif [[ -f "$INPUT_FILE" ]]; then
    INPUT_FILE="$(cd "$(dirname "$INPUT_FILE")" && pwd)/$(basename "$INPUT_FILE")"
else
    echo "  [error] File not found: $INPUT_FILE" >&2
    exit 1
fi

echo "========================================" >&2
echo "  Ingest Pipeline: $INPUT_FILE" >&2
echo "========================================" >&2

# --- Step 1: Convert to Markdown ---
echo "" >&2
echo "  [step 1/5] Converting to Markdown via markitdown..." >&2
if [[ "$IS_YOUTUBE_URL" == "true" ]]; then
    MD_FILE=$(python "$SCRIPT_DIR/youtube_markitdown.py" "$INPUT_FILE")
else
    MD_FILE=$(python "$SCRIPT_DIR/pdf_markitdown.py" "$INPUT_FILE")
fi
echo "  [step 1/5] Output: $MD_FILE" >&2

# --- Step 2: Ingest into knowledge graph ---
echo "" >&2
echo "  [step 2/5] Ingesting into knowledge graph..." >&2
python "$SCRIPT_DIR/auto_ingest.py" upsert "$MD_FILE" "${PROJECT_ARGS[@]+"${PROJECT_ARGS[@]}"}"

# --- Step 3: Extract entities via claude --print ---
echo "" >&2
echo "  [step 3/5] Extracting entities via Claude..." >&2

EXTRACT_PROMPT='You are an entity extraction system. Extract named entities and relationships from the document provided via stdin.

Entity types:
- PERSON: People, named individuals
- ORGANIZATION: Companies, teams, departments, institutions
- TECHNOLOGY: Technologies, tools, frameworks, programming languages, platforms
- REQUIREMENT: Requirements, specifications, constraints
- SCHEDULE: Dates, deadlines, timelines, milestones
- BUDGET: Financial figures, costs, budgets, numbers
- RISK: Risks, issues, concerns, threats
- PROPOSAL_PATTERN: Proposal patterns, solution approaches
- EVALUATION_CRITERIA: Evaluation criteria, metrics, KPIs
- DELIVERABLE: Deliverables, outputs, artifacts
- SECURITY: Security measures, protocols, compliance
- DOMAIN: Business domains, areas of expertise
- CONCEPT: Abstract concepts, methodologies, processes

Rules:
- Extract concrete, specific entities (not generic terms like "system" or "data")
- Normalize entity names (consistent casing, resolve abbreviations)
- Each entity needs a brief description (1-2 sentences)
- Relationships should capture meaningful connections between extracted entities
- Relationship types should be descriptive verbs/phrases (e.g., "uses", "manages", "depends_on")

Return ONLY valid JSON in this exact format (no markdown fences, no explanation):
{"entities": [{"name": "...", "type": "...", "description": "..."}], "relationships": [{"source": "...", "target": "...", "type": "...", "description": "..."}]}'

CLAUDE_STDERR=$(mktemp)
trap 'rm -f "$CLAUDE_STDERR"' EXIT

if ! ENTITY_JSON=$(claude --print "$EXTRACT_PROMPT" --model sonnet --output-format json --no-session-persistence < "$MD_FILE" 2>"$CLAUDE_STDERR"); then
    echo "  [error] claude --print failed:" >&2
    cat "$CLAUDE_STDERR" >&2
    exit 1
fi

# Validate JSON and extract if wrapped
if ! echo "$ENTITY_JSON" | python -c "import sys, json; json.load(sys.stdin)" 2>/dev/null; then
    echo "  [warn] Attempting to extract JSON from response..." >&2
    ENTITY_JSON=$(echo "$ENTITY_JSON" | python -c "
import sys, re, json
text = sys.stdin.read()
# Match JSON object containing 'entities' key
match = re.search(r'\{[^{}]*\"entities\".*\}', text, re.DOTALL)
if match:
    try:
        obj = json.loads(match.group())
        print(json.dumps(obj, ensure_ascii=False))
    except json.JSONDecodeError:
        print('{\"entities\": [], \"relationships\": []}')
        print('  [error] Could not parse extracted JSON', file=sys.stderr)
else:
    print('{\"entities\": [], \"relationships\": []}')
    print('  [error] No JSON found in Claude response', file=sys.stderr)
")
fi

ENTITY_COUNT=$(echo "$ENTITY_JSON" | python -c "import sys, json; d=json.load(sys.stdin); print(len(d.get('entities', [])))")
REL_COUNT=$(echo "$ENTITY_JSON" | python -c "import sys, json; d=json.load(sys.stdin); print(len(d.get('relationships', [])))")
echo "  [step 3/5] Extracted $ENTITY_COUNT entities, $REL_COUNT relationships" >&2

# --- Step 4: Save entities to graph ---
echo "" >&2
if [[ "$ENTITY_COUNT" -gt 0 ]]; then
    echo "  [step 4/5] Saving entities to graph..." >&2
    echo "$ENTITY_JSON" | python "$SCRIPT_DIR/save_entities.py" --source-path "$MD_FILE" "${PROJECT_ARGS[@]+"${PROJECT_ARGS[@]}"}"
else
    echo "  [step 4/5] No entities to save, skipping." >&2
fi

# --- Step 5: Community detection + relationship discovery ---
# Explicit execution required: post-entity-save hook does not trigger
# for commands inside this pipeline script.
echo "" >&2
if [[ "$ENTITY_COUNT" -gt 0 ]]; then
    echo "  [step 5/5] Running community detection..." >&2
    python "$SCRIPT_DIR/community_detection.py" "${PROJECT_ARGS[@]+"${PROJECT_ARGS[@]}"}"
    echo "  [step 5/5] Running relationship discovery..." >&2
    python "$SCRIPT_DIR/discover_relationships.py" --all "${PROJECT_ARGS[@]+"${PROJECT_ARGS[@]}"}"
else
    echo "  [step 5/5] Skipping (no entities)." >&2
fi

# --- Summary ---
echo "" >&2
echo "========================================" >&2
echo "  Pipeline Complete" >&2
echo "  Input:         $(basename "$INPUT_FILE")" >&2
echo "  Markdown:      $MD_FILE" >&2
echo "  Entities:      $ENTITY_COUNT" >&2
echo "  Relationships: $REL_COUNT" >&2
echo "========================================" >&2
