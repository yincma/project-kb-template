from __future__ import annotations

from pathlib import Path
from threading import BoundedSemaphore, Lock
from typing import Any

from kb.embeddings import BGEEmbedder
from kb.retrieval import ProjectRetriever
from kb.store import LanceDBStore, load_config

from .state import StateStore


class ChatService:
    def __init__(self, project_root: Path, store: StateStore) -> None:
        self.project_root = project_root.resolve()
        self.store = store
        self.max_concurrent_queries = 2
        self._query_semaphore = BoundedSemaphore(self.max_concurrent_queries)
        self._cache_lock = Lock()
        self._retriever_cache: dict[str, dict[str, Any]] = {}
        self.local_answer_engine = None

    def ask(
        self,
        question: str,
        *,
        source_mode: str | None = None,
        search_mode: str | None = None,
        provider: str | None = None,
        content_language: str | None = None,
    ) -> dict[str, Any]:
        source_mode = source_mode or self.store.get_setting("default_chat_source", "reviewed")
        search_mode = search_mode or "fast"
        provider = provider or "local_only"
        content_language = _normalize_content_language(content_language or self.store.get_setting("content_language", "zh"))

        with self._query_semaphore:
            payload = self._evidence_search(question, source_mode=source_mode, search_mode=search_mode)
        payload["requested_provider"] = provider
        payload["content_language"] = content_language

        if provider == "local_answer" and self.local_answer_engine is not None:
            answer_payload = self.local_answer_engine(question=question, evidence=payload["evidence"], content_language=content_language)
            source_refs = answer_payload.get("source_refs") or []
            if not source_refs:
                payload["warnings"].append("Local answer engine did not return source_refs; falling back to evidence search.")
            else:
                payload["mode"] = "local_answer"
                payload["answer_available"] = True
                payload["answer"] = str(answer_payload.get("answer") or "")
                payload["source_refs"] = source_refs
                return payload

        if provider == "external_llm":
            payload["mode"] = "external_llm"
            payload["answer_available"] = False
            payload["answer"] = None
            payload["warnings"].append(
                "External LLM mode is disabled by default. Configure and confirm it in Settings before use."
            )
            return payload

        payload["mode"] = "evidence_search"
        payload["answer_available"] = False
        payload["answer"] = None
        payload["warnings"].append("Evidence Search Mode: no answer engine is available, so no summary answer was generated.")
        return payload

    def _evidence_search(self, question: str, *, source_mode: str, search_mode: str) -> dict[str, Any]:
        configs = _configs_for_source_mode(source_mode)
        evidence: list[dict[str, Any]] = []
        warnings: list[str] = []
        for config_path in configs:
            result = self._search_config(question, config_path=config_path, search_mode=search_mode)
            evidence.extend(result["evidence"])
            warnings.extend(result["warnings"])
        if source_mode in {"raw", "both"}:
            warnings.append("Raw Sources may contain content that has not been reviewed by a human.")
        source_refs = [source_ref_for(item) for item in evidence]
        related_notes = sorted({ref["source_path"] for ref in source_refs if str(ref.get("source_path") or "").startswith("docs/")})
        return {
            "mode": "evidence_search",
            "answer_available": False,
            "answer": None,
            "evidence": evidence,
            "source_refs": source_refs,
            "related_notes": related_notes,
            "warnings": warnings,
            "suggested_actions": [
                {"action": "review_pending_notes", "label": "Review pending notes"},
                {"action": "publish_reviewed_docs", "label": "Publish reviewed docs"},
            ],
        }

    def _search_config(self, question: str, *, config_path: str, search_mode: str) -> dict[str, Any]:
        cfg_path = self.project_root / config_path
        cfg = load_config(cfg_path)
        store = LanceDBStore(cfg)
        if not store.table_exists() or not store.count_rows():
            return {
                "evidence": [],
                "warnings": [f"No searchable index is available for {config_path}. Run import or publish first."],
            }
        retriever, store, warmed = self._retriever_for_config(config_path, cfg=cfg, store=store)
        top_k = 8 if search_mode == "deep" else 5
        candidate_k = 50 if search_mode == "deep" else 20
        payload = retriever.search(question, top_k=top_k, candidate_k=candidate_k, high_precision=(search_mode == "deep"))
        evidence = []
        for result in payload.get("results", []):
            evidence.append(
                {
                    "snippet": result.get("snippet"),
                    "score": result.get("score"),
                    "source_path": result.get("source_path"),
                    "indexed_source_path": result.get("indexed_source_path"),
                    "heading": result.get("heading"),
                    "chunk_index": result.get("chunk_index"),
                    "page_number": result.get("page_number"),
                    "slide_number": result.get("slide_number"),
                    "sheet_name": result.get("sheet_name"),
                    "cell_range": result.get("cell_range"),
                    "config_path": config_path,
                }
            )
        warnings = list(payload.get("warnings", []))
        if warmed:
            warnings.append(f"Loaded local retriever for {config_path}. Later queries will reuse it while the index is unchanged.")
        return {"evidence": evidence, "warnings": warnings}

    def _retriever_for_config(self, config_path: str, *, cfg, store):
        cfg_path = self.project_root / config_path
        signature = _cache_signature(cfg_path, cfg.manifest_path)
        with self._cache_lock:
            cached = self._retriever_cache.get(config_path)
            if cached and cached.get("signature") == signature:
                return cached["retriever"], cached["store"], False
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
            self._retriever_cache[config_path] = {"signature": signature, "retriever": retriever, "store": store}
            return retriever, store, True


def _configs_for_source_mode(source_mode: str) -> list[str]:
    if source_mode == "raw":
        return ["kb/config.raw.yaml"]
    if source_mode == "both":
        return ["kb/config.yaml", "kb/config.raw.yaml"]
    return ["kb/config.yaml"]


def source_ref_for(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_path": result.get("source_path"),
        "indexed_source_path": result.get("indexed_source_path"),
        "heading": result.get("heading"),
        "chunk_index": result.get("chunk_index"),
        "page_number": result.get("page_number"),
        "slide_number": result.get("slide_number"),
        "sheet_name": result.get("sheet_name"),
        "cell_range": result.get("cell_range"),
    }


def _cache_signature(config_path: Path, manifest_path: Path) -> tuple[float | None, float | None]:
    return (_mtime(config_path), _mtime(manifest_path))


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _normalize_content_language(value: str) -> str:
    return value if value in {"zh", "ja", "en"} else "zh"
