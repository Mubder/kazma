"""XLSX engine for the unified document layer.

Spreadsheets don't share the document :class:`ContentModel` (they have their
own sheets/rows/cols shape), so this engine consumes the native ``sheets``
payload **plus** a :class:`~kazma_core.documents.profile.DocProfile`. The
profile supplies the shared theme (header fill, fonts, grid) and direction
(``sheet_view.rightToLeft`` for Arabic), so an Arabic workbook matches the
Arabic DOCX/PDF/HTML in design and direction.

Theme tokens come from :data:`kazma_core.documents.style_theme.THEME` via the
profile; nothing here hardcodes colours or direction.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from kazma_core.documents.profile import DocProfile

logger = logging.getLogger(__name__)

__all__ = ["XlsxEngine"]


class XlsxEngine:
    """Render a sheets payload to a themed ``.xlsx`` under a :class:`DocProfile`."""

    def __init__(self, profile: DocProfile) -> None:
        self.profile = profile
        self.theme = profile.theme

    def render(self, payload: dict[str, Any], output: Path | str) -> None:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

        t = self.theme
        rtl = self.profile.rtl
        font_name = "Calibri"
        header_fill_hex = str(t["heading_fill"]).lstrip("#")
        body_color_hex = str(t["body"]).lstrip("#")
        grid_hex = str(t["table_grid"]).lstrip("#")
        alt_row_hex = str(t["table_row_bg"]).lstrip("#")

        header_fill = PatternFill(fill_type="solid", fgColor=header_fill_hex)
        alt_fill = PatternFill(fill_type="solid", fgColor=alt_row_hex)
        header_font = Font(bold=True, color="FFFFFF", name=font_name, size=11)
        body_font = Font(name=font_name, color=body_color_hex, size=11)
        thin = Side(style="thin", color=grid_hex)
        cell_border = Border(left=thin, right=thin, top=thin, bottom=thin)
        # readingOrder: 1 = LTR context, 2 = RTL context
        align = Alignment(
            horizontal="right" if rtl else "left",
            vertical="center",
            readingOrder=2 if rtl else 1,
        )

        workbook = Workbook()
        workbook.remove(workbook.active)

        sheets = payload.get("sheets")
        if not isinstance(sheets, list):
            sheets = []
        for index, value in enumerate(sheets, 1):
            if not isinstance(value, dict):
                continue
            name = str(value.get("name", f"Sheet{index}"))[:31] or f"Sheet{index}"
            sheet = workbook.create_sheet(name)
            sheet.sheet_view.rightToLeft = rtl  # column A on the right for Arabic

            rows = value.get("rows")
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, list):
                    sheet.append([self._cell(item) for item in row])

            max_col = sheet.max_column or 1
            max_row = sheet.max_row or 0
            for r in range(1, max_row + 1):
                is_header = (r == 1)
                for c in range(1, max_col + 1):
                    cell = sheet.cell(row=r, column=c)
                    cell.font = header_font if is_header else body_font
                    cell.fill = header_fill if is_header else (
                        alt_fill if (r % 2 == 0) else PatternFill()
                    )
                    cell.border = cell_border
                    cell.alignment = align
            # Reasonable column widths (openpyxl can't autosize).
            for c in range(1, max_col + 1):
                length = max(
                    (len(str(sheet.cell(row=r, column=c).value or "")) for r in range(1, max_row + 1)),
                    default=0,
                )
                sheet.column_dimensions[sheet.cell(row=1, column=c).column_letter].width = min(48, max(10, length + 2))
            sheet.freeze_panes = "A2"  # keep header visible

        if not workbook.sheetnames:
            workbook.create_sheet("Sheet")
        workbook.save(str(output))

    @staticmethod
    def _cell(item: Any) -> Any:
        """Normalise a payload cell: None -> empty string, keep numbers/dates."""
        if item is None:
            return ""
        return item
