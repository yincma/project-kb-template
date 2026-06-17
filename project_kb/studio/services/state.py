from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
import uuid


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    """SQLite state for Studio cache, settings, jobs, and logs."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.state_dir = self.project_root / ".project-kb"
        self.jobs_dir = self.state_dir / "jobs"
        self.logs_dir = self.state_dir / "logs"
        self.locks_dir = self.state_dir / "locks"
        self.cache_dir = self.state_dir / "cache"
        self.db_path = self.state_dir / "state.sqlite"
        self.ensure_directories()
        self.initialize()

    def ensure_directories(self) -> None:
        for path in (self.state_dir, self.jobs_dir, self.logs_dir, self.locks_dir, self.cache_dir):
            path.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        schema = [
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                root_path TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY,
                rel_path TEXT NOT NULL UNIQUE,
                size INTEGER NOT NULL,
                mtime REAL NOT NULL,
                status TEXT NOT NULL,
                sha256 TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY,
                rel_path TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                source_refs_json TEXT NOT NULL DEFAULT '[]',
                warnings_json TEXT NOT NULL DEFAULT '[]',
                synced_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS review_items (
                id TEXT PRIMARY KEY,
                note_path TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                confidence TEXT,
                warnings_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS ingest_runs (
                id TEXT PRIMARY KEY,
                config_path TEXT NOT NULL,
                status TEXT NOT NULL,
                job_id TEXT,
                started_at TEXT,
                finished_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS curation_runs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                job_id TEXT,
                summary_json TEXT NOT NULL DEFAULT '{}'
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS publish_runs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                job_id TEXT,
                report_path TEXT,
                summary_json TEXT NOT NULL DEFAULT '{}'
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY,
                title TEXT,
                source_mode TEXT NOT NULL,
                answer_mode TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                source_refs_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                status TEXT NOT NULL,
                command TEXT NOT NULL,
                exit_code INTEGER,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                duration_ms INTEGER
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS job_logs (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                stream TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS agent_configs (
                id TEXT PRIMARY KEY,
                agent TEXT NOT NULL,
                config_path TEXT NOT NULL,
                status TEXT NOT NULL,
                last_checked_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        ]
        with self.connect() as conn:
            for statement in schema:
                conn.execute(statement)
            now = utc_now()
            conn.execute(
                """
                INSERT INTO projects (id, root_path, name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(root_path) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at
                """,
                ("default", str(self.project_root), self.project_root.name or "Demo Project", now, now),
            )

    def row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        return None if row is None else dict(row)

    def query_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as conn:
            return self.row_to_dict(conn.execute(sql, params).fetchone())

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self.connect() as conn:
            conn.execute(sql, params)

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            """
            INSERT INTO settings (key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
            """,
            (key, json.dumps(value, ensure_ascii=False), utc_now()),
        )

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value_json FROM settings WHERE key = ?", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value_json"])
        except json.JSONDecodeError:
            return default

    def get_settings(self) -> dict[str, Any]:
        rows = self.query_all("SELECT key, value_json FROM settings")
        settings: dict[str, Any] = {}
        for row in rows:
            try:
                settings[row["key"]] = json.loads(row["value_json"])
            except json.JSONDecodeError:
                settings[row["key"]] = None
        return settings

    def upsert_file(self, *, rel_path: str, size: int, mtime: float, status: str, sha256: str | None = None, metadata: dict[str, Any] | None = None) -> None:
        self.execute(
            """
            INSERT INTO files (id, rel_path, size, mtime, status, sha256, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(rel_path) DO UPDATE SET
                size=excluded.size,
                mtime=excluded.mtime,
                status=excluded.status,
                sha256=excluded.sha256,
                metadata_json=excluded.metadata_json
            """,
            (
                stable_id(rel_path),
                rel_path,
                int(size),
                float(mtime),
                status,
                sha256,
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )

    def upsert_note(self, *, rel_path: str, status: str, source_refs: list[dict[str, Any]], warnings: list[str]) -> str:
        note_id = stable_id(rel_path)
        now = utc_now()
        self.execute(
            """
            INSERT INTO notes (id, rel_path, status, source_refs_json, warnings_json, synced_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(rel_path) DO UPDATE SET
                status=excluded.status,
                source_refs_json=excluded.source_refs_json,
                warnings_json=excluded.warnings_json,
                synced_at=excluded.synced_at
            """,
            (note_id, rel_path, status, json.dumps(source_refs, ensure_ascii=False), json.dumps(warnings, ensure_ascii=False), now),
        )
        self.execute(
            """
            INSERT INTO review_items (id, note_path, status, confidence, warnings_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(note_path) DO UPDATE SET
                status=excluded.status,
                confidence=excluded.confidence,
                warnings_json=excluded.warnings_json,
                updated_at=excluded.updated_at
            """,
            (note_id, rel_path, status, None, json.dumps(warnings, ensure_ascii=False), now),
        )
        return note_id

    def create_job(self, *, job_type: str, command: dict[str, Any], status: str = "queued") -> str:
        job_id = uuid.uuid4().hex
        self.execute(
            """
            INSERT INTO jobs (id, type, status, command, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job_id, job_type, status, json.dumps(command, ensure_ascii=False), utc_now()),
        )
        (self.jobs_dir / job_id).mkdir(parents=True, exist_ok=True)
        return job_id

    def update_job(self, job_id: str, **fields: Any) -> None:
        allowed = {"status", "exit_code", "started_at", "finished_at", "duration_ms"}
        assignments = []
        values: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"Unsupported job field: {key}")
            assignments.append(f"{key} = ?")
            values.append(value)
        if not assignments:
            return
        values.append(job_id)
        self.execute(f"UPDATE jobs SET {', '.join(assignments)} WHERE id = ?", tuple(values))

    def add_job_log(self, job_id: str, stream: str, message: str) -> None:
        self.execute(
            "INSERT INTO job_logs (id, job_id, stream, message, created_at) VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, job_id, stream, message, utc_now()),
        )

    def active_heavy_job(self, heavy_types: set[str]) -> dict[str, Any] | None:
        placeholders = ",".join("?" for _ in heavy_types)
        rows = self.query_all(
            f"SELECT * FROM jobs WHERE status IN ('queued', 'running') AND type IN ({placeholders}) ORDER BY created_at LIMIT 1",
            tuple(sorted(heavy_types)),
        )
        return rows[0] if rows else None

    def mark_running_jobs_interrupted(self) -> int:
        rows = self.query_all("SELECT id FROM jobs WHERE status = 'running'")
        for row in rows:
            self.update_job(row["id"], status="interrupted", finished_at=utc_now())
        return len(rows)


def stable_id(value: str) -> str:
    import hashlib

    return hashlib.sha1(value.encode("utf-8")).hexdigest()

