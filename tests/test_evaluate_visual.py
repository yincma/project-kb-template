from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kb.evaluate_visual import QueryFormatError, _evaluate_item, load_query_set


ROOT = Path(__file__).resolve().parents[1]


def test_evaluator_top_k_match_can_pass_at_rank_two():
    item = {
        "id": "rank_two",
        "query": "API Gateway architecture",
        "expected": {
            "source_path": {"contains": "architecture.pdf"},
            "attachment_path": {"contains": "kb_assets"},
            "page_number": {"any_of": [3, 4]},
            "terms": {"all_of": ["API Gateway", "VPC"], "case_sensitive": False},
        },
        "must_not_include_raw_unreviewed_evidence": True,
    }
    results = [
        {
            "source_path": "sources/other.pdf",
            "attachment_path": "docs/_attachments/kb_assets/other.png",
            "page_number": 1,
            "snippet": "Unrelated content",
            "index_role": "curated",
            "raw_evidence": False,
            "review_status": "reviewed",
        },
        {
            "source_path": "sources/client architecture.pdf",
            "attachment_path": "docs/_attachments/kb_assets/client/diagram.png",
            "page_number": 3,
            "snippet": "API Gateway connects to VPC services.",
            "index_role": "curated",
            "raw_evidence": False,
            "review_status": "approved",
        },
    ]

    evaluated = _evaluate_item(item, results, strict=True)

    assert evaluated["passed"] is True
    assert evaluated["matched_rank"] == 2


def test_evaluator_checks_raw_unreviewed_across_all_top_k():
    item = {
        "id": "no_raw",
        "query": "diagram",
        "expected": {"terms": {"any_of": ["diagram"]}},
        "must_not_include_raw_unreviewed_evidence": True,
    }
    results = [
        {
            "source_path": "docs/reviewed.md",
            "snippet": "diagram",
            "index_role": "curated",
            "raw_evidence": False,
            "review_status": "reviewed",
        },
        {
            "source_path": "sources/raw.pdf",
            "snippet": "diagram",
            "index_role": "raw",
            "raw_evidence": True,
            "review_status": "unreviewed",
        },
    ]

    evaluated = _evaluate_item(item, results, strict=True)

    assert evaluated["passed"] is False
    assert any("raw/unreviewed" in reason for reason in evaluated["fail_reasons"])


def test_load_query_set_rejects_bad_yaml_shape(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("not_queries: true\n", encoding="utf-8")

    with pytest.raises(QueryFormatError):
        load_query_set(bad)


def test_evaluate_visual_cli_bad_yaml_exit_code(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("queries: nope\n", encoding="utf-8")

    result = subprocess.run(
        ["uv", "run", "project-kb-evaluate-visual", "--queries", str(bad), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=120,
    )

    assert result.returncode == 2
    assert "error" in result.stdout
