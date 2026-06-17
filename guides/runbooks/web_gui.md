# Project KB Studio Runbook

Project KB Studio is a local browser interface for the Project KB workflow. It is a control console plus evidence-first Chat. Obsidian remains the main space for long-form note editing, backlinks, Canvas, Graph View, and reviewed knowledge work.

## Start Studio

```bash
uv run project-kb-studio
```

Open:

```text
http://127.0.0.1:8765
```

Optional:

```bash
uv run project-kb-studio --no-browser
uv run project-kb-studio --port 8766
```

Studio stores local state in `.project-kb/`. This directory is ignored by Git.

## Upload Sources

1. Open Sources.
2. Drag files into the upload area or choose files.
3. Files are written only to `sources/`.
4. Same-name files are renamed automatically.
5. ZIP extraction is not supported in the MVP.

Use sanitized example names such as `Sample RFP` and `sample_proposal.pdf`.

## Import

Click `Import Sources` from Home or Sources. Studio starts a background job that runs the raw source ingest against `kb/config.raw.yaml`.

Only one heavy job can run at a time. Heavy jobs include import, OCR, curate, publish, and rebuild index.

Curate is not implemented in this MVP. Studio shows the Curate action as unavailable instead of creating a job that is known to fail. Use the CLI or an agent-based curator until a supported curation adapter is added.

## Chat

Open Chat and ask a question.

Default settings:

- Knowledge source: Reviewed Docs
- Mode: Fast
- Provider: Local only / disabled external

If there is no answer engine, Studio uses Evidence Search Mode. It returns evidence snippets, source references, and related notes. It does not generate a summary answer in this mode.

If Raw Sources or Both is selected, Studio warns that raw content may not be human-reviewed.

## Review

Open Review to scan Markdown frontmatter in `docs/`. Frontmatter is the source of truth.

Canonical status values:

```yaml
status: needs_review | reviewed | evidence_gap | possible_duplicate
```

Approval rules:

- Only `needs_review` can be approved.
- Notes without `source_refs` cannot be approved unless the user explicitly overrides.
- Overrides are recorded in `review_warnings`.
- Reviewed notes are not overwritten automatically.

For long editing, use Open in Obsidian.

## Publish

Open Publish to preview what will be published.

Default rules:

- Publish only `status=reviewed` or `status=approved`.
- Skip `needs_review`.
- Skip `evidence_gap`.
- Skip `possible_duplicate`.
- Skip Markdown files that do not have a review status in frontmatter.
- Warn on reviewed notes missing `source_refs`.

Click `Publish Reviewed Docs` to rebuild the curated agent index. The ingest layer enforces the same reviewed-only filter as the preview. Studio writes a report to `.project-kb/jobs/<job_id>/publish_report.json` after the job finishes.

## Configure Codex and Kiro

Open Agent Hub.

Before writing config, Studio:

1. Detects existing config.
2. Shows a preview or diff.
3. Creates a timestamped backup.
4. Merges only the `project-kb` MCP server block.
5. Writes only after confirmation.

Reload Codex or Kiro after changing MCP config.

## Jobs and Logs

Open Jobs to inspect queued, running, succeeded, failed, cancelled, or interrupted jobs.

Each job records:

- stdout
- stderr
- exit code
- created time
- start time
- finish time
- duration

MVP does not force-kill running jobs.

## Settings

Studio separates interface language from content language:

- UI Language: Chinese, Japanese, English, or Follow browser.
- Content Language: Chinese, Japanese, English, or Follow source language.

Source refs, filenames, and original snippets are never translated.

The default profile is `balanced`. Studio reads profile from `kb/config.yaml`; if the config has no profile, the UI falls back to `balanced`.

External LLM mode is disabled by default. If enabled, only necessary retrieved context is sent to the configured endpoint.

## Troubleshooting

- No Chat evidence: import sources or publish reviewed docs first.
- Codex/Kiro cannot connect: check Agent Hub status, then reload the external agent.
- Publish skips notes: check note frontmatter status and source refs.
- Upload fails: check filename, file size, and whether `sources/` resolves inside the project root.

## Privacy

Project KB Studio is local-first:

- Default host is `127.0.0.1`.
- No cloud upload is performed by default.
- Write APIs use CSRF and same-origin checks.
- Logs should not contain full sensitive project text.
- Real customer, bank, company, or project names must not be used in examples, tests, screenshots, or seed data.
