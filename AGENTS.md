# Project Knowledge Base Instructions

This project exposes a local read-only knowledge base through the MCP server `project-kb`.

For setup, indexing, rebuild, FTS rebuild, and diagnostics, follow README commands and use the CLI entrypoints such as `uv run project-kb-ingest` and `uv run project-kb-doctor`. Do not use MCP tools for maintenance.

When answering questions about project documents, requirements, meeting notes, design decisions, risks, owners, milestones, or historical context, call `search_project_kb_fast` first.

Use `search_project_kb_deep` only when fast results are insufficient or the user explicitly asks for high precision/deep search.

When using retrieved knowledge, cite `source_path`, `heading`, and `chunk_index`. When available, also cite `page_number`, `slide_number`, `sheet_name`, and `cell_range`.

Use top-k citations together; do not rely only on the first result when answering project questions. Prefer the default fast `top_k=5` for project-context answers.

When the user clearly narrows the domain, use `source_filter` to focus search, for example `风险`, `会议纪要`, `架构`, `代码`, or `需求`. Prefer narrowing `source_filter` over increasing `top_k`.

Do not call multiple KB search tools in parallel. The local MCP server is intentionally resource-limited.

If the knowledge base is installed in a nested directory, make sure the active Codex/Kiro workspace has merged MCP config and the MCP `cwd` points at the KB directory.

The knowledge base applies domain-aware ranking boosts and may use a local high-precision BGE reranker in deep mode. Treat ranking signals as retrieval aids, not as proof by themselves.

If search results are insufficient, say so clearly and do not invent missing details.

Call `read_kb_source` only when the snippet is not enough to answer the question, and keep `max_chars` small.

Do not use MCP tools to modify, delete, rebuild, or reindex the knowledge base or project files.

Return the minimum sensitive source text needed to answer the user's question.

## Obsidian Curator Rules

When organizing knowledge, client inputs, requirements, capabilities, case studies, delivery assets, commercial risks, or proposal blocks, first search the relevant Project KB source. Use CLI raw-index queries for `sources/` intake and the default MCP curated index for `docs/` knowledge unless the user says otherwise.

When generating Obsidian notes, preserve structured `source_refs` with `source_path`, `heading`, `chunk_index`, and available `page_number`, `slide_number`, `sheet_name`, or `cell_range`.

Do not write inferences as facts. Put unsupported content under `Assumptions` or `Evidence Gaps`.

AI-generated Obsidian notes must default to `status: needs_review`.

Do not overwrite files with `status: reviewed` unless the user explicitly asks.

Prefer small, clear Markdown notes over long documents.

Use Obsidian internal links such as `[[Client_A_Requirement_Matrix]]`.

After each curation pass, report proposed file paths, a short content summary, and questions requiring human confirmation.

Do not use MCP tools to modify, delete, rebuild, or reindex the knowledge base.
