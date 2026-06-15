---
inclusion: always
---

# Project KB MCP Usage

This workspace exposes a read-only local project knowledge base through the MCP server `project-kb`.

For setup, indexing, rebuild, FTS rebuild, and diagnostics, follow README commands and use CLI entrypoints such as `uv run project-kb-ingest` and `uv run project-kb-doctor`. Do not use MCP tools for maintenance.

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
