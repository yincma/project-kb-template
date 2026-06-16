from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any

from rich.console import Console
from rich.table import Table

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kb.embeddings import BGEEmbedder
from kb.query import _annotate_result_role
from kb.retrieval import ProjectRetriever
from kb.store import LanceDBStore, load_config


DEFAULT_OUTPUT_SENTINEL = "__default__"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run smoke-level evaluation for curated visual search results.")
    parser.add_argument("--queries", required=True, help="YAML file containing visual evaluation queries")
    parser.add_argument("--config", default="kb/config.yaml", help="Curated KB config path")
    parser.add_argument("--top-k", type=int, default=5, help="Default top_k when a query does not specify one")
    parser.add_argument("--strict", action="store_true", help="Fail when provenance fields are missing")
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    parser.add_argument(
        "--output",
        nargs="?",
        const=DEFAULT_OUTPUT_SENTINEL,
        default=None,
        help="Optional report path. With no value, writes to kb/cache/evaluation/runs/<timestamp>.json",
    )
    args = parser.parse_args()

    console = Console()
    try:
        query_set = load_query_set(args.queries)
    except QueryFormatError as exc:
        _emit_error(console, args.json, str(exc), exit_code=2)

    try:
        report = evaluate_visual_queries(
            query_set,
            config_path=args.config,
            default_top_k=args.top_k,
            strict=args.strict,
        )
    except Exception as exc:
        _emit_error(console, args.json, f"Query execution failed: {exc}", exit_code=3)

    output_path = _resolve_output_path(args.output)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["output_path"] = str(output_path)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(console, report)

    raise SystemExit(0 if report["overall_pass"] else 1)


class QueryFormatError(ValueError):
    pass


def load_query_set(path: str | Path) -> dict[str, Any]:
    try:
        import yaml
    except Exception as exc:  # pragma: no cover
        raise QueryFormatError("PyYAML is required to read visual evaluation queries.") from exc

    query_path = Path(path)
    if not query_path.exists():
        raise QueryFormatError(f"Query YAML does not exist: {query_path}")
    try:
        data = yaml.safe_load(query_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise QueryFormatError(f"Could not parse query YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise QueryFormatError("Query YAML must be a mapping with a `queries` list.")
    queries = data.get("queries")
    if not isinstance(queries, list):
        raise QueryFormatError("Query YAML must contain `queries: [...]`.")
    for index, item in enumerate(queries, start=1):
        if not isinstance(item, dict) or not item.get("query"):
            raise QueryFormatError(f"Query item #{index} must contain a `query` string.")
    return data


def evaluate_visual_queries(
    query_set: dict[str, Any],
    *,
    config_path: str | Path = "kb/config.yaml",
    default_top_k: int = 5,
    strict: bool = False,
) -> dict[str, Any]:
    cfg = load_config(config_path)
    store = LanceDBStore(cfg)
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
    results = []
    for item in query_set["queries"]:
        top_k = int(item.get("top_k") or default_top_k)
        payload = retriever.search(str(item["query"]), top_k=top_k, include_text=False)
        annotated_results = [_annotate_result_role(result, cfg.database.index_role) for result in payload.get("results", [])]
        results.append(_evaluate_item(item, annotated_results, strict=strict))

    passed = sum(1 for result in results if result["passed"])
    failed = len(results) - passed
    return {
        "overall_pass": failed == 0,
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "results": results,
    }


def _evaluate_item(item: dict[str, Any], results: list[dict[str, Any]], *, strict: bool) -> dict[str, Any]:
    expected = _normalize_expected(item)
    fail_reasons: list[str] = []
    warnings: list[str] = []
    matched_rank: int | None = None
    mismatch_summary: list[str] = []

    if not results:
        fail_reasons.append("no results returned")

    for rank, result in enumerate(results, start=1):
        provenance_warnings = _provenance_warnings(result)
        warnings.extend(f"rank {rank}: {warning}" for warning in provenance_warnings)
        mismatches = _expected_mismatches(result, expected)
        if not mismatches and matched_rank is None:
            matched_rank = rank
        if mismatches:
            mismatch_summary.append(f"rank {rank}: " + "; ".join(mismatches[:4]))

    if expected and matched_rank is None and results:
        fail_reasons.append("no top-k result matched expected fields")
        if mismatch_summary:
            fail_reasons.extend(mismatch_summary[:3])

    if item.get("must_not_include_raw_unreviewed_evidence"):
        violations = [
            rank
            for rank, result in enumerate(results, start=1)
            if _is_raw_unreviewed(result)
        ]
        if violations:
            fail_reasons.append(f"raw/unreviewed evidence appeared in ranks {violations}")

    if strict and warnings:
        fail_reasons.extend(warnings)

    return {
        "id": item.get("id") or item["query"],
        "query": item["query"],
        "passed": not fail_reasons,
        "matched_rank": matched_rank,
        "fail_reasons": fail_reasons,
        "warnings": warnings,
        "top_results_summary": [_summarize_result(rank, result) for rank, result in enumerate(results, start=1)],
    }


def _normalize_expected(item: dict[str, Any]) -> dict[str, Any]:
    expected = dict(item.get("expected") or {})
    legacy_map = {
        "expected_source_path": "source_path",
        "expected_attachment_path": "attachment_path",
        "expected_page_number": "page_number",
        "expected_terms": "terms",
    }
    for legacy_key, expected_key in legacy_map.items():
        if legacy_key in item and expected_key not in expected:
            expected[expected_key] = item[legacy_key]
    if "terms" in expected and isinstance(expected["terms"], list):
        expected["terms"] = {"all_of": expected["terms"], "case_sensitive": False}
    return expected


def _expected_mismatches(result: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    mismatches: list[str] = []
    for field in ("source_path", "attachment_path", "indexed_source_path", "visual_type"):
        if field in expected and not _matches_value(result.get(field), expected[field]):
            mismatches.append(f"{field} did not match")
    if "page_number" in expected and not _matches_number(result.get("page_number"), expected["page_number"]):
        mismatches.append("page_number did not match")
    if "terms" in expected and not _matches_terms(result, expected["terms"]):
        mismatches.append("terms did not match")
    return mismatches


def _matches_value(actual: Any, expectation: Any) -> bool:
    if expectation in (None, ""):
        return True
    actual_text = "" if actual is None else str(actual)
    if isinstance(expectation, list):
        return any(_matches_value(actual_text, item) for item in expectation)
    if isinstance(expectation, dict):
        if "any_of" in expectation:
            return any(_matches_value(actual_text, item) for item in expectation.get("any_of") or [])
        if "exact" in expectation and actual_text != str(expectation["exact"]):
            return False
        if "contains" in expectation and str(expectation["contains"]) not in actual_text:
            return False
        if "regex" in expectation and not re.search(str(expectation["regex"]), actual_text):
            return False
        return True
    return actual_text == str(expectation)


def _matches_number(actual: Any, expectation: Any) -> bool:
    if expectation in (None, ""):
        return True
    if isinstance(expectation, dict):
        if "any_of" in expectation:
            return _matches_number(actual, expectation["any_of"])
        if "exact" in expectation:
            return _matches_number(actual, expectation["exact"])
    if isinstance(expectation, list):
        return any(_matches_number(actual, item) for item in expectation)
    try:
        return int(actual) == int(expectation)
    except (TypeError, ValueError):
        return False


def _matches_terms(result: dict[str, Any], expectation: Any) -> bool:
    if expectation in (None, "", []):
        return True
    if isinstance(expectation, list):
        expectation = {"all_of": expectation}
    if not isinstance(expectation, dict):
        expectation = {"all_of": [expectation]}
    case_sensitive = bool(expectation.get("case_sensitive", False))
    haystack = " ".join(
        str(value)
        for value in (
            result.get("snippet"),
            result.get("source_path"),
            result.get("indexed_source_path"),
            result.get("attachment_path"),
            result.get("visual_type"),
        )
        if value
    )
    if not case_sensitive:
        haystack = haystack.lower()

    def contains(term: Any) -> bool:
        needle = str(term)
        if not case_sensitive:
            needle = needle.lower()
        return needle in haystack

    all_terms = expectation.get("all_of")
    if all_terms and not all(contains(term) for term in all_terms):
        return False
    any_terms = expectation.get("any_of")
    if any_terms and not any(contains(term) for term in any_terms):
        return False
    return True


def _provenance_warnings(result: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for field in ("index_role", "raw_evidence", "review_status"):
        if field not in result:
            warnings.append(f"missing provenance field `{field}`")
    return warnings


def _is_raw_unreviewed(result: dict[str, Any]) -> bool:
    review_status = str(result.get("review_status") or "").lower()
    return bool(
        result.get("raw_evidence") is True
        or result.get("index_role") == "raw"
        or review_status in {"unreviewed", "needs_review"}
    )


def _summarize_result(rank: int, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": rank,
        "score": result.get("score"),
        "asset_type": result.get("asset_type"),
        "visual_type": result.get("visual_type"),
        "source_path": result.get("source_path"),
        "indexed_source_path": result.get("indexed_source_path"),
        "attachment_path": result.get("attachment_path"),
        "page_number": result.get("page_number"),
        "slide_number": result.get("slide_number"),
        "index_role": result.get("index_role"),
        "raw_evidence": result.get("raw_evidence"),
        "review_status": result.get("review_status"),
        "snippet": result.get("snippet"),
    }


def _resolve_output_path(value: str | None) -> Path | None:
    if value is None:
        return None
    if value == DEFAULT_OUTPUT_SENTINEL:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return Path("kb/cache/evaluation/runs") / f"{timestamp}.json"
    return Path(value)


def _print_report(console: Console, report: dict[str, Any]) -> None:
    table = Table(title="Visual Evaluation")
    table.add_column("ID")
    table.add_column("Status")
    table.add_column("Matched Rank")
    table.add_column("Reasons")
    for result in report["results"]:
        table.add_row(
            str(result["id"]),
            "PASS" if result["passed"] else "FAIL",
            "" if result["matched_rank"] is None else str(result["matched_rank"]),
            "; ".join(result["fail_reasons"] or result["warnings"][:2]),
        )
    console.print(table)
    console.print(
        f"overall_pass={report['overall_pass']} total={report['total']} "
        f"passed={report['passed']} failed={report['failed']}"
    )
    if report.get("output_path"):
        console.print(f"report={report['output_path']}")


def _emit_error(console: Console, json_output: bool, message: str, *, exit_code: int) -> None:
    if json_output:
        print(json.dumps({"error": message}, ensure_ascii=False, indent=2))
    else:
        console.print(f"[red]{message}[/red]")
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
