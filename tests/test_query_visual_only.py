from __future__ import annotations

from kb.obsidian import to_obsidian_vault_path, to_obsidian_wikilink
from kb.query import (
    VISUAL_EMPTY_WARNING,
    _annotate_result_role,
    _search_visual_only,
    _source_display,
)


VISUAL_RESULT = {
    "asset_type": "visual",
    "source_path": "sources/architecture.pdf",
    "indexed_source_path": ".kb_cache_raw/multimodal/visual_chunks.jsonl",
    "attachment_path": "docs/_attachments/kb_assets/source_hash/image.png",
    "page_number": 3,
    "visual_type": "architecture_diagram",
    "snippet": "Visual summary from source page 3: API Gateway VPC",
}


class FakeRetriever:
    def __init__(self, *, visual_threshold: int | None) -> None:
        self.visual_threshold = visual_threshold
        self.calls: list[dict] = []

    def search(self, query: str, *, top_k: int, candidate_k: int, source_filter: str | None):
        self.calls.append({"query": query, "top_k": top_k, "candidate_k": candidate_k, "source_filter": source_filter})
        results = [
            {
                "asset_type": "text",
                "source_path": "docs/text.md",
                "snippet": "text result",
            }
        ]
        if self.visual_threshold is not None and candidate_k >= self.visual_threshold:
            results.append(dict(VISUAL_RESULT))
        return {
            "query": query,
            "top_k": top_k,
            "candidate_k": candidate_k,
            "warnings": [],
            "results": results,
        }


def test_obsidian_wikilink_is_vault_relative_for_docs_attachment():
    assert to_obsidian_vault_path("docs/_attachments/kb_assets/x.png") == "_attachments/kb_assets/x.png"
    assert to_obsidian_wikilink("docs/_attachments/kb_assets/x.png") == "![[_attachments/kb_assets/x.png]]"


def test_visual_only_expands_candidate_pool_before_filtering():
    retriever = FakeRetriever(visual_threshold=100)

    payload = _search_visual_only(
        retriever,
        "API Gateway",
        source_filter=None,
        requested_top_k=5,
        configured_candidate_k=20,
        index_role="raw",
    )

    assert retriever.calls[0]["candidate_k"] == 100
    assert payload["results"]
    assert payload["visual_candidate_k"] == 100
    assert payload["visual_retry_used"] is False
    assert all(result["asset_type"] == "visual" for result in payload["results"])


def test_visual_only_retries_with_larger_candidate_pool_when_empty():
    retriever = FakeRetriever(visual_threshold=300)

    payload = _search_visual_only(
        retriever,
        "API Gateway",
        source_filter=None,
        requested_top_k=5,
        configured_candidate_k=20,
        index_role="raw",
    )

    assert [call["candidate_k"] for call in retriever.calls] == [100, 300]
    assert payload["results"]
    assert payload["visual_candidate_k"] == 300
    assert payload["visual_retry_used"] is True


def test_visual_only_empty_retry_adds_clear_warning():
    retriever = FakeRetriever(visual_threshold=None)

    payload = _search_visual_only(
        retriever,
        "API Gateway",
        source_filter=None,
        requested_top_k=5,
        configured_candidate_k=20,
        index_role="raw",
    )

    assert payload["results"] == []
    assert payload["visual_retry_used"] is True
    assert VISUAL_EMPTY_WARNING in payload["warnings"]


def test_visual_result_json_and_text_output_use_vault_relative_wikilink():
    annotated = _annotate_result_role(dict(VISUAL_RESULT), "raw")
    rendered = _source_display(annotated)

    assert annotated["attachment_path"] == "docs/_attachments/kb_assets/source_hash/image.png"
    assert annotated["attachment_wikilink"] == "![[_attachments/kb_assets/source_hash/image.png]]"
    assert "attachment=docs/_attachments/kb_assets/source_hash/image.png" in rendered
    assert "wikilink=![[_attachments/kb_assets/source_hash/image.png]]" in rendered
    assert "![[docs/_attachments" not in rendered
