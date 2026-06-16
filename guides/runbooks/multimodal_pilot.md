# Multimodal Pilot Runbook

This runbook validates the raw visual evidence to curated Markdown workflow on a real project.

## Production Mode

- Use local OCR / local / stub providers by default.
- External vision must be explicitly enabled with `allow_external_vision: true`.
- `sources/` is the raw evidence archive.
- Raw index is an evidence pool, not the official knowledge base.
- `docs/` curated Markdown is the formal human/AI knowledge base.
- LanceDB tables are rebuildable caches, not source of truth.

## 1. Prepare Raw Sources

Place real project files under `sources/`, for example:

```text
sources/10_client_inputs/
sources/20_our_capabilities/
sources/30_case_studies/
```

Recommended pilot set:

- One scanned or image-heavy PDF.
- One PDF with an architecture or workflow diagram.
- One PPTX with embedded screenshots or diagrams.
- One standalone PNG/JPG screenshot.

## 2. Build Raw Index

```bash
uv run project-kb-ingest --config kb/config.raw.yaml --rebuild
uv run project-kb-doctor --config kb/config.raw.yaml
```

Raw multimodal intake saves generated visual assets under:

```text
docs/_attachments/kb_assets/<source_stem>_<source_hash>/
```

## 3. Raw Visual Evidence Search

Use this mode to quickly find original images, architecture diagrams, screenshots, and scanned pages.

These results are raw and unreviewed. Do not use them as formal answers until they are curated.

```bash
uv run project-kb-query "API Gateway 架构图" --config kb/config.raw.yaml --visual-only
uv run project-kb-query "API Gateway 架构图" --config kb/config.raw.yaml --visual-only --json
```

Check each result for:

- `index_role=raw`
- `raw_evidence=true`
- `review_status=unreviewed`
- `source_path`
- `attachment_path`
- `attachment_wikilink`
- `page_number` or `slide_number`
- OCR/caption snippet

Open `attachment_path` in Finder or use `attachment_wikilink` inside the `docs/` Obsidian Vault to inspect the original generated image. `--visual-only` automatically searches a larger visual candidate pool; if it still returns no visual results, rebuild the raw index or try broader terms.

## 4. Export Visual Summary Drafts

Preview first:

```bash
uv run project-kb-curate-visual --config kb/config.raw.yaml --dry-run
```

Export draft notes:

```bash
uv run project-kb-curate-visual --config kb/config.raw.yaml
```

Default output:

```text
docs/_generated/visual_summaries/needs_review/
```

Generated notes include Obsidian embeds such as:

```text
![[_attachments/kb_assets/.../image.png]]
```

The frontmatter still keeps the full project-root-relative `attachment_path`, for example `docs/_attachments/kb_assets/rfp_ab12cd/rfp_ab12cd_p038_page_dpi180_9f8e7d.png`.

## 5. Review Visual Summaries

Open `docs/` as an Obsidian Vault.

For each generated note:

1. Open the embedded image.
2. Compare OCR/caption against the original source.
3. Remove unsupported claims.
4. Add `Assumption` or `Evidence Gap` where needed.
5. Change both fields only after review:

```yaml
review_status: reviewed
status: reviewed
```

Use `approved` instead of `reviewed` only when evidence has already been confirmed:

```yaml
review_status: approved
status: approved
```

Notes with `review_status: needs_review` do not enter the default curated index.
Notes with `searchable: false` do not enter vector search even if reviewed.

## 6. Build Curated Index

```bash
uv run project-kb-ingest --config kb/config.yaml --rebuild
uv run project-kb-doctor --config kb/config.yaml
```

The curated scanner does not recursively index `docs/_attachments/`.
Reviewed Markdown visual summaries are indexed; image files themselves are not the source of truth.

## 7. Curated Visual Knowledge Search

Use the curated index for normal project answers:

```bash
uv run project-kb-query "API Gateway 架构图的关键组件是什么？" --config kb/config.yaml
uv run project-kb-query "API Gateway 架构图的关键组件是什么？" --config kb/config.yaml --json
```

In MCP sessions, use `search_project_kb_fast` first.

For a visual result, use `read_kb_result` to read the Markdown summary and attachment path. Prefer `read_kb_result` over `read_kb_source` for visual evidence.

## 8. Smoke Evaluation

Copy and edit the example query set:

```bash
cp examples/evaluation/multimodal_queries.example.yaml kb/cache/evaluation/multimodal_queries.yaml
```

Run:

```bash
uv run project-kb-evaluate-visual \
  --config kb/config.yaml \
  --queries kb/cache/evaluation/multimodal_queries.yaml
```

Save a JSON report:

```bash
uv run project-kb-evaluate-visual \
  --config kb/config.yaml \
  --queries kb/cache/evaluation/multimodal_queries.yaml \
  --output
```

## Evaluation Checklist

- [ ] Scanned PDF pages produce OCR text.
- [ ] Architecture diagram pages generate page render assets.
- [ ] PPTX embedded images generate visual summaries.
- [ ] `docs/_attachments/kb_assets/` is not directly bulk-indexed by curated ingest.
- [ ] `needs_review` visual summaries do not appear in curated search.
- [ ] `reviewed` or `approved` visual summaries can be retrieved by MCP / curated query.
- [ ] `read_kb_result` returns `attachment_path` and original `source_path`.
- [ ] External vision is disabled by default and does not upload images.
- [ ] When external vision is enabled, only resized/compressed copies are uploaded.
- [ ] Large files respect `max_rendered_pages_per_file` and `max_visual_assets_per_file`.

## Pilot Pass Criteria

- Raw visual search can find the expected diagrams or screenshots.
- At least one visual summary is reviewed and searchable through the curated index.
- Query results include traceable `source_path`, page/slide, and `attachment_path`.
- No raw unreviewed evidence appears in default curated answers.
