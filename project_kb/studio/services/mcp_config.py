from __future__ import annotations

from datetime import datetime, timezone
import difflib
import json
from pathlib import Path
import shutil
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
        proposed = self._proposed_config(agent)
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
        path.parent.mkdir(parents=True, exist_ok=True)
        backup_path = None
        if path.exists():
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            backup_path = path.with_name(path.name + f".bak.{timestamp}")
            shutil.copy2(path, backup_path)
        path.write_text(self._proposed_config(agent), encoding="utf-8")
        return {
            "agent": agent,
            "config_path": path.relative_to(self.project_root).as_posix(),
            "backup_path": backup_path.relative_to(self.project_root).as_posix() if backup_path else None,
            "status": "written",
        }

    def _config_path(self, agent: str) -> Path:
        if agent == "codex":
            return self.project_root / ".codex" / "config.toml"
        if agent == "kiro":
            return self.project_root / ".kiro" / "settings" / "mcp.json"
        raise ValueError("Unsupported agent.")

    def _proposed_config(self, agent: str) -> str:
        if agent == "codex":
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
        if agent == "kiro":
            return json.dumps(
                {
                    "mcpServers": {
                        "project-kb": {
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
                    }
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n"
        raise ValueError("Unsupported agent.")


def _contains(path: Path, text: str) -> bool:
    return path.exists() and text in path.read_text(encoding="utf-8", errors="replace")

