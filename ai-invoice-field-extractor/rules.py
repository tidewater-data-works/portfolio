"""Deterministic invoice and receipt parser.

Columns in a line-item row are separated by two or more spaces. Labeled
fields use ``Label: value``. A two-digit year is not turned into a date.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

_VENDOR = re.compile(r"(?im)^(?:vendor|merchant|seller)\s*[:\-]\s*(.+?)\s*$")
_NUMBER = re.compile(
    r"(?im)^(?:invoice|receipt|document)\s*(?:number|no\.?|#)\s*[:\-]\s*([A-Za-z0-9][A-Za-z0-9\-]*)\s*$"
)
_DATE = re.compile(
    r"(?im)^(?:invoice date|receipt date|document date|date)\s*[:\-]\s*(\S+)\s*$"
)
_BILL_TO = re.compile(r"(?im)^bill to\s*[:\-]\s*(.+?)\s*$")
_CURRENCY = re.compile(r"(?im)^currency\s*[:\-]\s*(\S+)\s*$")
_MONEY = re.compile(
    r"(?i)^(subtotal|tax|total|amount due)\s*[:\-]?\s*\$?\s*(\d[\d,]*\.\d{2})\s*$"
)
_INVOICE_LINE = re.compile(
    r"^(?P<description>.+?)\s{2,}(?P<qty>\d+(?:\.\d+)?)\s+"
    r"(?P<unit>\d[\d,]*\.\d{2})\s+(?P<amount>\d[\d,]*\.\d{2})\s*$"
)
_RECEIPT_LINE = re.compile(
    r"^(?P<description>.+?)\s{2,}(?P<amount>\d[\d,]*\.\d{2})\s*$"
)
_BARE_DATE = re.compile(r"^(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})$")
_SKIP_VENDOR = re.compile(r"(?i)^(thanks|thank you|hello|hi|dear)\b")


def rules_extract(text: str) -> tuple[dict, list[str]]:
    """Return a schema-shaped dict and parser flags."""
    flags: list[str] = []
    lines = text.splitlines()
    document_type, inferred = _document_type(lines)
    if inferred:
        flags.append("inferred_document_type")
    vendor = _first(_VENDOR, text)
    if vendor is None:
        vendor = _guess_vendor(lines)
        if vendor is not None:
            flags.append("weak_vendor")
    document_date, date_flags = _document_date(text, lines)
    flags.extend(date_flags)
    currency, currency_flag = _currency(text)
    if currency_flag:
        flags.append(currency_flag)
    amounts = _amounts(lines)
    return (
        {
            "document_type": document_type,
            "vendor_name": vendor,
            "document_number": _first(_NUMBER, text),
            "document_date": document_date,
            "bill_to": _first(_BILL_TO, text),
            "currency": currency,
            "subtotal": amounts.get("subtotal"),
            "tax": amounts.get("tax"),
            "total": amounts.get("total"),
            "line_items": _line_items(lines),
        },
        flags,
    )


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    if not match:
        return None
    value = " ".join(match.group(1).split())
    return value or None


def _document_type(lines: list[str]) -> tuple[str, bool]:
    for line in lines:
        heading = line.strip().casefold()
        if not heading:
            continue
        if heading in {"invoice", "receipt"}:
            return heading, False
        break
    blob = "\n".join(lines).casefold()
    if "receipt" in blob or "amount due" in blob:
        return "receipt", True
    return "invoice", True


def _guess_vendor(lines: list[str]) -> str | None:
    for line in lines:
        raw = line.strip()
        text = " ".join(raw.split())
        if not text:
            continue
        if text.casefold() in {"invoice", "receipt"}:
            continue
        if _SKIP_VENDOR.search(text):
            continue
        if _BARE_DATE.fullmatch(text):
            continue
        if _is_header(raw) or _labeled(raw) or _line_match(raw):
            continue
        if not re.search(r"[A-Za-z]", text):
            continue
        return text
    return None


def _document_date(text: str, lines: list[str]) -> tuple[str | None, list[str]]:
    match = _DATE.search(text)
    if match:
        iso, flag = _parse_date(match.group(1))
        return iso, [flag] if flag else []
    for line in lines:
        token = " ".join(line.split())
        if not _BARE_DATE.fullmatch(token):
            continue
        iso, flag = _parse_date(token)
        flags: list[str] = []
        if iso is not None:
            flags.append("unlabeled_date")
        if flag:
            flags.append(flag)
        return iso, flags
    return None, []


def _parse_date(value: str) -> tuple[str | None, str | None]:
    text = value.strip()
    try:
        return date.fromisoformat(text).isoformat(), None
    except ValueError:
        pass
    full = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if full:
        month, day, year = (int(part) for part in full.groups())
        try:
            return date(year, month, day).isoformat(), None
        except ValueError:
            return None, "unparsed_date"
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2}", text):
        return None, "ambiguous_date"
    return None, "unparsed_date"


def _currency(text: str) -> tuple[str, str | None]:
    match = _CURRENCY.search(text)
    if not match:
        return "USD", None
    token = match.group(1).strip()
    if re.fullmatch(r"[A-Za-z]{3}", token):
        return token.upper(), None
    return "USD", "bad_currency"


def _amounts(lines: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in lines:
        match = _MONEY.match(line.strip())
        if not match:
            continue
        label = match.group(1).casefold()
        key = "total" if label == "amount due" else label
        found.setdefault(key, _money(match.group(2)))
    return found


def _line_items(lines: list[str]) -> list[dict]:
    items: list[dict] = []
    for raw in lines:
        line = raw.rstrip()
        if not line.strip() or _is_header(line) or _labeled(line):
            continue
        invoice = _INVOICE_LINE.match(line.strip())
        if invoice:
            items.append(
                {
                    "description": " ".join(invoice.group("description").split()),
                    "quantity": _quantity(invoice.group("qty")),
                    "unit_price": _money(invoice.group("unit")),
                    "amount": _money(invoice.group("amount")),
                }
            )
            continue
        receipt = _RECEIPT_LINE.match(line.strip())
        if receipt and re.search(r"[A-Za-z]", receipt.group("description")):
            items.append(
                {
                    "description": " ".join(receipt.group("description").split()),
                    "quantity": None,
                    "unit_price": None,
                    "amount": _money(receipt.group("amount")),
                }
            )
    return items


def _line_match(text: str) -> bool:
    return _INVOICE_LINE.match(text) is not None or _RECEIPT_LINE.match(text) is not None


def _is_header(line: str) -> bool:
    compact = " ".join(line.split()).casefold()
    return compact.startswith("description") and compact.endswith("amount")


def _labeled(line: str) -> bool:
    text = line.strip()
    return any(
        pattern.match(text) or pattern.search(text)
        for pattern in (_VENDOR, _NUMBER, _DATE, _BILL_TO, _CURRENCY, _MONEY)
    )


def _money(value: str) -> str:
    number = Decimal(value.replace(",", "").replace("$", ""))
    return f"{number.quantize(Decimal('0.01'))}"


def _quantity(value: str) -> str:
    number = Decimal(value)
    if number == number.to_integral():
        return str(number.to_integral())
    return format(number, "f")
