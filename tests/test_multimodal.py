from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
import pytest
import yaml

from kb.curate_visual import curate_visual_summaries
from kb.ingest import index_project
from kb.multimodal.assets import caption_cache_key, ocr_cache_key, stable_occurrence_id
from kb.multimodal.manifest import MultimodalManifest
from kb.parsers import parse_file
from kb.parsers.pdf import parse_pdf_file
from kb.parsers.text import parse_text_file
from kb.retrieval import ProjectRetriever
from kb.store import LanceDBStore, ProjectKBConfig, load_config


class FakeEmbedder:
    batch_size = 8

    def embed_texts(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, query):
        return [1.0, 0.0, 0.0]


def test_image_parser_handles_png_and_searchable_local_caption(tmp_path: Path):
    cfg = _config(tmp_path, multimodal=True, provider="local", vision_enabled=True)
    image = tmp_path / "sources" / "sample_image.png"
    _write_png(image, "AWS Architecture")

    parsed = parse_file(image, config=cfg)

    assert parsed is not None
    assert parsed.sections
    assert parsed.sections[0].asset_type == "visual"
    assert parsed.sections[0].metadata["attachment_path"].startswith("docs/_attachments/kb_assets/")


def test_visual_asset_occurrence_ids_and_caption_cache_keys_are_stable():
    first = stable_occurrence_id(
        source_path="sources/a.pdf",
        page_number=1,
        occurrence_index=1,
        image_hash="abc",
        extraction_method="embedded_image",
    )
    second = stable_occurrence_id(
        source_path="sources/a.pdf",
        page_number=2,
        occurrence_index=1,
        image_hash="abc",
        extraction_method="embedded_image",
    )
    assert first != second

    ocr_key = ocr_cache_key(
        image_hash="abc",
        ocr_engine="rapidocr",
        ocr_engine_version="1",
        render_dpi=180,
        extraction_method="page_render",
    )
    caption_v1 = caption_cache_key(
        image_hash="abc",
        caption_provider="local",
        caption_model="m",
        prompt_version="v1",
        ocr_cache_key_value=ocr_key,
        ocr_text="AWS",
        extraction_method="page_render",
    )
    caption_v2 = caption_cache_key(
        image_hash="abc",
        caption_provider="local",
        caption_model="m",
        prompt_version="v2",
        ocr_cache_key_value=ocr_key,
        ocr_text="AWS",
        extraction_method="page_render",
    )
    assert caption_v1 != caption_v2


def test_pdf_vector_page_renders_with_explainable_reason(tmp_path: Path):
    cfg = _config(tmp_path, multimodal=True, provider="local", vision_enabled=True)
    cfg.parsing.multimodal.pdf.render_pages = "auto"
    cfg.parsing.multimodal.pdf.min_drawing_count_for_render = 1
    pdf = tmp_path / "sources" / "sample_architecture.pdf"
    _write_vector_pdf(pdf)

    parsed = parse_pdf_file(pdf, config=cfg)
    visual_sections = [section for section in parsed.sections if section.asset_type == "visual"]

    assert visual_sections
    decision = visual_sections[0].metadata["render_decision"]
    assert decision["should_render"] is True
    assert "drawing_count" in decision["reasons"] or "keyword" in decision["reasons"]
    assert visual_sections[0].metadata["attachment_path"].startswith("docs/_attachments/kb_assets/")


def test_pdf_duplicate_embedded_image_reuses_asset_but_keeps_occurrences(tmp_path: Path):
    cfg = _config(tmp_path, multimodal=True, provider="local", vision_enabled=True)
    cfg.parsing.multimodal.pdf.render_pages = "off"
    pdf = tmp_path / "sources" / "sample_duplicate_image.pdf"
    image_bytes = _png_bytes("Repeated AWS")
    _write_duplicate_image_pdf(pdf, image_bytes)

    parsed = parse_pdf_file(pdf, config=cfg)
    manifest = MultimodalManifest(cfg)
    assets = manifest.load_assets()
    occurrences = manifest.load_occurrences()

    assert len([section for section in parsed.sections if section.asset_type == "visual"]) == 2
    assert len(assets) == 1
    assert {row["page_number"] for row in occurrences.values()} == {1, 2}


def test_stub_without_ocr_does_not_create_searchable_visual_chunk(tmp_path: Path):
    cfg = _config(tmp_path, multimodal=True, provider="stub", vision_enabled=False)
    image = tmp_path / "sources" / "blank.png"
    _write_png(image, "")

    parsed = parse_file(image, config=cfg)
    manifest = MultimodalManifest(cfg)

    assert not parsed.sections
    assert manifest.load_assets()
    assert manifest.summary()["searchable_visual_chunk_count"] == 0


def test_visual_summary_frontmatter_becomes_visual_metadata(tmp_path: Path):
    note = tmp_path / "docs" / "visual_summary.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        """---
kb_type: visual_summary
review_status: needs_review
status: needs_review
source_path: sources/sample.pdf
attachment_path: docs/_attachments/kb_assets/sample/sample.png
page_number: 3
image_hash: abc
asset_id: asset_abc
occurrence_id: occ_abc
visual_type: architecture_diagram
caption_provider: local
prompt_version: vision-caption-v1
confidence: 0.5
---

# Visual Summary

AWS VPC architecture diagram.
""",
        encoding="utf-8",
    )

    parsed = parse_text_file(note)

    assert parsed.sections[0].asset_type == "visual"
    assert parsed.sections[0].metadata["source_path"] == "sources/sample.pdf"
    assert parsed.sections[0].metadata["attachment_path"].endswith("sample.png")
    assert parsed.sections[0].page_number == 3


def test_referenced_only_requires_explicit_markdown_reference_and_allowed_root(tmp_path: Path):
    cfg = _config(tmp_path, multimodal=True, provider="local", vision_enabled=True)
    cfg.parsing.multimodal.curated_attachments.mode = "referenced_only"
    asset = tmp_path / "docs" / "_attachments" / "kb_assets" / "src" / "asset.png"
    _write_png(asset, "Referenced AWS")
    unreferenced = tmp_path / "docs" / "_attachments" / "kb_assets" / "src" / "unreferenced.png"
    _write_png(unreferenced, "Unreferenced")
    note = tmp_path / "docs" / "note.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("![diagram](_attachments/kb_assets/src/asset.png)\n", encoding="utf-8")

    parsed = parse_text_file(note, config=cfg)
    occurrences = MultimodalManifest(cfg).load_occurrences()

    assert len([section for section in parsed.sections if section.asset_type == "visual"]) == 1
    assert len(occurrences) == 1


def test_raw_to_curated_visual_summary_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    raw_config = _write_config(tmp_path, raw=True)
    curated_config = _write_config(tmp_path, raw=False)
    image = tmp_path / "sources" / "sample_image.png"
    _write_png(image, "AWS VPC")
    monkeypatch.setattr("kb.ingest.build_embedder", lambda cfg: FakeEmbedder())

    raw_result = index_project(raw_config, rebuild=True)
    export_result = curate_visual_summaries(config_path=raw_config)
    curated_result = index_project(curated_config, rebuild=True)
    cfg = load_config(curated_config)
    payload = ProjectRetriever(config=cfg, store=LanceDBStore(cfg), embedder=FakeEmbedder()).search("AWS VPC", top_k=1)

    assert raw_result["visual_chunks"] >= 1
    assert export_result["exported"] >= 1
    assert curated_result["chunks"] >= 1
    assert payload["index_role"] == "curated"
    assert payload["results"][0]["asset_type"] == "visual"
    assert payload["results"][0]["attachment_path"].startswith("docs/_attachments/kb_assets/")


def test_multimodal_disabled_preserves_old_image_skip_behavior(tmp_path: Path):
    cfg = _config(tmp_path, multimodal=False)
    image = tmp_path / "sources" / "sample_image.png"
    _write_png(image, "AWS")

    parsed = parse_file(image, config=cfg)

    assert parsed is not None
    assert not parsed.sections


def _config(tmp_path: Path, *, multimodal: bool, provider: str = "local", vision_enabled: bool = True) -> ProjectKBConfig:
    cfg = ProjectKBConfig(project_root=str(tmp_path))
    cfg.database.vector_dimension = 3
    cfg.database.multimodal_cache_dir = ".kb_cache/multimodal"
    cfg.parsing.multimodal.enabled = multimodal
    cfg.parsing.multimodal.attachments_dir = "docs/_attachments/kb_assets"
    cfg.parsing.multimodal.pdf.render_pages = "off"
    cfg.parsing.multimodal.pdf.min_drawing_count_for_render = 1
    cfg.parsing.multimodal.images.min_image_width = 1
    cfg.parsing.multimodal.images.min_image_height = 1
    cfg.parsing.multimodal.images.skip_logo_like_images = False
    cfg.parsing.multimodal.images.skip_near_blank_images = False
    cfg.parsing.multimodal.vision.provider = provider
    cfg.parsing.multimodal.vision.enabled = vision_enabled
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
            "exclude_patterns": ["docs/_attachments/**", "docs/_templates/**", "docs/99_Inbox/**"],
        },
        "chunking": {"chunk_size": 1000, "chunk_overlap": 120},
        "embedding": {"model_name": "fake", "batch_size": 8},
        "parsing": {
            "ocr": {"enabled": False, "engine": "rapidocr"},
            "office": {"extract_images": False, "extract_notes": True},
            "multimodal": {
                "enabled": raw,
                "attachments_dir": "docs/_attachments/kb_assets",
                "pdf": {"extract_embedded_images": True, "render_pages": "off", "render_dpi": 180},
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
                },
                "curated_attachments": {"mode": "off", "allowed_roots": ["docs/_attachments/kb_assets"]},
            },
        },
        "retrieval": {"mode": "hybrid", "top_k": 5, "candidate_k": 20, "max_concurrent_queries": 1},
    }
    config_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return config_path


def _write_png(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_png_bytes(text))


def _png_bytes(text: str) -> bytes:
    image = Image.new("RGB", (320, 220), "white")
    draw = ImageDraw.Draw(image)
    if text:
        draw.text((20, 90), text, fill="black")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _write_vector_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import fitz

    document = fitz.open()
    page = document.new_page(width=400, height=300)
    page.insert_text((40, 40), "AWS architecture diagram", fontsize=14)
    page.draw_rect(fitz.Rect(50, 90, 160, 150), color=(0, 0, 0), width=1)
    page.draw_rect(fitz.Rect(230, 90, 340, 150), color=(0, 0, 0), width=1)
    page.draw_line((160, 120), (230, 120), color=(0, 0, 0), width=1)
    document.save(path)
    document.close()


def _write_duplicate_image_pdf(path: Path, image_bytes: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import fitz

    document = fitz.open()
    for page_number in range(2):
        page = document.new_page(width=400, height=300)
        page.insert_text((40, 40), f"Page {page_number + 1} AWS image")
        page.insert_image(fitz.Rect(40, 80, 260, 240), stream=image_bytes)
    document.save(path)
    document.close()

