"""Pydantic schema, confidence score, and review reasons for one document."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ExtractorName = Literal["rules", "llm"]
DocumentType = Literal["invoice", "receipt"]

PENALTY = {
    "missing_vendor": Decimal("0.20"),
    "weak_vendor": Decimal("0.10"),
    "missing_document_number": Decimal("0.15"),
    "missing_date": Decimal("0.15"),
    "ambiguous_date": Decimal("0.12"),
    "unparsed_date": Decimal("0.15"),
    "unlabeled_date": Decimal("0.05"),
    "missing_total": Decimal("0.25"),
    "no_line_items": Decimal("0.15"),
    "line_sum_mismatch": Decimal("0.15"),
    "line_amount_mismatch": Decimal("0.15"),
    "tax_total_mismatch": Decimal("0.20"),
    "negative_amount": Decimal("0.20"),
    "inferred_document_type": Decimal("0.05"),
    "bad_currency": Decimal("0.10"),
}
FORCE_REVIEW = {
    "missing_vendor",
    "weak_vendor",
    "missing_date",
    "ambiguous_date",
    "unparsed_date",
    "missing_total",
    "no_line_items",
    "line_sum_mismatch",
    "line_amount_mismatch",
    "tax_total_mismatch",
    "negative_amount",
    "bad_currency",
}
BASE_CONFIDENCE = {"rules": Decimal("0.90"), "llm": Decimal("0.97")}
REVIEW_THRESHOLD = Decimal("0.80")

REASONS = {
    "ambiguous_date": "Date is not a full ISO date",
    "bad_currency": "Currency is not a three-letter code",
    "inferred_document_type": "Document type was inferred",
    "line_amount_mismatch": "A line amount does not equal quantity times unit price",
    "line_sum_mismatch": "Line items do not add up to the subtotal or total",
    "llm_fallback": "Rules backend ran because the model backend did not return a usable payload",
    "llm_invalid_response": "Model response was not usable JSON",
    "llm_schema_invalid": "Model response did not match the field schema",
    "missing_date": "Date is missing",
    "missing_document_number": "Document number is missing",
    "missing_total": "Total is missing",
    "missing_vendor": "Vendor is missing",
    "negative_amount": "An amount is negative",
    "no_line_items": "No line items were read",
    "tax_total_mismatch": "Subtotal plus tax does not equal the total",
    "unlabeled_date": "Date was not on a labeled line",
    "unparsed_date": "Date could not be parsed",
    "weak_vendor": "Vendor was guessed from an unlabeled line",
}


def _blank(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _optional_decimal(value: object) -> object:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.strip().replace("$", "").replace(",", "")
        return text or None
    return value


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, value: object) -> str:
        text = _blank(value)
        if text is None:
            raise ValueError("empty description")
        return text

    @field_validator("quantity", "unit_price", "amount", mode="before")
    @classmethod
    def _decimal(cls, value: object) -> object:
        return _optional_decimal(value)


class FieldsIn(BaseModel):
    """Fields a backend is allowed to return. Extra keys are rejected."""

    model_config = ConfigDict(extra="forbid")

    document_type: DocumentType
    vendor_name: str | None = None
    document_number: str | None = None
    document_date: str | None = None
    bill_to: str | None = None
    currency: str = "USD"
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    line_items: list[LineItem] = Field(default_factory=list)

    @field_validator("vendor_name", "document_number", "bill_to", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _blank(value)

    @field_validator("document_date", mode="before")
    @classmethod
    def _iso_date(cls, value: object) -> str | None:
        if value is None or value == "":
            return None
        text = str(value).strip()
        date.fromisoformat(text)
        return text

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        text = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", text):
            raise ValueError("currency must be three letters")
        return text

    @field_validator("subtotal", "tax", "total", mode="before")
    @classmethod
    def _money(cls, value: object) -> object:
        return _optional_decimal(value)


class ExtractedDocument(FieldsIn):
    document_id: str = Field(min_length=1)
    confidence: Decimal = Field(ge=0, le=1)
    confidence_flags: list[str]
    needs_review: bool
    extractor: ExtractorName

    @field_validator("confidence_flags")
    @classmethod
    def _flags(cls, value: list[str]) -> list[str]:
        return sorted(set(value))


def quality_flags(fields: FieldsIn, parser_flags: list[str] | None = None) -> list[str]:
    """Flags that describe the numbers, independent of which backend read them."""
    seen = set(parser_flags or [])
    flags: list[str] = []
    if not fields.vendor_name:
        flags.append("missing_vendor")
    if not fields.document_number:
        flags.append("missing_document_number")
    if not fields.document_date and not ({"ambiguous_date", "unparsed_date"} & seen):
        flags.append("missing_date")
    if fields.total is None:
        flags.append("missing_total")
    if not fields.line_items:
        flags.append("no_line_items")
    if _negative(fields):
        flags.append("negative_amount")
    if fields.line_items:
        line_sum = sum((item.amount for item in fields.line_items), Decimal("0"))
        target = fields.subtotal
        if target is None and fields.total is not None and (fields.tax is None or fields.tax == 0):
            target = fields.total
        if target is not None and abs(line_sum - target) > Decimal("0.01"):
            flags.append("line_sum_mismatch")
        for item in fields.line_items:
            if item.quantity is None or item.unit_price is None:
                continue
            expected = (item.quantity * item.unit_price).quantize(Decimal("0.01"))
            if abs(expected - item.amount) > Decimal("0.01"):
                flags.append("line_amount_mismatch")
                break
    if None not in (fields.subtotal, fields.tax, fields.total):
        assert fields.subtotal is not None and fields.tax is not None and fields.total is not None
        if abs(fields.subtotal + fields.tax - fields.total) > Decimal("0.01"):
            flags.append("tax_total_mismatch")
    return flags


def _negative(fields: FieldsIn) -> bool:
    totals = [fields.subtotal, fields.tax, fields.total]
    if any(value is not None and value < 0 for value in totals):
        return True
    for item in fields.line_items:
        if item.amount < 0:
            return True
        if item.unit_price is not None and item.unit_price < 0:
            return True
        if item.quantity is not None and item.quantity < 0:
            return True
    return False


def confidence_for(extractor: ExtractorName, flags: list[str]) -> Decimal:
    score = BASE_CONFIDENCE[extractor]
    for flag in set(flags):
        score -= PENALTY.get(flag, Decimal("0"))
    if score < 0:
        score = Decimal("0")
    if score > 1:
        score = Decimal("1")
    return score.quantize(Decimal("0.01"))


def needs_review(score: Decimal, flags: list[str]) -> bool:
    if score < REVIEW_THRESHOLD:
        return True
    return any(flag in FORCE_REVIEW for flag in flags)


def review_reason(flags: list[str]) -> str:
    return "; ".join(REASONS[flag] for flag in flags if flag in REASONS)
