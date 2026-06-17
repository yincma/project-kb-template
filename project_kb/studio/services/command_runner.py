from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import subprocess
from typing import Any

from .safety import ensure_inside, resolve_project_root


class CommandEnum(str, Enum):
    STATUS = "status"
    DOCTOR = "doctor"
    IMPORT_SOURCES = "import_sources"
    CURATE_NOTES = "curate_notes"
    PUBLISH_REVIEWED_DOCS = "publish_reviewed_docs"
    ASK = "ask"
    INSTALL_CODEX_MCP = "install_codex_mcp"
    INSTALL_KIRO_MCP = "install_kiro_mcp"
    TEST_CODEX_MCP = "test_codex_mcp"
    TEST_KIRO_MCP = "test_kiro_mcp"


@dataclass
class CommandResult:
    command: CommandEnum
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    status: str = "completed"


class CommandRunner:
    def __init__(self, project_root: Path | str) -> None:
        self.project_root = resolve_project_root(project_root)

    def argv_for(self, command: CommandEnum, params: dict[str, Any] | None = None) -> list[str]:
        params = params or {}
        if command == CommandEnum.STATUS:
            return []
        if command == CommandEnum.DOCTOR:
            return ["uv", "run", "project-kb-doctor", "--config", "kb/config.yaml"]
        if command == CommandEnum.IMPORT_SOURCES:
            rebuild = bool(params.get("rebuild", False))
            argv = ["uv", "run", "project-kb-ingest", "--config", "kb/config.raw.yaml"]
            if rebuild:
                argv.append("--rebuild")
            return argv
        if command == CommandEnum.PUBLISH_REVIEWED_DOCS:
            return ["uv", "run", "project-kb-ingest", "--config", "kb/config.yaml", "--rebuild"]
        if command in {
            CommandEnum.CURATE_NOTES,
            CommandEnum.ASK,
            CommandEnum.INSTALL_CODEX_MCP,
            CommandEnum.INSTALL_KIRO_MCP,
            CommandEnum.TEST_CODEX_MCP,
            CommandEnum.TEST_KIRO_MCP,
        }:
            return []
        raise ValueError(f"Unsupported command: {command}")

    def run(self, command: CommandEnum | str, params: dict[str, Any] | None = None) -> CommandResult:
        try:
            enum_value = CommandEnum(command)
        except ValueError as exc:
            raise ValueError(f"Unknown command: {command}") from exc

        cwd = ensure_inside(self.project_root, self.project_root)
        argv = self.argv_for(enum_value, params=params)
        if enum_value == CommandEnum.STATUS:
            return CommandResult(enum_value, argv, 0, "Project KB Studio status is handled by the internal status service.", "")
        if enum_value in {
            CommandEnum.CURATE_NOTES,
            CommandEnum.ASK,
            CommandEnum.INSTALL_CODEX_MCP,
            CommandEnum.INSTALL_KIRO_MCP,
            CommandEnum.TEST_CODEX_MCP,
            CommandEnum.TEST_KIRO_MCP,
        }:
            return CommandResult(enum_value, argv, 2, "", f"{enum_value.value} is handled by a service adapter or is not implemented in MVP.", "not_implemented")

        completed = subprocess.run(argv, shell=False, cwd=str(cwd), text=True, capture_output=True)
        return CommandResult(enum_value, argv, int(completed.returncode), completed.stdout or "", completed.stderr or "")

