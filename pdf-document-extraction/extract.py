"""Extract invoice and report fields from the synthetic PDFs.

Tables come from the ruled grid. Header fields come from ``Label: value``
lines. Validation checks required fields, dates, and that line amounts,
subtotal, tax, and total agree to the cent.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber

logger = logging.getLogger(__name__)

INVOICE_HEADER = ["description", "quantity", "unit price", "amount"]
REPORT_HEADER = ["metric", "value", "notes"]
INVOICE_COLUMNS = [
    "source_file",
    "vendor_name",
    "invoice_number",
    "invoice_date",
    "due_date",
    "bill_to",
    "currency",
    "subtotal",
    "tax",
    "total",
]
LINE_COLUMNS = [
    "source_file",
    "invoice_number",
    "description",
    "quantity",
    "unit_price",
    "amount",
]
REPORT_COLUMNS = [
    "source_file",
    "organization",
    "prepared_by",
    "period_start",
    "period_end",
    "metric",
    "value",
    "notes",
]
LABELS = {
    "document type": "document_type",
    "vendor": "vendor_name",
    "invoice number": "invoice_number",
    "invoice date": "invoice_date",
    "due date": "due_date",
    "bill to": "bill_to",
    "currency": "currency",
    "subtotal": "subtotal",
    "tax": "tax",
    "total": "total",
    "title": "title",
    "organization": "organization",
    "period start": "period_start",
    "period end": "period_end",
    "prepared by": "prepared_by",
}
MONEY_FIELDS = {"subtotal", "tax", "total"}
INVOICE_NUMBER = re.compile(r"^(?=.*\d)[A-Z0-9]+(?:-[A-Z0-9]+)+$")
_FIELD = re.compile(r"^([A-Za-z][A-Za-z ]*):[ \t]*(.+)$")
_MONEY = re.compile(r"-?\d+(?:\.\d+)?$")
_QUANTITY = re.compile(r"\d+(?:\.\d+)?$")


class UnreadablePDF(Exception):
    """The file could not be opened as a PDF."""


def parse_money(value: str) -> Decimal:
    """Parse a dollar amount. More than two decimal places is rejected."""
    text = value.strip().replace("$", "").replace(",", "").replace(" ", "")
    if not _MONEY.fullmatch(text):
        raise ValueError(f"not a money amount: {value!r}")
    number = Decimal(text)
    if number.as_tuple().exponent < -2:
        raise ValueError(f"more than two decimal places: {value!r}")
    return number.quantize(Decimal("0.01"))


def parse_quantity(value: str) -> Decimal:
    text = value.strip().replace(",", "")
    if not _QUANTITY.fullmatch(text):
        raise ValueError(f"not a quantity: {value!r}")
    number = Decimal(text)
    if number.as_tuple().exponent < -2:
        raise ValueError(f"more than two decimal places: {value!r}")
    if number <= 0:
        raise ValueError(f"quantity must be positive: {value!r}")
    return number


def format_quantity(value: Decimal) -> str:
    if value == value.to_integral():
        return str(value.to_integral())
    return format(value, "f")


def format_money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}"


def read_pdf(path: Path) -> tuple[str, list[list[list[str | None]]]]:
    try:
        with pdfplumber.open(path) as pdf:
            texts: list[str] = []
            tables: list[list[list[str | None]]] = []
            for page in pdf.pages:
                texts.append(page.extract_text() or "")
                for table in page.extract_tables() or []:
                    tables.append(table)
    except Exception as exc:
        raise UnreadablePDF(path.name) from exc
    return "\n".join(texts), tables


def parse_fields(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for raw_line in text.splitlines():
        match = _FIELD.match(raw_line.strip())
        if not match:
            continue
        label = " ".join(match.group(1).split()).casefold()
        key = LABELS.get(label)
        if key is None:
            continue
        found[key] = " ".join(match.group(2).split())
    return found


def _header(row: list[str | None]) -> list[str]:
    return [" ".join((cell or "").split()).casefold() for cell in row]


def find_table(
    tables: list[list[list[str | None]]],
    expected: list[str],
) -> list[list[str | None]] | None:
    for table in tables:
        if table and _header(table[0]) == expected:
            return table
    return None


def parse_invoice_table(table: list[list[str | None]] | None) -> tuple[list[dict], list[str]]:
    if table is None:
        return [], ["missing_line_items"]
    items: list[dict] = []
    errors: list[str] = []
    for index, row in enumerate(table[1:], start=1):
        cells = [("" if cell is None else " ".join(cell.split())) for cell in row]
        if not any(cells):
            continue
        description, quantity, unit_price, amount = (cells + ["", "", "", ""])[:4]
        item: dict = {"description": description}
        if not description:
            errors.append(f"missing_description:{index}")
        try:
            item["quantity"] = parse_quantity(quantity)
        except ValueError:
            item["quantity"] = None
            errors.append(f"bad_quantity:{index}")
        try:
            item["unit_price"] = parse_money(unit_price)
        except ValueError:
            item["unit_price"] = None
            errors.append(f"bad_unit_price:{index}")
        try:
            item["amount"] = parse_money(amount)
        except ValueError:
            item["amount"] = None
            errors.append(f"bad_line_amount:{index}")
        items.append(item)
        if (
            item["quantity"] is not None
            and item["unit_price"] is not None
            and item["amount"] is not None
            and (item["quantity"] * item["unit_price"]).quantize(Decimal("0.01")) != item["amount"]
        ):
            errors.append(f"line_amount:{index}")
    if not items and "missing_line_items" not in errors:
        errors.append("missing_line_items")
    return items, errors


def parse_report_table(table: list[list[str | None]] | None) -> tuple[list[dict], list[str]]:
    if table is None:
        return [], ["missing_rows"]
    rows: list[dict] = []
    errors: list[str] = []
    for index, row in enumerate(table[1:], start=1):
        cells = [("" if cell is None else " ".join(cell.split())) for cell in row]
        if not any(cells):
            continue
        metric, value, notes = (cells + ["", "", ""])[:3]
        if not metric:
            errors.append(f"missing_metric:{index}")
        try:
            parsed = Decimal(value.replace(",", ""))
            if not re.fullmatch(r"-?\d+(?:\.\d+)?", value.replace(",", "")):
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            parsed = None
            errors.append(f"bad_value:{index}")
        rows.append({"metric": metric, "value": parsed, "notes": notes})
    if not rows:
        errors.append("missing_rows")
    return rows, errors


def _parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _money_field(raw: dict[str, str], key: str, errors: list[str]) -> Decimal | None:
    if key not in raw or raw[key] == "":
        errors.append(f"missing_{key}")
        return None
    try:
        return parse_money(raw[key])
    except ValueError:
        errors.append(f"bad_{key}")
        return None


def validate_invoice(raw: dict[str, str], line_items: list[dict], table_errors: list[str]) -> list[str]:
    errors = list(table_errors)
    for key in ("vendor_name", "invoice_number", "bill_to", "currency"):
        if not raw.get(key):
            errors.append(f"missing_{key}")
    invoice_number = raw.get("invoice_number") or ""
    if invoice_number and not INVOICE_NUMBER.fullmatch(invoice_number):
        errors.append("bad_invoice_number")
    currency = raw.get("currency") or ""
    if currency and not re.fullmatch(r"[A-Z]{3}", currency):
        errors.append("bad_currency")
    invoice_date = _require_date(raw, "invoice_date", errors)
    due_date = _require_date(raw, "due_date", errors)
    if invoice_date and due_date and due_date < invoice_date:
        errors.append("due_before_invoice")
    subtotal = _money_field(raw, "subtotal", errors)
    tax = _money_field(raw, "tax", errors)
    total = _money_field(raw, "total", errors)
    if tax is not None and tax < 0:
        errors.append("negative_tax")
    if total is not None and total < 0:
        errors.append("negative_total")
    amounts = [item["amount"] for item in line_items if item.get("amount") is not None]
    if subtotal is not None and amounts and len(amounts) == len(line_items):
        if sum(amounts, Decimal("0.00")) != subtotal:
            errors.append("line_sum_mismatch")
    if subtotal is not None and tax is not None and total is not None:
        if subtotal + tax != total:
            errors.append("tax_total_mismatch")
    return errors


def _require_date(raw: dict[str, str], key: str, errors: list[str]) -> date | None:
    if not raw.get(key):
        errors.append(f"missing_{key}")
        return None
    parsed = _parse_iso(raw[key])
    if parsed is None:
        errors.append(f"bad_{key}")
    return parsed


def validate_report(raw: dict[str, str], rows: list[dict], table_errors: list[str]) -> list[str]:
    errors = list(table_errors)
    for key in ("title", "organization", "prepared_by"):
        if not raw.get(key):
            errors.append(f"missing_{key}")
    start = _require_date(raw, "period_start", errors)
    end = _require_date(raw, "period_end", errors)
    if start and end and end < start:
        errors.append("period_order")
    return errors


def _invoice_dict(source_file: str, raw: dict[str, str], line_items: list[dict], errors: list[str]) -> dict:
    fields = {
        "vendor_name": raw.get("vendor_name") or None,
        "invoice_number": raw.get("invoice_number") or None,
        "invoice_date": raw.get("invoice_date") or None,
        "due_date": raw.get("due_date") or None,
        "bill_to": raw.get("bill_to") or None,
        "currency": raw.get("currency") or None,
        "subtotal": _money_or_raw(raw.get("subtotal")),
        "tax": _money_or_raw(raw.get("tax")),
        "total": _money_or_raw(raw.get("total")),
    }
    return {
        "source_file": source_file,
        "document_type": "invoice",
        "fields": fields,
        "line_items": [_line_item_dict(item) for item in line_items],
        "validation": {"ok": not errors, "errors": errors},
    }


def _money_or_raw(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return format_money(parse_money(value))
    except ValueError:
        return value


def _line_item_dict(item: dict) -> dict:
    return {
        "description": item.get("description") or "",
        "quantity": format_quantity(item["quantity"]) if item.get("quantity") is not None else None,
        "unit_price": format_money(item["unit_price"]) if item.get("unit_price") is not None else None,
        "amount": format_money(item["amount"]) if item.get("amount") is not None else None,
    }


def _report_value(value: Decimal | None, raw: str) -> str | None:
    if value is None:
        return raw or None
    return format(value, "f")


def _report_dict(source_file: str, raw: dict[str, str], rows: list[dict], errors: list[str]) -> dict:
    return {
        "source_file": source_file,
        "document_type": "report",
        "fields": {
            "title": raw.get("title") or None,
            "organization": raw.get("organization") or None,
            "period_start": raw.get("period_start") or None,
            "period_end": raw.get("period_end") or None,
            "prepared_by": raw.get("prepared_by") or None,
        },
        "rows": [
            {
                "metric": row["metric"],
                "value": _report_value(row["value"], ""),
                "notes": row["notes"],
            }
            for row in rows
        ],
        "validation": {"ok": not errors, "errors": errors},
    }


def extract_pdf(path: Path) -> dict:
    """Extract one PDF into a JSON-ready document with a validation block."""
    try:
        text, tables = read_pdf(path)
    except UnreadablePDF:
        logger.error("unreadable pdf %s", path.name)
        return {
            "source_file": path.name,
            "document_type": None,
            "validation": {"ok": False, "errors": ["unreadable_pdf"]},
        }
    raw = parse_fields(text)
    document_type = (raw.get("document_type") or "").casefold()
    if document_type == "invoice":
        items, table_errors = parse_invoice_table(find_table(tables, INVOICE_HEADER))
        errors = validate_invoice(raw, items, table_errors)
        return _invoice_dict(path.name, raw, items, errors)
    if document_type == "report":
        rows, table_errors = parse_report_table(find_table(tables, REPORT_HEADER))
        errors = validate_report(raw, rows, table_errors)
        return _report_dict(path.name, raw, rows, errors)
    return {
        "source_file": path.name,
        "document_type": document_type or None,
        "fields": {key: value for key, value in raw.items() if key != "document_type"},
        "validation": {"ok": False, "errors": ["unknown_document_type"]},
    }


def extract_directory(input_dir: Path) -> list[dict]:
    paths = sorted(path for path in input_dir.glob("*.pdf") if path.is_file())
    return [extract_pdf(path) for path in paths]


def write_outputs(documents: list[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {"documents": documents}
    (output_dir / "invoices.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_csv(output_dir / "invoices.csv", INVOICE_COLUMNS, list(_invoice_rows(documents)))
    _write_csv(output_dir / "line_items.csv", LINE_COLUMNS, list(_line_rows(documents)))
    _write_csv(output_dir / "report_rows.csv", REPORT_COLUMNS, list(_report_rows(documents)))


def _invoice_rows(documents: list[dict]):
    for document in documents:
        if document.get("document_type") != "invoice" or not document["validation"]["ok"]:
            continue
        fields = document["fields"]
        yield {"source_file": document["source_file"], **fields}


def _line_rows(documents: list[dict]):
    for document in documents:
        if document.get("document_type") != "invoice" or not document["validation"]["ok"]:
            continue
        number = document["fields"]["invoice_number"]
        for item in document["line_items"]:
            yield {
                "source_file": document["source_file"],
                "invoice_number": number,
                **item,
            }


def _report_rows(documents: list[dict]):
    for document in documents:
        if document.get("document_type") != "report" or not document["validation"]["ok"]:
            continue
        fields = document["fields"]
        for row in document["rows"]:
            yield {
                "source_file": document["source_file"],
                "organization": fields["organization"],
                "prepared_by": fields["prepared_by"],
                "period_start": fields["period_start"],
                "period_end": fields["period_end"],
                "metric": row["metric"],
                "value": row["value"],
                "notes": row["notes"],
            }


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def execute(input_dir: Path, output_dir: Path) -> int:
    if not input_dir.is_dir():
        logger.error("input directory does not exist: %s", input_dir)
        return 1
    documents = extract_directory(input_dir)
    if not documents:
        logger.error("no PDFs in %s", input_dir)
        return 1
    write_outputs(documents, output_dir)
    failed = [doc["source_file"] for doc in documents if not doc["validation"]["ok"]]
    if failed:
        logger.error("validation failed for %s", ", ".join(failed))
        return 1
    logger.info("wrote %s documents to %s", len(documents), output_dir)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Extract tables and fields from sample PDFs.")
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    return execute(args.input_dir, args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
