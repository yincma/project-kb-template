from __future__ import annotations

from pathlib import Path
from typing import Any

from kb.multimodal.assets import (
    CaptionResult,
    VisualAsset,
    VisualOccurrence,
    caption_cache_key,
    hash_text,
    ocr_cache_key,
)
from kb.multimodal.manifest import MultimodalManifest
from kb.multimodal.prompts import VISION_CAPTION_PROMPT_VERSION

EMPTY_STUB_PHRASES = {
    "this is an image asset",
    "no caption available",
    "image could not be processed",
}


def caption_visual_asset(
    *,
    asset: VisualAsset,
    occurrence: VisualOccurrence,
    image_path: Path,
    config,
    manifest: MultimodalManifest,
    render_dpi: int | None = None,
) -> CaptionResult:
    multimodal = config.parsing.multimodal
    vision = multimodal.vision
    provider = vision.provider
    prompt_version = vision.prompt_version or VISION_CAPTION_PROMPT_VERSION
    ocr_engine = config.parsing.ocr.engine if (config.parsing.ocr.enabled or provider in {"ocr_only", "local", "local_vision"}) else None
    extraction_method = occurrence.extraction_method

    ocr_key = ocr_cache_key(
        image_hash=asset.image_hash,
        ocr_engine=ocr_engine,
        ocr_engine_version=_ocr_engine_version(ocr_engine),
        render_dpi=render_dpi,
        extraction_method=extraction_method,
    )
    ocr_payload = manifest.load_ocr(ocr_key)
    if ocr_payload is None:
        ocr_payload = _run_ocr(image_path, ocr_engine)
        manifest.save_ocr(ocr_key, ocr_payload)

    raw_ocr_text = str(ocr_payload.get("text") or "")
    ocr_text = raw_ocr_text[: max(0, int(vision.max_ocr_chars or 0))] if vision.max_ocr_chars else raw_ocr_text
    ocr_confidence = ocr_payload.get("confidence")

    caption_text = ""
    caption_confidence: float | None = None
    caption_model = vision.model
    caption_provider = provider

    if provider in {"openai_compatible", "azure", "gemini", "cloud_vision"} and not vision.allow_external_vision:
        caption_provider = "external_blocked"
    elif provider in {"local", "local_vision"} and vision.enabled:
        caption_text = _local_context_caption(asset, occurrence)
        caption_confidence = 0.35 if caption_text else None
    elif provider == "stub":
        caption_text = ""
    elif provider == "ocr_only":
        caption_text = ""

    caption_text = _clean_stub_caption(caption_text)
    if caption_text and vision.max_caption_chars:
        caption_text = caption_text[: int(vision.max_caption_chars)]

    cap_key = caption_cache_key(
        image_hash=asset.image_hash,
        caption_provider=caption_provider,
        caption_model=caption_model,
        prompt_version=prompt_version,
        ocr_cache_key_value=ocr_key,
        ocr_text=ocr_text,
        extraction_method=extraction_method,
    )
    cached = manifest.load_caption(cap_key)
    if cached is not None:
        return CaptionResult(**cached)

    visual_type = infer_visual_type(ocr_text, caption_text, occurrence)
    entities = infer_entities(ocr_text + "\n" + caption_text + "\n" + (occurrence.nearby_text or ""))
    relationships = infer_relationships(ocr_text + "\n" + caption_text)
    uncertain_items = []
    if not caption_text and not ocr_text.strip():
        uncertain_items.append("No OCR text or caption was available.")

    searchable = bool(caption_text.strip() or ocr_text.strip())
    text_for_embedding = ""
    if searchable:
        text_for_embedding = build_text_for_embedding(
            asset=asset,
            occurrence=occurrence,
            ocr_text=ocr_text,
            caption=caption_text,
            visual_type=visual_type,
            entities=entities,
            relationships=relationships,
            architecture_notes=_architecture_notes(ocr_text, caption_text, occurrence),
            uncertain_items=uncertain_items,
        )

    result = CaptionResult(
        asset_id=asset.asset_id,
        occurrence_id=occurrence.occurrence_id,
        image_hash=asset.image_hash,
        ocr_text=ocr_text,
        caption=caption_text,
        visual_type=visual_type,
        entities=entities,
        relationships=relationships,
        architecture_notes=_architecture_notes(ocr_text, caption_text, occurrence),
        uncertain_items=uncertain_items,
        text_for_embedding=text_for_embedding,
        caption_provider=caption_provider if caption_text else ("ocr_only" if ocr_text.strip() else caption_provider),
        caption_model=caption_model,
        prompt_version=prompt_version,
        ocr_engine=ocr_engine,
        ocr_confidence=ocr_confidence,
        searchable=searchable,
        confidence=caption_confidence if caption_text else (ocr_confidence if ocr_text.strip() else None),
        ocr_cache_key=ocr_key,
        caption_cache_key=cap_key,
    )
    manifest.save_caption(result)
    return result


def build_text_for_embedding(
    *,
    asset: VisualAsset,
    occurrence: VisualOccurrence,
    ocr_text: str,
    caption: str,
    visual_type: str,
    entities: list[str],
    relationships: list[str],
    architecture_notes: list[str],
    uncertain_items: list[str],
) -> str:
    page_or_slide = ""
    if occurrence.page_number:
        page_or_slide = f"Page: {occurrence.page_number}"
    elif occurrence.slide_number:
        page_or_slide = f"Slide: {occurrence.slide_number}"
    return "\n".join(
        [
            "Visual Asset Summary",
            f"Source: {occurrence.source_path}",
            page_or_slide,
            f"Visual type: {visual_type}",
            f"Context title: {occurrence.context_title or ''}",
            "Nearby text:",
            occurrence.nearby_text or "",
            "",
            "OCR Text:",
            ocr_text,
            "",
            "Visual Caption:",
            caption,
            "",
            "Key Entities:",
            _bullet_lines(entities),
            "",
            "Relationships:",
            _bullet_lines(relationships),
            "",
            "Architecture Notes:",
            _bullet_lines(architecture_notes),
            "",
            "Uncertain Items:",
            _bullet_lines(uncertain_items),
            "",
            f"Attachment: {asset.attachment_path}",
        ]
    ).strip()


def infer_visual_type(text: str, caption: str, occurrence: VisualOccurrence | None = None) -> str:
    normalized = " ".join([text, caption, occurrence.nearby_text if occurrence else ""]).lower()
    if any(term in normalized for term in ("architecture", "構成図", "システム構成", "架构", "网络拓扑", "aws", "vpc")):
        return "architecture_diagram"
    if any(term in normalized for term in ("chart", "graph", "trend", "グラフ", "图表")):
        return "chart"
    if any(term in normalized for term in ("table", "表", "spreadsheet")):
        return "table_image"
    if any(term in normalized for term in ("screenshot", "screen", "画面")):
        return "screenshot"
    if any(term in normalized for term in ("form", "申請", "申请")):
        return "form"
    return "unknown"


def infer_entities(text: str) -> list[str]:
    candidates = []
    for token in ("AWS", "VPC", "EC2", "S3", "Lambda", "RDS", "API Gateway", "CloudWatch", "IAM", "ECS", "EKS"):
        if token.lower() in text.lower():
            candidates.append(token)
    return candidates[:20]


def infer_relationships(text: str) -> list[str]:
    relationships = []
    for marker in ("->", "→", "=>"):
        if marker in text:
            for line in text.splitlines():
                if marker in line:
                    relationships.append(line.strip())
    return relationships[:20]


def _architecture_notes(ocr_text: str, caption: str, occurrence: VisualOccurrence) -> list[str]:
    text = "\n".join(part for part in (occurrence.nearby_text or "", ocr_text, caption) if part)
    notes = []
    if "aws" in text.lower():
        notes.append("AWS-related architecture evidence is present.")
    if any(term in text.lower() for term in ("auth", "iam", "認証", "认证", "authorization")):
        notes.append("Authentication or authorization is mentioned.")
    if any(term in text.lower() for term in ("log", "monitor", "cloudwatch", "監視", "监控")):
        notes.append("Monitoring or logging is mentioned.")
    return notes


def _run_ocr(image_path: Path, ocr_engine: str | None) -> dict[str, Any]:
    if not ocr_engine:
        return {"text": "", "confidence": None, "engine": None}
    try:
        from kb.parsers.ocr import OCRProcessor

        result = OCRProcessor(ocr_engine).ocr_image(str(image_path))
        return {"text": result.text or "", "confidence": result.confidence, "engine": ocr_engine}
    except Exception as exc:
        return {"text": "", "confidence": None, "engine": ocr_engine, "error": str(exc)}


def _ocr_engine_version(ocr_engine: str | None) -> str | None:
    if not ocr_engine:
        return None
    try:
        import importlib.metadata

        return importlib.metadata.version(ocr_engine)
    except Exception:
        return "unknown"


def _local_context_caption(asset: VisualAsset, occurrence: VisualOccurrence) -> str:
    context = (occurrence.nearby_text or occurrence.context_title or "").strip()
    if not context:
        return ""
    location = f"page {occurrence.page_number}" if occurrence.page_number else f"slide {occurrence.slide_number}" if occurrence.slide_number else "source"
    return f"Local context summary for visual evidence from {location}: {context[:800]}"


def _clean_stub_caption(caption: str) -> str:
    normalized = " ".join((caption or "").strip().split()).lower()
    if not normalized or normalized in EMPTY_STUB_PHRASES:
        return ""
    return caption.strip()


def _bullet_lines(values: list[str]) -> str:
    if not values:
        return "- "
    return "\n".join(f"- {value}" for value in values)

