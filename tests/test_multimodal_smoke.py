from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw
import yaml

from kb.mcp_server import clear_runtime_cache, read_kb_result
from kb.multimodal.manifest import MultimodalManifest
from kb.store import LanceDBStore, load_config


ROOT = Path(__file__).resolve().parents[1]


def test_multimodal_cli_smoke_raw_to_curated_visual_search(tmp_path: Path, monkeypatch):
    raw_config = _write_config(tmp_path, raw=True)
    curated_config = _write_config(tmp_path, raw=False)
    _write_architecture_pdf(tmp_path / "sources" / "architecture.pdf")
    _write_png(tmp_path / "sources" / "screenshot.png", "AWS SCREENSHOT")
    env = {
        **os.environ,
        "PROJECT_KB_TEST_FAKE_EMBEDDINGS": "1",
        "PROJECT_KB_TEST_FAKE_EMBEDDING_DIM": "3",
    }

    _run_cli(["uv", "run", "project-kb-ingest", "--config", str(raw_config), "--rebuild"], env=env)
    raw_cfg = load_config(raw_config)
    raw_store = LanceDBStore(raw_cfg)
    raw_rows_before = raw_store.count_rows()
    raw_manifest = MultimodalManifest(raw_cfg)
    assert raw_rows_before and raw_rows_before >= 1
    assert raw_manifest.load_assets()
    assert raw_manifest.load_occurrences()

    first_export = _run_cli(["uv", "run", "project-kb-curate-visual", "--config", str(raw_config)], env=env)
    second_export = _run_cli(["uv", "run", "project-kb-curate-visual", "--config", str(raw_config)], env=env)
    assert "exported=" in first_export.stdout
    assert "skipped_existing=" in second_export.stdout

    _run_cli(["uv", "run", "project-kb-ingest", "--config", str(curated_config), "--rebuild"], env=env)
    curated_cfg = load_config(curated_config)
    assert LanceDBStore(curated_cfg).count_rows() == 0

    generated_notes = list((tmp_path / "docs" / "_generated" / "visual_summaries" / "needs_review").glob("*.md"))
    assert generated_notes
    for note in generated_notes:
        note.write_text(
            note.read_text(encoding="utf-8")
            .replace("review_status: needs_review", "review_status: reviewed")
            .replace("status: needs_review", "status: reviewed"),
            encoding="utf-8",
        )

    _run_cli(["uv", "run", "project-kb-ingest", "--config", str(curated_config), "--rebuild"], env=env)
    query = _run_cli(
        ["uv", "run", "project-kb-query", "AWS architecture OCRTERM", "--config", str(curated_config), "--json"],
        env=env,
    )
    query_table = _run_cli(
        ["uv", "run", "project-kb-query", "AWS architecture OCRTERM", "--config", str(curated_config)],
        env=env,
    )
    payload = json.loads(query.stdout)
    result = payload["results"][0]
    assert result["asset_type"] == "visual"
    assert "architecture" in result["snippet"].lower() or "OCRTERM" in result["snippet"]
    assert result["attachment_path"].startswith("docs/_attachments/kb_assets/")
    assert result["indexed_source_path"]
    assert result["source_path"].startswith("sources/")
    assert result["page_number"] or result["attachment_path"]
    assert "visual_type=" in query_table.stdout
    assert "attachment=" in query_table.stdout

    monkeypatch.setenv("KB_CONFIG", str(curated_config))
    clear_runtime_cache()
    read_payload = read_kb_result(
        chunk_id=result["chunk_id"],
        source_path=result["source_path"],
        indexed_source_path=result["indexed_source_path"],
        asset_type="visual",
    )
    assert "Visual Summary" in read_payload["text"]
    assert read_payload["attachment_path"] == result["attachment_path"]

    _run_cli(["uv", "run", "project-kb-ingest", "--config", str(raw_config)], env=env)
    assert LanceDBStore(raw_cfg).count_rows() == raw_rows_before


def _run_cli(args: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, env=env, text=True, capture_output=True, check=True, timeout=120)


def _write_config(tmp_path: Path, *, raw: bool) -> Path:
    config_path = tmp_path / "kb" / ("config.raw.yaml" if raw else "config.yaml")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "project_root": ".",
        "path_base": "config_dir",
        "database": {
            "db_path": ".lancedb_raw" if raw else ".lancedb",
            "table_name": "project_kb_raw" if raw else "project_kb",
            "manifest_path": ".lancedb_raw/manifest.json" if raw else ".lancedb/manifest.json",
            "vector_dimension": 3,
            "extracted_cache_dir": ".kb_cache_raw/extracted" if raw else ".kb_cache/extracted",
            "multimodal_cache_dir": ".kb_cache_raw/multimodal" if raw else ".kb_cache/multimodal",
            "index_role": "raw" if raw else "curated",
        },
        "scan": {
            "source_dirs": ["sources"] if raw else ["docs"],
            "include_patterns": ["**/*.pdf", "**/*.png"] if raw else ["**/*.md"],
            "exclude_patterns": [
                "docs/_attachments/**",
                "docs/_templates/**",
                "docs/99_Inbox/**",
                "docs/_generated/visual_summaries/needs_review/**",
                "docs/_generated/**/needs_review/**",
            ],
        },
        "chunking": {"chunk_size": 1000, "chunk_overlap": 120},
        "embedding": {"model_name": "fake", "batch_size": 8},
        "parsing": {
            "ocr": {"enabled": False, "engine": "rapidocr"},
            "office": {"extract_images": False, "extract_notes": True},
            "multimodal": {
                "enabled": raw,
                "attachments_dir": "docs/_attachments/kb_assets",
                "pdf": {
                    "extract_embedded_images": True,
                    "render_pages": "auto" if raw else "off",
                    "render_dpi": 180,
                    "min_drawing_count_for_render": 1,
                },
                "images": {
                    "min_image_width": 1,
                    "min_image_height": 1,
                    "skip_small_icons": False,
                    "skip_near_blank_images": False,
                    "skip_logo_like_images": False,
                    "max_image_pixels": 16000000,
                },
                "vision": {
                    "enabled": True,
                    "provider": "local",
                    "allow_external_vision": False,
                    "prompt_version": "vision-caption-v1",
                    "max_upload_pixels": 4000000,
                    "max_upload_bytes": 5000000,
                    "resize_long_edge": 1600,
                    "jpeg_quality": 85,
                    "timeout": 60,
                    "max_retries": 1,
                },
                "curated_attachments": {"mode": "off", "allowed_roots": ["docs/_attachments/kb_assets"]},
            },
        },
        "retrieval": {"mode": "hybrid", "top_k": 5, "candidate_k": 20, "max_concurrent_queries": 1},
        "curation": {
            "index_review_statuses": ["reviewed", "approved"],
            "skip_needs_review": True,
            "index_non_searchable_visual_summaries": False,
        },
    }
    config_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return config_path


def _write_png(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (320, 220), "white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 90), text, fill="black")
    output = BytesIO()
    image.save(output, format="PNG")
    path.write_bytes(output.getvalue())


def _write_architecture_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import fitz

    document = fitz.open()
    page = document.new_page(width=400, height=300)
    page.insert_text((40, 40), "AWS architecture OCRTERM VPC RDS", fontsize=14)
    page.draw_rect(fitz.Rect(50, 90, 160, 150), color=(0, 0, 0), width=1)
    page.draw_rect(fitz.Rect(230, 90, 340, 150), color=(0, 0, 0), width=1)
    page.draw_line((160, 120), (230, 120), color=(0, 0, 0), width=1)
    document.save(path)
    document.close()
