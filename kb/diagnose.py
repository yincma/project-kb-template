from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

from rich.console import Console
from rich.table import Table

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kb.parsers import SUPPORTED_EXTENSIONS
from kb.parsers.ocr import ocr_dependency_status
from kb.retrieval import reranker_dependency_status
from kb.store import STORE_SCHEMA_VERSION, LanceDBStore, load_config


PARSER_DEPENDENCIES = {
    "pdf": "pymupdf",
    "pptx": "pptx",
    "xlsx": "openpyxl",
    "docx": "docx",
    "images": "PIL",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose Project KB v2 local dependencies and index state.")
    parser.add_argument("--config", default=None, help="Path to kb/config.yaml")
    parser.add_argument(
        "--deep-reranker-check",
        action="store_true",
        help="Load the reranker model and run one compute_score smoke test.",
    )
    parser.add_argument("--json", action="store_true", help="Print structured JSON")
    args = parser.parse_args()

    payload = diagnose_project(args.config, deep_reranker_check=args.deep_reranker_check)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print_diagnostics(payload)


def diagnose_project(config_path: str | Path | None = None, *, deep_reranker_check: bool = False) -> dict[str, Any]:
    cfg = load_config(config_path)
    db_path_exists = cfg.db_path.exists()
    store = LanceDBStore(cfg)
    table_exists = store.table_exists() if db_path_exists else False
    schema_version = store.detect_schema_version() if table_exists else None
    metadata_summary = store.metadata_summary() if table_exists else None

    parser_status = {
        name: {"available": importlib.util.find_spec(module_name) is not None, "module": module_name}
        for name, module_name in PARSER_DEPENDENCIES.items()
    }
    ocr_status = ocr_dependency_status(cfg.parsing.ocr.engine)
    reranker_status = reranker_dependency_status(
        cfg.retrieval.reranker_model_name,
        deep_check=deep_reranker_check,
    )

    warnings: list[str] = []
    warnings.extend(_path_warnings(cfg))
    if schema_version is not None and schema_version < STORE_SCHEMA_VERSION:
        warnings.append("LanceDB table uses the v1 schema; rebuild is required for v2 indexing.")
    if metadata_summary and metadata_summary["missing_columns"]:
        warnings.append(f"V2 metadata columns are missing: {', '.join(metadata_summary['missing_columns'])}")
    if cfg.parsing.ocr.enabled and not ocr_status["available"]:
        warnings.append("OCR is enabled but no local OCR dependency is importable.")
    if cfg.retrieval.high_precision and not reranker_status["available"]:
        warnings.append(f"High precision reranker is enabled but not fully available: {reranker_status['error']}")

    return {
        "config_path": str(getattr(cfg, "_config_path", "") or ""),
        "path_base": cfg.path_base,
        "base_dir": str(getattr(cfg, "_base_dir", "") or ""),
        "project_root": str(cfg.root_path),
        "db_path": str(cfg.db_path),
        "table_name": cfg.database.table_name,
        "table_exists": table_exists,
        "schema_version": schema_version,
        "expected_schema_version": STORE_SCHEMA_VERSION,
        "row_count": store.count_rows() if table_exists else 0,
        "metadata_summary": metadata_summary,
        "supported_formats": sorted(SUPPORTED_EXTENSIONS),
        "parser_dependencies": parser_status,
        "ocr": {
            "enabled": cfg.parsing.ocr.enabled,
            "engine": cfg.parsing.ocr.engine,
            **ocr_status,
        },
        "reranker": {
            "enabled": cfg.retrieval.high_precision,
            "name": cfg.retrieval.reranker,
            "model": cfg.retrieval.reranker_model_name,
            **reranker_status,
        },
        "warnings": warnings,
    }


def print_diagnostics(payload: dict[str, Any]) -> None:
    console = Console()
    summary = Table(title="Project KB Diagnostics")
    summary.add_column("Check")
    summary.add_column("Value")
    for key in (
        "config_path",
        "path_base",
        "base_dir",
        "project_root",
        "db_path",
        "table_name",
        "table_exists",
        "schema_version",
        "expected_schema_version",
        "row_count",
    ):
        summary.add_row(key, str(payload.get(key)))
    console.print(summary)

    deps = Table(title="Parser Dependencies")
    deps.add_column("Parser")
    deps.add_column("Module")
    deps.add_column("Available")
    for name, status in payload["parser_dependencies"].items():
        deps.add_row(name, status["module"], str(status["available"]))
    console.print(deps)

    feature = Table(title="V2 Features")
    feature.add_column("Feature")
    feature.add_column("Status")
    feature.add_row("OCR", f"enabled={payload['ocr']['enabled']} available={payload['ocr']['available']}")
    feature.add_row(
        "High precision reranker",
        (
            f"enabled={payload['reranker']['enabled']} "
            f"import={payload['reranker']['import_available']} "
            f"model={payload['reranker']['model_loadable']} "
            f"compute={payload['reranker']['compute_score_available']}"
        ),
    )
    console.print(feature)

    if payload.get("metadata_summary"):
        metadata = payload["metadata_summary"]
        metadata_table = Table(title="Metadata Summary")
        metadata_table.add_column("Check")
        metadata_table.add_column("Value")
        metadata_table.add_row("sampled_rows", str(metadata["sampled_rows"]))
        metadata_table.add_row("missing_columns", ", ".join(metadata["missing_columns"]) or "none")
        metadata_table.add_row("source_formats", ", ".join(metadata["source_formats"]) or "none")
        metadata_table.add_row("parser_names", ", ".join(metadata["parser_names"]) or "none")
        metadata_table.add_row("location_fields_with_values", json.dumps(metadata["location_fields_with_values"], ensure_ascii=False))
        metadata_table.add_row("ocr_used_rows", str(metadata["ocr_used_rows"]))
        console.print(metadata_table)

    for warning in payload["warnings"]:
        console.print(f"[yellow]{warning}[/yellow]")


def _path_warnings(cfg) -> list[str]:
    warnings: list[str] = []
    config_path = getattr(cfg, "_config_path", None)
    if not config_path or Path(config_path).parent.name == "kb":
        return warnings

    paths = [cfg.db_path, cfg.manifest_path]
    paths.extend((cfg.root_path / source_dir).resolve() for source_dir in cfg.scan.source_dirs)
    repeated = [path for path in paths if _has_adjacent_duplicate_parts(path)]
    if repeated:
        warnings.append(
            "Path configuration may be using config-dir-relative paths unintentionally. "
            "For external configs, set `path_base: project_root` or use `project_root: ..`."
        )
    return warnings


def _has_adjacent_duplicate_parts(path: Path) -> bool:
    parts = path.parts
    return any(left == right for left, right in zip(parts, parts[1:]))


if __name__ == "__main__":
    main()
