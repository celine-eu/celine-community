"""Safe CSV and XLSX rendering for manager-facing technical tables."""

from __future__ import annotations

import csv
from io import BytesIO, StringIO
from typing import Any, Literal

import xlsxwriter
from fastapi import Response

ExportFormat = Literal["csv", "xlsx"]


def _safe(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def tabular_response(
    *,
    filename: str,
    columns: list[tuple[str, str]],
    rows: list[dict[str, Any]],
    format: ExportFormat,
) -> Response:
    headers = [label for _, label in columns]
    if format == "csv":
        output = StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([_safe(row.get(key)) for key, _ in columns])
        content = "\ufeff" + output.getvalue()
        media_type = "text/csv; charset=utf-8"
    else:
        binary = BytesIO()
        workbook = xlsxwriter.Workbook(binary, {"in_memory": True})
        worksheet = workbook.add_worksheet("CELINE")
        header_format = workbook.add_format({"bold": True, "bg_color": "#CCFBF1"})
        for column, label in enumerate(headers):
            worksheet.write(0, column, label, header_format)
        for row_index, row in enumerate(rows, start=1):
            for column, (key, _) in enumerate(columns):
                worksheet.write(row_index, column, _safe(row.get(key)))
        worksheet.autofilter(0, 0, max(0, len(rows)), max(0, len(headers) - 1))
        worksheet.freeze_panes(1, 0)
        workbook.close()
        content = binary.getvalue()
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    extension = format
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}.{extension}"',
            "Cache-Control": "private, no-store",
        },
    )
