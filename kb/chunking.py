from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import re


@dataclass(frozen=True)
class TextChunk:
    text: str
    heading: str | None
    chunk_index: int
    start_char: int
    end_char: int
    metadata: dict[str, Any] = field(default_factory=dict)


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def chunk_document(
    text: str,
    *,
    file_ext: str = "",
    chunk_size: int = 1000,
    chunk_overlap: int = 120,
    default_heading: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> list[TextChunk]:
    """Split text into contextual chunks.

    The system uses character windows as a stable v1 approximation for tokens.
    Markdown files are first split into heading sections, then oversized sections
    are split with overlap.
    """
    if not text or not text.strip():
        return []

    chunk_size = max(200, int(chunk_size))
    chunk_overlap = max(0, min(int(chunk_overlap), chunk_size // 2))

    if file_ext.lower() == ".md":
        chunks = _chunk_markdown(text, chunk_size, chunk_overlap, metadata or {})
    else:
        chunks = _chunk_plain_text(text, default_heading, 0, chunk_size, chunk_overlap, metadata or {})

    return [
        TextChunk(
            text=chunk.text,
            heading=chunk.heading,
            chunk_index=index,
            start_char=chunk.start_char,
            end_char=chunk.end_char,
            metadata=dict(chunk.metadata),
        )
        for index, chunk in enumerate(chunks)
        if chunk.text.strip()
    ]


def chunk_parsed_document(
    parsed_document,
    *,
    file_ext: str = "",
    chunk_size: int = 1000,
    chunk_overlap: int = 120,
) -> list[TextChunk]:
    """Split parsed sections while preserving section-level citation metadata."""
    chunks: list[TextChunk] = []
    sections = getattr(parsed_document, "sections", None) or []
    if not sections and getattr(parsed_document, "text", ""):
        return chunk_document(
            parsed_document.text,
            file_ext=file_ext,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    for section_index, section in enumerate(sections):
        section_text = getattr(section, "text", "")
        if not section_text or not section_text.strip():
            continue
        section_metadata = section.to_metadata() if hasattr(section, "to_metadata") else {}
        section_metadata["section_index"] = section_index
        section_chunks = chunk_document(
            section_text,
            file_ext=file_ext,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            default_heading=getattr(section, "heading", None),
            metadata=section_metadata,
        )
        for chunk in section_chunks:
            chunks.append(
                TextChunk(
                    text=chunk.text,
                    heading=chunk.heading or getattr(section, "heading", None),
                    chunk_index=len(chunks),
                    start_char=chunk.start_char,
                    end_char=chunk.end_char,
                    metadata=dict(chunk.metadata),
                )
            )
    return chunks


def _chunk_markdown(text: str, chunk_size: int, chunk_overlap: int, metadata: dict[str, Any]) -> list[TextChunk]:
    headings = list(HEADING_RE.finditer(text))
    if not headings:
        return _chunk_plain_text(text, None, 0, chunk_size, chunk_overlap, metadata)

    chunks: list[TextChunk] = []
    if headings[0].start() > 0:
        chunks.extend(_chunk_plain_text(text[: headings[0].start()], None, 0, chunk_size, chunk_overlap, metadata))

    for index, match in enumerate(headings):
        section_start = match.start()
        section_end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        heading = match.group(2).strip()
        section_text = text[section_start:section_end]
        chunks.extend(_chunk_plain_text(section_text, heading, section_start, chunk_size, chunk_overlap, metadata))

    return chunks


def _chunk_plain_text(
    text: str,
    heading: str | None,
    offset: int,
    chunk_size: int,
    chunk_overlap: int,
    metadata: dict[str, Any],
) -> list[TextChunk]:
    stripped = text.strip()
    if not stripped:
        return []

    if len(text) <= chunk_size:
        start = _first_non_space(text)
        end = _last_non_space(text)
        return [
            TextChunk(
                text=text[start:end],
                heading=heading,
                chunk_index=0,
                start_char=offset + start,
                end_char=offset + end,
                metadata=dict(metadata),
            )
        ]

    chunks: list[TextChunk] = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + chunk_size, text_len)
        if end < text_len:
            end = _choose_breakpoint(text, start, end, chunk_size)

        raw = text[start:end]
        leading = _first_non_space(raw)
        trailing = _last_non_space(raw)
        if trailing > leading:
            chunks.append(
                TextChunk(
                    text=raw[leading:trailing],
                    heading=heading,
                    chunk_index=len(chunks),
                    start_char=offset + start + leading,
                    end_char=offset + start + trailing,
                    metadata=dict(metadata),
                )
            )

        if end >= text_len:
            break
        next_start = max(end - chunk_overlap, start + 1)
        start = next_start

    return chunks


def _choose_breakpoint(text: str, start: int, proposed_end: int, chunk_size: int) -> int:
    min_end = start + max(100, chunk_size // 2)
    window = text[min_end:proposed_end]
    for separator in ("\n\n", "\n", ". ", " "):
        relative = window.rfind(separator)
        if relative >= 0:
            return min_end + relative + len(separator)
    return proposed_end


def _first_non_space(text: str) -> int:
    for index, char in enumerate(text):
        if not char.isspace():
            return index
    return 0


def _last_non_space(text: str) -> int:
    for index in range(len(text) - 1, -1, -1):
        if not text[index].isspace():
            return index + 1
    return len(text)
