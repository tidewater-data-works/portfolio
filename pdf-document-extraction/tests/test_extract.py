"""Offline extraction tests. PDFs are generated in-process or read from samples/."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from catalog import INVOICES, REPORTS
from extract import execute, extract_directory, main, parse_money
from generate_samples import build_pdf, write_catalog, write_invoice

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
ROOT_README = Path(__file__).resolve().parents[2] / "README.md"


def _expected_invoice(spec: dict) -> dict:
    return {
        "source_file": spec["filename"],
        "document_type": "invoice",
        "fields": dict(spec["fields"]),
        "line_items": [dict(item) for item in spec["line_items"]],
        "validation": {"ok": True, "errors": []},
    }


def _expected_report(spec: dict) -> dict:
    return {
        "source_file": spec["filename"],
        "document_type": "report",
        "fields": dict(spec["fields"]),
        "rows": [dict(row) for row in spec["rows"]],
        "validation": {"ok": True, "errors": []},
    }


def test_parse_money_strips_symbol_and_commas():
    assert parse_money("$1,240.50") == parse_money("1240.50")
    assert parse_money(" 1800 ") == parse_money("1800.00")
    with pytest.raises(ValueError):
        parse_money("1.005")
    with pytest.raises(ValueError):
        parse_money("n/a")


def test_catalog_pdfs_extract_to_the_source_figures(tmp_path):
    write_catalog(tmp_path)
    documents = extract_directory(tmp_path)
    expected = [_expected_invoice(spec) for spec in INVOICES]
    expected.append(_expected_report(REPORTS[0]))
    assert documents == sorted(expected, key=lambda document: document["source_file"])


def test_rebuilding_a_pdf_is_byte_stable(tmp_path):
    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    write_invoice(first, INVOICES[0])
    write_invoice(second, INVOICES[0])
    assert first.read_bytes() == second.read_bytes()


def test_committed_samples_match_a_fresh_extract():
    documents = extract_directory(SAMPLES)
    payload = json.loads((SAMPLES / "invoices.json").read_text(encoding="utf-8"))
    assert payload == {"documents": documents}
    assert all(document["validation"]["ok"] for document in documents)
    with (SAMPLES / "line_items.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == sum(len(spec["line_items"]) for spec in INVOICES)
    assert {row["invoice_number"] for row in rows} == {
        spec["fields"]["invoice_number"] for spec in INVOICES
    }
    with (SAMPLES / "report_rows.csv").open(encoding="utf-8", newline="") as handle:
        report_rows = list(csv.DictReader(handle))
    assert [row["metric"] for row in report_rows] == [row["metric"] for row in REPORTS[0]["rows"]]
    assert report_rows[0]["organization"] == "Cedar Wharf Logistics"


def test_line_amount_and_total_mismatches_fail_validation(tmp_path):
    spec = {
        "filename": "bad.pdf",
        "fields": {
            "vendor_name": "Harbor & Pine Supply Co.",
            "invoice_number": "HP-2026-0999",
            "invoice_date": "2026-02-12",
            "due_date": "2026-02-01",
            "bill_to": "Lantern Street Clinic",
            "currency": "USD",
            "subtotal": "12.50",
            "tax": "1.00",
            "total": "12.00",
        },
        "line_items": [
            {
                "description": "Archive boxes",
                "quantity": "2",
                "unit_price": "6.25",
                "amount": "10.00",
            }
        ],
    }
    write_invoice(tmp_path / "bad.pdf", spec)
    document = extract_directory(tmp_path)[0]
    assert document["validation"]["ok"] is False
    assert document["validation"]["errors"] == [
        "line_amount:1",
        "due_before_invoice",
        "line_sum_mismatch",
        "tax_total_mismatch",
    ]


def test_report_period_order_and_missing_title(tmp_path):
    build_pdf(
        tmp_path / "report.pdf",
        heading="QUARTERLY REPORT",
        field_lines=[
            "Document type: report",
            "Organization: Cedar Wharf Logistics",
            "Period start: 2026-03-31",
            "Period end: 2026-01-01",
            "Prepared by: Northbank Analytics LLC",
        ],
        table_header=["Metric", "Value", "Notes"],
        table_rows=[["Shipments", "10", "Closed bills of lading"]],
        col_widths=[160, 90, 210],
    )
    document = extract_directory(tmp_path)[0]
    assert document["validation"]["errors"] == ["missing_title", "period_order"]


def test_unknown_document_type_and_unreadable_pdf(tmp_path):
    build_pdf(
        tmp_path / "note.pdf",
        heading="NOTE",
        field_lines=["Document type: memo", "Vendor: Blue Heron Printworks"],
        table_header=["Metric", "Value", "Notes"],
        table_rows=[["Shipments", "1", "n/a"]],
        col_widths=[160, 90, 210],
    )
    (tmp_path / "broken.pdf").write_bytes(b"this is not a pdf")
    documents = {document["source_file"]: document for document in extract_directory(tmp_path)}
    assert documents["note.pdf"]["validation"]["errors"] == ["unknown_document_type"]
    assert documents["broken.pdf"]["validation"]["errors"] == ["unreadable_pdf"]


def test_invalid_invoice_is_omitted_from_csv_and_cli_exits_1(tmp_path):
    source = tmp_path / "in"
    output = tmp_path / "out"
    source.mkdir()
    write_invoice(source / INVOICES[0]["filename"], INVOICES[0])
    bad = {
        "filename": "bad-total.pdf",
        "fields": dict(INVOICES[0]["fields"], total="999.99", invoice_number="HP-2026-1000"),
        "line_items": INVOICES[0]["line_items"],
    }
    write_invoice(source / bad["filename"], bad)
    assert main(["--input-dir", str(source), "--output-dir", str(output)]) == 1
    payload = json.loads((output / "invoices.json").read_text(encoding="utf-8"))
    assert {document["source_file"] for document in payload["documents"]} == {
        INVOICES[0]["filename"],
        "bad-total.pdf",
    }
    with (output / "invoices.csv").open(encoding="utf-8", newline="") as handle:
        numbers = [row["invoice_number"] for row in csv.DictReader(handle)]
    assert numbers == ["HP-2026-0142"]


def test_cli_rewrites_the_sample_outputs(tmp_path):
    code = execute(SAMPLES, tmp_path)
    assert code == 0
    assert (tmp_path / "invoices.json").read_text(encoding="utf-8") == (
        SAMPLES / "invoices.json"
    ).read_text(encoding="utf-8")
    assert (tmp_path / "line_items.csv").read_text(encoding="utf-8") == (
        SAMPLES / "line_items.csv"
    ).read_text(encoding="utf-8")


def test_missing_directory_and_empty_directory(tmp_path):
    assert main(["--input-dir", str(tmp_path / "missing"), "--output-dir", str(tmp_path)]) == 1
    empty = tmp_path / "empty"
    empty.mkdir()
    assert execute(empty, tmp_path / "out") == 1
    assert not (tmp_path / "out" / "invoices.json").exists()


def test_portfolio_readme_links_this_demo():
    text = ROOT_README.read_text(encoding="utf-8")
    assert "](pdf-document-extraction/)" in text
    assert "pdf-document-extraction/README.md" in text
