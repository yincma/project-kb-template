from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .safety import ensure_inside
from .state import StateStore, stable_id


REVIEW_STATUSES = {"needs_review", "reviewed", "evidence_gap", "possible_duplicate"}


@dataclass
class NoteRecord:
    id: str
    rel_path: str
    status: str
    title: str
    source_refs: list[dict[str, Any]]
    warnings: list[str]
    body_preview: str


class ReviewService:
    def __init__(self, project_root: Path, store: StateStore) -> None:
        self.project_root = project_root.resolve()
        self.docs_dir = (self.project_root / "docs").resolve(strict=False)
        self.store = store

    def scan_notes(self) -> list[NoteRecord]:
        notes: list[NoteRecord] = []
        if not self.docs_dir.exists():
            return notes
        for path in sorted(self.docs_dir.rglob("*.md")):
            if _skip_note(path):
                continue
            rel_path = path.relative_to(self.project_root).as_posix()
            frontmatter, body = read_markdown(path)
            status = str(frontmatter.get("status") or frontmatter.get("review_status") or "needs_review")
            source_refs = normalize_source_refs(frontmatter.get("source_refs"))
            warnings = note_warnings(status, source_refs)
            record = NoteRecord(
                id=stable_id(rel_path),
                rel_path=rel_path,
                status=status,
                title=_title_from_body(body, path.stem),
                source_refs=source_refs,
                warnings=warnings,
                body_preview=body.strip()[:2000],
            )
            notes.append(record)
            self.store.upsert_note(rel_path=rel_path, status=status, source_refs=source_refs, warnings=warnings)
        return notes

    def get_note_by_id(self, note_id: str) -> NoteRecord:
        notes = self.scan_notes()
        for note in notes:
            if note.id == note_id:
                return note
        raise ValueError("Note not found.")

    def approve(self, note_id: str, *, override_missing_refs: bool = False, reviewer: str = "project-kb-studio") -> NoteRecord:
        note = self.get_note_by_id(note_id)
        if note.status != "needs_review":
            raise ValueError("Only needs_review notes can be approved.")
        if missing_source_refs(note.source_refs) and not override_missing_refs:
            raise ValueError("Cannot approve without source_refs unless override is explicit.")
        path = ensure_inside(self.project_root / note.rel_path, self.docs_dir)
        frontmatter, body = read_markdown(path)
        now = datetime.now(timezone.utc).isoformat()
        frontmatter["status"] = "reviewed"
        frontmatter["updated_at"] = now
        frontmatter["reviewed_at"] = now
        frontmatter["reviewed_by"] = reviewer
        frontmatter.setdefault("created_by", "project-kb-studio")
        if missing_source_refs(note.source_refs):
            warnings = list(frontmatter.get("review_warnings") or [])
            warnings.append("Approved with missing source_refs override.")
            frontmatter["review_warnings"] = warnings
        write_markdown(path, frontmatter, body)
        return self.get_note_by_id(note_id)

    def mark_status(self, note_id: str, status: str) -> NoteRecord:
        if status not in {"evidence_gap", "possible_duplicate"}:
            raise ValueError("Unsupported review status.")
        note = self.get_note_by_id(note_id)
        if note.status == "reviewed":
            raise ValueError("Reviewed notes require backup and explicit high-risk confirmation before modification.")
        path = ensure_inside(self.project_root / note.rel_path, self.docs_dir)
        frontmatter, body = read_markdown(path)
        frontmatter["status"] = status
        frontmatter["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_markdown(path, frontmatter, body)
        return self.get_note_by_id(note_id)


def read_markdown(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}, text
    rest = text[4:]
    marker = rest.find("\n---")
    if marker < 0:
        return {}, text
    yaml_text = rest[:marker]
    body = rest[marker + 4 :]
    if body.startswith("\n"):
        body = body[1:]
    data = yaml.safe_load(yaml_text) or {}
    return data if isinstance(data, dict) else {}, body


def write_markdown(path: Path, frontmatter: dict[str, Any], body: str) -> None:
    rendered = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
    path.write_text(f"---\n{rendered}\n---\n\n{body.lstrip()}", encoding="utf-8")


def normalize_source_refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized = []
    for item in value:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "source_path": item.get("source_path") or item.get("file") or "",
                "heading": item.get("heading") or item.get("section") or "",
                "chunk_index": item.get("chunk_index"),
                "page_number": item.get("page_number") or item.get("page"),
                "slide_number": item.get("slide_number"),
                "sheet_name": item.get("sheet_name"),
                "cell_range": item.get("cell_range"),
                "quote_id": item.get("quote_id"),
            }
        )
    return normalized


def missing_source_refs(source_refs: list[dict[str, Any]]) -> bool:
    if not source_refs:
        return True
    return not any(str(ref.get("source_path") or "").strip() for ref in source_refs)


def note_warnings(status: str, source_refs: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if status == "reviewed" and missing_source_refs(source_refs):
        warnings.append("Reviewed note is missing source_refs.")
    elif status != "reviewed" and missing_source_refs(source_refs):
        warnings.append("Missing source_refs.")
    return warnings


def _skip_note(path: Path) -> bool:
    parts = set(path.parts)
    return "_attachments" in parts or "_templates" in parts or ".obsidian" in parts


def _title_from_body(body: str, fallback: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or fallback
    return fallback

