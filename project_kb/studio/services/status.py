from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kb.doctor import doctor_project

from .mcp_config import MCPConfigService
from .publish import PublishService
from .review import ReviewService
from .state import StateStore


class StatusService:
    def __init__(self, project_root: Path, store: StateStore, review_service: ReviewService, publish_service: PublishService) -> None:
        self.project_root = project_root.resolve()
        self.store = store
        self.review_service = review_service
        self.publish_service = publish_service

    def sources(self) -> list[dict[str, Any]]:
        sources_root = self.project_root / "sources"
        indexed = self._indexed_raw_sources()
        rows = []
        if not sources_root.exists():
            return rows
        for path in sorted(p for p in sources_root.rglob("*") if p.is_file() and p.name != ".gitkeep"):
            rel = path.relative_to(self.project_root).as_posix()
            stat = path.stat()
            status = "Indexed" if rel in indexed else "New"
            self.store.upsert_file(rel_path=rel, size=stat.st_size, mtime=stat.st_mtime, status=status)
            rows.append(
                {
                    "rel_path": rel,
                    "name": path.name,
                    "type": path.suffix.lower().lstrip(".") or "file",
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "status": status,
                }
            )
        return rows

    def project_status(self) -> dict[str, Any]:
        sources = self.sources()
        notes = self.review_service.scan_notes()
        publish = self.publish_service.preview()
        agent = MCPConfigService(self.project_root).status()
        indexed_count = sum(1 for item in sources if item["status"] == "Indexed")
        failed_count = sum(1 for item in sources if item["status"] == "Failed")
        needs_review = sum(1 for note in notes if note.status == "needs_review")
        evidence_gap = sum(1 for note in notes if note.status == "evidence_gap" or "Missing source_refs." in note.warnings)
        reviewed = sum(1 for note in notes if note.status == "reviewed")
        next_step = self._recommended_next_step(len(sources), indexed_count, needs_review, reviewed)
        return {
            "project_root": str(self.project_root),
            "sources": {"count": len(sources), "indexed": indexed_count, "failed": failed_count},
            "ai_notes": {"generated": len(notes), "needs_review": needs_review, "evidence_gaps": evidence_gap},
            "reviewed_docs": {"reviewed": reviewed, "publishable": publish["reviewed_count"]},
            "agent_knowledge": {"codex": agent["codex"], "kiro": agent["kiro"], "index_path": publish["index_path"]},
            "recommended_next_step": next_step,
        }

    def doctor(self) -> dict[str, Any]:
        return doctor_project(self.project_root / "kb" / "config.yaml")

    def _indexed_raw_sources(self) -> set[str]:
        manifest_path = self.project_root / ".lancedb_raw" / "manifest.json"
        if not manifest_path.exists():
            return set()
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return set()
        return set((manifest.get("files") or {}).keys())

    def _recommended_next_step(self, source_count: int, indexed_count: int, needs_review: int, reviewed: int) -> str:
        if source_count == 0:
            return "Upload source files to sources/."
        if indexed_count < source_count:
            return "Import sources."
        if needs_review:
            return "Continue review."
        if reviewed:
            return "Publish reviewed docs."
        return "Ask AI or add reviewed notes."

