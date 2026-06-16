from __future__ import annotations

from pathlib import Path
from typing import Any

from kb.parsers.ocr import OCRProcessor
from kb.parsers.registry import ParsedDocument, ParsedSection
from kb.multimodal.extraction import (
    new_visual_stats,
    record_render_decision,
    register_visual_bytes,
    should_render_pdf_page,
)


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
    visual_stats = new_visual_stats()
    try:
        document = pymupdf.open(path)
    except Exception as exc:
        return ParsedDocument(path=path, warnings=[f"Failed to parse {path}: {exc}"])

    try:
        page_count = min(len(document), max_pages)
        if len(document) > max_pages:
            warnings.append(f"Skipped PDF pages after {max_pages}: {path}")

        rendered_pages = 0
        visual_assets = 0
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
                page_text = ""
            if page_text:
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
            if _multimodal_enabled(config):
                multimodal_cfg = _cfg_value(_cfg_value(_cfg_value(config, "parsing", None), "multimodal", None), "pdf", None)
                max_rendered = int(_cfg_value(multimodal_cfg, "max_rendered_pages_per_file", 30) or 30)
                max_assets = int(_cfg_value(multimodal_cfg, "max_visual_assets_per_file", 200) or 200)
                if bool(_cfg_value(multimodal_cfg, "extract_embedded_images", True)):
                    for image_number, image_bytes in enumerate(_embedded_images(page, document), start=1):
                        if visual_assets >= max_assets:
                            visual_stats["limit_warnings"].append(f"Skipped PDF visual assets after {max_assets}: {path}")
                            break
                        section = register_visual_bytes(
                            image_bytes=image_bytes,
                            source_path=path,
                            config=config,
                            generated_from="embedded_image",
                            occurrence_index=image_number,
                            page_number=page_index + 1,
                            context_title=f"Page {page_index + 1}",
                            nearby_text=page_text[:2000],
                            ext=".png",
                            stats=visual_stats,
                        )
                        visual_assets += 1
                        if section:
                            sections.append(section)

                decision = should_render_pdf_page(page, native_text, config)
                record_render_decision(visual_stats, decision)
                if decision.should_render and rendered_pages < max_rendered and visual_assets < max_assets:
                    try:
                        render_dpi = int(_cfg_value(multimodal_cfg, "render_dpi", 180) or 180)
                        matrix = pymupdf.Matrix(render_dpi / 72, render_dpi / 72)
                        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                        section = register_visual_bytes(
                            image_bytes=pixmap.tobytes("png"),
                            source_path=path,
                            config=config,
                            generated_from="page_render",
                            occurrence_index=10000 + page_index + 1,
                            page_number=page_index + 1,
                            context_title=f"Page {page_index + 1}",
                            nearby_text=page_text[:2000],
                            ext=".png",
                            render_dpi=render_dpi,
                            stats=visual_stats,
                        )
                        rendered_pages += 1
                        visual_assets += 1
                        visual_stats["rendered_pages"] += 1
                        if section:
                            section.metadata["render_decision"] = decision.to_json()
                            sections.append(section)
                    except Exception as exc:
                        warnings.append(f"PDF page render failed for {path} page {page_index + 1}: {exc}")
                elif decision.should_render and rendered_pages >= max_rendered:
                    visual_stats["limit_warnings"].append(f"Skipped PDF page renders after {max_rendered}: {path}")
    finally:
        document.close()

    for warning in visual_stats.get("warnings", []):
        warnings.append(str(warning))
    for warning in visual_stats.get("limit_warnings", []):
        warnings.append(str(warning))
    return ParsedDocument(path=path, sections=sections, warnings=warnings)


def _ocr_config(config: Any | None) -> Any | None:
    return _cfg_value(_cfg_value(config, "parsing", None), "ocr", None)


def _multimodal_enabled(config: Any | None) -> bool:
    return bool(_cfg_value(_cfg_value(_cfg_value(config, "parsing", None), "multimodal", None), "enabled", False))


def _embedded_images(page, document) -> list[bytes]:
    images: list[bytes] = []
    seen_xrefs: set[int] = set()
    try:
        page_images = page.get_images(full=True)
    except Exception:
        return images
    for item in page_images:
        if not item:
            continue
        xref = int(item[0])
        if xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)
        try:
            extracted = document.extract_image(xref)
            data = extracted.get("image")
            if data:
                images.append(bytes(data))
        except Exception:
            continue
    return images


def _cfg_value(config: Any | None, name: str, default: Any = None) -> Any:
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(name, default)
    return getattr(config, name, default)
