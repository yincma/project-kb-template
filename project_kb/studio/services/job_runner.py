from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Any, Callable

from .command_runner import CommandEnum, CommandResult, CommandRunner
from .state import StateStore, utc_now


JOB_STATUSES = {"queued", "running", "succeeded", "failed", "cancelled", "interrupted"}
HEAVY_JOB_TYPES = {"import", "OCR", "curate", "publish", "rebuild index"}
STREAMING_COMMANDS = {CommandEnum.IMPORT_SOURCES, CommandEnum.PUBLISH_REVIEWED_DOCS}
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
INDEXING_RE = re.compile(r"Indexing file\s+(\d+)/(\d+):\s*(.+)")
EMBEDDING_RE = re.compile(r"Embedding\s+([^:]+)")


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
        self.store.update_job(job_id, status="running", started_at=started, progress_message="Starting", progress_updated_at=started)
        try:
            streaming = command in STREAMING_COMMANDS
            result = self._run_streaming_command(job_id, command, params) if streaming else self.command_runner.run(command, params=params)
            status = "succeeded" if result.exit_code == 0 else "failed"
            if not streaming:
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
                progress_percent=100 if status == "succeeded" else None,
                progress_message="Completed" if status == "succeeded" else "Failed",
                progress_updated_at=utc_now(),
            )
            if on_complete:
                on_complete(job_id, result, status)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started_perf) * 1000)
            self._write_log_files(job_id, "", str(exc))
            self.store.add_job_log(job_id, "stderr", str(exc))
            self.store.update_job(
                job_id,
                status="failed",
                exit_code=1,
                finished_at=utc_now(),
                duration_ms=duration_ms,
                progress_percent=None,
                progress_message=str(exc),
                progress_updated_at=utc_now(),
            )

    def _run_streaming_command(self, job_id: str, command: CommandEnum, params: dict[str, Any]) -> CommandResult:
        argv = self.command_runner.argv_for(command, params=params)
        process = subprocess.Popen(
            argv,
            shell=False,
            cwd=str(self.command_runner.project_root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )
        job_dir = self.store.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = job_dir / "stdout.log"
        stderr_path = job_dir / "stderr.log"
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        def reader(stream, stream_name: str, path: Path, chunks: list[str]) -> None:
            if stream is None:
                return
            with path.open("a", encoding="utf-8") as file:
                for raw in iter(stream.readline, ""):
                    file.write(raw)
                    file.flush()
                    chunks.append(raw)
                    clean = _clean_line(raw)
                    if clean:
                        self.store.add_job_log(job_id, stream_name, _truncate_log(clean))
                    progress = parse_progress_line(raw)
                    if progress:
                        self.store.update_job(job_id, **progress, progress_updated_at=utc_now())

        threads = [
            threading.Thread(target=reader, args=(process.stdout, "stdout", stdout_path, stdout_chunks), daemon=True),
            threading.Thread(target=reader, args=(process.stderr, "stderr", stderr_path, stderr_chunks), daemon=True),
        ]
        for thread in threads:
            thread.start()
        exit_code = process.wait()
        for thread in threads:
            thread.join()
        return CommandResult(command, argv, int(exit_code), "".join(stdout_chunks), "".join(stderr_chunks))

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


def parse_progress_line(value: str) -> dict[str, Any] | None:
    clean = _clean_line(value)
    match = INDEXING_RE.search(clean)
    if match:
        current = int(match.group(1))
        total = max(1, int(match.group(2)))
        percent = round((current / total) * 100, 1)
        return {
            "progress_current": current,
            "progress_total": total,
            "progress_percent": percent,
            "progress_message": f"Indexing file {current}/{total}: {match.group(3).strip()}",
        }
    match = EMBEDDING_RE.search(clean)
    if match:
        return {
            "progress_percent": None,
            "progress_message": f"Vectorizing {match.group(1).strip()}",
        }
    if "Done." in clean:
        return {"progress_percent": 100, "progress_message": "Completed"}
    return None


def _clean_line(value: str) -> str:
    return ANSI_RE.sub("", value.replace("\r", "\n")).strip()
