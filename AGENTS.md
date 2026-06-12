# Project Knowledge Base Instructions

This project exposes a local read-only knowledge base through the MCP server `project-kb`.

When answering questions about project documents, requirements, meeting notes, design decisions, risks, owners, milestones, or historical context, call `search_project_kb` first.

When using retrieved knowledge, cite `source_path`, `heading`, and `chunk_index`. When available, also cite `page_number`, `slide_number`, `sheet_name`, and `cell_range`.

Use top-k citations together; do not rely only on the first result when answering project questions. Prefer `top_k >= 5` for project-context answers.

When the user clearly narrows the domain, use `source_filter` to focus search, for example `风险`, `会议纪要`, `架构`, `代码`, or `需求`.

The knowledge base applies domain-aware ranking boosts and may use a local high-precision BGE reranker. Treat ranking signals as retrieval aids, not as proof by themselves.

If search results are insufficient, say so clearly and do not invent missing details.

Call `read_kb_source` only when the snippet is not enough to answer the question.

Do not use MCP tools to modify, delete, rebuild, or reindex the knowledge base or project files.

Return the minimum sensitive source text needed to answer the user's question.
