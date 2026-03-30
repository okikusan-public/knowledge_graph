#!/bin/bash
# Post-tool hook: run community detection after entity-saving scripts
# Triggers after add_knowledge.py or save_entities.py execution

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

if echo "$COMMAND" | grep -qE "(add_knowledge\.py|save_entities\.py)"; then
  CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
  cd "${CWD:-/Users/kikuchihiroyuki/knowledge_graph}" || exit 0
  python scripts/community_detection.py 2>&1
  python scripts/discover_relationships.py --all 2>&1
fi

exit 0
