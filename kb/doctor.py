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
    if cfg.parsing.multimodal.enabled:
        warnings.append("multimodal.enabled=true can render pages and extract images; raw intake uses conservative limits by default.")
    if diagnose.get("needs_rebuild"):
        warnings.append("LanceDB schema is older than expected; run rebuild before querying.")
    if cfg.retrieval.max_concurrent_queries != 1:
        warnings.append("max_concurrent_queries is not 1; set it to 1 to reduce local machine stalls.")

    fts_index = _index_report(store, "fts")
    vector_index = _index_report(store, "vector")
    vector_search_available = _vector_search_available(store)

    return {
        **diagnose,
        "profile": cfg.profile,
        "config_exists": Path(config_path).exists(),
        "fts_index": fts_index["status"],
        "fts_index_detail": fts_index["detail"],
        "vector_index": vector_index["status"],
        "vector_index_detail": vector_index["detail"],
        "vector_search_available": vector_search_available,
        "manual_vector_index_required": False,
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
        "multimodal": diagnose.get("multimodal", {}),
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
        "expected_schema_version",
        "needs_rebuild",
        "index_role",
        "row_count",
        "fts_index",
        "vector_index",
        "vector_search_available",
        "manual_vector_index_required",
    ):
        table.add_row(key, str(payload.get(key)))
    table.add_row("vector_index_detail", str(payload.get("vector_index_detail")))
    mcp = payload["mcp_config"]
    table.add_row("mcp_config", f"codex={mcp['codex']} kiro={mcp['kiro']} agents={mcp['agents']}")
    models = payload["models"]
    table.add_row("models", f"embedding={models['embedding']} sentence_transformers={models['sentence_transformers']} flag_embedding={models['flag_embedding']}")
    multimodal = payload.get("multimodal", {})
    if multimodal:
        table.add_row(
            "multimodal",
            (
                f"enabled={multimodal.get('enabled')} provider={multimodal.get('vision_provider')} "
                f"external={multimodal.get('external_vision_enabled')} render_pages={multimodal.get('render_pages')}"
            ),
        )
        manifest = multimodal.get("manifest", {})
        table.add_row(
            "multimodal_manifest",
            (
                f"assets={manifest.get('asset_count', 0)} occurrences={manifest.get('occurrence_count', 0)} "
                f"ocr_cache={manifest.get('ocr_cache_count', 0)} caption_cache={manifest.get('caption_cache_count', 0)}"
            ),
        )
    console.print(table)
    for warning in payload["warnings"]:
        console.print(f"[yellow]{warning}[/yellow]")


def _index_report(store: LanceDBStore, kind: str) -> dict[str, str]:
    if not store.config.db_path.exists():
        return {"status": "missing_table", "detail": "LanceDB directory does not exist yet."}
    if not store.table_exists():
        return {"status": "missing_table", "detail": "LanceDB table does not exist yet."}
    try:
        table = store.open_table()
        if hasattr(table, "list_indices"):
            indices = table.list_indices()
            rendered = " ".join(str(index).lower() for index in indices)
            if kind in rendered:
                return {"status": "present", "detail": f"LanceDB reports a {kind} index."}
            return {
                "status": "not_reported_by_lancedb",
                "detail": f"LanceDB did not report a {kind} index through list_indices().",
            }
    except Exception as exc:
        return {
            "status": "not_reported_by_lancedb",
            "detail": f"LanceDB index metadata is unavailable: {exc}",
        }
    return {
        "status": "not_reported_by_lancedb",
        "detail": "This LanceDB table object does not expose list_indices().",
    }


def _vector_search_available(store: LanceDBStore) -> bool:
    if not store.config.db_path.exists() or not store.table_exists():
        return False
    return "vector" in store.schema_field_names()


if __name__ == "__main__":
    main()
