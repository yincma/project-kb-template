from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
import pytest
import yaml

from kb.curate_visual import curate_visual_summaries
from kb.ingest import index_project
from kb.mcp_server import clear_runtime_cache, read_kb_result
from kb.multimodal.manifest import MultimodalManifest
import kb.multimodal.captioning as captioning
from kb.parsers import parse_file
from kb.parsers.pdf import parse_pdf_file
from kb.retrieval import ProjectRetriever
from kb.store import LanceDBStore, ProjectKBConfig, load_config


class FakeEmbedder:
    batch_size = 8

    def __init__(self, *args, **kwargs):
        pass

    def embed_texts(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, query):
        return [1.0, 0.0, 0.0]


def test_visual_e2e_raw_export_reviewed_curated_search_and_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    raw_config = _write_config(tmp_path, raw=True)
    curated_config = _write_config(tmp_path, raw=False)
    _write_architecture_pdf(tmp_path / "sources" / "sample_architecture.pdf")
    _write_png(tmp_path / "sources" / "sample_image.png", "AWS OCR TERM")
    monkeypatch.setattr("kb.ingest.build_embedder", lambda cfg: FakeEmbedder())
    monkeypatch.setattr("kb.mcp_server.BGEEmbedder", FakeEmbedder)
    monkeypatch.setenv("KB_CONFIG", str(curated_config))
    clear_runtime_cache()

    raw_result = index_project(raw_config, rebuild=True)
    raw_manifest = MultimodalManifest(load_config(raw_config))
    exported = curate_visual_summaries(config_path=raw_config)
    curated_needs_review = index_project(curated_config, rebuild=True)

    assert raw_result["visual_chunks"] >= 1
    assert raw_manifest.load_assets()
    assert raw_manifest.load_occurrences()
    assert raw_manifest.summary()["caption_cache_count"] >= 1
    assert exported["exported"] >= 1
    assert curated_needs_review["chunks"] == 0

    generated_notes = list((tmp_path / "docs" / "_generated" / "visual_summaries" / "needs_review").glob("*.md"))
    assert generated_notes
    for note in generated_notes:
        note.write_text(
            note.read_text(encoding="utf-8")
            .replace("review_status: needs_review", "review_status: reviewed")
            .replace("status: needs_review", "status: reviewed"),
            encoding="utf-8",
        )

    curated_reviewed = index_project(curated_config, rebuild=True)
    cfg = load_config(curated_config)
    payload = ProjectRetriever(config=cfg, store=LanceDBStore(cfg), embedder=FakeEmbedder()).search(
        "AWS architecture", top_k=1
    )
    result = payload["results"][0]
    read_payload = read_kb_result(
        source_path=result["source_path"],
        indexed_source_path=result["indexed_source_path"],
        attachment_path=result["attachment_path"],
        asset_type=result["asset_type"],
        occurrence_id=result["metadata"].get("occurrence_id"),
    )

    assert curated_reviewed["chunks"] >= 1
    assert result["asset_type"] == "visual"
    assert result["attachment_path"].startswith("docs/_attachments/kb_assets/")
    assert "Visual Summary" in read_payload["text"]
    assert read_payload["attachment_path"] == result["attachment_path"]


def test_repeated_raw_ingest_is_idempotent_for_assets_and_captions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    raw_config = _write_config(tmp_path, raw=True)
    _write_png(tmp_path / "sources" / "sample_image.png", "AWS")
    monkeypatch.setattr("kb.ingest.build_embedder", lambda cfg: FakeEmbedder())

    index_project(raw_config, rebuild=True)
    manifest = MultimodalManifest(load_config(raw_config))
    first_assets = len(manifest.load_assets())
    first_captions = manifest.summary()["caption_cache_count"]
    second = index_project(raw_config)

    assert second["indexed_files"] == 0
    assert len(manifest.load_assets()) == first_assets
    assert manifest.summary()["caption_cache_count"] == first_captions


def test_multimodal_disabled_keeps_image_out_of_text_index(tmp_path: Path):
    cfg = ProjectKBConfig(project_root=str(tmp_path))
    cfg.parsing.multimodal.enabled = False
    image = tmp_path / "sources" / "sample_image.png"
    _write_png(image, "AWS")

    parsed = parse_file(image, config=cfg)

    assert parsed is not None
    assert parsed.sections == []


def test_large_pdf_render_limit_reports_warning(tmp_path: Path):
    cfg = _config(tmp_path, multimodal=True)
    cfg.parsing.multimodal.pdf.render_pages = "all"
    cfg.parsing.multimodal.pdf.max_rendered_pages_per_file = 1
    pdf = tmp_path / "sources" / "large.pdf"
    _write_multi_page_pdf(pdf, pages=3)

    parsed = parse_pdf_file(pdf, config=cfg)

    assert any("Skipped PDF page renders after 1" in warning for warning in parsed.warnings)


def test_openai_provider_mock_caption_can_enter_visual_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _config(tmp_path, multimodal=True, provider="openai_compatible")
    cfg.parsing.multimodal.vision.allow_external_vision = True
    cfg.parsing.multimodal.vision.model = "vision-test"
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        captioning,
        "OPENAI_COMPATIBLE_TRANSPORT",
        lambda **kwargs: {
            "choices": [
                {
                    "message": {
                        "content": '{"caption":"AWS architecture diagram with VPC and RDS","visual_type":"architecture_diagram","entities":["AWS","VPC","RDS"],"relationships":["VPC -> RDS"],"architecture_notes":["RDS is downstream"],"uncertain_items":[],"confidence":0.9}'
                    }
                }
            ]
        },
    )
    image = tmp_path / "sources" / "vision.png"
    _write_png(image, "")

    parsed = parse_file(image, config=cfg)

    assert parsed.sections
    assert "AWS architecture diagram" in parsed.sections[0].text
    assert parsed.sections[0].metadata["caption_provider"] == "openai_compatible"


def _config(tmp_path: Path, *, multimodal: bool, provider: str = "local") -> ProjectKBConfig:
    cfg = ProjectKBConfig(project_root=str(tmp_path))
    cfg.database.vector_dimension = 3
    cfg.parsing.multimodal.enabled = multimodal
    cfg.parsing.multimodal.attachments_dir = "docs/_attachments/kb_assets"
    cfg.parsing.multimodal.images.min_image_width = 1
    cfg.parsing.multimodal.images.min_image_height = 1
    cfg.parsing.multimodal.images.skip_logo_like_images = False
    cfg.parsing.multimodal.images.skip_near_blank_images = False
    cfg.parsing.multimodal.vision.enabled = True
    cfg.parsing.multimodal.vision.provider = provider
    cfg.parsing.multimodal.vision.allow_external_vision = False
    return cfg


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
            "include_patterns": ["**/*.png", "**/*.pdf"] if raw else ["**/*.md"],
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
                    "model": None,
                    "base_url": None,
                    "api_key_env": "OPENAI_API_KEY",
                    "allow_external_vision": False,
                    "prompt_version": "vision-caption-v1",
                },
                "curated_attachments": {"mode": "off", "allowed_roots": ["docs/_attachments/kb_assets"]},
            },
        },
        "retrieval": {"mode": "hybrid", "top_k": 5, "candidate_k": 20, "max_concurrent_queries": 1},
        "curation": {"index_review_statuses": ["reviewed", "approved"], "skip_needs_review": True},
    }
    config_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return config_path


def _write_png(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (320, 220), "white")
    draw = ImageDraw.Draw(image)
    if text:
        draw.text((20, 90), text, fill="black")
    output = BytesIO()
    image.save(output, format="PNG")
    path.write_bytes(output.getvalue())


def _write_architecture_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import fitz

    document = fitz.open()
    page = document.new_page(width=400, height=300)
    page.insert_text((40, 40), "AWS architecture diagram VPC RDS", fontsize=14)
    page.draw_rect(fitz.Rect(50, 90, 160, 150), color=(0, 0, 0), width=1)
    page.draw_rect(fitz.Rect(230, 90, 340, 150), color=(0, 0, 0), width=1)
    page.draw_line((160, 120), (230, 120), color=(0, 0, 0), width=1)
    document.save(path)
    document.close()


def _write_multi_page_pdf(path: Path, *, pages: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import fitz

    document = fitz.open()
    for index in range(pages):
        page = document.new_page(width=400, height=300)
        page.insert_text((40, 40), f"AWS architecture diagram page {index + 1}", fontsize=14)
        page.draw_rect(fitz.Rect(50, 90, 160, 150), color=(0, 0, 0), width=1)
    document.save(path)
    document.close()
