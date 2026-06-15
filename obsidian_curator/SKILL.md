# Obsidian Curator Skill

Use this skill when the user asks to organize raw project knowledge, client inputs, requirements, capabilities, case studies, delivery assets, commercial risks, or proposal-ready knowledge blocks into an Obsidian-friendly Project KB.

## Principles

- Preserve raw sources. Do not delete, overwrite, or rewrite files under `sources/`.
- Write curated Markdown notes under `docs/`.
- Treat `docs/` as the source of truth for the curated index.
- Keep generated notes small, linked, and readable.
- Every factual claim must have structured `source_refs`.
- Unsupported statements must be placed under `Assumptions` or `Evidence Gaps`.
- Do not overwrite `status: reviewed` notes unless the user explicitly asks.
- Do not implement multi-agent workflows, Proposal Agent behavior, or consulting-agent behavior.
- Do not use MCP tools to modify, rebuild, or delete any knowledge base.

## Workflow 1: Raw Source Intake

1. Identify the raw source type: client input, capability, case study, delivery asset, commercial item, or compliance item.
2. Recommend the correct `sources/` directory.
3. Generate a source inventory with file name, source type, suggested category, date if available, and open questions.
4. Do not change the raw file contents.

## Workflow 2: Evidence Extraction

1. Search raw or curated Project KB before writing notes.
2. Extract only evidence-backed client facts, requirements, capabilities, case details, risks, commercial facts, and delivery information.
3. Output an Evidence Table with claim, evidence snippet, `source_path`, `heading`, `chunk_index`, and page/slide/sheet/cell metadata when available.
4. Mark missing or weak evidence as `Evidence Gap`.

## Workflow 3: Obsidian Note Generation

1. Select the right template from `docs/_templates/`.
2. Generate Markdown with YAML frontmatter.
3. Set `status: needs_review` for AI-generated drafts.
4. Add Obsidian links such as `[[Client_Name_Requirement_Matrix]]`.
5. Add structured `source_refs`.
6. Add `Open Questions` for human review.

## Workflow 4: Map of Content Generation

1. Maintain `docs/00_HOME.md`.
2. Build and update pages under `docs/01_Maps/`.
3. Link clients, requirements, capabilities, case studies, delivery notes, risks, and proposal blocks.
4. Prefer navigable MOCs over long narrative documents.

## Workflow 5: Review

1. Check that every factual note has `source_refs`.
2. Check that unsupported claims are not written as facts.
3. Check for duplicate or near-duplicate notes.
4. Check that notes are readable by humans.
5. Check that headings and snippets are useful for Project KB indexing.
6. Report proposed file paths, summaries, and questions requiring human confirmation.
