from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any
from urllib import request

from kb.multimodal.assets import (
    CaptionResult,
    VisualAsset,
    VisualOccurrence,
    caption_cache_key,
    hash_text,
    ocr_cache_key,
)
from kb.multimodal.manifest import MultimodalManifest
from kb.multimodal.prompts import VISION_CAPTION_PROMPT, VISION_CAPTION_PROMPT_VERSION

EMPTY_STUB_PHRASES = {
    "this is an image asset",
    "no caption available",
    "image could not be processed",
}

OPENAI_COMPATIBLE_TRANSPORT = None


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
    ocr_engine = config.parsing.ocr.engine if (config.parsing.ocr.enabled or provider != "stub") else None
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

    cached_provider = provider
    if provider in {"openai_compatible", "azure", "gemini", "cloud_vision"} and not vision.allow_external_vision:
        cached_provider = "external_blocked"
    cap_key = caption_cache_key(
        image_hash=asset.image_hash,
        caption_provider=cached_provider,
        caption_model=vision.model,
        prompt_version=prompt_version,
        ocr_cache_key_value=ocr_key,
        ocr_text=ocr_text,
        extraction_method=extraction_method,
    )
    cached = manifest.load_caption(cap_key)
    if cached is not None:
        return CaptionResult(**cached)

    provider_payload: dict[str, Any] = {}
    caption_text = ""
    caption_confidence: float | None = None
    caption_model = vision.model
    caption_provider = provider

    if provider in {"openai_compatible", "azure", "gemini", "cloud_vision"} and not vision.allow_external_vision:
        caption_provider = "external_blocked"
    elif provider == "openai_compatible" and vision.enabled:
        provider_payload = _caption_with_openai_compatible(image_path=image_path, vision=vision, occurrence=occurrence)
        caption_text = str(provider_payload.get("caption") or "")
        caption_confidence = provider_payload.get("confidence")
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

    visual_type = str(provider_payload.get("visual_type") or infer_visual_type(ocr_text, caption_text, occurrence))
    entities = _string_list(provider_payload.get("entities")) or infer_entities(
        ocr_text + "\n" + caption_text + "\n" + (occurrence.nearby_text or "")
    )
    relationships = _string_list(provider_payload.get("relationships")) or infer_relationships(ocr_text + "\n" + caption_text)
    architecture_notes = _string_list(provider_payload.get("architecture_notes")) or _architecture_notes(
        ocr_text, caption_text, occurrence
    )
    uncertain_items = _string_list(provider_payload.get("uncertain_items"))
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
            architecture_notes=architecture_notes,
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
        architecture_notes=architecture_notes,
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


def _caption_with_openai_compatible(*, image_path: Path, vision, occurrence: VisualOccurrence) -> dict[str, Any]:
    api_key_env = getattr(vision, "api_key_env", "OPENAI_API_KEY") or "OPENAI_API_KEY"
    api_key = os.environ.get(api_key_env)
    if not api_key:
        return {"caption": "", "uncertain_items": [f"Missing API key env var: {api_key_env}"]}

    model = getattr(vision, "model", None) or "gpt-4.1-mini"
    base_url = (getattr(vision, "base_url", None) or "https://api.openai.com/v1").rstrip("/")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            VISION_CAPTION_PROMPT
                            + "\nReturn only compact JSON with keys: caption, visual_type, entities, "
                            "relationships, architecture_notes, uncertain_items, confidence.\n"
                            f"Source context title: {occurrence.context_title or ''}\n"
                            f"Nearby text: {(occurrence.nearby_text or '')[:1500]}"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_data_url(image_path)},
                    },
                ],
            }
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    transport = OPENAI_COMPATIBLE_TRANSPORT or _default_openai_transport
    response = transport(url=f"{base_url}/chat/completions", headers=headers, payload=payload)
    return _parse_openai_compatible_response(response)


def _default_openai_transport(*, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with request.urlopen(req, timeout=60) as response:  # noqa: S310 - explicit opt-in external provider
        return json.loads(response.read().decode("utf-8"))


def _parse_openai_compatible_response(response: dict[str, Any]) -> dict[str, Any]:
    content = (
        response.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    if isinstance(content, list):
        content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    if isinstance(content, dict):
        parsed = content
    else:
        try:
            parsed = json.loads(str(content))
        except Exception:
            parsed = {"caption": str(content)}
    return {
        "caption": str(parsed.get("caption") or ""),
        "visual_type": str(parsed.get("visual_type") or "unknown"),
        "entities": _string_list(parsed.get("entities")),
        "relationships": _string_list(parsed.get("relationships")),
        "architecture_notes": _string_list(parsed.get("architecture_notes")),
        "uncertain_items": _string_list(parsed.get("uncertain_items")),
        "confidence": _float_or_none(parsed.get("confidence")),
    }


def _image_data_url(image_path: Path) -> str:
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(image_path.suffix.lower(), "image/png")
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


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


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [line.strip(" -") for line in value.splitlines() if line.strip(" -")]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_stub_caption(caption: str) -> str:
    normalized = " ".join((caption or "").strip().split()).lower()
    if not normalized or normalized in EMPTY_STUB_PHRASES:
        return ""
    return caption.strip()


def _bullet_lines(values: list[str]) -> str:
    if not values:
        return "- "
    return "\n".join(f"- {value}" for value in values)
