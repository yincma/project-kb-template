from __future__ import annotations

from pathlib import Path
from typing import Any

from kb.multimodal.extraction import new_visual_stats, register_visual_bytes
from kb.parsers.registry import ParsedDocument


def parse_image_file(path: Path, config: Any | None = None) -> ParsedDocument:
    warnings: list[str] = []
    if not _multimodal_enabled(config):
        return ParsedDocument(path=path, warnings=["Skipped image visual parsing because multimodal.enabled=false."])

    stats = new_visual_stats()
    try:
        image_bytes = path.read_bytes()
    except Exception as exc:
        return ParsedDocument(path=path, warnings=[f"Failed to read image {path}: {exc}"])

    section = register_visual_bytes(
        image_bytes=image_bytes,
        source_path=path,
        config=config,
        generated_from="standalone_image",
        occurrence_index=1,
        context_title=path.stem,
        nearby_text=f"Standalone image file: {path.name}",
        ext=path.suffix.lower(),
        stats=stats,
    )
    warnings.extend(stats.get("warnings", []))
    if section is None:
        warnings.append(f"Image did not produce searchable visual text: {path}")
        return ParsedDocument(path=path, warnings=warnings)
    return ParsedDocument(path=path, sections=[section], warnings=warnings)


def _multimodal_enabled(config: Any | None) -> bool:
    return bool(_cfg_value(_cfg_value(_cfg_value(config, "parsing", None), "multimodal", None), "enabled", False))


def _cfg_value(config: Any | None, name: str, default: Any = None) -> Any:
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(name, default)
    return getattr(config, name, default)

