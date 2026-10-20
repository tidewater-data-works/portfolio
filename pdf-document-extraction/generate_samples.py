"""Build the synthetic invoice and report PDFs in samples/.

Output is deterministic: reportlab's invariant mode pins the document id
and timestamps, and every string in the file comes from catalog.py.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from catalog import INVOICES, REPORTS

rl_config.invariant = 1

AUTHOR = "Tidewater Data Works"
INVOICE_HEADERS = ["Description", "Quantity", "Unit price", "Amount"]
REPORT_HEADERS = ["Metric", "Value", "Notes"]


def build_pdf(
    path: Path,
    *,
    heading: str,
    field_lines: list[str],
    table_header: list[str],
    table_rows: list[list[str]],
    footer_lines: list[str] | None = None,
    col_widths: list[int] | None = None,
) -> None:
    """Write one single-page PDF. ``field_lines`` are ``Label: value`` strings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    document = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        title=heading,
        author=AUTHOR,
    )
    story: list = [Paragraph(escape(heading), styles["Title"])]
    for line in field_lines:
        story.append(Paragraph(escape(line), styles["Normal"]))
    story.append(Spacer(1, 12))
    data = [table_header, *table_rows]
    table = Table(data, colWidths=col_widths)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(table)
    if footer_lines:
        story.append(Spacer(1, 12))
        for line in footer_lines:
            story.append(Paragraph(escape(line), styles["Normal"]))
    document.build(story)


def invoice_field_lines(fields: dict[str, str]) -> list[str]:
    return [
        "Document type: invoice",
        f"Vendor: {fields['vendor_name']}",
        f"Invoice number: {fields['invoice_number']}",
        f"Invoice date: {fields['invoice_date']}",
        f"Due date: {fields['due_date']}",
        f"Bill to: {fields['bill_to']}",
        f"Currency: {fields['currency']}",
    ]


def invoice_table(line_items: list[dict[str, str]]) -> list[list[str]]:
    return [
        [item["description"], item["quantity"], item["unit_price"], item["amount"]]
        for item in line_items
    ]


def invoice_footer(fields: dict[str, str]) -> list[str]:
    return [
        f"Subtotal: {fields['subtotal']}",
        f"Tax: {fields['tax']}",
        f"Total: {fields['total']}",
    ]


def write_invoice(path: Path, spec: dict) -> None:
    fields = spec["fields"]
    build_pdf(
        path,
        heading="INVOICE",
        field_lines=invoice_field_lines(fields),
        table_header=INVOICE_HEADERS,
        table_rows=invoice_table(spec["line_items"]),
        footer_lines=invoice_footer(fields),
        col_widths=[250, 70, 70, 70],
    )


def write_report(path: Path, spec: dict) -> None:
    fields = spec["fields"]
    build_pdf(
        path,
        heading="QUARTERLY REPORT",
        field_lines=[
            "Document type: report",
            f"Title: {fields['title']}",
            f"Organization: {fields['organization']}",
            f"Period start: {fields['period_start']}",
            f"Period end: {fields['period_end']}",
            f"Prepared by: {fields['prepared_by']}",
        ],
        table_header=REPORT_HEADERS,
        table_rows=[[row["metric"], row["value"], row["notes"]] for row in spec["rows"]],
        col_widths=[160, 90, 210],
    )


def write_catalog(output_dir: Path) -> list[Path]:
    """Write every catalog PDF and return the paths."""
    written: list[Path] = []
    for spec in INVOICES:
        path = output_dir / spec["filename"]
        write_invoice(path, spec)
        written.append(path)
    for spec in REPORTS:
        path = output_dir / spec["filename"]
        write_report(path, spec)
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the synthetic sample PDFs.")
    parser.add_argument("--output-dir", type=Path, default=Path("samples"))
    args = parser.parse_args(argv)
    paths = write_catalog(args.output_dir)
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
