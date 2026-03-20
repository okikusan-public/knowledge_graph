---
name: visual-extract
description: Render PDF pages as PNG images and extract semantic information including diagrams, OCR, and layout via Claude's vision
disable-model-invocation: false
argument-hint: [filename.pdf] [--dpi 200] [--project project_name]
allowed-tools: Bash, Read, Write, Glob
---

# Visual Extract

Render each page of a PDF as an image and extract semantic information — text, diagrams, OCR, and layout — using Claude's vision capabilities.

Users must convert Office documents (PPTX, DOCX, etc.) to PDF beforehand.

## Usage

```
/visual-extract report.pdf
/visual-extract report.pdf --dpi 300
/visual-extract report.pdf --project project_a
```

## Workflow

### 1. Render Pages

Search for the PDF file in `docs/` (Glob `docs/**/$FILENAME`), then convert all pages to PNG using render_pages.py.

```bash
python scripts/render_pages.py "<file_path>" -o /tmp/visual_extract_$$
```

Capture the list of image paths from stdout.

### 2. Check Page Count and Decide Strategy

- **10 pages or fewer**: Process all pages
- **11 pages or more**: Ask the user whether to process all pages or specific ones only

**Critical: Context / Token Management**
- Read images **one at a time** and write extraction results to the output file immediately
- **Never read multiple pages at once** — follow the cycle: read 1 page → extract → write to file → next page
- Append each page's results to the output file immediately; do not accumulate in context

### 3. Extract Semantic Information Per Page

Read each image **one at a time** with the Read tool and extract:

- **Text content**: Headings, body text, bullet points, table cell values
- **Diagram semantics**: Flowcharts, graphs, diagrams, concept maps — their content and meaning
- **In-image text (OCR)**: Labels, annotations, legends within figures
- **Layout structure**: Section divisions, emphasis, visual hierarchy

Append results to the output file **immediately** in this format:

```markdown
## Page N

### Text Content
(text elements)

### Diagrams & Visuals
(description and content of diagrams)

### Metadata
(layout features, emphasis, etc.)
```

Output file: Save in the same directory as the input file as `{original_name}_visual_extract.md`.

```
docs/report.pdf → docs/report_visual_extract.md
```

### 4. Cleanup

Remove the temporary directory:

```bash
rm -rf /tmp/visual_extract_$$
```

### 5. Auto-Ingest into Knowledge Graph

Ingest the extracted markdown into the knowledge graph:

```bash
python scripts/auto_ingest.py upsert "<output_md_path>" [-p <project>]
```

### 6. Extract Entities

Read the output markdown file and extract entities and relationships, then save to the graph. Follow the same entity extraction process as the `/ingest` skill:

- Analyze the extracted content for entities (PERSON, ORGANIZATION, TECHNOLOGY, REQUIREMENT, SCHEDULE, BUDGET, RISK, PROPOSAL_PATTERN, EVALUATION_CRITERIA, DELIVERABLE, SECURITY, DOMAIN, CONCEPT)
- Extract concrete, specific entities with brief descriptions
- Identify meaningful relationships between entities

Save via:

```bash
cat <<'ENTITIES_JSON' | python scripts/save_entities.py --source-path "<output_md_path>" [-p <project>]
{"entities": [...], "relationships": [...]}
ENTITIES_JSON
```

### 7. Run Community Detection

If entities were extracted (count > 0), run community detection to create/update Community nodes:

```bash
python scripts/community_detection.py [-p <project>]
```

This uses the GDS Leiden algorithm to create 3-level hierarchical communities with BELONGS_TO and CHILD_OF relationships.

### 8. Report Results

- Number of pages processed
- Output file path
- Summary of extracted diagrams and visual elements
- Ingestion result (chunk count, entity count, relationship count, community count)

## Notes

- Input must be PDF. Convert PPTX/DOCX via Keynote, PowerPoint, or print-to-PDF
- Higher DPI improves readability of fine text but increases image size (default 200 dpi is usually sufficient)
- Always confirm with the user before processing large documents
- Entity extraction is performed by Claude Code itself — no ANTHROPIC_API_KEY needed
