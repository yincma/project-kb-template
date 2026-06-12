from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys
from typing import Any

from rich.console import Console
from rich.table import Table

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kb.diagnose import diagnose_project
from kb.store import LanceDBStore, load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Project KB usability and performance checks.")
    parser.add_argument("--config", default="kb/config.yaml")
    args = parser.parse_args()
    payload = doctor_project(args.config)
    print_doctor(payload)


def doctor_project(config_path: str | Path = "kb/config.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    store = LanceDBStore(cfg)
    diagnose = diagnose_project(config_path)
    warnings = list(diagnose.get("warnings", []))

    if cfg.retrieval.high_precision:
        warnings.append("high_precision=true loads the cross-encoder reranker; keep it for deep mode only on low-resource machines.")
    if cfg.parsing.ocr.enabled:
        warnings.append("OCR is enabled; scanned files and embedded images can make indexing slow.")
    if cfg.parsing.office.extract_images:
        warnings.append("office.extract_images=true can trigger OCR/image extraction and increase memory usage.")
    if cfg.retrieval.max_concurrent_queries != 1:
        warnings.append("max_concurrent_queries is not 1; set it to 1 to reduce local machine stalls.")

    return {
        **diagnose,
        "profile": cfg.profile,
        "config_exists": Path(config_path).exists(),
        "fts_index": _index_status(store, "fts"),
        "vector_index": _index_status(store, "vector"),
        "mcp_config": {
            "codex": (cfg.root_path / ".codex" / "config.toml").exists(),
            "kiro": (cfg.root_path / ".kiro" / "settings" / "mcp.json").exists(),
            "agents": (cfg.root_path / "AGENTS.md").exists(),
        },
        "models": {
            "embedding": cfg.embedding.model_name,
            "reranker": cfg.retrieval.reranker_model_name,
            "sentence_transformers": importlib.util.find_spec("sentence_transformers") is not None,
            "flag_embedding": importlib.util.find_spec("FlagEmbedding") is not None,
        },
        "warnings": warnings,
    }


def print_doctor(payload: dict[str, Any]) -> None:
    console = Console()
    table = Table(title="Project KB Doctor")
    table.add_column("Check")
    table.add_column("Value")
    for key in (
        "config_exists",
        "profile",
        "table_exists",
        "schema_version",
        "row_count",
        "fts_index",
        "vector_index",
    ):
        table.add_row(key, str(payload.get(key)))
    mcp = payload["mcp_config"]
    table.add_row("mcp_config", f"codex={mcp['codex']} kiro={mcp['kiro']} agents={mcp['agents']}")
    models = payload["models"]
    table.add_row("models", f"embedding={models['embedding']} sentence_transformers={models['sentence_transformers']} flag_embedding={models['flag_embedding']}")
    console.print(table)
    for warning in payload["warnings"]:
        console.print(f"[yellow]{warning}[/yellow]")


def _index_status(store: LanceDBStore, kind: str) -> str:
    if not store.config.db_path.exists():
        return "missing table"
    if not store.table_exists():
        return "missing table"
    try:
        table = store.open_table()
        if hasattr(table, "list_indices"):
            indices = table.list_indices()
            rendered = " ".join(str(index).lower() for index in indices)
            return "present" if kind in rendered else "unknown"
    except Exception:
        pass
    return "unknown"


if __name__ == "__main__":
    main()
