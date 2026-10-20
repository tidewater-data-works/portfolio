"""Rules parser and confidence scoring. No model and no network."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from extract import extract_document
from schema import FieldsIn, confidence_for, needs_review, quality_flags

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "documents"


def _text(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


def test_clean_invoice_scores_high_and_reconciles():
    document = extract_document(_text("hp-2026-0142.txt"), document_id="hp-2026-0142")
    assert document.extractor == "rules"
    assert document.needs_review is False
    assert document.confidence == Decimal("0.90")
    assert document.confidence_flags == []
    assert document.vendor_name == "Harbor & Pine Supply Co."
    assert document.document_number == "HP-2026-0142"
    assert document.document_date == "2026-02-12"
    assert document.bill_to == "Lantern Street Clinic"
    assert document.subtotal == Decimal("245.40")
    assert document.tax == Decimal("12.27")
    assert document.total == Decimal("257.67")
    assert [item.amount for item in document.line_items] == [
        Decimal("114.00"),
        Decimal("75.00"),
        Decimal("56.40"),
    ]
    assert document.line_items[1].quantity == Decimal("12")
    assert document.line_items[1].unit_price == Decimal("6.25")


def test_clean_receipt_has_amounts_without_quantities():
    document = extract_document(_text("bhp-receipt-88421.txt"), document_id="bhp-receipt-88421")
    assert document.document_type == "receipt"
    assert document.needs_review is False
    assert document.confidence == Decimal("0.90")
    assert document.document_number == "R-88421"
    assert document.total == Decimal("27.30")
    assert [item.quantity for item in document.line_items] == [None, None]
    assert [item.description for item in document.line_items] == [
        "Pallet labels, roll",
        "Bill of lading pad",
    ]


def test_messy_receipt_is_queued_with_stable_flags():
    document = extract_document(_text("counter-2026-0318.txt"), document_id="counter-2026-0318")
    assert document.document_type == "receipt"
    assert document.vendor_name == "blue heron counter"
    assert document.document_date is None
    assert document.document_number is None
    assert document.total == Decimal("9.00")
    assert document.confidence == Decimal("0.33")
    assert document.needs_review is True
    assert document.confidence_flags == [
        "ambiguous_date",
        "inferred_document_type",
        "line_sum_mismatch",
        "missing_document_number",
        "weak_vendor",
    ]


def test_partial_invoice_is_missing_vendor_and_number():
    document = extract_document(_text("partial-nb-draft.txt"), document_id="partial-nb-draft")
    assert document.vendor_name is None
    assert document.document_number is None
    assert document.document_date == "2026-04-02"
    assert document.total == Decimal("1800.00")
    assert document.line_items[0].description == "Dashboard retainer"
    assert document.confidence == Decimal("0.55")
    assert document.needs_review is True
    assert document.confidence_flags == ["missing_document_number", "missing_vendor"]


def test_line_amount_mismatch_and_us_date_and_bad_currency():
    text = "\n".join(
        [
            "INVOICE",
            "Vendor: Blue Heron Printworks",
            "Invoice number: BHP-1001",
            "Invoice date: 03/18/2026",
            "Currency: US$",
            "",
            "Description                          Qty    Unit price    Amount",
            "Pallet labels                        2      14.00         20.00",
            "",
            "Subtotal: 20.00",
            "Tax: 0.00",
            "Total: 20.00",
        ]
    )
    document = extract_document(text, document_id="bhp-1001")
    assert document.document_date == "2026-03-18"
    assert document.currency == "USD"
    assert document.confidence_flags == ["bad_currency", "line_amount_mismatch"]
    assert document.needs_review is True
    assert document.confidence == Decimal("0.65")


def test_unparsed_date_does_not_also_count_as_missing():
    text = "\n".join(
        [
            "RECEIPT",
            "Merchant: Blue Heron Printworks",
            "Receipt number: R-1",
            "Receipt date: March-2",
            "Description                          Amount",
            "Pallet labels                        10.00",
            "Total: 10.00",
        ]
    )
    document = extract_document(text, document_id="r-1")
    assert document.document_date is None
    assert "unparsed_date" in document.confidence_flags
    assert "missing_date" not in document.confidence_flags


def test_negative_amount_is_a_review_flag_and_still_validates():
    fields = FieldsIn.model_validate(
        {
            "document_type": "receipt",
            "vendor_name": "Blue Heron Printworks",
            "document_number": "R-9",
            "document_date": "2026-03-18",
            "total": "-1.00",
            "line_items": [{"description": "Counter credit", "amount": "-1.00"}],
        }
    )
    assert "negative_amount" in quality_flags(fields)
    assert needs_review(Decimal("0.70"), ["negative_amount"]) is True


def test_review_threshold_keeps_a_clean_rules_row():
    assert confidence_for("rules", []) == Decimal("0.90")
    assert needs_review(Decimal("0.90"), []) is False
    assert needs_review(Decimal("0.80"), []) is False
    assert needs_review(Decimal("0.80"), ["weak_vendor"]) is True
    assert confidence_for("llm", ["line_sum_mismatch"]) == Decimal("0.82")
