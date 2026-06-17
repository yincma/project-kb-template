from __future__ import annotations

import json
from pathlib import Path
import os
import sqlite3
import subprocess
import time

from fastapi.testclient import TestClient
import pytest
import yaml

from kb.ingest import discover_files
from kb.store import load_config
from project_kb.studio.app import create_app
from project_kb.studio.services.chat_service import ChatService
from project_kb.studio.services.command_runner import CommandEnum, CommandResult, CommandRunner
from project_kb.studio.services.i18n import TRANSLATIONS, browser_language
from project_kb.studio.services.job_runner import JobRunner
from project_kb.studio.services.mcp_config import MCPConfigService
from project_kb.studio.services.publish import PublishService
from project_kb.studio.services.review import ReviewService
from project_kb.studio.services.sensitive_terms import default_scan_paths, load_sensitive_terms, scan_paths
from project_kb.studio.services.state import StateStore


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def studio_root(tmp_path: Path) -> Path:
    for rel in ("docs", "sources", "kb"):
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
    (tmp_path / "kb" / "config.yaml").write_text("project_root: .\npath_base: config_dir\n", encoding="utf-8")
    (tmp_path / "kb" / "config.raw.yaml").write_text(
        "project_root: .\npath_base: config_dir\ndatabase:\n  db_path: .lancedb_raw\n  table_name: project_kb_raw\n  manifest_path: .lancedb_raw/manifest.json\n  index_role: raw\nscan:\n  source_dirs: [sources]\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def client(studio_root: Path) -> TestClient:
    return TestClient(create_app(studio_root))


def csrf(client: TestClient) -> dict[str, str]:
    return {"x-csrf-token": client.app.state.csrf_token}


def test_studio_app_starts(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "Project KB Studio" in response.text


def test_status_api(client: TestClient):
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["sources"]["count"] == 0


def test_upload_rejects_path_traversal(client: TestClient):
    response = client.post(
        "/api/sources/upload",
        headers=csrf(client),
        files=[("files", ("../evil.txt", b"bad", "text/plain"))],
    )
    assert response.status_code in {400, 422}


def test_upload_rejects_empty_filename(client: TestClient):
    response = client.post(
        "/api/sources/upload",
        headers=csrf(client),
        files=[("files", ("", b"bad", "text/plain"))],
    )
    assert response.status_code in {400, 422}


def test_upload_rejects_path_separators(client: TestClient):
    response = client.post(
        "/api/sources/upload",
        headers=csrf(client),
        files=[("files", ("nested/file.txt", b"bad", "text/plain"))],
    )
    assert response.status_code == 400


def test_upload_only_writes_to_sources(client: TestClient, studio_root: Path):
    response = client.post(
        "/api/sources/upload",
        headers=csrf(client),
        files=[("files", ("sample_proposal.pdf", b"demo", "application/pdf"))],
    )
    assert response.status_code == 200
    assert (studio_root / "sources" / "sample_proposal.pdf").read_bytes() == b"demo"
    assert not (studio_root / "sample_proposal.pdf").exists()


def test_upload_does_not_overwrite_same_name(client: TestClient, studio_root: Path):
    for content in (b"one", b"two"):
        response = client.post(
            "/api/sources/upload",
            headers=csrf(client),
            files=[("files", ("Sample RFP.txt", content, "text/plain"))],
        )
        assert response.status_code == 200
    assert (studio_root / "sources" / "Sample RFP.txt").read_bytes() == b"one"
    assert (studio_root / "sources" / "Sample RFP-1.txt").read_bytes() == b"two"


def test_csrf_required_for_write_routes(client: TestClient):
    response = client.post("/api/settings", json={"ui_language": "en"})
    assert response.status_code == 403


def test_origin_check_rejects_cross_origin_write(client: TestClient):
    headers = {**csrf(client), "origin": "http://evil.example"}
    response = client.post("/api/settings", headers=headers, json={"ui_language": "en"})
    assert response.status_code == 403


def test_command_whitelist_rejects_unknown_command(studio_root: Path):
    runner = CommandRunner(studio_root)
    with pytest.raises(ValueError):
        runner.run("rm_everything")


def test_command_runner_uses_shell_false(monkeypatch, studio_root: Path):
    calls = {}

    def fake_run(argv, *, shell, cwd, text, capture_output):
        calls["argv"] = argv
        calls["shell"] = shell
        calls["cwd"] = cwd
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = CommandRunner(studio_root).run(CommandEnum.DOCTOR)
    assert result.exit_code == 0
    assert calls["shell"] is False
    assert calls["argv"] == ["uv", "run", "project-kb-doctor", "--config", "kb/config.yaml"]
    assert calls["cwd"] == str(studio_root.resolve())


def test_command_runner_validates_project_root(tmp_path: Path):
    with pytest.raises(ValueError):
        CommandRunner(tmp_path / "missing")


def test_job_creation(studio_root: Path):
    store = StateStore(studio_root)
    job_id = store.create_job(job_type="import", command={"command": "import_sources"})
    job = store.query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    assert job["status"] == "queued"


def test_job_logs_saved(studio_root: Path):
    store = StateStore(studio_root)
    job_id = store.create_job(job_type="import", command={"command": "import_sources"})
    store.add_job_log(job_id, "stdout", "indexed_files=1")
    logs = store.query_all("SELECT * FROM job_logs WHERE job_id = ?", (job_id,))
    assert logs[0]["message"] == "indexed_files=1"


def test_heavy_job_lock_allows_only_one_running(studio_root: Path):
    store = StateStore(studio_root)
    store.create_job(job_type="import", command={"command": "import_sources"}, status="running")
    runner = JobRunner(studio_root, store, CommandRunner(studio_root))
    store.create_job(job_type="import", command={"command": "import_sources"}, status="running")
    with pytest.raises(RuntimeError):
        runner.enqueue_command(job_type="publish", command=CommandEnum.PUBLISH_REVIEWED_DOCS, start=False)


def test_running_job_marked_interrupted_on_startup(studio_root: Path):
    store = StateStore(studio_root)
    job_id = store.create_job(job_type="import", command={"command": "import_sources"}, status="running")
    JobRunner(studio_root, store, CommandRunner(studio_root))
    job = store.query_one("SELECT status FROM jobs WHERE id = ?", (job_id,))
    assert job["status"] == "interrupted"


def test_chat_defaults_to_reviewed_docs(monkeypatch, studio_root: Path):
    store = StateStore(studio_root)
    service = ChatService(studio_root, store)
    captured = {}

    def fake_search(question, *, source_mode, search_mode):
        captured["source_mode"] = source_mode
        return {"evidence": [], "warnings": [], "source_refs": [], "related_notes": [], "suggested_actions": []}

    monkeypatch.setattr(service, "_evidence_search", fake_search)
    service.ask("What is the goal?")
    assert captured["source_mode"] == "reviewed"


def test_chat_without_llm_returns_evidence_search(monkeypatch, studio_root: Path):
    store = StateStore(studio_root)
    service = ChatService(studio_root, store)
    monkeypatch.setattr(
        service,
        "_evidence_search",
        lambda question, *, source_mode, search_mode: {
            "evidence": [{"source_path": "docs/overview.md", "snippet": "Demo Project goal"}],
            "source_refs": [],
            "related_notes": [],
            "warnings": [],
            "suggested_actions": [],
        },
    )
    payload = service.ask("What is the goal?")
    assert payload["mode"] == "evidence_search"
    assert payload["answer_available"] is False
    assert payload["answer"] is None


def test_local_answer_requires_source_refs(monkeypatch, studio_root: Path):
    store = StateStore(studio_root)
    service = ChatService(studio_root, store)
    service.local_answer_engine = lambda **kwargs: {"answer": "Demo Project answer", "source_refs": []}
    monkeypatch.setattr(
        service,
        "_evidence_search",
        lambda question, *, source_mode, search_mode: {
            "evidence": [],
            "source_refs": [],
            "related_notes": [],
            "warnings": [],
            "suggested_actions": [],
        },
    )
    payload = service.ask("Question", provider="local_answer")
    assert payload["mode"] == "evidence_search"
    assert payload["answer_available"] is False
    assert any("source_refs" in warning for warning in payload["warnings"])


def test_chat_page_hides_external_llm_provider(client: TestClient):
    response = client.get("/chat")
    assert response.status_code == 200
    assert 'value="external_llm"' not in response.text
    assert "External LLM" not in response.text


def test_settings_hides_external_llm_checkbox(client: TestClient):
    response = client.get("/settings")
    assert response.status_code == 200
    assert "external-llm-enabled" not in response.text
    assert "External LLM enabled" not in response.text


def test_chat_external_llm_returns_501_without_search(monkeypatch, client: TestClient):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("external_llm must not call chat_service.ask")

    monkeypatch.setattr(client.app.state.chat_service, "ask", fail_if_called)
    response = client.post(
        "/api/chat/messages",
        headers=csrf(client),
        json={"question": "Can external LLM answer?", "provider": "external_llm"},
    )
    assert response.status_code == 501
    rows = client.app.state.store.query_all("SELECT * FROM chat_messages")
    assert rows == []


def test_chat_api_uses_threadpool_and_saves_assistant_message(monkeypatch, client: TestClient):
    import project_kb.studio.routes.chat as chat_route

    calls = {"threadpool": 0, "ask": 0}

    async def fake_run_in_threadpool(func, *args, **kwargs):
        calls["threadpool"] += 1
        return func(*args, **kwargs)

    def fake_ask(question, **kwargs):
        calls["ask"] += 1
        return {
            "mode": "evidence_search",
            "answer_available": False,
            "answer": None,
            "evidence": [],
            "source_refs": [{"source_path": "docs/reviewed.md", "heading": "Demo", "chunk_index": 2}],
            "related_notes": [],
            "warnings": ["Evidence Search Mode"],
            "suggested_actions": [],
            "requested_provider": kwargs.get("provider"),
        }

    monkeypatch.setattr(chat_route, "run_in_threadpool", fake_run_in_threadpool)
    monkeypatch.setattr(client.app.state.chat_service, "ask", fake_ask)

    response = client.post(
        "/api/chat/messages",
        headers=csrf(client),
        json={"question": "What changed?", "provider": "local_only"},
    )

    assert response.status_code == 200
    assert calls == {"threadpool": 1, "ask": 1}
    rows = client.app.state.store.query_all(
        "SELECT role, content, source_refs_json, warnings_json, mode, provider FROM chat_messages ORDER BY rowid"
    )
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assistant = rows[1]
    assert assistant["content"].startswith("Evidence Search Mode")
    assert json.loads(assistant["source_refs_json"])[0]["source_path"] == "docs/reviewed.md"
    assert json.loads(assistant["warnings_json"]) == ["Evidence Search Mode"]
    assert assistant["mode"] == "evidence_search"
    assert assistant["provider"] == "local_only"


def test_chat_messages_schema_migrates_old_database(studio_root: Path):
    db_path = studio_root / ".project-kb" / "state.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE chat_messages (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            source_refs_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    StateStore(studio_root)

    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(chat_messages)").fetchall()}
    conn.close()
    assert {"warnings_json", "mode", "provider"}.issubset(columns)


def test_review_scans_markdown_frontmatter(studio_root: Path):
    _write_note(studio_root / "docs" / "overview.md", status="needs_review", source_path="sources/sample_proposal.pdf")
    service = ReviewService(studio_root, StateStore(studio_root))
    notes = service.scan_notes()
    assert notes[0].status == "needs_review"
    assert notes[0].source_refs[0]["source_path"] == "sources/sample_proposal.pdf"


def test_approve_requires_source_refs_or_override(studio_root: Path):
    _write_note(studio_root / "docs" / "gap.md", status="needs_review", source_path="")
    service = ReviewService(studio_root, StateStore(studio_root))
    note = service.scan_notes()[0]
    with pytest.raises(ValueError):
        service.approve(note.id)
    updated = service.approve(note.id, override_missing_refs=True)
    assert updated.status == "reviewed"
    frontmatter = yaml.safe_load((studio_root / "docs" / "gap.md").read_text(encoding="utf-8").split("---", 2)[1])
    assert "review_warnings" in frontmatter


def test_publish_skips_needs_review(studio_root: Path):
    _write_note(studio_root / "docs" / "reviewed.md", status="reviewed", source_path="sources/sample_proposal.pdf")
    _write_note(studio_root / "docs" / "pending.md", status="needs_review", source_path="sources/sample_proposal.pdf")
    store = StateStore(studio_root)
    review = ReviewService(studio_root, store)
    preview = PublishService(studio_root, store, review).preview()
    assert preview["reviewed_count"] == 1
    assert preview["skipped_by_status"]["needs_review"] == 1


def test_publish_skips_evidence_gap_and_duplicates(studio_root: Path):
    _write_note(studio_root / "docs" / "gap.md", status="evidence_gap", source_path="sources/sample_proposal.pdf")
    _write_note(studio_root / "docs" / "dup.md", status="possible_duplicate", source_path="sources/sample_proposal.pdf")
    store = StateStore(studio_root)
    preview = PublishService(studio_root, store, ReviewService(studio_root, store)).preview()
    assert preview["skipped_by_status"]["evidence_gap"] == 1
    assert preview["skipped_by_status"]["possible_duplicate"] == 1


def test_publish_report_created(studio_root: Path):
    _write_note(studio_root / "docs" / "reviewed.md", status="reviewed", source_path="sources/sample_proposal.pdf")
    store = StateStore(studio_root)
    service = PublishService(studio_root, store, ReviewService(studio_root, store))
    job_id = store.create_job(job_type="publish", command={"command": "publish_reviewed_docs"})
    report = service.write_report(job_id)
    assert report.exists()
    assert "reviewed_count" in report.read_text(encoding="utf-8")


def test_curated_discover_files_filters_review_statuses(studio_root: Path):
    (studio_root / "kb" / "config.yaml").write_text(
        """
project_root: .
path_base: config_dir
database:
  index_role: curated
scan:
  source_dirs: [docs]
  include_patterns: ["**/*.md"]
curation:
  skip_needs_review: true
  index_review_statuses: [reviewed, approved]
""",
        encoding="utf-8",
    )
    _write_note(studio_root / "docs" / "reviewed.md", status="reviewed", source_path="sources/sample_proposal.pdf")
    _write_note(studio_root / "docs" / "approved.md", status="approved", source_path="sources/sample_proposal.pdf")
    _write_note(studio_root / "docs" / "pending.md", status="needs_review", source_path="sources/sample_proposal.pdf")
    _write_note(studio_root / "docs" / "gap.md", status="evidence_gap", source_path="sources/sample_proposal.pdf")
    _write_note(studio_root / "docs" / "dup.md", status="possible_duplicate", source_path="sources/sample_proposal.pdf")
    (studio_root / "docs" / "missing.md").write_text("# Missing status\n", encoding="utf-8")

    files = {path.relative_to(studio_root).as_posix() for path in discover_files(load_config(studio_root / "kb" / "config.yaml"))}
    assert files == {"docs/approved.md", "docs/reviewed.md"}


def test_publish_report_includes_actual_index_state(studio_root: Path):
    manifest_dir = studio_root / ".lancedb"
    manifest_dir.mkdir()
    (manifest_dir / "manifest.json").write_text(
        '{"version": 3, "files": {"docs/reviewed.md": {"chunk_count": 1}}}',
        encoding="utf-8",
    )
    _write_note(studio_root / "docs" / "reviewed.md", status="reviewed", source_path="sources/sample_proposal.pdf")
    store = StateStore(studio_root)
    service = PublishService(studio_root, store, ReviewService(studio_root, store))
    job_id = store.create_job(job_type="publish", command={"command": "publish_reviewed_docs"})
    report = service.write_report(job_id, extra={"job_status": "succeeded"})
    payload = __import__("json").loads(report.read_text(encoding="utf-8"))
    assert payload["actual_index"]["indexed_source_paths"] == ["docs/reviewed.md"]


def test_curate_api_returns_501_without_creating_job(client: TestClient):
    response = client.post("/api/jobs/curate", headers=csrf(client), json={})
    assert response.status_code == 501
    jobs = client.get("/api/jobs").json()["jobs"]
    assert jobs == []


def test_agent_hub_install_requires_preview_backup_confirm(studio_root: Path):
    config_path = studio_root / ".codex" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("old = true\n", encoding="utf-8")
    service = MCPConfigService(studio_root)
    preview = service.preview_install("codex")
    assert "old = true" in preview["diff"]
    result = service.confirm_install("codex")
    assert result["backup_path"]
    assert (studio_root / result["backup_path"]).exists()
    assert "project-kb" in config_path.read_text(encoding="utf-8")


def test_agent_hub_codex_merge_preserves_existing_mcp(studio_root: Path):
    config_path = studio_root / ".codex" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        """
theme = "dark"

[mcp_servers.other]
command = "other"
args = ["serve"]
""",
        encoding="utf-8",
    )
    result = MCPConfigService(studio_root).confirm_install("codex")
    text = config_path.read_text(encoding="utf-8")
    assert result["backup_path"]
    assert 'theme = "dark"' in text
    assert "[mcp_servers.other]" in text
    assert '[mcp_servers."project-kb"]' in text


def test_agent_hub_kiro_merge_preserves_existing_server(studio_root: Path):
    config_path = studio_root / ".kiro" / "settings" / "mcp.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        '{"theme": "dark", "mcpServers": {"other": {"command": "other"}}}',
        encoding="utf-8",
    )
    result = MCPConfigService(studio_root).confirm_install("kiro")
    data = __import__("json").loads(config_path.read_text(encoding="utf-8"))
    assert result["backup_path"]
    assert data["theme"] == "dark"
    assert data["mcpServers"]["other"]["command"] == "other"
    assert data["mcpServers"]["project-kb"]["command"] == "uv"


def test_agent_hub_invalid_config_does_not_overwrite(studio_root: Path):
    codex_path = studio_root / ".codex" / "config.toml"
    codex_path.parent.mkdir(parents=True)
    codex_path.write_text("[invalid", encoding="utf-8")
    with pytest.raises(ValueError):
        MCPConfigService(studio_root).confirm_install("codex")
    assert codex_path.read_text(encoding="utf-8") == "[invalid"
    assert not list(codex_path.parent.glob("config.toml.bak.*"))


def test_agent_hub_test_and_prompt(client: TestClient, studio_root: Path):
    service = MCPConfigService(studio_root)
    service.confirm_install("codex")
    response = client.post("/api/agent-hub/codex/test", headers=csrf(client), json={})
    assert response.status_code == 200
    assert response.json()["status"] == "config_check_only"
    prompt = client.post("/api/agent-hub/codex/prompt", headers=csrf(client), json={})
    assert prompt.status_code == 200
    assert "search_project_kb_fast" in prompt.json()["prompt"]


def test_review_api_returns_single_note_and_approves_second(client: TestClient, studio_root: Path):
    _write_note(studio_root / "docs" / "a.md", status="needs_review", source_path="sources/sample_proposal.pdf")
    _write_note(studio_root / "docs" / "b.md", status="needs_review", source_path="sources/sample_proposal.pdf")
    items = client.get("/api/review-items").json()["items"]
    second = items[1]
    single = client.get(f"/api/review-items/{second['id']}")
    assert single.status_code == 200
    assert single.json()["note"]["rel_path"] == "docs/b.md"
    approved = client.post(f"/api/review/{second['id']}/approve", headers=csrf(client), json={})
    assert approved.status_code == 200
    first_frontmatter = yaml.safe_load((studio_root / "docs" / "a.md").read_text(encoding="utf-8").split("---", 2)[1])
    second_frontmatter = yaml.safe_load((studio_root / "docs" / "b.md").read_text(encoding="utf-8").split("---", 2)[1])
    assert first_frontmatter["status"] == "needs_review"
    assert second_frontmatter["status"] == "reviewed"


def test_i18n_supports_zh_ja_en():
    assert {"zh", "ja", "en"}.issubset(TRANSLATIONS)
    assert TRANSLATIONS["ja"]["nav.home"]
    assert TRANSLATIONS["en"]["chat.evidence_mode"]


def test_i18n_browser_fallback_to_zh():
    assert browser_language("fr-FR,fr;q=0.9") == "zh"
    assert browser_language("ja-JP,ja;q=0.9") == "ja"


def test_ui_and_content_language_are_separate(client: TestClient):
    response = client.post(
        "/api/settings",
        headers=csrf(client),
        json={"ui_language": "ja", "content_language": "en"},
    )
    assert response.status_code == 200
    settings = response.json()["settings"]
    assert settings["ui_language"] == "ja"
    assert settings["content_language"] == "en"


def test_saved_ui_language_overrides_old_cookie(client: TestClient):
    client.cookies.set("lang", "en")
    response = client.post("/api/settings", headers=csrf(client), json={"ui_language": "ja"})
    assert response.status_code == 200
    page = client.get("/")
    assert "ローカルプロジェクト知識ベース" in page.text


def test_settings_default_profile_is_balanced(client: TestClient):
    response = client.get("/api/settings")
    assert response.status_code == 200
    assert response.json()["settings"]["profile"] == "balanced"


def test_settings_rejects_invalid_enum(client: TestClient):
    response = client.post("/api/settings", headers=csrf(client), json={"ui_language": "fr"})
    assert response.status_code == 400


def test_settings_rejects_profile_mismatch(client: TestClient):
    response = client.post("/api/settings", headers=csrf(client), json={"profile": "lite"})
    assert response.status_code == 400


def test_chat_reuses_cached_retriever(monkeypatch, studio_root: Path):
    class FakeEmbedding:
        model_name = "fake"
        batch_size = 1
        device = None
        use_fp16 = None

    class FakeCfg:
        embedding = FakeEmbedding()
        manifest_path = studio_root / ".lancedb" / "manifest.json"

    class FakeStore:
        def __init__(self, cfg):
            self.cfg = cfg

        def table_exists(self):
            return True

        def count_rows(self):
            return 1

    class FakeRetriever:
        def __init__(self, *, config, store, embedder):
            self.embedder = embedder

        def search(self, *args, **kwargs):
            return {"results": [], "warnings": []}

    created = {"embedder": 0}

    class FakeEmbedder:
        def __init__(self, *args, **kwargs):
            created["embedder"] += 1

    import project_kb.studio.services.chat_service as chat_module

    (studio_root / ".lancedb").mkdir()
    (studio_root / ".lancedb" / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(chat_module, "load_config", lambda path: FakeCfg())
    monkeypatch.setattr(chat_module, "LanceDBStore", FakeStore)
    monkeypatch.setattr(chat_module, "ProjectRetriever", FakeRetriever)
    monkeypatch.setattr(chat_module, "BGEEmbedder", FakeEmbedder)

    service = ChatService(studio_root, StateStore(studio_root))
    service.ask("one")
    service.ask("two")
    assert created["embedder"] == 1


def test_sensitive_terms_loaded_from_local_file_or_env(monkeypatch, studio_root: Path):
    local_term = "DoNotCommit" + "Client"
    env_term = "DoNotCommit" + "Bank"
    local = studio_root / ".project-kb" / "sensitive_terms.local.txt"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(local_term + "\n", encoding="utf-8")
    monkeypatch.setenv("PROJECT_KB_SENSITIVE_TERMS", env_term)
    terms = load_sensitive_terms(studio_root)
    assert local_term in terms
    assert env_term in terms


def test_no_real_customer_names_in_templates():
    paths = default_scan_paths(ROOT)
    rel_paths = {path.relative_to(ROOT).as_posix() for path in paths}
    assert "README.md" in rel_paths
    assert any(path.startswith("project_kb/studio/static/") for path in rel_paths)
    assert any(path.startswith(".codex/") for path in rel_paths)
    assert any(path.startswith(".kiro/") for path in rel_paths)
    findings = scan_paths(ROOT, paths)
    assert findings == {}


def _write_note(path: Path, *, status: str, source_path: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = {
        "status": status,
        "created_by": "project-kb-studio",
        "source_refs": [
            {
                "source_path": source_path,
                "heading": "Sample RFP",
                "chunk_index": 0,
                "page_number": 1,
                "slide_number": None,
                "sheet_name": None,
                "cell_range": None,
                "quote_id": None,
            }
        ],
    }
    path.write_text("---\n" + yaml.safe_dump(frontmatter, sort_keys=False) + "---\n\n# Demo Project\n", encoding="utf-8")
