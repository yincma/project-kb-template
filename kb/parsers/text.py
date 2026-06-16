from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import re
import tomllib

from kb.multimodal.extraction import new_visual_stats, register_visual_bytes
from kb.parsers.registry import ParsedDocument, ParsedSection


def parse_text_file(path: Path, config=None) -> ParsedDocument:
    warnings: list[str] = []
    ext = path.suffix.lower()
    try:
        if ext == ".csv":
            text = _with_document_context(path, _parse_csv(path))
        elif ext == ".json":
            text = _with_document_context(path, _parse_json(path))
        elif ext in {".yaml", ".yml"}:
            text = _with_document_context(path, _parse_yaml(path))
        elif ext == ".toml":
            text = _with_document_context(path, _parse_toml(path))
        elif ext == ".ini":
            text = _with_document_context(path, _read_text(path))
        else:
            text = _read_text(path)
    except Exception as exc:
        warnings.append(f"Failed to parse {path}: {exc}")
        return ParsedDocument(path=path, warnings=warnings)

    frontmatter, body = _markdown_frontmatter(text) if ext == ".md" else ({}, text)
    metadata = _frontmatter_metadata(frontmatter)
    section = ParsedSection(
        text=body if frontmatter else text,
        parser_name="text",
        source_format=ext,
        extraction_method="text",
        asset_type=metadata.get("asset_type", "document"),
        page_number=metadata.get("page_number"),
        slide_number=metadata.get("slide_number"),
        metadata=metadata,
    )
    sections = [section]
    if ext == ".md" and _should_parse_referenced_attachments(frontmatter, config):
        visual_stats = new_visual_stats()
        for index, image_path in enumerate(_referenced_images(body, path, config), start=1):
            try:
                visual_section = register_visual_bytes(
                    image_bytes=image_path.read_bytes(),
                    source_path=image_path,
                    config=config,
                    generated_from="referenced_attachment",
                    occurrence_index=index,
                    context_title=path.stem,
                    nearby_text=body[:2000],
                    ext=image_path.suffix.lower(),
                    stats=visual_stats,
                )
                if visual_section:
                    sections.append(visual_section)
            except Exception as exc:
                warnings.append(f"Failed to parse referenced image {image_path}: {exc}")
        warnings.extend(str(warning) for warning in visual_stats.get("warnings", []))
    return ParsedDocument(path=path, sections=sections, warnings=warnings)


def _read_text(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(errors="replace")


def _parse_csv(path: Path) -> str:
    content = _read_text(path)
    reader = csv.reader(io.StringIO(content))
    rows = ["\t".join(cell.strip() for cell in row) for row in reader]
    return "\n".join(row for row in rows if row.strip())


def _parse_json(path: Path) -> str:
    content = _read_text(path)
    parsed = json.loads(content)
    return json.dumps(parsed, ensure_ascii=False, indent=2)


def _parse_yaml(path: Path) -> str:
    try:
        import yaml
    except Exception:
        return _read_text(path)

    content = _read_text(path)
    parsed = yaml.safe_load(content)
    return yaml.safe_dump(parsed, allow_unicode=True, sort_keys=False)


def _parse_toml(path: Path) -> str:
    parsed = tomllib.loads(_read_text(path))
    return json.dumps(parsed, ensure_ascii=False, indent=2)


def _with_document_context(path: Path, text: str) -> str:
    doc_type = infer_document_type(path)
    if not doc_type:
        return text
    return f"Document type: {doc_type}\nSource file: {path.name}\n\n{text}"


def _markdown_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    try:
        _, yaml_text, body = text.split("---", 2)
    except ValueError:
        return {}, text
    try:
        import yaml

        data = yaml.safe_load(yaml_text) or {}
    except Exception:
        return {}, text
    if not isinstance(data, dict):
        return {}, text
    return data, body.lstrip("\n")


def _frontmatter_metadata(frontmatter: dict) -> dict:
    if frontmatter.get("kb_type") != "visual_summary":
        return {}
    metadata = {
        "kb_type": "visual_summary",
        "asset_type": "visual",
        "visual_type": frontmatter.get("visual_type"),
        "attachment_path": frontmatter.get("attachment_path"),
        "image_hash": frontmatter.get("image_hash"),
        "asset_id": frontmatter.get("asset_id"),
        "occurrence_id": frontmatter.get("occurrence_id"),
        "caption_provider": frontmatter.get("caption_provider"),
        "caption_model": frontmatter.get("caption_model"),
        "prompt_version": frontmatter.get("prompt_version"),
        "confidence": frontmatter.get("confidence"),
        "searchable": True,
    }
    if frontmatter.get("source_path"):
        metadata["source_path"] = frontmatter.get("source_path")
    for field in ("page_number", "slide_number"):
        value = frontmatter.get(field)
        if value not in (None, ""):
            metadata[field] = int(value)
    return {key: value for key, value in metadata.items() if value is not None}


def _should_parse_referenced_attachments(frontmatter: dict, config) -> bool:
    if frontmatter.get("kb_type") == "visual_summary":
        return False
    multimodal = _cfg_value(_cfg_value(_cfg_value(config, "parsing", None), "multimodal", None), "curated_attachments", None)
    root_multimodal = _cfg_value(_cfg_value(config, "parsing", None), "multimodal", None)
    return bool(
        _cfg_value(root_multimodal, "enabled", False)
        and _cfg_value(multimodal, "mode", "off") == "referenced_only"
    )


def _referenced_images(markdown: str, note_path: Path, config) -> list[Path]:
    refs = []
    refs.extend(match.group(1).strip() for match in re.finditer(r"!\[\[([^\]]+)\]\]", markdown))
    refs.extend(match.group(1).strip() for match in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", markdown))
    images: list[Path] = []
    seen: set[Path] = set()
    for ref in refs:
        ref = ref.split("|", 1)[0].strip()
        if not ref or "://" in ref:
            continue
        candidate = _resolve_ref(ref, note_path, config)
        if candidate and candidate.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} and candidate.exists():
            resolved = candidate.resolve()
            if resolved not in seen and _allowed_attachment(resolved, config):
                seen.add(resolved)
                images.append(resolved)
    return images


def _resolve_ref(ref: str, note_path: Path, config) -> Path | None:
    root = getattr(config, "root_path", None)
    if root is None:
        return None
    if ref.startswith("../") or ref.startswith("./"):
        return (note_path.parent / ref).resolve()
    if ref.startswith("docs/"):
        return (root / ref).resolve()
    return (root / "docs" / ref).resolve()


def _allowed_attachment(path: Path, config) -> bool:
    root = config.root_path
    allowed = _cfg_value(
        _cfg_value(_cfg_value(_cfg_value(config, "parsing", None), "multimodal", None), "curated_attachments", None),
        "allowed_roots",
        ["docs/_attachments/kb_assets"],
    )
    for rel in allowed:
        allowed_root = (root / rel).resolve()
        if path == allowed_root or allowed_root in path.parents:
            return True
    return False


def _cfg_value(config, name: str, default=None):
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(name, default)
    return getattr(config, name, default)


def infer_document_type(path: Path) -> str | None:
    normalized = path.as_posix().lower()
    name = path.name.lower()
    parent = path.parent.as_posix().lower()

    if "risk" in normalized or "风险" in normalized:
        return "risk register"
    if "milestone" in normalized or "里程碑" in normalized:
        return "milestones"
    if "owner" in normalized or "负责人" in normalized:
        return "owners"
    if "architecture" in normalized or "架构" in normalized or name.startswith("adr"):
        return "architecture decisions"
    if "environment" in normalized or "配置" in parent:
        return "configuration"
    if "schema" in normalized:
        return "database schema"
    return None
