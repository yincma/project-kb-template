from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path
from urllib import error

from PIL import Image, ImageDraw
import pytest
import yaml

from kb.curate_visual import curate_visual_summaries
from kb.ingest import index_project
from kb.mcp_server import _find_result_row, clear_runtime_cache, read_kb_result
from kb.multimodal.assets import caption_cache_key, ocr_cache_key, stable_occurrence_id
import kb.multimodal.captioning as captioning
from kb.multimodal.manifest import MultimodalManifest
from kb.multimodal.extraction import should_render_pdf_page
from kb.parsers import parse_file
from kb.parsers.pdf import parse_pdf_file
from kb.parsers.text import parse_text_file
from kb.query import _source_display
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


def test_curate_visual_only_searchable_and_no_only_searchable_modes(tmp_path: Path):
    config_path = _write_config(tmp_path, raw=True, provider="stub")
    cfg = load_config(config_path)
    image = tmp_path / "sources" / "blank.png"
    _write_png(image, "")
    parsed = parse_file(image, config=cfg)

    default_export = curate_visual_summaries(config_path=config_path)
    dry_run_export = curate_visual_summaries(config_path=config_path, only_searchable=False, dry_run=True)
    notes_after_dry_run = list((tmp_path / "docs" / "_generated" / "visual_summaries" / "needs_review").glob("*.md"))
    audit_export = curate_visual_summaries(config_path=config_path, only_searchable=False)
    second_export = curate_visual_summaries(config_path=config_path, only_searchable=False)
    overwrite_export = curate_visual_summaries(config_path=config_path, only_searchable=False, overwrite=True)
    notes = list((tmp_path / "docs" / "_generated" / "visual_summaries" / "needs_review").glob("*.md"))

    assert parsed.sections == []
    assert default_export["exported"] == 0
    assert default_export["skipped_not_searchable"] == 1
    assert dry_run_export["exported"] == 1
    assert dry_run_export["dry_run"] is True
    assert dry_run_export["planned_paths"]
    assert not notes_after_dry_run
    assert audit_export["exported"] == 1
    assert second_export["skipped_existing"] == 1
    assert overwrite_export["exported"] == 1
    assert "searchable: false" in notes[0].read_text(encoding="utf-8")


def test_visual_summary_frontmatter_becomes_visual_metadata(tmp_path: Path):
    note = tmp_path / "docs" / "visual_summary.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        """---
kb_type: visual_summary
review_status: reviewed
status: reviewed
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


def test_needs_review_visual_summary_is_skipped_but_plain_markdown_indexes(tmp_path: Path):
    cfg = _config(tmp_path, multimodal=False)
    visual_note = tmp_path / "docs" / "needs_review_visual.md"
    visual_note.parent.mkdir(parents=True)
    visual_note.write_text(
        """---
kb_type: visual_summary
review_status: needs_review
source_path: sources/sample.pdf
attachment_path: docs/_attachments/kb_assets/sample/sample.png
---

AWS VPC architecture.
""",
        encoding="utf-8",
    )
    plain_note = tmp_path / "docs" / "plain.md"
    plain_note.write_text("# Plain\n\nNormal curated note.\n", encoding="utf-8")

    skipped = parse_text_file(visual_note, config=cfg)
    plain = parse_text_file(plain_note, config=cfg)

    assert skipped.sections == []
    assert plain.sections and plain.sections[0].text.startswith("# Plain")


def test_non_searchable_visual_summary_is_skipped_even_when_reviewed(tmp_path: Path):
    cfg = _config(tmp_path, multimodal=False)
    note = tmp_path / "docs" / "visual_reviewed_non_searchable.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        """---
kb_type: visual_summary
review_status: reviewed
status: reviewed
searchable: false
source_path: sources/sample.pdf
attachment_path: docs/_attachments/kb_assets/sample/sample.png
---

Metadata-only visual note.
""",
        encoding="utf-8",
    )

    parsed = parse_text_file(note, config=cfg)

    assert parsed.sections == []


def test_searchable_reviewed_visual_summary_still_indexes(tmp_path: Path):
    cfg = _config(tmp_path, multimodal=False)
    note = tmp_path / "docs" / "visual_reviewed_searchable.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        """---
kb_type: visual_summary
review_status: reviewed
status: reviewed
searchable: true
source_path: sources/sample.pdf
attachment_path: docs/_attachments/kb_assets/sample/sample.png
---

AWS architecture visual evidence.
""",
        encoding="utf-8",
    )

    parsed = parse_text_file(note, config=cfg)

    assert parsed.sections
    assert parsed.sections[0].metadata["searchable"] is True


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

    assert raw_result["visual_chunks"] >= 1
    assert export_result["exported"] >= 1
    assert curated_result["chunks"] == 0

    for note in (tmp_path / "docs" / "_generated" / "visual_summaries" / "needs_review").glob("*.md"):
        note.write_text(note.read_text(encoding="utf-8").replace("review_status: needs_review", "review_status: reviewed").replace("status: needs_review", "status: reviewed"), encoding="utf-8")
    curated_result = index_project(curated_config, rebuild=True)
    payload = ProjectRetriever(config=cfg, store=LanceDBStore(cfg), embedder=FakeEmbedder()).search("AWS VPC", top_k=1)

    assert curated_result["chunks"] >= 1
    assert payload["index_role"] == "curated"
    assert payload["results"][0]["asset_type"] == "visual"
    assert payload["results"][0]["attachment_path"].startswith("docs/_attachments/kb_assets/")


def test_read_kb_result_returns_visual_summary_and_attachment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    curated_config = _write_config(tmp_path, raw=False)
    note = tmp_path / "docs" / "visual_reviewed.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        """---
kb_type: visual_summary
review_status: reviewed
status: reviewed
source_path: sources/sample.pdf
indexed_source_path: docs/visual_reviewed.md
attachment_path: docs/_attachments/kb_assets/sample/diagram.png
page_number: 2
image_hash: abc
asset_id: asset_abc
occurrence_id: occ_abc
visual_type: architecture_diagram
caption_provider: local
prompt_version: vision-caption-v1
---

# Visual Summary

AWS VPC architecture visual summary.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("kb.ingest.build_embedder", lambda cfg: FakeEmbedder())
    monkeypatch.setenv("KB_CONFIG", str(curated_config))
    clear_runtime_cache()

    index_project(curated_config, rebuild=True)
    payload = read_kb_result(
        source_path="sources/sample.pdf",
        indexed_source_path="docs/visual_reviewed.md",
        asset_type="visual",
        attachment_path="docs/_attachments/kb_assets/sample/diagram.png",
        occurrence_id="occ_abc",
    )

    assert "AWS VPC architecture visual summary" in payload["text"]
    assert payload["attachment_path"].endswith("diagram.png")
    assert payload["source_path"] == "sources/sample.pdf"


def test_read_kb_result_exact_lookup_fields_and_asset_multi_occurrence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    curated_config = _write_config(tmp_path, raw=False)
    first = tmp_path / "docs" / "visual reviewed one.md"
    second = tmp_path / "docs" / "visual reviewed two.md"
    first.parent.mkdir(parents=True, exist_ok=True)
    for path, occurrence_id, page in ((first, "occ_one", 1), (second, "occ_two", 2)):
        path.write_text(
            f"""---
kb_type: visual_summary
review_status: reviewed
status: reviewed
searchable: true
source_path: sources/source with spaces.pdf
attachment_path: docs/_attachments/kb_assets/sample/diagram.png
page_number: {page}
image_hash: abc
asset_id: asset_shared
occurrence_id: {occurrence_id}
visual_type: architecture_diagram
caption_provider: local
prompt_version: vision-caption-v1
---

# Visual Summary {page}

AWS VPC architecture visual summary {page}.
""",
            encoding="utf-8",
        )
    monkeypatch.setattr("kb.ingest.build_embedder", lambda cfg: FakeEmbedder())
    monkeypatch.setenv("KB_CONFIG", str(curated_config))
    clear_runtime_cache()
    index_project(curated_config, rebuild=True)
    store = LanceDBStore(load_config(curated_config))
    rows = store.preview_rows(10)
    chunk_id = next(row["id"] for row in rows if row["occurrence_id"] == "occ_one")

    by_occurrence = read_kb_result(occurrence_id="occ_one", asset_type="visual")
    by_chunk = read_kb_result(chunk_id=chunk_id, asset_type="visual")
    by_indexed_path = read_kb_result(indexed_source_path="docs/visual reviewed one.md", asset_type="visual")
    by_asset = read_kb_result(asset_id="asset_shared", asset_type="visual")

    assert "visual summary 1" in by_occurrence["text"]
    assert "visual summary 1" in by_chunk["text"]
    assert "visual summary 1" in by_indexed_path["text"]
    assert by_asset["matched_by"] == "asset_id"
    assert by_asset["occurrence_id"] in {"occ_one", "occ_two"}


def test_find_result_row_graceful_fallback_for_old_schema():
    class OldStore:
        config = ProjectKBConfig(project_root=".")

        def table_exists(self):
            return True

        def schema_field_names(self):
            return {"id", "source_path", "metadata_json"}

        def open_table(self):
            raise AssertionError("where query should not be attempted without field")

        def preview_rows(self, limit=1000):
            return [
                {
                    "id": "row1",
                    "source_path": "sources/a.pdf",
                    "metadata_json": json.dumps({"occurrence_id": "occ_old"}),
                }
            ]

    row, matched_by = _find_result_row(
        OldStore(),
        chunk_id=None,
        occurrence_id="occ_old",
        asset_id=None,
        indexed_source_path=None,
        source_path=None,
    )

    assert row["id"] == "row1"
    assert matched_by == "occurrence_id"


def test_openai_compatible_external_block_does_not_call_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _config(tmp_path, multimodal=True, provider="openai_compatible", vision_enabled=True)
    cfg.parsing.multimodal.vision.allow_external_vision = False
    image = tmp_path / "sources" / "sample_image.png"
    _write_png(image, "AWS")

    def fail_transport(**kwargs):
        raise AssertionError("transport should not be called")

    monkeypatch.setattr(captioning, "OPENAI_COMPATIBLE_TRANSPORT", fail_transport)
    monkeypatch.setattr(captioning, "_run_ocr", lambda image_path, ocr_engine: {"text": "AWS OCR", "confidence": 0.7})
    parsed = parse_file(image, config=cfg)

    assert parsed.sections
    assert parsed.sections[0].metadata["caption_provider"] == "ocr_only"


def test_openai_compatible_mock_response_creates_structured_caption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _config(tmp_path, multimodal=True, provider="openai_compatible", vision_enabled=True)
    cfg.parsing.multimodal.vision.allow_external_vision = True
    cfg.parsing.multimodal.vision.model = "vision-test"
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    calls = []

    def fake_transport(**kwargs):
        calls.append(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"caption":"Architecture shows AWS VPC to RDS","visual_type":"architecture_diagram","entities":["AWS","VPC","RDS"],"relationships":["VPC -> RDS"],"architecture_notes":["Database tier is shown"],"uncertain_items":["CIDR unknown"],"confidence":0.82}'
                    }
                }
            ]
        }

    monkeypatch.setattr(captioning, "OPENAI_COMPATIBLE_TRANSPORT", fake_transport)
    image = tmp_path / "sources" / "sample_image.png"
    _write_png(image, "")

    parsed = parse_file(image, config=cfg)

    assert calls
    assert parsed.sections[0].metadata["caption_provider"] == "openai_compatible"
    assert parsed.sections[0].metadata["visual_type"] == "architecture_diagram"
    assert "VPC -> RDS" in parsed.sections[0].text


def test_openai_compatible_fenced_json_and_plain_text_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _config(tmp_path, multimodal=True, provider="openai_compatible", vision_enabled=True)
    cfg.parsing.multimodal.vision.allow_external_vision = True
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    responses = [
        {
            "choices": [
                {
                    "message": {
                        "content": """```json
{"caption":"Fenced AWS diagram","visual_type":"architecture_diagram","entities":["AWS"],"relationships":[],"architecture_notes":[],"uncertain_items":[],"confidence":0.7}
```"""
                    }
                }
            ]
        },
        {"choices": [{"message": {"content": "Plain visual caption text"}}]},
    ]

    monkeypatch.setattr(captioning, "OPENAI_COMPATIBLE_TRANSPORT", lambda **kwargs: responses.pop(0))
    first = tmp_path / "sources" / "first.png"
    second = tmp_path / "sources" / "second.png"
    _write_png(first, "")
    _write_png(second, "second")

    first_parsed = parse_file(first, config=cfg)
    second_parsed = parse_file(second, config=cfg)

    assert first_parsed.sections[0].metadata["visual_type"] == "architecture_diagram"
    assert "Fenced AWS diagram" in first_parsed.sections[0].text
    assert "Plain visual caption text" in second_parsed.sections[0].text
    assert "structured parse failed" in second_parsed.sections[0].text


def test_openai_compatible_compresses_large_upload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _config(tmp_path, multimodal=True, provider="openai_compatible", vision_enabled=True)
    cfg.parsing.multimodal.vision.allow_external_vision = True
    cfg.parsing.multimodal.vision.resize_long_edge = 400
    cfg.parsing.multimodal.vision.max_upload_bytes = 2_000_000
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    uploads = []

    def fake_transport(**kwargs):
        data_url = kwargs["payload"]["messages"][0]["content"][1]["image_url"]["url"]
        uploads.append(base64.b64decode(data_url.split(",", 1)[1]))
        return {"choices": [{"message": {"content": '{"caption":"compressed","visual_type":"unknown"}'}}]}

    monkeypatch.setattr(captioning, "OPENAI_COMPATIBLE_TRANSPORT", fake_transport)
    image = tmp_path / "sources" / "large.png"
    _write_large_png(image, size=(2400, 1800))

    parsed = parse_file(image, config=cfg)
    uploaded_image = Image.open(BytesIO(uploads[0]))

    assert parsed.sections
    assert max(uploaded_image.size) <= 400
    assert len(uploads[0]) < image.stat().st_size


def test_openai_compatible_upload_over_limit_and_http_error_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _config(tmp_path, multimodal=True, provider="openai_compatible", vision_enabled=True)
    cfg.parsing.multimodal.vision.allow_external_vision = True
    cfg.parsing.multimodal.vision.max_upload_bytes = 10
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    called = {"count": 0}

    def fail_transport(**kwargs):
        called["count"] += 1
        raise AssertionError("transport should not be called when upload is over limit")

    monkeypatch.setattr(captioning, "OPENAI_COMPATIBLE_TRANSPORT", fail_transport)
    image = tmp_path / "sources" / "too_large.png"
    _write_large_png(image, size=(600, 600))

    parsed = parse_file(image, config=cfg)
    captions = _caption_cache_rows(MultimodalManifest(cfg))

    assert parsed.sections == []
    assert called["count"] == 0
    assert captions[-1]["caption_provider"] == "openai_compatible_skipped"

    cfg2 = _config(tmp_path / "http", multimodal=True, provider="openai_compatible", vision_enabled=True)
    cfg2.parsing.multimodal.vision.allow_external_vision = True
    cfg2.parsing.multimodal.vision.max_retries = 0
    monkeypatch.setattr(
        captioning,
        "OPENAI_COMPATIBLE_TRANSPORT",
        lambda **kwargs: (_ for _ in ()).throw(error.HTTPError(kwargs["url"], 429, "rate limited", hdrs=None, fp=None)),
    )
    http_image = tmp_path / "http" / "sources" / "http.png"
    _write_png(http_image, "")
    parsed_http = parse_file(http_image, config=cfg2)
    captions_http = _caption_cache_rows(MultimodalManifest(cfg2))

    assert parsed_http.sections == []
    assert captions_http[-1]["caption_provider"] == "openai_compatible_error"


def test_pdf_image_area_ratio_uses_displayed_bbox_not_pixel_area():
    class FakeRect:
        width = 400
        height = 400

    class FakeSmallLogoPage:
        rect = FakeRect()

        def get_images(self, full=True):
            return [object()]

        def get_image_info(self, xrefs=True):
            return [{"width": 3000, "height": 3000, "bbox": (10, 10, 30, 30)}]

        def get_drawings(self):
            return []

    class FakeLargeDisplayedImagePage(FakeSmallLogoPage):
        def get_image_info(self, xrefs=True):
            return [{"width": 50, "height": 50, "bbox": (0, 0, 360, 360)}]

    cfg = _config(Path("/tmp/project-kb-test"), multimodal=True)
    cfg.parsing.multimodal.pdf.render_pages = "auto"
    cfg.parsing.multimodal.pdf.min_page_text_chars = 0
    cfg.parsing.multimodal.pdf.min_image_area_ratio_for_render = 0.25
    cfg.parsing.multimodal.pdf.min_drawing_count_for_render = 99

    small = should_render_pdf_page(FakeSmallLogoPage(), "native text", cfg)
    large = should_render_pdf_page(FakeLargeDisplayedImagePage(), "native text", cfg)

    assert small.image_area_ratio < 0.01
    assert "image_area_ratio" not in small.reasons
    assert large.image_area_ratio > 0.25
    assert "image_area_ratio" in large.reasons


def test_multimodal_disabled_preserves_old_image_skip_behavior(tmp_path: Path):
    cfg = _config(tmp_path, multimodal=False)
    image = tmp_path / "sources" / "sample_image.png"
    _write_png(image, "AWS")

    parsed = parse_file(image, config=cfg)

    assert parsed is not None
    assert not parsed.sections


def test_query_source_display_includes_visual_provenance():
    rendered = _source_display(
        {
            "asset_type": "visual",
            "source_path": "sources/rfp.pdf",
            "visual_type": "architecture_diagram",
            "page_number": 38,
            "attachment_path": "docs/_attachments/kb_assets/rfp_ab12cd/rfp_ab12cd_p038_page_dpi180_9f8e7d.png",
            "indexed_source_path": "docs/_generated/visual_summaries/needs_review/rfp_visual_summary.md",
        }
    )

    assert "asset_type=visual" in rendered
    assert "visual_type=architecture_diagram" in rendered
    assert "page=38" in rendered
    assert "attachment=" in rendered


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


def _write_config(tmp_path: Path, *, raw: bool, provider: str = "local") -> Path:
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
                    "provider": provider,
                    "model": None,
                    "base_url": None,
                    "api_key_env": "OPENAI_API_KEY",
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
    path.write_bytes(_png_bytes(text))


def _write_large_png(path: Path, *, size: tuple[int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", size, (30, 80, 150, 200))
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, size[0] - 10, size[1] - 10), outline=(255, 255, 255, 255), width=8)
    output = BytesIO()
    image.save(output, format="PNG")
    path.write_bytes(output.getvalue())


def _caption_cache_rows(manifest: MultimodalManifest) -> list[dict]:
    rows = []
    for path in sorted(manifest.caption_cache_dir.glob("*.json")):
        rows.append(json.loads(path.read_text(encoding="utf-8")))
    return rows


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
