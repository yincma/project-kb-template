from __future__ import annotations

from pathlib import Path
from typing import Any

from kb.parsers.registry import ParsedDocument, ParsedSection


def parse_xlsx_file(path: Path, config: Any | None = None) -> ParsedDocument:
    warnings: list[str] = []
    try:
        import openpyxl
        from openpyxl.utils import get_column_letter
    except Exception as exc:
        return ParsedDocument(path=path, warnings=[f"Failed to parse {path}: openpyxl is unavailable: {exc}"])

    office_cfg = _cfg_value(_cfg_value(config, "parsing", None), "office", None)
    max_rows = int(_cfg_value(office_cfg, "max_sheet_rows", 20000) or 20000)

    try:
        workbook = openpyxl.load_workbook(path, data_only=False, read_only=True)
        cached_workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:
        return ParsedDocument(path=path, warnings=[f"Failed to parse {path}: {exc}"])

    sections: list[ParsedSection] = []
    try:
        for sheet_name in workbook.sheetnames:
            sheet = workbook[sheet_name]
            cached_sheet = cached_workbook[sheet_name]
            max_row = min(sheet.max_row or 0, max_rows)
            if (sheet.max_row or 0) > max_rows:
                warnings.append(f"Skipped rows after {max_rows} in {path} sheet {sheet_name}")
            if max_row <= 0 or (sheet.max_column or 0) <= 0:
                continue

            rows: list[str] = []
            for row_index, row in enumerate(
                sheet.iter_rows(min_row=1, max_row=max_row, max_col=sheet.max_column),
                start=1,
            ):
                rendered_cells: list[str] = []
                for col_index, cell in enumerate(row, start=1):
                    cached_cell = cached_sheet.cell(row=row_index, column=col_index)
                    rendered_cells.append(_render_cell(cell.value, cached_cell.value))
                if any(value.strip() for value in rendered_cells):
                    rows.append("\t".join(rendered_cells))

            if not rows:
                continue
            row_range = f"1:{max_row}"
            cell_range = f"A1:{get_column_letter(sheet.max_column)}{max_row}"
            sections.append(
                ParsedSection(
                    text="\n".join(rows),
                    heading=sheet_name,
                    sheet_name=sheet_name,
                    row_range=row_range,
                    cell_range=cell_range,
                    parser_name="xlsx",
                    source_format=".xlsx",
                    extraction_method="table",
                    asset_type="sheet",
                )
            )
    finally:
        workbook.close()
        cached_workbook.close()

    return ParsedDocument(path=path, sections=sections, warnings=warnings)


def _render_cell(value: Any, cached_value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if text.startswith("="):
        cached = "" if cached_value is None else str(cached_value)
        return f"{text} (cached: {cached})" if cached else text
    return text


def _cfg_value(config: Any | None, name: str, default: Any = None) -> Any:
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(name, default)
    return getattr(config, name, default)
