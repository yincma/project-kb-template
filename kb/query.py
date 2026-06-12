from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from rich.console import Console
from rich.table import Table

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kb.embeddings import BGEEmbedder
from kb.retrieval import ProjectRetriever
from kb.store import LanceDBStore, load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Query the local project knowledge base.")
    parser.add_argument("query", help="Question or search phrase")
    parser.add_argument("--config", default=None, help="Path to kb/config.yaml")
    parser.add_argument("--top-k", type=int, default=None, help="Number of results to return")
    parser.add_argument("--source-filter", default=None, help="Limit results to matching source_path text")
    parser.add_argument("--json", action="store_true", help="Print structured JSON output")
    args = parser.parse_args()

    console = Console()
    cfg = load_config(args.config)
    retriever = ProjectRetriever(
        config=cfg,
        store=LanceDBStore(cfg),
        embedder=BGEEmbedder(
            cfg.embedding.model_name,
            batch_size=cfg.embedding.batch_size,
            device=cfg.embedding.device,
            use_fp16=cfg.embedding.use_fp16,
        ),
    )

    try:
        payload = retriever.search(args.query, top_k=args.top_k, source_filter=args.source_filter)
    except Exception as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
            raise SystemExit(1) from exc
        console.print(f"[red]Query failed:[/red] {exc}")
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    for warning in payload["warnings"]:
        console.print(f"[yellow]{warning}[/yellow]")

    results = payload["results"]
    if not results:
        console.print("[yellow]No results found. Try rebuilding the index or broadening the query.[/yellow]")
        return

    table = Table(title=f"Project KB Results: {args.query}")
    table.add_column("#", justify="right")
    table.add_column("Score")
    table.add_column("Source")
    table.add_column("Heading")
    table.add_column("Chunk")
    table.add_column("Snippet")

    for index, result in enumerate(results, start=1):
        score = "" if result["score"] is None else f"{result['score']:.4f}"
        table.add_row(
            str(index),
            score,
            str(result["source_path"]),
            str(result["heading"] or ""),
            str(result["chunk_index"]),
            str(result["snippet"]),
        )
    console.print(table)


if __name__ == "__main__":
    main()
