from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import threading
import time
from typing import Any, Callable

from .command_runner import CommandEnum, CommandResult, CommandRunner
from .state import StateStore, utc_now


JOB_STATUSES = {"queued", "running", "succeeded", "failed", "cancelled", "interrupted"}
HEAVY_JOB_TYPES = {"import", "OCR", "curate", "publish", "rebuild index"}


class JobRunner:
    def __init__(self, project_root: Path, store: StateStore, command_runner: CommandRunner) -> None:
        self.project_root = project_root.resolve()
        self.store = store
        self.command_runner = command_runner
        self.store.mark_running_jobs_interrupted()

    def enqueue_command(
        self,
        *,
        job_type: str,
        command: CommandEnum,
        params: dict[str, Any] | None = None,
        start: bool = True,
        on_complete: Callable[[str, CommandResult, str], None] | None = None,
    ) -> str:
        if job_type in HEAVY_JOB_TYPES:
            active = self.store.active_heavy_job(HEAVY_JOB_TYPES)
            if active:
                raise RuntimeError(f"Heavy job already active: {active['id']}")
        job_id = self.store.create_job(job_type=job_type, command={"command": command.value, "params": params or {}})
        if start:
            thread = threading.Thread(target=self._run_job, args=(job_id, command, params or {}, on_complete), daemon=True)
            thread.start()
        return job_id

    def cancel_queued(self, job_id: str) -> None:
        row = self.store.query_one("SELECT status FROM jobs WHERE id = ?", (job_id,))
        if not row:
            raise ValueError("Job not found.")
        if row["status"] != "queued":
            raise ValueError("Only queued jobs can be cancelled.")
        self.store.update_job(job_id, status="cancelled", finished_at=utc_now())

    def _run_job(
        self,
        job_id: str,
        command: CommandEnum,
        params: dict[str, Any],
        on_complete: Callable[[str, CommandResult, str], None] | None,
    ) -> None:
        started = utc_now()
        started_perf = time.perf_counter()
        self.store.update_job(job_id, status="running", started_at=started)
        try:
            result = self.command_runner.run(command, params=params)
            status = "succeeded" if result.exit_code == 0 else "failed"
            self._write_log_files(job_id, result.stdout, result.stderr)
            if result.stdout:
                self.store.add_job_log(job_id, "stdout", _truncate_log(result.stdout))
            if result.stderr:
                self.store.add_job_log(job_id, "stderr", _truncate_log(result.stderr))
            duration_ms = int((time.perf_counter() - started_perf) * 1000)
            self.store.update_job(
                job_id,
                status=status,
                exit_code=result.exit_code,
                finished_at=utc_now(),
                duration_ms=duration_ms,
            )
            if on_complete:
                on_complete(job_id, result, status)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started_perf) * 1000)
            self._write_log_files(job_id, "", str(exc))
            self.store.add_job_log(job_id, "stderr", str(exc))
            self.store.update_job(job_id, status="failed", exit_code=1, finished_at=utc_now(), duration_ms=duration_ms)

    def _write_log_files(self, job_id: str, stdout: str, stderr: str) -> None:
        job_dir = self.store.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "stdout.log").write_text(stdout, encoding="utf-8")
        (job_dir / "stderr.log").write_text(stderr, encoding="utf-8")


def _truncate_log(value: str, max_chars: int = 4000) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"
