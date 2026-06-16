from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any

from rich.console import Console
from tqdm import tqdm

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kb.chunking import chunk_parsed_document
from kb.embeddings import BGEEmbedder
from kb.parsers import parse_file
from kb.multimodal.manifest import MultimodalManifest
from kb.store import (
    STORE_SCHEMA_VERSION,
    LanceDBStore,
    extracted_cache_path,
    load_config,
    load_manifest,
    save_manifest,
    utc_now_iso,
)


DEFAULT_EXCLUDED_DIR_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".lancedb",
    ".kb_cache",
    ".codex",
}

DEFAULT_IGNORED_FILE_NAMES = {
    ".gitkeep",
    ".keep",
    ".placeholder",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Index project documents into local LanceDB.")
    parser.add_argument("--config", default=None, help="Path to kb/config.yaml")
    parser.add_argument("--rebuild", action="store_true", help="Delete and rebuild the local index")
    parser.add_argument("--rebuild-fts", action="store_true", help="Rebuild the full-text search index")
    args = parser.parse_args()

    console = Console()
    index_project(args.config, rebuild=args.rebuild, rebuild_fts=args.rebuild_fts, console=console)


def index_project(
    config_path: str | Path | None = None,
    *,
    rebuild: bool = False,
    rebuild_fts: bool = False,
    console: Console | None = None,
) -> dict[str, Any]:
    console = console or Console()
    started = time.perf_counter()
    cfg = load_config(config_path)
    store = LanceDBStore(cfg)

    if rebuild:
        if cfg.db_path.exists():
            shutil.rmtree(cfg.db_path)
        store = LanceDBStore(cfg)

    if store.table_exists() and store.detect_schema_version() < STORE_SCHEMA_VERSION:
        raise RuntimeError(
            "Existing LanceDB table uses an older schema. Run "
            "`uv run project-kb-ingest --config kb/config.yaml --rebuild` "
            "to rebuild the local index with the v3 schema."
        )

    manifest = {"version": STORE_SCHEMA_VERSION, "files": {}} if rebuild else load_manifest(cfg)
    if int(manifest.get("version", 1)) < STORE_SCHEMA_VERSION and manifest.get("files"):
        raise RuntimeError(
            "Existing manifest uses an older schema. Run "
            "`uv run project-kb-ingest --config kb/config.yaml --rebuild` "
            "to rebuild the local index with the v3 manifest."
        )
    manifest["version"] = STORE_SCHEMA_VERSION
    store.open_or_create_table()
    files = discover_files(cfg)
    stale_summary = None
    if cfg.parsing.multimodal.enabled:
        stale_summary = MultimodalManifest(cfg).mark_stale_missing_sources()

    indexed_files = 0
    skipped_files = 0
    warning_count = 0
    chunk_count = 0
    visual_chunk_count = 0
    embedder = None

    if not files:
        console.print("[yellow]No source files found. Add documents under docs/ or update kb/config.yaml.[/yellow]")

    total_files = len(files)
    for file_index, path in enumerate(tqdm(files, desc="Indexing files", unit="file"), start=1):
        rel_path = path.relative_to(cfg.root_path).as_posix()
        console.print(f"[cyan]Indexing file {file_index}/{total_files}:[/cyan] {rel_path}")
        sha256 = file_sha256(path)
        modified_time = path.stat().st_mtime
        previous = manifest.get("files", {}).get(rel_path)
        if previous and previous.get("sha256") == sha256:
            skipped_files += 1
            continue

        parsed = parse_file(path, config=cfg)
        if parsed is None or not parsed.text.strip():
            skipped_files += 1
            warning_count += len(parsed.warnings if parsed else [])
            for warning in parsed.warnings if parsed else []:
                console.print(f"[yellow]{warning}[/yellow]")
            continue

        write_extracted_cache(cfg, sha256, parsed.text)

        for warning in parsed.warnings:
            warning_count += 1
            console.print(f"[yellow]{warning}[/yellow]")

        chunks = chunk_parsed_document(
            parsed,
            file_ext=path.suffix.lower(),
            chunk_size=cfg.chunking.chunk_size,
            chunk_overlap=cfg.chunking.chunk_overlap,
        )
        if not chunks:
            skipped_files += 1
            continue
        visual_chunks = [chunk for chunk in chunks if _metadata_value(chunk, "asset_type") == "visual"]

        if embedder is None:
            embedder = build_embedder(cfg)
        vectors = embed_chunks(embedder, chunks, rel_path=rel_path)
        indexed_at = utc_now_iso()
        rows = [
            row_for_chunk(
                chunk=chunk,
                vector=vector,
                source_path=rel_path,
                file_path=path,
                sha256=sha256,
                modified_time=modified_time,
                indexed_at=indexed_at,
            )
            for chunk, vector in zip(chunks, vectors)
        ]
        store.delete_sources([rel_path])
        store.add_rows(rows)

        manifest.setdefault("files", {})[rel_path] = {
            "sha256": sha256,
            "modified_time": modified_time,
            "chunk_count": len(rows),
            "extracted_cache_path": _display_path(extracted_cache_path(cfg, sha256), cfg.root_path),
            "indexed_at": indexed_at,
        }
        indexed_files += 1
        chunk_count += len(rows)
        visual_chunk_count += len(visual_chunks)

    fts_warning = store.ensure_fts_index(replace=bool(rebuild or rebuild_fts))
    if fts_warning:
        warning_count += 1
        console.print(f"[yellow]{fts_warning}[/yellow]")
    save_manifest(cfg, manifest)
    if cfg.parsing.multimodal.enabled:
        MultimodalManifest(cfg).append_ingest_run(
            {
                "config_path": str(config_path or ""),
                "rebuild": bool(rebuild),
                "indexed_files": indexed_files,
                "chunks": chunk_count,
                "searchable_visual_chunks": visual_chunk_count,
                "stale_summary": stale_summary,
                "warnings": warning_count,
            }
        )

    elapsed = time.perf_counter() - started
    console.print(
        f"[green]Done.[/green] indexed_files={indexed_files} chunks={chunk_count} "
        f"visual_chunks={visual_chunk_count} skipped_files={skipped_files} "
        f"warnings={warning_count} elapsed={elapsed:.1f}s"
    )
    return {
        "indexed_files": indexed_files,
        "chunks": chunk_count,
        "visual_chunks": visual_chunk_count,
        "skipped_files": skipped_files,
        "warnings": warning_count,
        "elapsed": elapsed,
    }


def build_embedder(cfg):
    return BGEEmbedder(
        cfg.embedding.model_name,
        batch_size=cfg.embedding.batch_size,
        device=cfg.embedding.device,
        use_fp16=cfg.embedding.use_fp16,
    )


def embed_chunks(embedder, chunks, *, rel_path: str) -> list[list[float]]:
    batch_size = max(1, int(getattr(embedder, "batch_size", len(chunks)) or len(chunks)))
    vectors: list[list[float]] = []
    with tqdm(total=len(chunks), desc=f"Embedding {Path(rel_path).name}", unit="chunk", leave=False) as progress:
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            vectors.extend(embedder.embed_texts([chunk.text for chunk in batch]))
            progress.update(len(batch))
            progress.set_postfix_str(f"{min(start + len(batch), len(chunks))}/{len(chunks)}")
    return vectors


def discover_files(cfg) -> list[Path]:
    files: list[Path] = []
    for source_dir in cfg.scan.source_dirs:
        root = (cfg.root_path / source_dir).resolve()
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if should_include(path, cfg.root_path, cfg.scan.include_patterns, cfg.scan.exclude_patterns, cfg=cfg):
                files.append(path)
    return sorted(files)


def should_include(
    path: Path,
    project_root: Path,
    include_patterns: list[str],
    exclude_patterns: list[str],
    cfg=None,
) -> bool:
    rel = path.relative_to(project_root).as_posix()
    if path.name in DEFAULT_IGNORED_FILE_NAMES:
        return False
    if any(part in DEFAULT_EXCLUDED_DIR_NAMES for part in path.relative_to(project_root).parts):
        return False
    if any(fnmatch.fnmatch(rel, pattern) for pattern in exclude_patterns):
        if not _reviewed_generated_visual_summary(path, cfg):
            return False
    return any(fnmatch.fnmatch(rel, pattern) for pattern in include_patterns)


def _reviewed_generated_visual_summary(path: Path, cfg) -> bool:
    if cfg is None or path.suffix.lower() != ".md":
        return False
    try:
        rel = path.relative_to(cfg.root_path).as_posix()
    except Exception:
        return False
    if "docs/_generated/" not in rel or "/needs_review/" not in rel:
        return False
    try:
        import yaml

        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.startswith("---\n"):
            return False
        _, yaml_text, _ = text.split("---", 2)
        data = yaml.safe_load(yaml_text) or {}
        if data.get("kb_type") != "visual_summary":
            return False
        review_status = str(data.get("review_status") or data.get("status") or "").lower()
        allowed = {str(value).lower() for value in getattr(cfg.curation, "index_review_statuses", ["reviewed", "approved"])}
        return review_status in allowed
    except Exception:
        return False


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_extracted_cache(cfg, sha256: str, text: str) -> Path:
    path = extracted_cache_path(cfg, sha256)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")
    return path


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def row_for_chunk(
    *,
    chunk,
    vector: list[float],
    source_path: str,
    file_path: Path,
    sha256: str,
    modified_time: float,
    indexed_at: str,
) -> dict[str, Any]:
    metadata = {
        "indexed_source_path": source_path,
        "source_path": _metadata_value(chunk, "source_path", source_path),
        "file_name": file_path.name,
        "file_ext": file_path.suffix.lower(),
        "heading": chunk.heading,
        "chunk_id": chunk_id(source_path, sha256, chunk.chunk_index),
        "chunk_index": chunk.chunk_index,
        "start_char": chunk.start_char,
        "end_char": chunk.end_char,
        "parser_name": _metadata_value(chunk, "parser_name", "text"),
        "source_format": _metadata_value(chunk, "source_format", file_path.suffix.lower()),
        "page_number": _metadata_value(chunk, "page_number"),
        "slide_number": _metadata_value(chunk, "slide_number"),
        "sheet_name": _metadata_value(chunk, "sheet_name"),
        "row_range": _metadata_value(chunk, "row_range"),
        "cell_range": _metadata_value(chunk, "cell_range"),
        "ocr_used": bool(_metadata_value(chunk, "ocr_used", False)),
        "ocr_confidence": _metadata_value(chunk, "ocr_confidence"),
        "extraction_method": _metadata_value(chunk, "extraction_method", "text"),
        "asset_type": _metadata_value(chunk, "asset_type", "document"),
        "asset_id": _metadata_value(chunk, "asset_id"),
        "occurrence_id": _metadata_value(chunk, "occurrence_id"),
        "attachment_path": _metadata_value(chunk, "attachment_path"),
        "visual_type": _metadata_value(chunk, "visual_type"),
        "image_hash": _metadata_value(chunk, "image_hash"),
        "caption_provider": _metadata_value(chunk, "caption_provider"),
        "caption_model": _metadata_value(chunk, "caption_model"),
        "prompt_version": _metadata_value(chunk, "prompt_version"),
        "searchable": _metadata_value(chunk, "searchable", True),
        "confidence": _metadata_value(chunk, "confidence"),
        "section_index": _metadata_value(chunk, "section_index"),
        "sha256": sha256,
        "modified_time": modified_time,
        "indexed_at": indexed_at,
    }
    for key, value in (getattr(chunk, "metadata", None) or {}).items():
        metadata.setdefault(key, value)
    return {
        "id": metadata["chunk_id"],
        "text": chunk.text,
        "vector": vector,
        "indexed_source_path": source_path,
        "source_path": metadata["source_path"],
        "file_name": file_path.name,
        "heading": chunk.heading or "",
        "chunk_index": chunk.chunk_index,
        "parser_name": metadata["parser_name"] or "",
        "source_format": metadata["source_format"] or file_path.suffix.lower(),
        "page_number": metadata["page_number"],
        "slide_number": metadata["slide_number"],
        "sheet_name": metadata["sheet_name"] or "",
        "row_range": metadata["row_range"] or "",
        "cell_range": metadata["cell_range"] or "",
        "ocr_used": bool(metadata["ocr_used"]),
        "ocr_confidence": metadata["ocr_confidence"],
        "extraction_method": metadata["extraction_method"] or "",
        "asset_type": metadata["asset_type"] or "",
        "asset_id": metadata["asset_id"] or "",
        "occurrence_id": metadata["occurrence_id"] or "",
        "attachment_path": metadata["attachment_path"] or "",
        "visual_type": metadata["visual_type"] or "",
        "image_hash": metadata["image_hash"] or "",
        "caption_provider": metadata["caption_provider"] or "",
        "caption_model": metadata["caption_model"] or "",
        "prompt_version": metadata["prompt_version"] or "",
        "searchable": bool(metadata["searchable"]),
        "confidence": metadata["confidence"],
        "sha256": sha256,
        "modified_time": modified_time,
        "indexed_at": indexed_at,
        "metadata_json": json.dumps(metadata, ensure_ascii=False),
    }


def chunk_id(source_path: str, sha256: str, chunk_index: int) -> str:
    raw = f"{source_path}:{sha256}:{chunk_index}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _metadata_value(chunk, key: str, default=None):
    metadata = getattr(chunk, "metadata", None) or {}
    return metadata.get(key, default)


if __name__ == "__main__":
    main()
