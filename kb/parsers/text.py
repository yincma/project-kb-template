from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import tomllib

from kb.parsers.registry import ParsedDocument, ParsedSection


def parse_text_file(path: Path) -> ParsedDocument:
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

    section = ParsedSection(
        text=text,
        parser_name="text",
        source_format=ext,
        extraction_method="text",
        asset_type="document",
    )
    return ParsedDocument(path=path, sections=[section], warnings=warnings)


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
