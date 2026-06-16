# Project KB Obsidian Template

This template creates a local, read-only Project KB with two layers:

- `sources/`: original evidence archive. Never rewrite or delete original source files.
- `docs/`: curated knowledge source for day-to-day human and AI work. This is also the Obsidian Vault and the default MCP-indexed layer.
- `docs/_attachments/kb_assets/`: generated visual evidence attachments from raw multimodal intake.

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

## First-run Agent Behavior

After installation, Codex or another agent may not see the `project-kb` MCP tools until the workspace/session is reloaded. The agent should check the current session before answering KB questions:

1. Look for `kb_status` and `search_project_kb_fast` in the available tools.
2. If MCP is available, call `kb_status` first and state: `Using MCP`.
3. If `.codex/config.toml` exists but MCP tools are not available, state:

```text
当前会话尚未加载 project-kb MCP。请重新打开 Codex / 重新加载 workspace，或者确认 .codex/config.toml 位于当前 workspace 根目录。
```

4. If the user wants to continue immediately, use CLI only as an explicit fallback and state: `Using CLI fallback`.

Task routing:

- Maintenance tasks use CLI: install, doctor, diagnose, ingest, rebuild, and FTS rebuild.
- Project Q&A uses MCP first: requirements, risks, meeting notes, historical decisions, owners, and milestones.
- Raw intake and Obsidian curation use CLI raw-index queries first, then write curated notes under `docs/`, then rebuild the curated index.

When the user says the raw index is already built, the agent should start curation instead of only explaining the process:

1. Ask which language to use for generated notes, Maps, and Canvas labels: `中文`, `English`, `日本語`, or `follow source language`.
2. Check raw index status with `uv run project-kb-doctor --config kb/config.raw.yaml`.
3. Inventory files under `sources/`.
4. Query raw KB with `uv run project-kb-query ... --config kb/config.raw.yaml`.
5. Generate small Markdown notes under `docs/` with structured `source_refs`.
6. Update Markdown Maps and `docs/01_Maps/Knowledge_Vault.canvas`.
7. Rebuild curated index with `uv run project-kb-ingest --config kb/config.yaml --rebuild`.
8. Tell the user how to open `docs/` in Obsidian and inspect Graph View / Canvas.

## Common Commands

Raw index:

```bash
uv run project-kb-ingest --config kb/config.raw.yaml --rebuild
uv run project-kb-query "客户A有哪些核心需求？" --config kb/config.raw.yaml
uv run project-kb-curate-visual --config kb/config.raw.yaml
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
- Open `docs/01_Maps/Knowledge_Vault.canvas` for the visual vault overview.
- Use Obsidian Graph View for link-level exploration after curated notes are generated.
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
- Ask for the output language before generating notes, Maps, or Canvas labels.
- Keep directory names stable; localize visible headings and body text according to the selected language.
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

## Multimodal-aware RAG

This template is multimodal-aware, but the first phase is not native image-vector search. Images, rendered PDF pages, and Office embedded images are converted into OCR/caption-backed Markdown or visual chunks, then searched through the existing text vector field.

Default boundary:

- Raw index uses `kb/config.raw.yaml` and can extract visual evidence from `sources/`.
- Curated index uses `kb/config.yaml`, stays Markdown-first, and remains the default MCP target.
- Raw visual evidence does not automatically become trusted curated knowledge.
- To make visual evidence available to daily MCP answers, export it as visual summary Markdown, review it, then rebuild the curated index.

Recommended visual workflow:

```bash
uv run project-kb-ingest --config kb/config.raw.yaml --rebuild
uv run project-kb-query "architecture diagram AWS VPC" --config kb/config.raw.yaml
uv run project-kb-curate-visual --config kb/config.raw.yaml
# review generated notes, then set review_status: reviewed or move them to a formal docs/ folder
uv run project-kb-ingest --config kb/config.yaml --rebuild
```

`project-kb-curate-visual` writes Obsidian notes to:

```text
docs/_generated/visual_summaries/needs_review/
```

Each note embeds the generated asset with Obsidian syntax, keeps structured `source_refs`, and includes source path, page/slide, attachment path, image hash, caption provider, prompt version, OCR text, caption, entities, relationships, architecture notes, and uncertain items.

Generated notes default to `review_status: needs_review` and are excluded from the default curated index. After review, set `review_status: reviewed` / `status: reviewed` or move the note into a formal `docs/` folder, then rebuild the curated index.

Visual assets are saved under:

```text
docs/_attachments/kb_assets/<source_stem>_<source_hash>/
```

The curated scanner does not recursively index `docs/_attachments/`. If you need opt-in attachment parsing, set:

```yaml
parsing:
  multimodal:
    enabled: true
    curated_attachments:
      mode: "referenced_only"
      allowed_roots:
        - "docs/_attachments/kb_assets"
```

This parses only images explicitly referenced by Markdown notes, not the whole attachments folder.

Privacy defaults:

- External vision providers are disabled by default.
- `allow_external_vision: false` prevents image upload even if API keys exist.
- Default raw intake uses conservative limits such as `render_pages: auto`, `max_rendered_pages_per_file`, `max_visual_assets_per_file`, `max_image_pixels`, and icon/logo skipping.
- OCR can read text inside images, but it does not replace visual understanding. Caption quality depends on the configured provider.

Configuration examples:

Low-resource text only:

```yaml
parsing:
  multimodal:
    enabled: false
```

Raw multimodal intake:

```yaml
database:
  index_role: "raw"
parsing:
  multimodal:
    enabled: true
    pdf:
      render_pages: "auto"
      max_rendered_pages_per_file: 30
    vision:
      provider: "ocr_only"
      allow_external_vision: false
```

External vision opt-in:

```yaml
parsing:
  multimodal:
    vision:
      enabled: true
      provider: "openai_compatible"
      model: "<vision_model_name>"
      base_url: null
      api_key_env: "OPENAI_API_KEY"
      # Images are uploaded only when this is true.
      allow_external_vision: true
```

## MCP Tools

```text
search_project_kb_fast   Default tool, top_k=5, candidate_k=20, RRF, no full text
search_project_kb        Fast alias for compatibility
search_project_kb_deep   Deep tool, top_k=8, candidate_k=50, BGE cross-encoder, no full text
read_kb_source           Reads bounded cached/source text only when snippets are insufficient
read_kb_result           Reads a specific result; visual results return Markdown summary and attachment_path
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

Raw index supports `pdf`, `pptx`, `xlsx`, `docx`, `png`, `jpg`, `jpeg`, `webp`, `md`, `txt`, `csv`, `json`, `yaml`, `yml`, `py`, `ts`, `tsx`, `js`, `sql`, `toml`, and `ini`.

Curated index is intentionally Markdown-first and indexes `md`, `csv`, `yaml`, and `yml` under `docs/`.
