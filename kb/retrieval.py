from __future__ import annotations

import fnmatch
import importlib.util
import json
from threading import BoundedSemaphore
from typing import Any
import warnings

from kb.obsidian import to_obsidian_wikilink
from kb.store import ProjectKBConfig, _arrowish_to_rows


RRFReranker = None
FlagReranker = None


class ProjectRetriever:
    def __init__(self, *, config: ProjectKBConfig, store, embedder) -> None:
        self.config = config
        self.store = store
        self.embedder = embedder
        self._high_precision_reranker: BGEHighPrecisionReranker | None = None
        self._query_semaphore = BoundedSemaphore(max(1, int(config.retrieval.max_concurrent_queries or 1)))

    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        candidate_k: int | None = None,
        source_filter: str | None = None,
        rerank: bool = True,
        high_precision: bool | None = None,
        include_text: bool = False,
    ) -> dict[str, Any]:
        with self._query_semaphore:
            return self._search_locked(
                query,
                top_k=top_k,
                candidate_k=candidate_k,
                source_filter=source_filter,
                rerank=rerank,
                high_precision=high_precision,
                include_text=include_text,
            )

    def _search_locked(
        self,
        query: str,
        *,
        top_k: int | None = None,
        candidate_k: int | None = None,
        source_filter: str | None = None,
        rerank: bool = True,
        high_precision: bool | None = None,
        include_text: bool = False,
    ) -> dict[str, Any]:
        top_k = int(top_k or self.config.retrieval.top_k)
        use_high_precision = self.config.retrieval.high_precision if high_precision is None else bool(high_precision)
        candidate_k = self._candidate_k(top_k, candidate_k, use_high_precision)
        if hasattr(self.store, "count_rows"):
            row_count = self.store.count_rows()
            if row_count == 0:
                return self._payload(
                    query,
                    top_k,
                    candidate_k,
                    [],
                    [],
                    reranker="none",
                    post_ranking="none",
                    include_text=include_text,
                )

        query_vector = self.embedder.embed_query(query)
        table = self.store.open_table()
        warnings_list: list[str] = []

        try:
            rows = self._hybrid_search(table, query, query_vector, top_k, candidate_k, source_filter, rerank, warnings_list)
        except Exception as exc:
            warnings_list.append(f"Hybrid search failed; falling back to vector search: {exc}")
            rows = self._vector_search(table, query_vector, top_k, candidate_k, source_filter)

        deduped_rows = dedupe_rows(rows)
        ranked_rows = apply_domain_boosts(query, deduped_rows, self.config)
        post_ranking = "domain_boost" if self.config.retrieval.boosts.enabled else "none"
        reranker_used = "rrf" if rerank else "none"

        if self._should_high_precision(rerank, use_high_precision):
            rerank_top_k = max(top_k, int(self.config.retrieval.rerank_top_k or top_k))
            pool = ranked_rows[:rerank_top_k]
            remainder = ranked_rows[rerank_top_k:]
            try:
                ranked_rows = self._get_high_precision_reranker().rerank(query, pool) + remainder
                reranker_used = "bge_cross_encoder"
                post_ranking = f"{post_ranking}+bge_rerank" if post_ranking != "none" else "bge_rerank"
            except Exception as exc:
                warnings_list.append(f"High precision reranker unavailable; fell back to rrf: {exc}")
                reranker_used = "rrf" if rerank else "none"

        return self._payload(
            query,
            top_k,
            candidate_k,
            warnings_list,
            ranked_rows[:top_k],
            reranker=reranker_used,
            post_ranking=post_ranking,
            include_text=include_text,
        )

    def _candidate_k(self, top_k: int, candidate_k: int | None, high_precision: bool) -> int:
        candidate_k = max(top_k, int(candidate_k or self.config.retrieval.candidate_k or top_k))
        if self._should_high_precision(True, high_precision):
            candidate_k = max(candidate_k, int(self.config.retrieval.rerank_top_k or top_k))
        return candidate_k

    def _should_high_precision(self, rerank: bool, high_precision: bool) -> bool:
        return bool(rerank and high_precision)

    def _get_high_precision_reranker(self) -> "BGEHighPrecisionReranker":
        if self._high_precision_reranker is None:
            self._high_precision_reranker = BGEHighPrecisionReranker(self.config.retrieval.reranker_model_name)
        return self._high_precision_reranker

    def _payload(
        self,
        query: str,
        top_k: int,
        candidate_k: int,
        warnings_list: list[str],
        rows: list[dict[str, Any]],
        *,
        reranker: str,
        post_ranking: str,
        include_text: bool,
    ) -> dict[str, Any]:
        return {
            "query": query,
            "top_k": top_k,
            "candidate_k": candidate_k,
            "mode": self.config.retrieval.mode,
            "index_role": self.config.database.index_role,
            "reranker": reranker,
            "post_ranking": post_ranking,
            "warnings": warnings_list,
            "results": [
                format_result(row, query, self.config.retrieval.max_snippet_chars, include_text=include_text)
                for row in rows
            ],
        }

    def _hybrid_search(
        self,
        table,
        query: str,
        query_vector: list[float],
        top_k: int,
        candidate_k: int,
        source_filter: str | None,
        rerank: bool,
        warnings_list: list[str],
    ) -> list[dict[str, Any]]:
        return _builder_to_rows(
            self._hybrid_builder(table, query, query_vector, source_filter, rerank, warnings_list).limit(candidate_k)
        )

    def _hybrid_builder(
        self,
        table,
        query: str,
        query_vector: list[float],
        source_filter: str | None,
        rerank: bool,
        warnings_list: list[str],
    ):
        builder = table.search(query_type="hybrid").vector(query_vector).text(query)
        if source_filter:
            builder = builder.where(_source_filter_sql(source_filter))

        if rerank:
            reranker_name = (self.config.retrieval.fallback_reranker or "rrf").lower()
            if reranker_name != "rrf" and not any("using LanceDB RRF" in warning for warning in warnings_list):
                warnings_list.append(
                    f"Fallback reranker `{reranker_name}` is unsupported; using LanceDB RRF."
                )
            builder = builder.rerank(_make_rrf_reranker())
        return builder

    def _vector_search(
        self,
        table,
        query_vector: list[float],
        top_k: int,
        candidate_k: int,
        source_filter: str | None,
    ) -> list[dict[str, Any]]:
        return _builder_to_rows(self._vector_builder(table, query_vector, source_filter).limit(candidate_k))

    def _vector_builder(self, table, query_vector: list[float], source_filter: str | None):
        builder = table.search(query_vector)
        if source_filter:
            builder = builder.where(_source_filter_sql(source_filter))
        return builder


def _make_rrf_reranker():
    global RRFReranker
    if RRFReranker is None:
        try:
            from lancedb.rerankers import RRFReranker as ImportedRRFReranker
        except Exception as exc:
            raise RuntimeError("LanceDB RRFReranker is unavailable in this installation.") from exc
        RRFReranker = ImportedRRFReranker
    return RRFReranker()


def _builder_to_rows(builder) -> list[dict[str, Any]]:
    if hasattr(builder, "to_list"):
        return list(builder.to_list())
    if hasattr(builder, "to_arrow"):
        rows = _arrowish_to_rows(builder.to_arrow())
        if rows:
            return rows
    if hasattr(builder, "to_table"):
        rows = _arrowish_to_rows(builder.to_table())
        if rows:
            return rows
    return list(builder)


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    index_by_key: dict[tuple[str, Any], int] = {}
    for row in rows:
        key = (str(row.get("indexed_source_path") or row.get("source_path") or ""), row.get("chunk_index"))
        if key not in index_by_key:
            index_by_key[key] = len(deduped)
            deduped.append(row)
            continue

        existing_index = index_by_key[key]
        if _row_quality(row) > _row_quality(deduped[existing_index]):
            deduped[existing_index] = row
    return deduped


def apply_domain_boosts(query: str, rows: list[dict[str, Any]], config: ProjectKBConfig) -> list[dict[str, Any]]:
    boost_config = config.retrieval.boosts
    if not boost_config.enabled or not rows:
        return rows

    scored_rows: list[tuple[float, int, dict[str, Any]]] = []
    for index, row in enumerate(rows):
        score = _score_for_sort(row)
        boosted_score, signals = _boost_score(query, row, score, boost_config.rules)
        enriched = dict(row)
        enriched["_domain_boost_score"] = boosted_score
        enriched["_ranking_signals"] = signals
        scored_rows.append((boosted_score, index, enriched))

    scored_rows.sort(key=lambda item: (-item[0], item[1]))
    return [row for _, _, row in scored_rows]


def _boost_score(query: str, row: dict[str, Any], base_score: float, rules) -> tuple[float, list[dict[str, Any]]]:
    query_lower = query.lower()
    source_path = str(row.get("source_path") or "")
    source_lower = source_path.lower()
    score = base_score
    signals: list[dict[str, Any]] = []

    for rule in rules:
        matched_terms = [term for term in rule.query_terms if term.lower() in query_lower]
        if not matched_terms:
            continue
        matched_globs = [pattern for pattern in rule.source_globs if fnmatch.fnmatch(source_lower, pattern.lower())]
        if not matched_globs:
            continue
        score += float(rule.weight)
        signals.append(
            {
                "rule": rule.name,
                "weight": float(rule.weight),
                "query_terms": matched_terms,
                "source_globs": matched_globs,
            }
        )
    return score, signals


def _score_for_sort(row: dict[str, Any]) -> float:
    score = _score_from_row(row)
    if score is None:
        distance = row.get("_distance") or row.get("distance")
        try:
            return -float(distance)
        except (TypeError, ValueError):
            return 0.0
    if "_distance" in row or "distance" in row:
        return -score
    return score


def _row_quality(row: dict[str, Any]) -> tuple[int, int, int]:
    return (
        1 if row.get("source_path") else 0,
        1 if row.get("text") else 0,
        len(str(row.get("text") or "")),
    )


def format_result(row: dict[str, Any], query: str, max_snippet_chars: int, *, include_text: bool = False) -> dict[str, Any]:
    text = str(row.get("text") or "")
    metadata = _parse_metadata(row.get("metadata_json"))
    page_number = _field_or_metadata(row, metadata, "page_number")
    slide_number = _field_or_metadata(row, metadata, "slide_number")
    sheet_name = _field_or_metadata(row, metadata, "sheet_name")
    cell_range = _field_or_metadata(row, metadata, "cell_range")
    row_range = _field_or_metadata(row, metadata, "row_range")
    ocr_used = _field_or_metadata(row, metadata, "ocr_used")
    asset_type = _field_or_metadata(row, metadata, "asset_type")
    attachment_path = _field_or_metadata(row, metadata, "attachment_path")
    visual_type = _field_or_metadata(row, metadata, "visual_type")
    image_hash = _field_or_metadata(row, metadata, "image_hash")
    asset_id = _field_or_metadata(row, metadata, "asset_id")
    occurrence_id = _field_or_metadata(row, metadata, "occurrence_id")
    indexed_source_path = _field_or_metadata(row, metadata, "indexed_source_path")
    caption_provider = _field_or_metadata(row, metadata, "caption_provider")
    caption_model = _field_or_metadata(row, metadata, "caption_model")
    prompt_version = _field_or_metadata(row, metadata, "prompt_version")
    searchable = _field_or_metadata(row, metadata, "searchable")
    confidence = _field_or_metadata(row, metadata, "confidence")
    snippet = make_snippet(text, query, max_snippet_chars)
    if asset_type == "visual":
        location = f"page {page_number}" if page_number else f"slide {slide_number}" if slide_number else "source"
        snippet = f"Visual summary from source {location}: {snippet}"
    result = {
        "result_id": row.get("id"),
        "chunk_id": metadata.get("chunk_id") or row.get("id"),
        "score": _score_from_row(row),
        "indexed_source_path": indexed_source_path or row.get("indexed_source_path"),
        "source_path": row.get("source_path"),
        "heading": row.get("heading") or None,
        "chunk_index": row.get("chunk_index"),
        "asset_type": asset_type or None,
        "asset_id": asset_id or None,
        "occurrence_id": occurrence_id or None,
        "visual_type": visual_type or None,
        "attachment_path": attachment_path or None,
        "attachment_wikilink": to_obsidian_wikilink(str(attachment_path)) if asset_type == "visual" and attachment_path else None,
        "image_hash": image_hash or None,
        "caption_provider": caption_provider or None,
        "caption_model": caption_model or None,
        "prompt_version": prompt_version or None,
        "searchable": searchable,
        "confidence": confidence,
        "page_number": page_number,
        "slide_number": slide_number,
        "sheet_name": sheet_name or None,
        "row_range": row_range or None,
        "cell_range": cell_range or None,
        "ocr_used": bool(ocr_used) if ocr_used is not None else None,
        "snippet": snippet,
        "metadata": metadata,
        "ranking_signals": row.get("_ranking_signals", []),
    }
    if include_text:
        result["text"] = text
    return result


def make_snippet(text: str, query: str, max_chars: int = 500) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized

    lower_text = normalized.lower()
    terms = [term.lower() for term in query.split() if len(term) >= 2]
    hit = min((lower_text.find(term) for term in terms if lower_text.find(term) >= 0), default=-1)
    if hit < 0:
        return normalized[:max_chars].rstrip() + "..."

    start = max(0, hit - max_chars // 3)
    end = min(len(normalized), start + max_chars)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(normalized) else ""
    return prefix + normalized[start:end].strip() + suffix


def _score_from_row(row: dict[str, Any]) -> float | None:
    for key in ("_reranker_score", "_relevance_score", "_score", "score", "_distance", "distance"):
        if key in row and row[key] is not None:
            try:
                return float(row[key])
            except (TypeError, ValueError):
                return None
    return None


def _parse_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        warnings.warn("Could not parse metadata_json from LanceDB row.")
        return {}


def _source_filter_sql(source_filter: str) -> str:
    escaped = source_filter.replace("'", "''").replace("%", "\\%")
    return f"(source_path LIKE '%{escaped}%' OR indexed_source_path LIKE '%{escaped}%')"


def _field_or_metadata(row: dict[str, Any], metadata: dict[str, Any], key: str):
    value = row.get(key)
    if value in ("", None):
        value = metadata.get(key)
    return value


class BGEHighPrecisionReranker:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model = None

    def rerank(self, query: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return rows
        model = self._load_model()
        pairs = [[query, str(row.get("text") or "")] for row in rows]
        try:
            scores = model.compute_score(pairs, normalize=True)
        except TypeError:
            scores = model.compute_score(pairs)
        scores_list = _score_list(scores)

        scored_rows: list[tuple[float, int, dict[str, Any]]] = []
        for index, row in enumerate(rows):
            score = scores_list[index] if index < len(scores_list) else 0.0
            enriched = dict(row)
            enriched["_reranker_score"] = score
            scored_rows.append((score, index, enriched))
        scored_rows.sort(key=lambda item: (-item[0], item[1]))
        return [row for _, _, row in scored_rows]

    def _load_model(self):
        if self._model is not None:
            return self._model

        global FlagReranker
        if FlagReranker is None:
            try:
                from FlagEmbedding import FlagReranker as ImportedFlagReranker
            except Exception as exc:
                raise RuntimeError("FlagEmbedding FlagReranker is unavailable.") from exc
            FlagReranker = ImportedFlagReranker

        kwargs = {"use_fp16": _cuda_available()}
        self._model = FlagReranker(self.model_name, **kwargs)
        return self._model


def reranker_dependency_status(model_name: str = "BAAI/bge-reranker-v2-m3", *, deep_check: bool = False) -> dict[str, Any]:
    import_available = importlib.util.find_spec("FlagEmbedding") is not None
    status: dict[str, Any] = {
        "available": import_available,
        "import_available": import_available,
        "model_loadable": None,
        "compute_score_available": None,
        "runtime_checked": deep_check,
        "engine": "FlagEmbedding.FlagReranker",
        "error": None if import_available else "FlagEmbedding is not importable.",
    }
    if not import_available or not deep_check:
        return status

    try:
        reranker = BGEHighPrecisionReranker(model_name)
        reranker._load_model()
        status["model_loadable"] = True
    except Exception as exc:
        status["available"] = False
        status["model_loadable"] = False
        status["compute_score_available"] = False
        status["error"] = f"Model load failed: {exc}"
        return status

    try:
        reranked = reranker.rerank("query", [{"text": "passage"}])
        status["compute_score_available"] = bool(reranked and "_reranker_score" in reranked[0])
        status["available"] = bool(status["compute_score_available"])
        if not status["available"]:
            status["error"] = "compute_score did not return a usable score."
    except Exception as exc:
        status["available"] = False
        status["compute_score_available"] = False
        status["error"] = f"compute_score failed: {exc}"
    return {
        **status,
    }


def _safe_score(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _score_list(scores: Any) -> list[float]:
    if hasattr(scores, "tolist"):
        scores = scores.tolist()
    if isinstance(scores, (list, tuple)):
        return [_safe_score(score) for score in scores]
    return [_safe_score(scores)]


def _cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False
