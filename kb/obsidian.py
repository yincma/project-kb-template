from __future__ import annotations


def to_obsidian_vault_path(path: str | None, *, docs_root: str = "docs") -> str:
    if not path:
        return ""
    normalized = str(path).replace("\\", "/").lstrip("./")
    root = docs_root.strip("/").replace("\\", "/")
    prefix = f"{root}/"
    if normalized.startswith(prefix):
        return normalized[len(prefix) :]
    return normalized


def to_obsidian_wikilink(path: str | None, *, docs_root: str = "docs") -> str:
    vault_path = to_obsidian_vault_path(path, docs_root=docs_root)
    return f"![[{vault_path}]]" if vault_path else ""
