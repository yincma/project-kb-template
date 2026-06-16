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
    parser.add_argument("--visual-only", action="store_true", help="Return only visual evidence results")
    parser.add_argument("--json", action="store_true", help="Print structured JSON output")
    args = parser.parse_args()

    console = Console()
    cfg = load_config(args.config)
    store = LanceDBStore(cfg)
    if args.visual_only and not store.table_exists():
        message = (
            f"Visual index table `{cfg.database.table_name}` does not exist. "
            "请先运行 uv run project-kb-ingest --config kb/config.raw.yaml --rebuild"
        )
        if args.json:
            print(json.dumps({"error": message, "index_role": cfg.database.index_role}, ensure_ascii=False, indent=2))
        else:
            console.print(f"[red]{message}[/red]")
        raise SystemExit(1)

    requested_top_k = int(args.top_k or cfg.retrieval.top_k)
    search_top_k = max(requested_top_k, int(cfg.retrieval.candidate_k or requested_top_k)) if args.visual_only else args.top_k
    retriever = ProjectRetriever(
        config=cfg,
        store=store,
        embedder=BGEEmbedder(
            cfg.embedding.model_name,
            batch_size=cfg.embedding.batch_size,
            device=cfg.embedding.device,
            use_fp16=cfg.embedding.use_fp16,
        ),
    )

    try:
        payload = retriever.search(args.query, top_k=search_top_k, source_filter=args.source_filter)
    except Exception as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
            raise SystemExit(1) from exc
        console.print(f"[red]Query failed:[/red] {exc}")
        raise SystemExit(1) from exc

    if args.visual_only:
        payload = _visual_only_payload(payload, cfg.database.index_role, requested_top_k)

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
            _source_display(result),
            str(result["heading"] or ""),
            str(result["chunk_index"]),
            str(result["snippet"]),
        )
    console.print(table)
    _print_visual_details(console, results)


def _source_display(result: dict) -> str:
    source = str(result.get("source_path") or "")
    if result.get("asset_type") != "visual":
        return source
    location = ""
    if result.get("page_number"):
        location = f"page={result['page_number']}"
    elif result.get("slide_number"):
        location = f"slide={result['slide_number']}"
    details = [
        source,
        "asset_type=visual",
        f"visual_type={result.get('visual_type') or 'unknown'}",
    ]
    if result.get("index_role"):
        details.append(f"index_role={result['index_role']}")
    if result.get("raw_evidence") is True:
        details.append("raw_evidence=true review_status=unreviewed")
    if location:
        details.append(location)
    if result.get("confidence") is not None:
        details.append(f"confidence={result['confidence']}")
    if result.get("searchable") is not None:
        details.append(f"searchable={result['searchable']}")
    if result.get("attachment_path"):
        attachment = str(result["attachment_path"])
        details.append(f"attachment={_middle_truncate(attachment)}")
        details.append(f"wikilink=![[{attachment}]]")
    if result.get("indexed_source_path"):
        details.append(f"indexed={_middle_truncate(str(result['indexed_source_path']))}")
    return "\n".join(details)


def _visual_only_payload(payload: dict, index_role: str, requested_top_k: int) -> dict:
    enriched = []
    for result in payload.get("results", []):
        if result.get("asset_type") != "visual":
            continue
        enriched.append(_annotate_result_role(result, index_role))
    payload = dict(payload)
    payload["top_k"] = requested_top_k
    payload["results"] = enriched[:requested_top_k]
    payload["visual_only"] = True
    return payload


def _annotate_result_role(result: dict, index_role: str) -> dict:
    annotated = dict(result)
    metadata = annotated.get("metadata") if isinstance(annotated.get("metadata"), dict) else {}
    annotated["index_role"] = index_role
    if annotated.get("asset_type") == "visual" and annotated.get("attachment_path"):
        annotated["attachment_wikilink"] = f"![[{annotated['attachment_path']}]]"
    if index_role == "raw":
        annotated["raw_evidence"] = True
        annotated["curated"] = False
        annotated["review_status"] = "unreviewed"
    else:
        annotated["raw_evidence"] = False
        annotated["curated"] = True
        annotated["review_status"] = metadata.get("review_status") or metadata.get("status") or annotated.get("review_status")
    return annotated


def _middle_truncate(value: str, max_chars: int = 72) -> str:
    if len(value) <= max_chars:
        return value
    keep = max(8, (max_chars - 3) // 2)
    return value[:keep] + "..." + value[-keep:]


def _print_visual_details(console: Console, results: list[dict]) -> None:
    visual_results = [(index, result) for index, result in enumerate(results, start=1) if result.get("asset_type") == "visual"]
    if not visual_results:
        return
    console.print("\n[bold]Visual evidence[/bold]")
    for index, result in visual_results:
        attachment = result.get("attachment_path") or ""
        indexed = result.get("indexed_source_path") or ""
        location = ""
        if result.get("page_number"):
            location = f" page={result['page_number']}"
        elif result.get("slide_number"):
            location = f" slide={result['slide_number']}"
        wikilink = f"![[{attachment}]]" if attachment else ""
        console.print(
            f"[{index}] source={result.get('source_path') or ''}{location} "
            f"visual_type={result.get('visual_type') or 'unknown'} "
            f"attachment_path={attachment} indexed_source_path={indexed} {wikilink}",
            markup=False,
        )


if __name__ == "__main__":
    main()
