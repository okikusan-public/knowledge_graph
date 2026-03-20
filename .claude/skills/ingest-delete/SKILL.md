---
name: ingest-delete
description: Remove previously ingested files from the GraphRAG knowledge graph
disable-model-invocation: true
argument-hint: [filename] [--project project_name]
allowed-tools: Bash, Read, Glob
---

# Delete Ingested File

Remove the Document node and all associated Chunks for a given file from the knowledge graph.

## Usage

```
/ingest-delete report.pdf
/ingest-delete report.docx --project project_a
```

## Workflow

1. Search for file `$0` in `docs/` (Glob `docs/**/$0`)
2. If not found, confirm with the user
3. Run for each file:

```bash
python ${CLAUDE_SKILL_DIR}/../../../scripts/auto_ingest.py delete "<file_path>" $1 $2
```

4. Report the number of deleted chunks
