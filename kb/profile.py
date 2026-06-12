from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

from rich.console import Console

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kb.store import load_config


PROFILES: dict[str, dict[str, Any]] = {
    "lite": {
        "database": {"vector_dimension": 384},
        "embedding": {"model_name": "sentence-transformers/all-MiniLM-L6-v2", "batch_size": 8},
        "parsing": {"ocr": {"enabled": False}, "office": {"extract_images": False}},
        "retrieval": {
            "top_k": 5,
            "candidate_k": 20,
            "high_precision": False,
            "reranker": "rrf",
            "max_snippet_chars": 320,
            "max_return_chars": 6000,
        },
    },
    "balanced": {
        "database": {"vector_dimension": 1024},
        "embedding": {"model_name": "BAAI/bge-m3", "batch_size": 4},
        "parsing": {"ocr": {"enabled": False}, "office": {"extract_images": False}},
        "retrieval": {
            "top_k": 5,
            "candidate_k": 20,
            "high_precision": False,
            "reranker": "rrf",
            "max_snippet_chars": 320,
            "max_return_chars": 6000,
        },
    },
    "accurate": {
        "database": {"vector_dimension": 1024},
        "embedding": {"model_name": "BAAI/bge-m3", "batch_size": 4},
        "parsing": {"ocr": {"enabled": False}, "office": {"extract_images": False}},
        "retrieval": {
            "top_k": 8,
            "candidate_k": 50,
            "high_precision": True,
            "reranker": "bge_cross_encoder",
            "rerank_top_k": 40,
            "max_snippet_chars": 320,
            "max_return_chars": 6000,
        },
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Switch Project KB runtime profile.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    set_parser = subparsers.add_parser("set", help="Set a profile: lite, balanced, or accurate")
    set_parser.add_argument("profile", choices=sorted(PROFILES))
    set_parser.add_argument("--config", default="kb/config.yaml")
    args = parser.parse_args()

    if args.command == "set":
        set_profile(args.profile, Path(args.config))


def set_profile(profile: str, config_path: Path) -> None:
    console = Console()
    import yaml

    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    before = load_config(config_path)
    _deep_update(data, PROFILES[profile])
    config_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    after = load_config(config_path)

    console.print(f"[green]Profile set:[/green] {profile}")
    console.print(f"embedding={after.embedding.model_name} dimension={after.database.vector_dimension}")
    if before.database.vector_dimension != after.database.vector_dimension and before.db_path.exists():
        console.print(
            "[yellow]Vector dimension changed. Run "
            "`uv run python kb/ingest.py --config kb/config.yaml --rebuild` before querying.[/yellow]"
        )


def _deep_update(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict):
            current = target.setdefault(key, {})
            if isinstance(current, dict):
                _deep_update(current, value)
            else:
                target[key] = value
        else:
            target[key] = value


if __name__ == "__main__":
    main()
