---
name: generate-document
description: Creates a downloadable office file (PDF, DOCX, or PPTX) from Markdown content. Use only when the user explicitly asks to save, download, print, or share a file — e.g. "create a PDF report", "make a PowerPoint", "export as Word". Do not use for charts, diagrams, dashboards, or any visual output.
---

# generate_document

Generates a formatted downloadable file from structured Markdown.

## When to use

The user must explicitly ask for a **file** — not a visual. Trigger phrases:
- "create a PDF report / summary / memo"
- "make a PowerPoint / presentation / slide deck"
- "export as Word / .docx"
- "I need a document I can download / print / send"

If the user wants to **see** something in chat → use `inline-rendering` instead.

## Formats and Markdown structure

### PPTX (PowerPoint)

Use `##` H2 headings as slide separators — each H2 becomes a new slide title.

```markdown
# Quarterly Sales Review

## Executive Summary
The Q3 results exceeded targets by 12%.
Key drivers: EMEA expansion and product upsell.

## Revenue Breakdown
- EMEA: $4.2M (+28% YoY)
- APAC: $2.1M (+15% YoY)
- Americas: $6.8M (+5% YoY)

## Recommendations
- Accelerate EMEA headcount
- Launch APAC partnership programme
```

### DOCX (Word)

Use `#` H1 for sections, `##` H2 for subsections, `-` for bullets.

```markdown
# Project Proposal: Data Platform Migration

## Overview
This proposal outlines the migration from legacy ETL to a modern streaming architecture.

## Technical approach
- Phase 1: Schema audit and mapping
- Phase 2: Parallel run and validation
- Phase 3: Cutover and decommission

## Timeline
Estimated 14 weeks from kick-off.
```

### PDF

Same Markdown structure as DOCX. Rendered with ReportLab — supports bold, italic, and `code` inline.

## Tool call

```json
{
  "format": "pptx" | "docx" | "pdf",
  "title": "Document title (used as cover and filename)",
  "content": "Full Markdown body as described above"
}
```

## Do not

- Use this for charts, diagrams, or visual content → use `inline-rendering`
- Use this when the user wants to see a result in chat
- Use this for plain text responses
