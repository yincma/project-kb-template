from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any

from rich.console import Console

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kb.multimodal.manifest import MultimodalManifest
from kb.store import load_config


DEFAULT_OUTPUT_DIR = "docs/_generated/visual_summaries/needs_review"


def main() -> None:
    parser = argparse.ArgumentParser(description="Export raw visual evidence as Obsidian visual summary notes.")
    parser.add_argument("--config", default="kb/config.raw.yaml", help="Raw KB config path")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--review-status", choices=["needs_review", "reviewed", "approved"], default="needs_review")
    parser.add_argument("--source-filter", default=None)
    parser.add_argument("--visual-type", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--only-searchable",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Export only searchable visual evidence by default; use --no-only-searchable for audit notes.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action=argparse.BooleanOptionalAction, default=False, help="Overwrite existing generated notes")
    parser.add_argument("--force", action="store_true", help="Deprecated alias for --overwrite")
    args = parser.parse_args()

    console = Console()
    payload = curate_visual_summaries(
        config_path=args.config,
        output_dir=args.output_dir,
        review_status=args.review_status,
        source_filter=args.source_filter,
        visual_type=args.visual_type,
        limit=args.limit,
        only_searchable=args.only_searchable,
        dry_run=args.dry_run,
        overwrite=bool(args.overwrite or args.force),
    )
    console.print(
        f"[green]Done.[/green] exported={payload['exported']} "
        f"skipped_existing={payload['skipped_existing']} "
        f"skipped_not_searchable={payload['skipped_not_searchable']} "
        f"skipped_non_reviewable={payload['skipped_non_reviewable']} "
        f"skipped_no_caption_or_ocr={payload['skipped_no_caption_or_ocr']} "
        f"dry_run={payload['dry_run']} "
        f"output_dir={payload['output_dir']}"
    )
    if args.dry_run:
        for path in payload.get("planned_paths", []):
            console.print(f"[cyan]Would export:[/cyan] {path}")


def curate_visual_summaries(
    *,
    config_path: str | Path = "kb/config.raw.yaml",
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    review_status: str = "needs_review",
    source_filter: str | None = None,
    visual_type: str | None = None,
    limit: int | None = None,
    only_searchable: bool = True,
    dry_run: bool = False,
    overwrite: bool = False,
    force: bool | None = None,
) -> dict[str, Any]:
    if force is not None:
        overwrite = bool(force)
    cfg = load_config(config_path)
    manifest = MultimodalManifest(cfg)
    assets = manifest.load_assets()
    occurrences = manifest.load_occurrences()
    captions = _load_captions(manifest.caption_cache_dir)
    output_root = _resolve_output_dir(cfg, output_dir)
    if not dry_run:
        output_root.mkdir(parents=True, exist_ok=True)

    exported = 0
    skipped_existing = 0
    skipped_not_searchable = 0
    skipped_non_reviewable = 0
    skipped_no_caption_or_ocr = 0
    skipped_filtered = 0
    exported_non_searchable = 0
    seen_sources: set[str] = set()
    seen_assets: set[str] = set()
    seen_occurrences: set[str] = set()
    planned_paths: list[str] = []
    for caption in captions:
        caption_searchable = bool(caption.get("searchable"))
        has_caption_or_ocr = bool(str(caption.get("caption") or "").strip() or str(caption.get("ocr_text") or "").strip())
        if only_searchable and not caption_searchable:
            skipped_not_searchable += 1
            continue
        occurrence = occurrences.get(str(caption.get("occurrence_id")))
        asset = assets.get(str(caption.get("asset_id")))
        if not occurrence or not asset:
            skipped_non_reviewable += 1
            continue
        if source_filter and source_filter not in str(occurrence.get("source_path", "")):
            skipped_filtered += 1
            continue
        if visual_type and visual_type != str(caption.get("visual_type") or ""):
            skipped_filtered += 1
            continue
        if limit is not None and exported >= limit:
            break

        note_path = output_root / _note_file_name(occurrence, caption)
        if note_path.exists() and not overwrite:
            skipped_existing += 1
            continue
        planned_paths.append(_display_path(note_path, cfg.root_path))
        if not dry_run:
            note_path.write_text(_render_note(asset, occurrence, caption, review_status=review_status), encoding="utf-8")
        if not caption_searchable:
            exported_non_searchable += 1
        seen_sources.add(str(occurrence.get("source_path") or ""))
        seen_assets.add(str(asset.get("asset_id") or ""))
        seen_occurrences.add(str(occurrence.get("occurrence_id") or ""))
        exported += 1

    return {
        "exported": exported,
        "skipped": skipped_existing + skipped_not_searchable + skipped_non_reviewable + skipped_filtered,
        "skipped_existing": skipped_existing,
        "skipped_not_searchable": skipped_not_searchable,
        "skipped_non_reviewable": skipped_non_reviewable,
        "skipped_no_caption_or_ocr": skipped_no_caption_or_ocr,
        "skipped_filtered": skipped_filtered,
        "exported_non_searchable": exported_non_searchable,
        "sources_count": len(seen_sources),
        "assets_count": len(seen_assets),
        "occurrences_count": len(seen_occurrences),
        "dry_run": dry_run,
        "output_dir": _display_path(output_root, cfg.root_path),
        "planned_paths": planned_paths,
    }


def _load_captions(caption_cache_dir: Path) -> list[dict[str, Any]]:
    if not caption_cache_dir.exists():
        return []
    rows = []
    for path in sorted(caption_cache_dir.glob("*.json")):
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return rows


def _render_note(
    asset: dict[str, Any],
    occurrence: dict[str, Any],
    caption: dict[str, Any],
    *,
    review_status: str = "needs_review",
) -> str:
    try:
        import yaml
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("PyYAML is required to export visual summary notes.") from exc

    source_path = occurrence.get("source_path", "")
    attachment_path = asset.get("attachment_path", "")
    source_ref = {
        "source_path": source_path,
        "heading": occurrence.get("context_title") or "",
        "chunk_index": None,
        "page_number": occurrence.get("page_number"),
        "slide_number": occurrence.get("slide_number"),
        "sheet_name": occurrence.get("sheet_name"),
        "cell_range": None,
    }
    frontmatter = {
        "kb_type": "visual_summary",
        "review_status": review_status,
        "status": review_status,
        "source_path": source_path,
        "attachment_path": attachment_path,
        "page_number": occurrence.get("page_number"),
        "slide_number": occurrence.get("slide_number"),
        "image_hash": asset.get("image_hash"),
        "asset_id": asset.get("asset_id"),
        "occurrence_id": occurrence.get("occurrence_id"),
        "visual_type": caption.get("visual_type"),
        "caption_provider": caption.get("caption_provider"),
        "caption_model": caption.get("caption_model"),
        "prompt_version": caption.get("prompt_version"),
        "confidence": caption.get("confidence"),
        "searchable": bool(caption.get("searchable")),
        "source_refs": [source_ref],
    }
    page_or_slide = _page_or_slide(occurrence)
    vault_attachment = _vault_attachment_path(attachment_path)
    body = f"""# Visual Summary - {_title_fragment(occurrence)}

![[{vault_attachment}]]

## Source

- Source: `{source_path}`
- Attachment: `{attachment_path}`
- {page_or_slide}
- Searchable: `{bool(caption.get("searchable"))}`

## Context Title

{occurrence.get("context_title") or ""}

## Nearby Text

{occurrence.get("nearby_text") or ""}

## OCR Text

{caption.get("ocr_text") or ""}

## Visual Caption

{caption.get("caption") or ""}

## Key Entities

{_bullets(caption.get("entities") or [])}

## Relationships

{_bullets(caption.get("relationships") or [])}

## Architecture Notes

{_bullets(caption.get("architecture_notes") or [])}

## Uncertain Items

{_bullets(caption.get("uncertain_items") or [])}
"""
    return "---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False) + "---\n\n" + body


def _note_file_name(occurrence: dict[str, Any], caption: dict[str, Any]) -> str:
    source_stem = Path(str(occurrence.get("source_path", "source"))).stem
    location = "source"
    if occurrence.get("page_number"):
        location = f"p{int(occurrence['page_number']):03d}"
    elif occurrence.get("slide_number"):
        location = f"s{int(occurrence['slide_number']):03d}"
    visual_type = caption.get("visual_type") or "visual"
    occurrence_id = str(occurrence.get("occurrence_id", "occ"))[-8:]
    return f"{_safe(source_stem)}_{location}_{_safe(visual_type)}_{occurrence_id}.md"


def _title_fragment(occurrence: dict[str, Any]) -> str:
    source_stem = Path(str(occurrence.get("source_path", "source"))).stem
    return f"{source_stem} {_page_or_slide(occurrence)}".strip()


def _page_or_slide(occurrence: dict[str, Any]) -> str:
    if occurrence.get("page_number"):
        return f"Page: {occurrence['page_number']}"
    if occurrence.get("slide_number"):
        return f"Slide: {occurrence['slide_number']}"
    return "Location: source"


def _vault_attachment_path(path: str) -> str:
    if path.startswith("docs/"):
        return path[len("docs/") :]
    return path


def _bullets(values: list[str]) -> str:
    if not values:
        return "- "
    return "\n".join(f"- {value}" for value in values)


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "visual"


def _resolve_output_dir(cfg, output_dir: str | Path) -> Path:
    path = Path(output_dir)
    return path if path.is_absolute() else cfg.root_path / path


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    main()
