from __future__ import annotations

from pathlib import Path
from typing import Any

from kb.parsers.ocr import OCRProcessor
from kb.parsers.registry import ParsedDocument, ParsedSection


def parse_pdf_file(path: Path, config: Any | None = None) -> ParsedDocument:
    warnings: list[str] = []
    try:
        try:
            import pymupdf
        except Exception:
            import fitz as pymupdf
    except Exception as exc:
        return ParsedDocument(path=path, warnings=[f"Failed to parse {path}: PyMuPDF is unavailable: {exc}"])

    ocr_cfg = _ocr_config(config)
    ocr = OCRProcessor(_cfg_value(ocr_cfg, "engine", "rapidocr")) if _cfg_value(ocr_cfg, "enabled", True) else None
    min_chars = int(_cfg_value(ocr_cfg, "min_text_chars_per_page", 30) or 30)
    max_pages = int(_cfg_value(ocr_cfg, "max_pages_per_file", 300) or 300)
    image_dpi = int(_cfg_value(ocr_cfg, "image_dpi", 180) or 180)

    sections: list[ParsedSection] = []
    try:
        document = pymupdf.open(path)
    except Exception as exc:
        return ParsedDocument(path=path, warnings=[f"Failed to parse {path}: {exc}"])

    try:
        page_count = min(len(document), max_pages)
        if len(document) > max_pages:
            warnings.append(f"Skipped PDF pages after {max_pages}: {path}")

        for page_index in range(page_count):
            page = document[page_index]
            native_text = (page.get_text("text") or "").strip()
            text_parts = [native_text] if native_text else []
            ocr_used = False
            ocr_confidence = None
            extraction_method = "text"

            if ocr and len(native_text) < min_chars:
                try:
                    matrix = pymupdf.Matrix(image_dpi / 72, image_dpi / 72)
                    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                    ocr_result = ocr.ocr_image(pixmap.tobytes("png"))
                    if ocr_result.text.strip():
                        text_parts.append(ocr_result.text.strip())
                        ocr_used = True
                        ocr_confidence = ocr_result.confidence
                        extraction_method = "ocr" if not native_text else "text+ocr"
                except Exception as exc:
                    warnings.append(f"OCR failed for {path} page {page_index + 1}: {exc}")

            page_text = "\n".join(part for part in text_parts if part.strip()).strip()
            if not page_text:
                continue
            sections.append(
                ParsedSection(
                    text=page_text,
                    heading=f"Page {page_index + 1}",
                    page_number=page_index + 1,
                    ocr_used=ocr_used,
                    ocr_confidence=ocr_confidence,
                    parser_name="pdf",
                    source_format=".pdf",
                    extraction_method=extraction_method,
                    asset_type="page",
                )
            )
    finally:
        document.close()

    return ParsedDocument(path=path, sections=sections, warnings=warnings)


def _ocr_config(config: Any | None) -> Any | None:
    return _cfg_value(_cfg_value(config, "parsing", None), "ocr", None)


def _cfg_value(config: Any | None, name: str, default: Any = None) -> Any:
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(name, default)
    return getattr(config, name, default)
