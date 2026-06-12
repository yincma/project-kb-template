from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".csv",
    ".json",
    ".yaml",
    ".yml",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".sql",
    ".toml",
    ".ini",
}

OFFICE_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | OFFICE_EXTENSIONS


@dataclass
class ParsedSection:
    text: str
    heading: str | None = None
    page_number: int | None = None
    slide_number: int | None = None
    sheet_name: str | None = None
    row_range: str | None = None
    cell_range: str | None = None
    ocr_used: bool = False
    ocr_confidence: float | None = None
    parser_name: str | None = None
    source_format: str | None = None
    extraction_method: str | None = None
    asset_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        metadata = dict(self.metadata)
        for key in (
            "page_number",
            "slide_number",
            "sheet_name",
            "row_range",
            "cell_range",
            "ocr_used",
            "ocr_confidence",
            "parser_name",
            "source_format",
            "extraction_method",
            "asset_type",
        ):
            value = getattr(self, key)
            if value is not None:
                metadata[key] = value
        return metadata


@dataclass
class ParsedDocument:
    path: Path
    text: str = ""
    sections: list[ParsedSection] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.sections and not self.text:
            self.text = "\n\n".join(section.text for section in self.sections if section.text.strip())
        elif self.text and not self.sections:
            self.sections = [
                ParsedSection(
                    text=self.text,
                    parser_name="text",
                    source_format=self.path.suffix.lower(),
                    extraction_method="text",
                    asset_type="document",
                )
            ]


def parse_file(path: str | Path, config: Any | None = None) -> ParsedDocument | None:
    path = Path(path)
    ext = path.suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        return ParsedDocument(path=path, warnings=[f"Skipped unsupported file type: {path}"])

    if ext in TEXT_EXTENSIONS:
        from kb.parsers.text import parse_text_file

        return parse_text_file(path)

    if ext == ".pdf":
        from kb.parsers.pdf import parse_pdf_file

        return parse_pdf_file(path, config=config)

    if ext == ".pptx":
        from kb.parsers.pptx import parse_pptx_file

        return parse_pptx_file(path, config=config)

    if ext == ".xlsx":
        from kb.parsers.xlsx import parse_xlsx_file

        return parse_xlsx_file(path, config=config)

    if ext == ".docx":
        from kb.parsers.docx import parse_docx_file

        return parse_docx_file(path, config=config)

    return ParsedDocument(path=path, warnings=[f"Skipped unsupported file type: {path}"])
