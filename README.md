# Project KB Obsidian Template

This template creates a local, read-only Project KB with two layers:

- `sources/`: original evidence archive. Never rewrite or delete original source files.
- `docs/`: curated knowledge source for day-to-day human and AI work. This is also the Obsidian Vault and the default MCP-indexed layer.

Use `sources/` to verify evidence and `docs/` for reviewed operating knowledge. LanceDB indexes are rebuildable caches.

## Prerequisites

```text
Python 3.11
uv
Git
Network access for first dependency/model download
At least 10GB free disk
```

Install and pin Python:

```bash
uv python install 3.11
uv python pin 3.11
uv sync --extra ocr
uv run project-kb-doctor --config kb/config.yaml
```

## Recommended Workflow

1. Put original files into `sources/`.
2. Build the raw index with `kb/config.raw.yaml`.
3. Query the raw index with CLI and let AI draft Obsidian notes.
4. Human reviewers check the notes and keep approved knowledge in `docs/`.
5. Build the curated index with `kb/config.yaml`.
6. Future Proposal Agent or consulting Agent should query the curated `docs/` index by default.

Default MCP points to the curated index in `kb/config.yaml`. During raw source intake, use CLI queries against `kb/config.raw.yaml`; do not switch the default MCP unless you intentionally want raw-source retrieval in an AI session.

## Common Commands

Raw index:

```bash
uv run project-kb-ingest --config kb/config.raw.yaml --rebuild
uv run project-kb-query "客户A有哪些核心需求？" --config kb/config.raw.yaml
```

Curated index:

```bash
uv run project-kb-ingest --config kb/config.yaml --rebuild
uv run project-kb-query "客户A有哪些核心需求？" --config kb/config.yaml
```

Incremental curated update:

```bash
uv run project-kb-ingest --config kb/config.yaml
```

Diagnostics:

```bash
uv run project-kb-doctor --config kb/config.raw.yaml
uv run project-kb-doctor --config kb/config.yaml
uv run project-kb-diagnose --config kb/config.yaml --deep-reranker-check
```

Module fallback:

```bash
uv run python -m kb.ingest --config kb/config.yaml
uv run python -m kb.query "项目有哪些关键风险？" --config kb/config.yaml
uv run python -m kb.doctor --config kb/config.yaml
```

Avoid `uv run python kb/*.py`; module or console-script entrypoints are more stable.

## Open `docs/` In Obsidian

Open Obsidian and choose `Open folder as vault`, then select this project's `docs/` folder.

Recommended Obsidian usage:

- Keep AI drafts as `status: needs_review`.
- Move reviewed notes to `status: reviewed` only after human confirmation.
- Use internal links such as `[[Client_A_Requirement_Matrix]]`.
- Keep attachments in `docs/_attachments/`; this path is excluded from the curated index.
- Use `docs/99_Inbox/` for temporary drafts; this path is excluded from the curated index.

## Obsidian Curator

The `obsidian_curator/` directory contains a single-agent curation skill, prompts, and examples for converting raw evidence into Obsidian notes.

Curator rules:

- Do not overwrite raw files in `sources/`.
- Do not overwrite `status: reviewed` notes unless explicitly requested.
- AI-generated notes default to `status: needs_review`.
- Every factual note must keep structured `source_refs`.
- Unsupported statements belong under `Assumptions` or `Evidence Gaps`.
- Generate small, linked Markdown notes rather than long reports.
- Do not implement Proposal Agent or consulting Agent behavior in this layer.

Structured `source_refs` format:

```yaml
source_refs:
  - source_path: "sources/10_client_inputs/example.pdf"
    heading: "Page 2"
    chunk_index: 3
    page_number: 2
    slide_number:
    sheet_name:
    cell_range:
```

## MCP Tools

```text
search_project_kb_fast   Default tool, top_k=5, candidate_k=20, RRF, no full text
search_project_kb        Fast alias for compatibility
search_project_kb_deep   Deep tool, top_k=8, candidate_k=50, BGE cross-encoder, no full text
read_kb_source           Reads bounded cached/source text only when snippets are insufficient
kb_status
```

Default MCP config uses:

```json
["run", "python", "-m", "kb.mcp_server"]
```

If this KB template is installed in a nested folder but Codex/Kiro opens the parent project, copy or merge `.codex` and `.kiro` MCP config into the parent project and set MCP `cwd` to this KB folder.

## Profiles

```bash
uv run project-kb-profile set lite
uv run project-kb-profile set balanced
uv run project-kb-profile set accurate
```

- `lite`: `sentence-transformers/all-MiniLM-L6-v2`, 384d, RRF, no OCR
- `balanced`: `BAAI/bge-m3`, 1024d, RRF, no cross-encoder
- `accurate`: `BAAI/bge-m3`, BGE cross-encoder, deep mode

Switching embedding dimensions requires rebuild:

```bash
uv run project-kb-ingest --config kb/config.yaml --rebuild
```

## Performance Notes

Default settings limit single-process query concurrency and math-library threads:

```text
max_concurrent_queries=1
TOKENIZERS_PARALLELISM=false
OMP_NUM_THREADS=1
MKL_NUM_THREADS=1
OPENBLAS_NUM_THREADS=1
VECLIB_MAXIMUM_THREADS=1
NUMEXPR_NUM_THREADS=1
```

stdio MCP locks only work inside one MCP process. Multiple clients may start multiple MCP processes and load models more than once.

## Supported Formats

Raw index supports `pdf`, `pptx`, `xlsx`, `docx`, `md`, `txt`, `csv`, `json`, `yaml`, `yml`, `py`, `ts`, `tsx`, `js`, `sql`, `toml`, and `ini`.

Curated index is intentionally Markdown-first and indexes `md`, `csv`, `yaml`, and `yml` under `docs/`.
