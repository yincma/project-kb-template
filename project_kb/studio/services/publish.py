from __future__ import annotations

from pathlib import Path
import json
from typing import Any

from kb.store import load_config, load_manifest

from .review import ReviewService, missing_source_refs
from .state import StateStore, utc_now


SKIPPED_STATUSES = {"needs_review", "evidence_gap", "possible_duplicate"}


class PublishService:
    def __init__(self, project_root: Path, store: StateStore, review_service: ReviewService) -> None:
        self.project_root = project_root.resolve()
        self.store = store
        self.review_service = review_service

    def preview(self) -> dict[str, Any]:
        allowed_statuses = self.allowed_review_statuses()
        notes = self.review_service.scan_notes()
        reviewed = [note for note in notes if note.status.lower() in allowed_statuses]
        skipped = [note for note in notes if note.status in SKIPPED_STATUSES or note.status.lower() not in allowed_statuses]
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
            "allowed_review_statuses": sorted(allowed_statuses),
            "config_path": "kb/config.yaml",
            "index_path": ".lancedb",
            "generated_at": utc_now(),
        }

    def report_path(self, job_id: str) -> Path:
        return self.store.jobs_dir / job_id / "publish_report.json"

    def write_report(self, job_id: str, extra: dict[str, Any] | None = None) -> Path:
        job_dir = self.store.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        report_path = self.report_path(job_id)
        payload = {
            **self.preview(),
            "job_id": job_id,
            "actual_index": self.actual_index_state(),
            "extra": extra or {},
        }
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.store.execute(
            """
            INSERT INTO publish_runs (id, status, job_id, report_path, summary_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                report_path=excluded.report_path,
                summary_json=excluded.summary_json
            """,
            (
                job_id,
                str(extra.get("job_status", "created") if extra else "created"),
                job_id,
                str(report_path.relative_to(self.project_root)),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        return report_path

    def allowed_review_statuses(self) -> set[str]:
        try:
            cfg = load_config(self.project_root / "kb" / "config.yaml")
            return {str(value).strip().lower() for value in cfg.curation.index_review_statuses if str(value).strip()}
        except Exception:
            return {"reviewed", "approved"}

    def actual_index_state(self) -> dict[str, Any]:
        try:
            cfg = load_config(self.project_root / "kb" / "config.yaml")
            manifest = load_manifest(cfg)
            files = sorted(str(path) for path in manifest.get("files", {}))
            return {
                "config_path": "kb/config.yaml",
                "manifest_path": str(cfg.manifest_path.relative_to(self.project_root)),
                "indexed_count": len(files),
                "indexed_source_paths": files,
            }
        except Exception as exc:
            return {"config_path": "kb/config.yaml", "error": str(exc)}
