from __future__ import annotations

from collections import Counter
from io import BytesIO
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat

from kb.multimodal.assets import (
    CaptionResult,
    RenderDecision,
    VisualAsset,
    VisualOccurrence,
    relative_path,
    sha256_bytes,
    stable_asset_id,
    stable_occurrence_id,
)
from kb.multimodal.captioning import caption_visual_asset
from kb.multimodal.manifest import MultimodalManifest
from kb.parsers.registry import ParsedSection


RENDER_KEYWORDS = [
    "architecture",
    "diagram",
    "構成図",
    "システム構成",
    "フロー",
    "架构",
    "流程",
    "网络拓扑",
    "aws",
]


def new_visual_stats() -> dict[str, Any]:
    return {
        "visual_assets_extracted": 0,
        "visual_assets_reused": 0,
        "visual_occurrences_added": 0,
        "visual_occurrences_reused": 0,
        "skipped_small_images": 0,
        "skipped_near_blank_images": 0,
        "skipped_logo_like_images": 0,
        "skipped_duplicates": 0,
        "rendered_pages": 0,
        "render_reason_counts": {},
        "ocr_caption_failures": 0,
        "searchable_visual_chunks": 0,
        "non_searchable_visual_assets": 0,
        "limit_warnings": [],
        "warnings": [],
    }


def register_visual_bytes(
    *,
    image_bytes: bytes,
    source_path: Path,
    config,
    generated_from: str,
    occurrence_index: int,
    page_number: int | None = None,
    slide_number: int | None = None,
    sheet_name: str | None = None,
    bbox: list[float] | None = None,
    context_title: str | None = None,
    nearby_text: str | None = None,
    ext: str = ".png",
    render_dpi: int | None = None,
    stats: dict[str, Any] | None = None,
) -> ParsedSection | None:
    stats = stats if stats is not None else new_visual_stats()
    multimodal = config.parsing.multimodal
    if not multimodal.enabled:
        return None

    try:
        image = Image.open(BytesIO(image_bytes))
        image.load()
    except Exception as exc:
        stats["warnings"].append(f"Could not read visual asset from {source_path}: {exc}")
        stats["ocr_caption_failures"] += 1
        return None

    width, height = image.size
    if not _passes_quality_filters(image, generated_from, config, stats):
        return None

    image_hash = sha256_bytes(image_bytes)
    source_hash = _file_hash(source_path)
    source_rel = relative_path(source_path, config.root_path)
    asset_id = stable_asset_id(image_hash)
    extraction_method = generated_from

    manifest = MultimodalManifest(config)
    existing_assets = manifest.load_assets()
    existing_asset = existing_assets.get(asset_id)
    if existing_asset and existing_asset.get("attachment_path"):
        attachment_rel = str(existing_asset["attachment_path"])
        attachment_path = config.root_path / attachment_rel
        stats["visual_assets_reused"] += 1
        stats["skipped_duplicates"] += 1
    else:
        attachment_path = _attachment_path(
            config=config,
            source_path=source_path,
            source_hash=source_hash,
            image_hash=image_hash,
            page_number=page_number,
            slide_number=slide_number,
            occurrence_index=occurrence_index,
            generated_from=generated_from,
            ext=_normalized_ext(ext, image.format),
            render_dpi=render_dpi,
        )
        attachment_path.parent.mkdir(parents=True, exist_ok=True)
        if not attachment_path.exists():
            attachment_path.write_bytes(image_bytes)
        attachment_rel = relative_path(attachment_path, config.root_path)
        stats["visual_assets_extracted"] += 1

    asset = VisualAsset(
        asset_id=asset_id,
        image_hash=image_hash,
        attachment_path=attachment_rel,
        width=width,
        height=height,
        mime_type=Image.MIME.get(image.format or "", "image/png"),
        ext=_normalized_ext(ext, image.format),
        generated_from=generated_from,
        source_hash=source_hash,
        metadata={"source_path": source_rel},
    )
    manifest.upsert_asset(asset)

    occurrence_id = stable_occurrence_id(
        source_path=source_rel,
        page_number=page_number,
        slide_number=slide_number,
        sheet_name=sheet_name,
        occurrence_index=occurrence_index,
        image_hash=image_hash,
        extraction_method=extraction_method,
    )
    occurrence = VisualOccurrence(
        occurrence_id=occurrence_id,
        source_path=source_rel,
        page_number=page_number,
        slide_number=slide_number,
        sheet_name=sheet_name,
        bbox=bbox,
        occurrence_index=occurrence_index,
        asset_id=asset_id,
        image_hash=image_hash,
        extraction_method=extraction_method,
        context_title=context_title,
        nearby_text=nearby_text,
    )
    if manifest.upsert_occurrence(occurrence):
        stats["visual_occurrences_added"] += 1
    else:
        stats["visual_occurrences_reused"] += 1

    caption = caption_visual_asset(
        asset=asset,
        occurrence=occurrence,
        image_path=attachment_path,
        config=config,
        manifest=manifest,
        render_dpi=render_dpi,
    )
    if not caption.searchable:
        stats["non_searchable_visual_assets"] += 1
        return None
    stats["searchable_visual_chunks"] += 1
    return visual_section_from_caption(asset=asset, occurrence=occurrence, caption=caption)


def visual_section_from_caption(*, asset: VisualAsset, occurrence: VisualOccurrence, caption: CaptionResult) -> ParsedSection:
    metadata = {
        "indexed_source_path": occurrence.source_path,
        "source_path": occurrence.source_path,
        "asset_id": asset.asset_id,
        "occurrence_id": occurrence.occurrence_id,
        "attachment_path": asset.attachment_path,
        "visual_type": caption.visual_type,
        "image_hash": asset.image_hash,
        "caption_provider": caption.caption_provider,
        "caption_model": caption.caption_model,
        "prompt_version": caption.prompt_version,
        "searchable": caption.searchable,
        "confidence": caption.confidence,
        "ocr_text": caption.ocr_text,
        "caption": caption.caption,
        "entities": caption.entities,
        "relationships": caption.relationships,
        "architecture_notes": caption.architecture_notes,
        "uncertain_items": caption.uncertain_items,
        "future": {"image_embedding": None},
    }
    heading = occurrence.context_title
    if not heading:
        if occurrence.page_number:
            heading = f"Visual summary from source PDF page {occurrence.page_number}"
        elif occurrence.slide_number:
            heading = f"Visual summary from source slide {occurrence.slide_number}"
        else:
            heading = "Visual summary"
    return ParsedSection(
        text=caption.text_for_embedding,
        heading=heading,
        page_number=occurrence.page_number,
        slide_number=occurrence.slide_number,
        sheet_name=occurrence.sheet_name,
        ocr_used=bool(caption.ocr_text.strip()),
        ocr_confidence=caption.ocr_confidence,
        parser_name="multimodal",
        source_format=Path(occurrence.source_path).suffix.lower(),
        extraction_method=occurrence.extraction_method,
        asset_type="visual",
        metadata=metadata,
    )


def should_render_pdf_page(page, native_text: str, config) -> RenderDecision:
    pdf_cfg = config.parsing.multimodal.pdf
    mode = _render_mode(pdf_cfg)
    native_text_chars = len(native_text.strip())
    keyword_hits = [keyword for keyword in RENDER_KEYWORDS if keyword.lower() in native_text.lower()]
    image_count = 0
    image_area_ratio = 0.0
    drawing_count = 0

    try:
        images = page.get_images(full=True)
        image_count = len(images)
    except Exception:
        image_count = 0

    try:
        page_area = float(page.rect.width * page.rect.height) or 1.0
        infos = page.get_image_info(xrefs=True)
        image_area = sum(float(info.get("width", 0) or 0) * float(info.get("height", 0) or 0) for info in infos)
        image_area_ratio = min(1.0, image_area / page_area) if page_area else 0.0
    except Exception:
        image_area_ratio = 0.0

    try:
        drawing_count = len(page.get_drawings())
    except Exception:
        drawing_count = 0

    reasons: list[str] = []
    if mode == "off":
        return RenderDecision(False, [], native_text_chars, image_count, image_area_ratio, drawing_count, keyword_hits, mode)
    if mode == "all":
        reasons.append("mode_all")
    if native_text_chars < int(pdf_cfg.min_page_text_chars):
        reasons.append("low_native_text")
    if drawing_count >= int(pdf_cfg.min_drawing_count_for_render):
        reasons.append("drawing_count")
    if image_area_ratio >= float(pdf_cfg.min_image_area_ratio_for_render):
        reasons.append("image_area_ratio")
    if keyword_hits:
        reasons.append("keyword")

    if mode == "keyword_only":
        should_render = bool(keyword_hits)
        reasons = ["keyword"] if keyword_hits else []
    else:
        should_render = bool(reasons)
    return RenderDecision(should_render, reasons, native_text_chars, image_count, image_area_ratio, drawing_count, keyword_hits, mode)


def record_render_decision(stats: dict[str, Any], decision: RenderDecision) -> None:
    counts = Counter(stats.get("render_reason_counts", {}))
    for reason in decision.reasons:
        counts[reason] += 1
    stats["render_reason_counts"] = dict(counts)


def _passes_quality_filters(image: Image.Image, generated_from: str, config, stats: dict[str, Any]) -> bool:
    if generated_from in {"page_render", "slide_render"}:
        return True
    images_cfg = config.parsing.multimodal.images
    width, height = image.size
    if width * height > int(images_cfg.max_image_pixels):
        stats["limit_warnings"].append(f"Skipped image over max_image_pixels: {width}x{height}")
        return False
    if images_cfg.skip_small_icons and (width < int(images_cfg.min_image_width) or height < int(images_cfg.min_image_height)):
        stats["skipped_small_images"] += 1
        return False
    if images_cfg.skip_near_blank_images and _is_near_blank(image):
        stats["skipped_near_blank_images"] += 1
        return False
    if images_cfg.skip_logo_like_images and _is_logo_like(image):
        stats["skipped_logo_like_images"] += 1
        return False
    return True


def _is_near_blank(image: Image.Image) -> bool:
    grayscale = image.convert("L").resize((32, 32))
    stat = ImageStat.Stat(grayscale)
    return bool(stat.stddev and stat.stddev[0] < 2.0)


def _is_logo_like(image: Image.Image) -> bool:
    width, height = image.size
    return max(width, height) <= 256 and 0.6 <= width / max(height, 1) <= 1.8


def _attachment_path(
    *,
    config,
    source_path: Path,
    source_hash: str,
    image_hash: str,
    page_number: int | None,
    slide_number: int | None,
    occurrence_index: int,
    generated_from: str,
    ext: str,
    render_dpi: int | None,
) -> Path:
    stem = _safe_stem(source_path.stem)
    source_prefix = source_hash[:6]
    folder = config.multimodal_attachments_dir / f"{stem}_{source_prefix}"
    if generated_from == "page_render":
        page = f"p{int(page_number or 0):03d}"
        return folder / f"{stem}_{source_prefix}_{page}_page_dpi{int(render_dpi or 0)}_{image_hash[:6]}{ext}"
    if generated_from == "slide_render":
        slide = f"s{int(slide_number or 0):03d}"
        return folder / f"{stem}_{source_prefix}_{slide}_slide_dpi{int(render_dpi or 0)}_{image_hash[:6]}{ext}"
    if page_number:
        location = f"p{int(page_number):03d}_img{occurrence_index:02d}"
    elif slide_number:
        location = f"s{int(slide_number):03d}_img{occurrence_index:02d}"
    else:
        location = f"img{occurrence_index:02d}"
    return folder / f"{stem}_{source_prefix}_{location}_{image_hash[:6]}{ext}"


def _safe_stem(stem: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return safe or "source"


def _file_hash(path: Path) -> str:
    digest = __import__("hashlib").sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalized_ext(ext: str, image_format: str | None) -> str:
    if ext and ext.startswith("."):
        return ext.lower()
    if image_format:
        return "." + image_format.lower().replace("jpeg", "jpg")
    return ".png"


def _render_mode(pdf_cfg) -> str:
    if getattr(pdf_cfg, "render_all_pages", None) is True and getattr(pdf_cfg, "render_pages", "off") in {"off", None}:
        return "all"
    return getattr(pdf_cfg, "render_pages", "off") or "off"
