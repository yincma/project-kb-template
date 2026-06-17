from __future__ import annotations

from datetime import datetime, timezone
import difflib
import json
from pathlib import Path
import shutil
import tomllib
from typing import Any


class MCPConfigService:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def status(self) -> dict[str, Any]:
        codex_path = self.project_root / ".codex" / "config.toml"
        kiro_path = self.project_root / ".kiro" / "settings" / "mcp.json"
        return {
            "codex": {
                "config_exists": codex_path.exists(),
                "config_path": ".codex/config.toml",
                "mcp_command_valid": _contains(codex_path, "project-kb-mcp") or _contains(codex_path, "kb.mcp_server"),
                "cwd_valid": _contains(codex_path, 'cwd = "."'),
                "reload_needed": True,
            },
            "kiro": {
                "config_exists": kiro_path.exists(),
                "config_path": ".kiro/settings/mcp.json",
                "mcp_command_valid": _contains(kiro_path, "kb.mcp_server"),
                "steering_installed": (self.project_root / ".kiro" / "steering" / "project-kb.md").exists(),
                "reload_needed": True,
            },
        }

    def preview_install(self, agent: str) -> dict[str, Any]:
        path = self._config_path(agent)
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        proposed = self._merged_config(agent, current)
        diff = "\n".join(
            difflib.unified_diff(
                current.splitlines(),
                proposed.splitlines(),
                fromfile=str(path.relative_to(self.project_root)) if path.exists() else "new",
                tofile="proposed",
                lineterm="",
            )
        )
        return {
            "agent": agent,
            "config_path": path.relative_to(self.project_root).as_posix(),
            "exists": path.exists(),
            "preview": proposed,
            "diff": diff,
            "requires_backup": path.exists(),
        }

    def confirm_install(self, agent: str) -> dict[str, Any]:
        path = self._config_path(agent)
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        proposed = self._merged_config(agent, current)
        path.parent.mkdir(parents=True, exist_ok=True)
        backup_path = None
        if path.exists():
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            backup_path = path.with_name(path.name + f".bak.{timestamp}")
            shutil.copy2(path, backup_path)
        path.write_text(proposed, encoding="utf-8")
        return {
            "agent": agent,
            "config_path": path.relative_to(self.project_root).as_posix(),
            "backup_path": backup_path.relative_to(self.project_root).as_posix() if backup_path else None,
            "status": "written",
        }

    def test(self, agent: str) -> dict[str, Any]:
        path = self._config_path(agent)
        if not path.exists():
            return {"agent": agent, "ok": False, "status": "config_missing", "message": "Config file is missing."}
        if agent == "codex":
            data = self._parse_codex(path.read_text(encoding="utf-8"))
            server = data.get("mcp_servers", {}).get("project-kb", {}) if isinstance(data, dict) else {}
            ok = server.get("command") == "uv" and "kb.mcp_server" in " ".join(str(item) for item in server.get("args", []))
            return {
                "agent": agent,
                "ok": bool(ok),
                "status": "config_check_only",
                "message": "Config check only; restart Codex to load the MCP server.",
            }
        if agent == "kiro":
            data = self._parse_kiro(path.read_text(encoding="utf-8"))
            server = data.get("mcpServers", {}).get("project-kb", {}) if isinstance(data, dict) else {}
            ok = server.get("command") == "uv" and "kb.mcp_server" in " ".join(str(item) for item in server.get("args", []))
            return {
                "agent": agent,
                "ok": bool(ok),
                "status": "config_check_only",
                "message": "Config check only; restart Kiro to load the MCP server.",
            }
        raise ValueError("Unsupported agent.")

    def prompt(self, agent: str) -> dict[str, Any]:
        self._config_path(agent)
        return {
            "agent": agent,
            "prompt": (
                "Use the local project-kb MCP tools for project-document questions. "
                "Start with search_project_kb_fast, cite source_path, heading, and chunk_index, "
                "and do not modify or rebuild the knowledge base through MCP."
            ),
        }

    def _config_path(self, agent: str) -> Path:
        if agent == "codex":
            return self.project_root / ".codex" / "config.toml"
        if agent == "kiro":
            return self.project_root / ".kiro" / "settings" / "mcp.json"
        raise ValueError("Unsupported agent.")

    def _merged_config(self, agent: str, current: str) -> str:
        if agent == "codex":
            if current.strip():
                self._parse_codex(current)
            base = _remove_toml_tables(current, {'[mcp_servers."project-kb"]', '[mcp_servers."project-kb".env]'})
            return _join_config(base, self._codex_project_kb_block())
        if agent == "kiro":
            data = self._parse_kiro(current) if current.strip() else {}
            if not isinstance(data, dict):
                raise ValueError("Kiro MCP config must be a JSON object.")
            servers = data.setdefault("mcpServers", {})
            if not isinstance(servers, dict):
                raise ValueError("Kiro mcpServers must be a JSON object.")
            servers["project-kb"] = self._kiro_project_kb_server()
            return json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        raise ValueError("Unsupported agent.")

    def _codex_project_kb_block(self) -> str:
        return """[mcp_servers."project-kb"]
command = "uv"
args = ["run", "python", "-m", "kb.mcp_server"]
cwd = "."
enabled = true
startup_timeout_sec = 30
tool_timeout_sec = 120
enabled_tools = ["kb_status", "search_project_kb", "search_project_kb_fast", "search_project_kb_deep", "read_kb_source"]

[mcp_servers."project-kb".env]
KB_CONFIG = "kb/config.yaml"
KB_DB_PATH = ".lancedb"
KB_READ_ONLY = "true"
TOKENIZERS_PARALLELISM = "false"
OMP_NUM_THREADS = "1"
MKL_NUM_THREADS = "1"
OPENBLAS_NUM_THREADS = "1"
VECLIB_MAXIMUM_THREADS = "1"
NUMEXPR_NUM_THREADS = "1"
"""

    def _kiro_project_kb_server(self) -> dict[str, Any]:
        return {
            "command": "uv",
            "args": ["run", "python", "-m", "kb.mcp_server"],
            "env": {
                "KB_CONFIG": "kb/config.yaml",
                "KB_DB_PATH": ".lancedb",
                "KB_READ_ONLY": "true",
                "TOKENIZERS_PARALLELISM": "false",
                "OMP_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "VECLIB_MAXIMUM_THREADS": "1",
                "NUMEXPR_NUM_THREADS": "1",
            },
            "disabled": False,
            "disabledTools": [],
        }

    def _parse_codex(self, text: str) -> dict[str, Any]:
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"Cannot safely parse Codex TOML config: {exc}") from exc

    def _parse_kiro(self, text: str) -> dict[str, Any]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Cannot safely parse Kiro JSON config: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("Kiro MCP config must be a JSON object.")
        return data


def _contains(path: Path, text: str) -> bool:
    return path.exists() and text in path.read_text(encoding="utf-8", errors="replace")


def _remove_toml_tables(text: str, table_headers: set[str]) -> str:
    lines = text.splitlines()
    kept: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            skipping = stripped in table_headers
            if skipping:
                continue
        if not skipping:
            kept.append(line)
    return "\n".join(kept).strip()


def _join_config(base: str, block: str) -> str:
    base = base.strip()
    block = block.strip()
    return f"{base}\n\n{block}\n" if base else f"{block}\n"
