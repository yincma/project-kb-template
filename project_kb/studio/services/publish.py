from __future__ import annotations

from pathlib import Path
import json
from typing import Any

from .review import ReviewService, missing_source_refs
from .state import StateStore, utc_now


SKIPPED_STATUSES = {"needs_review", "evidence_gap", "possible_duplicate"}


class PublishService:
    def __init__(self, project_root: Path, store: StateStore, review_service: ReviewService) -> None:
        self.project_root = project_root.resolve()
        self.store = store
        self.review_service = review_service

    def preview(self) -> dict[str, Any]:
        notes = self.review_service.scan_notes()
        reviewed = [note for note in notes if note.status == "reviewed"]
        skipped = [note for note in notes if note.status in SKIPPED_STATUSES]
        missing_refs = [note for note in reviewed if missing_source_refs(note.source_refs)]
        by_status: dict[str, int] = {}
        for note in notes:
            by_status[note.status] = by_status.get(note.status, 0) + 1
        return {
            "reviewed_count": len(reviewed),
            "skipped_count": len(skipped),
            "skipped_by_status": {status: by_status.get(status, 0) for status in sorted(SKIPPED_STATUSES)},
            "missing_source_refs_count": len(missing_refs),
            "missing_source_refs": [note.rel_path for note in missing_refs],
            "config_path": "kb/config.yaml",
            "index_path": ".lancedb",
            "generated_at": utc_now(),
        }

    def write_report(self, job_id: str, extra: dict[str, Any] | None = None) -> Path:
        job_dir = self.store.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        report_path = job_dir / "publish_report.json"
        payload = {**self.preview(), "job_id": job_id, "extra": extra or {}}
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.store.execute(
            "INSERT INTO publish_runs (id, status, job_id, report_path, summary_json) VALUES (?, ?, ?, ?, ?)",
            (job_id, "created", job_id, str(report_path.relative_to(self.project_root)), json.dumps(payload, ensure_ascii=False)),
        )
        return report_path

