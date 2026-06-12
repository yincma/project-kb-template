from __future__ import annotations

from pathlib import Path
import os
import sys
from typing import Any

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.fastmcp import FastMCP

from kb.embeddings import BGEEmbedder
from kb.parsers import SUPPORTED_EXTENSIONS, parse_file
from kb.parsers.ocr import ocr_dependency_status
from kb.retrieval import ProjectRetriever, reranker_dependency_status
from kb.store import LanceDBStore, load_config


mcp = FastMCP("project-kb")
_RUNTIME_CACHE: dict[tuple[str | None, str | None, str | None], tuple[Any, Any, Any]] = {}


def _load_runtime():
    cache_key = (os.environ.get("KB_CONFIG"), os.environ.get("KB_DB_PATH"), os.environ.get("KB_TABLE_NAME"))
    if cache_key in _RUNTIME_CACHE:
        return _RUNTIME_CACHE[cache_key]

    cfg = load_config(os.environ.get("KB_CONFIG"))
    store = LanceDBStore(cfg)
    embedder = BGEEmbedder(
        cfg.embedding.model_name,
        batch_size=cfg.embedding.batch_size,
        device=cfg.embedding.device,
        use_fp16=cfg.embedding.use_fp16,
    )
    runtime = (cfg, store, ProjectRetriever(config=cfg, store=store, embedder=embedder))
    _RUNTIME_CACHE[cache_key] = runtime
    return runtime


def clear_runtime_cache() -> None:
    _RUNTIME_CACHE.clear()


@mcp.tool()
def kb_status() -> dict[str, Any]:
    """Return read-only project knowledge-base status."""
    cfg, store, _ = _load_runtime()
    ocr_status = ocr_dependency_status(cfg.parsing.ocr.engine)
    reranker_status = reranker_dependency_status()
    return {
        "name": "project-kb",
        "read_only": os.environ.get("KB_READ_ONLY", "true").lower() == "true",
        "project_root": str(cfg.root_path),
        "db_path": str(cfg.db_path),
        "table_name": cfg.database.table_name,
        "table_exists": store.table_exists(),
        "schema_version": store.detect_schema_version() if store.table_exists() else None,
        "row_count": store.count_rows(),
        "embedding_model": cfg.embedding.model_name,
        "retriever": cfg.retrieval.mode,
        "high_precision": cfg.retrieval.high_precision,
        "reranker": cfg.retrieval.reranker,
        "reranker_model": cfg.retrieval.reranker_model_name,
        "reranker_available": reranker_status["available"],
        "reranker_import_available": reranker_status["import_available"],
        "reranker_model_loadable": reranker_status["model_loadable"],
        "reranker_compute_score_available": reranker_status["compute_score_available"],
        "reranker_runtime_checked": reranker_status["runtime_checked"],
        "ocr_enabled": cfg.parsing.ocr.enabled,
        "ocr_engine": cfg.parsing.ocr.engine,
        "ocr_available": ocr_status["available"],
        "supported_formats": sorted(SUPPORTED_EXTENSIONS),
    }


@mcp.tool()
def search_project_kb(
    query: str,
    top_k: int = 8,
    source_filter: str | None = None,
    rerank: bool = True,
) -> dict[str, Any]:
    """Search the local project knowledge base using read-only hybrid retrieval."""
    _, _, retriever = _load_runtime()
    payload = retriever.search(query, top_k=top_k, source_filter=source_filter, rerank=rerank)
    payload["citations"] = [
        _citation_for_result(
            {
                "source_path": result["source_path"],
                "heading": result["heading"],
                "chunk_index": result["chunk_index"],
                "snippet": result["snippet"],
                "text": result["text"],
                "page_number": result.get("page_number"),
                "slide_number": result.get("slide_number"),
                "sheet_name": result.get("sheet_name"),
                "cell_range": result.get("cell_range"),
                "ocr_used": result.get("ocr_used"),
            }
        )
        for result in payload["results"]
    ]
    return payload


@mcp.tool()
def read_kb_source(source_path: str, max_chars: int = 12000) -> dict[str, Any]:
    """Read a bounded amount of a source file from the project root."""
    cfg, _, _ = _load_runtime()
    max_chars = max(1, min(int(max_chars), cfg.retrieval.max_return_chars))
    requested = Path(source_path)
    resolved = requested if requested.is_absolute() else cfg.root_path / requested
    resolved = resolved.resolve()

    if not resolved.is_relative_to(cfg.root_path):
        return {"error": "source_path is outside the project root", "source_path": source_path}
    if not resolved.exists() or not resolved.is_file():
        return {"error": "source file does not exist", "source_path": source_path}

    if resolved.suffix.lower() in {".pdf", ".docx", ".pptx", ".xlsx"}:
        parsed = parse_file(resolved, config=cfg)
        text = parsed.text if parsed else ""
    else:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > max_chars
    return {
        "source_path": resolved.relative_to(cfg.root_path).as_posix(),
        "max_chars": max_chars,
        "truncated": truncated,
        "text": text[:max_chars],
    }


def _citation_for_result(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if value not in (None, "")}


if __name__ == "__main__":
    mcp.run()
