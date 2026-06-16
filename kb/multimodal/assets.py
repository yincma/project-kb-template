from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any


@dataclass
class VisualAsset:
    asset_id: str
    image_hash: str
    attachment_path: str
    width: int
    height: int
    mime_type: str
    ext: str
    generated_from: str
    source_hash: str
    stale: bool = False
    ocr_cache_key: str | None = None
    caption_cache_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VisualOccurrence:
    occurrence_id: str
    source_path: str
    page_number: int | None
    slide_number: int | None
    sheet_name: str | None
    bbox: list[float] | None
    occurrence_index: int
    asset_id: str
    image_hash: str
    extraction_method: str
    context_title: str | None = None
    nearby_text: str | None = None
    stale: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CaptionResult:
    asset_id: str
    occurrence_id: str
    image_hash: str
    ocr_text: str = ""
    caption: str = ""
    visual_type: str = "unknown"
    entities: list[str] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)
    architecture_notes: list[str] = field(default_factory=list)
    uncertain_items: list[str] = field(default_factory=list)
    text_for_embedding: str = ""
    caption_provider: str = "stub"
    caption_model: str | None = None
    prompt_version: str = "vision-caption-v1"
    ocr_engine: str | None = None
    ocr_confidence: float | None = None
    searchable: bool = False
    confidence: float | None = None
    ocr_cache_key: str | None = None
    caption_cache_key: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RenderDecision:
    should_render: bool
    reasons: list[str]
    native_text_chars: int
    image_count: int
    image_area_ratio: float
    drawing_count: int
    keyword_hits: list[str]
    mode: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def stable_asset_id(image_hash: str) -> str:
    return "asset_" + image_hash[:16]


def stable_occurrence_id(
    *,
    source_path: str,
    page_number: int | None = None,
    slide_number: int | None = None,
    sheet_name: str | None = None,
    occurrence_index: int = 0,
    image_hash: str,
    extraction_method: str,
) -> str:
    raw = json.dumps(
        {
            "source_path": source_path,
            "page_number": page_number,
            "slide_number": slide_number,
            "sheet_name": sheet_name,
            "occurrence_index": occurrence_index,
            "image_hash": image_hash,
            "extraction_method": extraction_method,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return "occ_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ocr_cache_key(
    *,
    image_hash: str,
    ocr_engine: str | None,
    ocr_engine_version: str | None,
    render_dpi: int | None,
    extraction_method: str,
) -> str:
    raw = {
        "image_hash": image_hash,
        "ocr_engine": ocr_engine or "none",
        "ocr_engine_version": ocr_engine_version or "unknown",
        "render_dpi": render_dpi,
        "extraction_method": extraction_method,
    }
    return "ocr_" + hashlib.sha256(json.dumps(raw, sort_keys=True).encode("utf-8")).hexdigest()


def caption_cache_key(
    *,
    image_hash: str,
    caption_provider: str,
    caption_model: str | None,
    prompt_version: str,
    ocr_cache_key_value: str | None,
    ocr_text: str,
    extraction_method: str,
) -> str:
    raw = {
        "image_hash": image_hash,
        "caption_provider": caption_provider,
        "caption_model": caption_model or "",
        "prompt_version": prompt_version,
        "ocr_cache_key": ocr_cache_key_value or "",
        "ocr_text_hash": hash_text(ocr_text or ""),
        "extraction_method": extraction_method,
    }
    return "caption_" + hashlib.sha256(json.dumps(raw, sort_keys=True).encode("utf-8")).hexdigest()


def relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)

