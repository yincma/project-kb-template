---
inclusion: always
---

# Project KB MCP Usage

This workspace exposes a read-only local project knowledge base through the MCP server `project-kb`.

On the first KB-related task in a session, check whether `kb_status` and `search_project_kb_fast` are available in the current tool list. If they are available, call `kb_status` before project Q&A and say that MCP was used.

If `.codex/config.toml` is present but MCP tools are not available in the current session, do not silently fallback. Tell the user: `当前会话尚未加载 project-kb MCP。请重新打开 Codex / 重新加载 workspace，或者确认 .codex/config.toml 位于当前 workspace 根目录。`

If the user wants to continue immediately without reloading, use CLI only as an explicit fallback and say that CLI fallback was used.

For setup, indexing, rebuild, FTS rebuild, and diagnostics, follow README commands and use CLI entrypoints such as `uv run project-kb-ingest` and `uv run project-kb-doctor`. Do not use MCP tools for maintenance.

Maintenance tasks use CLI: install, doctor, diagnose, ingest, rebuild, and FTS rebuild.

When the user asks about project documents, requirements, meeting notes, design decisions, risks, owners, milestones, or historical context, call `search_project_kb_fast` before answering.

Use `search_project_kb_deep` only when fast results are insufficient or the user explicitly asks for high precision/deep search.

Use `kb_status` to check whether the local knowledge base is indexed and available.

When using retrieved knowledge, cite `source_path`, `heading`, and `chunk_index`. When available, also cite `page_number`, `slide_number`, `sheet_name`, and `cell_range`.

Use top-k citations together; do not rely only on the first result when answering project questions. Prefer the default fast `top_k=5` for project-context answers.

When the user clearly narrows the domain, use `source_filter` to focus search, for example `风险`, `会议纪要`, `架构`, `代码`, or `需求`. Prefer narrowing `source_filter` over increasing `top_k`.

Do not call multiple KB search tools in parallel. The local MCP server is intentionally resource-limited.

If the knowledge base is installed in a nested directory, make sure the active Codex/Kiro workspace has merged MCP config and the MCP `cwd` points at the KB directory.

The knowledge base applies domain-aware ranking boosts and may use a local high-precision BGE reranker in deep mode. Treat ranking signals as retrieval aids, not as proof by themselves.

If search results are insufficient, say so clearly and do not invent missing details.

Call `read_kb_source` only when the returned snippet is not enough, and keep `max_chars` small.

Do not modify, delete, rebuild, or reindex the knowledge base through MCP. The MCP server intentionally exposes only read-only tools.

Return only the minimum sensitive source text needed to answer the user's question.

When answering project questions, explicitly state whether the answer used MCP or CLI fallback.

## Obsidian Curator Rules

When organizing knowledge, client inputs, requirements, capabilities, case studies, delivery assets, commercial risks, or proposal blocks, first search the relevant Project KB source. Use CLI raw-index queries for `sources/` intake and the default MCP curated index for `docs/` knowledge unless the user says otherwise.

When the user says the raw index is complete, proactively start the Obsidian curation flow: check raw index status, inventory `sources/`, query raw KB with `uv run project-kb-query ... --config kb/config.raw.yaml`, draft small Markdown notes under `docs/`, preserve structured `source_refs`, update Maps and Canvas, rebuild the curated index, then explain how to open Obsidian Graph View and `docs/01_Maps/Knowledge_Vault.canvas`.

Before generating or updating Obsidian notes, Maps, or Canvas labels, ask the user which language to use: `中文`, `English`, `日本語`, or follow the source language. If the user has not answered, pause note generation until the language is confirmed.

When generating Obsidian notes, preserve structured `source_refs` with `source_path`, `heading`, `chunk_index`, and available `page_number`, `slide_number`, `sheet_name`, or `cell_range`.

Do not write inferences as facts. Put unsupported content under `Assumptions` or `Evidence Gaps`.

AI-generated Obsidian notes must default to `status: needs_review`.

Do not overwrite files with `status: reviewed` unless the user explicitly asks.

Prefer small, clear Markdown notes over long documents.

Use Obsidian internal links such as `[[Client_A_Requirement_Matrix]]`.

Maintain both Markdown Maps and the visual vault Canvas at `docs/01_Maps/Knowledge_Vault.canvas`.

After each curation pass, report proposed file paths, a short content summary, and questions requiring human confirmation.

Do not use MCP tools to modify, delete, rebuild, or reindex the knowledge base.
