from __future__ import annotations

import os
from pathlib import Path


DEFAULT_FORBIDDEN_TERMS = {
    "REAL_CUSTOMER_NAME",
    "REAL_PROJECT_NAME",
    "REAL_BANK_NAME",
    "TODO_REAL_CUSTOMER",
    "真实客户名",
    "真实项目名",
}


def load_sensitive_terms(project_root: Path) -> set[str]:
    terms = set(DEFAULT_FORBIDDEN_TERMS)
    local_file = project_root / ".project-kb" / "sensitive_terms.local.txt"
    if local_file.exists():
        terms.update(_lines(local_file.read_text(encoding="utf-8", errors="replace")))
    env_value = os.environ.get("PROJECT_KB_SENSITIVE_TERMS")
    if env_value:
        terms.update(term.strip() for term in env_value.split(",") if term.strip())
    return {term for term in terms if term}


def scan_paths(project_root: Path, paths: list[Path]) -> dict[str, list[str]]:
    terms = load_sensitive_terms(project_root)
    findings: dict[str, list[str]] = {}
    for path in paths:
        if not path.exists() or path.is_dir():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = sorted(term for term in terms if term in text)
        if hits:
            findings[path.relative_to(project_root).as_posix()] = hits
    return findings


def _lines(text: str) -> set[str]:
    return {line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")}

