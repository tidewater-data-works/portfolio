"""Declarative quality checks for a synthetic orders file.

Thresholds live in the constants below. ``evaluate`` scores each check as
pass, warn, or fail. Hard row failures are collected for quarantine; a
warning alone does not quarantine the row.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable

ORDER_COLUMNS = (
    "order_id",
    "customer_id",
    "ordered_on",
    "status",
    "amount",
    "quantity",
    "ship_to_city",
)

ALLOWED_STATUS = ("booked", "shipped", "delivered", "cancelled")
AMOUNT_MIN = Decimal("0.01")
AMOUNT_MAX = Decimal("50000")
QUANTITY_MIN = 1
QUANTITY_MAX = 500
FRESH_PASS_DAYS = 7
FRESH_WARN_DAYS = 30
DRIFT_WARN = Decimal("0.15")
DRIFT_FAIL = Decimal("0.50")

QUARANTINE_COLUMNS = ("source_row",) + ORDER_COLUMNS + ("failed_checks",)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str
    failed_rows: tuple[int, ...] = ()
    hard: bool = True


@dataclass(frozen=True)
class QualityReport:
    as_of: str
    gate: str
    row_count: int
    checks: tuple[CheckResult, ...]
    quarantine: tuple[dict[str, str], ...]

    def to_json(self) -> str:
        payload = {
            "as_of": self.as_of,
            "gate": self.gate,
            "row_count": self.row_count,
            "quarantine_count": len(self.quarantine),
            "checks": [
                {"name": check.name, "status": check.status, "detail": check.detail}
                for check in self.checks
            ],
        }
        return json.dumps(payload, indent=2) + "\n"

    def to_markdown(self) -> str:
        lines = [
            "# Data quality report",
            "",
            f"As of: {self.as_of}",
            f"Orders: {self.row_count}",
            f"Quarantined rows: {len(self.quarantine)}",
            f"Gate: {self.gate}",
            "",
            "| Check | Result | Detail |",
            "| --- | --- | --- |",
        ]
        for check in self.checks:
            lines.append(f"| {check.name} | {check.status} | {check.detail} |")
        lines.append("")
        return "\n".join(lines)


def evaluate(
    orders: list[dict[str, str]],
    *,
    customer_ids: set[str] | None,
    previous_count: int | None,
    as_of: date,
    missing_columns: Iterable[str] | None = None,
) -> QualityReport:
    """Score ``orders``. ``customer_ids`` is None when the customer list is unusable."""
    orders = [_with_source_row(row) for row in orders]
    missing = [column for column in missing_columns or ()]
    row_count = len(orders)
    if missing:
        check = CheckResult(
            "required_columns",
            "fail",
            "missing " + ", ".join(missing),
        )
        return QualityReport(
            as_of=as_of.isoformat(),
            gate="fail",
            row_count=row_count,
            checks=(check,),
            quarantine=(),
        )

    checks: list[CheckResult] = [CheckResult("required_columns", "pass", "ok")]
    checks.append(_customer_list(customer_ids))
    checks.extend(_null_checks(orders))
    checks.append(_iso_dates(orders))
    checks.append(_amount_numeric(orders))
    checks.append(_quantity_numeric(orders))
    checks.append(_unique_order_id(orders))
    checks.append(_amount_range(orders))
    checks.append(_quantity_range(orders))
    checks.append(_status_allowed(orders))
    checks.append(_customer_known(orders, customer_ids))
    checks.append(_city_present(orders))
    checks.append(_freshness(orders, as_of))
    checks.append(_row_count(row_count))
    checks.append(_drift(row_count, previous_count))
    gate = _gate(checks)
    return QualityReport(
        as_of=as_of.isoformat(),
        gate=gate,
        row_count=row_count,
        checks=tuple(checks),
        quarantine=tuple(_quarantine(orders, checks)),
    )


def _gate(checks: list[CheckResult]) -> str:
    if any(check.status == "fail" for check in checks):
        return "fail"
    if any(check.status == "warn" for check in checks):
        return "warn"
    return "pass"


def _customer_list(customer_ids: set[str] | None) -> CheckResult:
    if customer_ids is None:
        return CheckResult("customer_list", "fail", "customer list unavailable")
    if not customer_ids:
        return CheckResult("customer_list", "fail", "0 customers")
    noun = "customer" if len(customer_ids) == 1 else "customers"
    return CheckResult("customer_list", "pass", f"{len(customer_ids)} {noun}")


def _null_checks(orders: list[dict[str, str]]) -> list[CheckResult]:
    specs = (
        ("order_id_not_null", "order_id"),
        ("customer_id_not_null", "customer_id"),
        ("ordered_on_not_null", "ordered_on"),
        ("status_not_null", "status"),
        ("amount_not_null", "amount"),
        ("quantity_not_null", "quantity"),
    )
    results = []
    for name, column in specs:
        bad = tuple(row["source_row"] for row in orders if not row[column])
        results.append(_counted(name, bad, "blank"))
    return results


def _iso_dates(orders: list[dict[str, str]]) -> CheckResult:
    bad = tuple(
        row["source_row"]
        for row in orders
        if row["ordered_on"] and _parse_iso(row["ordered_on"]) is None
    )
    return _counted("ordered_on_iso", bad, "not an ISO date")


def _amount_numeric(orders: list[dict[str, str]]) -> CheckResult:
    bad = tuple(
        row["source_row"]
        for row in orders
        if row["amount"] and _parse_amount(row["amount"]) is None
    )
    return _counted("amount_numeric", bad, "not numeric")


def _quantity_numeric(orders: list[dict[str, str]]) -> CheckResult:
    bad = tuple(
        row["source_row"]
        for row in orders
        if row["quantity"] and _parse_quantity(row["quantity"]) is None
    )
    return _counted("quantity_numeric", bad, "not an integer")


def _unique_order_id(orders: list[dict[str, str]]) -> CheckResult:
    grouped: dict[str, list[int]] = {}
    for row in orders:
        if row["order_id"]:
            grouped.setdefault(row["order_id"], []).append(row["source_row"])
    duplicates = {order_id: rows for order_id, rows in grouped.items() if len(rows) > 1}
    if not duplicates:
        return CheckResult("unique_order_id", "pass", "ok")
    failed: list[int] = []
    parts = []
    for order_id in sorted(duplicates):
        rows = duplicates[order_id]
        failed.extend(rows)
        parts.append(f"{order_id} on {len(rows)} rows")
    return CheckResult(
        "unique_order_id",
        "fail",
        "repeated " + "; ".join(parts),
        tuple(sorted(failed)),
    )


def _amount_range(orders: list[dict[str, str]]) -> CheckResult:
    bad = []
    for row in orders:
        # A blank is an amount_not_null failure, not a range failure.
        amount = _parse_amount(row["amount"]) if row["amount"] else None
        if amount is None:
            continue
        if amount < AMOUNT_MIN or amount > AMOUNT_MAX:
            bad.append(row["source_row"])
    return _counted(
        "amount_range",
        tuple(bad),
        f"outside {AMOUNT_MIN}-{AMOUNT_MAX}",
    )


def _quantity_range(orders: list[dict[str, str]]) -> CheckResult:
    bad = []
    for row in orders:
        quantity = _parse_quantity(row["quantity"]) if row["quantity"] else None
        if quantity is None:
            continue
        if quantity < QUANTITY_MIN or quantity > QUANTITY_MAX:
            bad.append(row["source_row"])
    return _counted(
        "quantity_range",
        tuple(bad),
        f"outside {QUANTITY_MIN}-{QUANTITY_MAX}",
    )


def _status_allowed(orders: list[dict[str, str]]) -> CheckResult:
    allowed = set(ALLOWED_STATUS)
    bad = tuple(
        row["source_row"]
        for row in orders
        if row["status"] and row["status"] not in allowed
    )
    listed = ", ".join(ALLOWED_STATUS)
    return _counted("status_allowed", bad, f"outside {listed}")


def _customer_known(
    orders: list[dict[str, str]],
    customer_ids: set[str] | None,
) -> CheckResult:
    if customer_ids is None:
        return CheckResult("customer_id_known", "fail", "customer list unavailable")
    bad = tuple(
        row["source_row"]
        for row in orders
        if row["customer_id"] and row["customer_id"] not in customer_ids
    )
    return _counted("customer_id_known", bad, "with an unknown customer_id")


def _city_present(orders: list[dict[str, str]]) -> CheckResult:
    bad = tuple(row["source_row"] for row in orders if not row["ship_to_city"])
    if not bad:
        return CheckResult("ship_to_city_present", "pass", "ok")
    return CheckResult(
        "ship_to_city_present",
        "warn",
        _count_phrase(len(bad), "blank"),
        bad,
        hard=False,
    )


def _freshness(orders: list[dict[str, str]], as_of: date) -> CheckResult:
    parsed = [
        _parse_iso(row["ordered_on"])
        for row in orders
        if _parse_iso(row["ordered_on"]) is not None
    ]
    if not parsed:
        return CheckResult("freshness", "fail", "no parseable ordered_on")
    newest = max(parsed)
    if newest > as_of:
        return CheckResult(
            "freshness",
            "fail",
            f"newest {newest.isoformat()} is after {as_of.isoformat()}",
        )
    age = (as_of - newest).days
    detail = f"newest {newest.isoformat()} is {age} days old"
    if age <= FRESH_PASS_DAYS:
        status = "pass"
    elif age <= FRESH_WARN_DAYS:
        status = "warn"
    else:
        status = "fail"
    return CheckResult("freshness", status, detail)


def _row_count(row_count: int) -> CheckResult:
    detail = f"{row_count} rows" if row_count != 1 else "1 row"
    if row_count == 0:
        return CheckResult("row_count", "fail", detail)
    return CheckResult("row_count", "pass", detail)


def _drift(row_count: int, previous_count: int | None) -> CheckResult:
    if previous_count is None:
        return CheckResult("row_count_drift", "fail", "previous row count is missing")
    if previous_count <= 0:
        return CheckResult("row_count_drift", "fail", "previous row count is 0")
    ratio = abs(Decimal(row_count) - Decimal(previous_count)) / Decimal(previous_count)
    percent = (ratio * Decimal(100)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    detail = f"{row_count} vs {previous_count} ({percent}%)"
    if ratio > DRIFT_FAIL:
        status = "fail"
    elif ratio > DRIFT_WARN:
        status = "warn"
    else:
        status = "pass"
    return CheckResult("row_count_drift", status, detail)


def _quarantine(
    orders: list[dict[str, str]],
    checks: list[CheckResult],
) -> list[dict[str, str]]:
    failed: dict[int, list[str]] = {}
    for check in checks:
        if not check.hard:
            continue
        for source_row in check.failed_rows:
            failed.setdefault(source_row, []).append(check.name)
    rows_by_source = {int(row["source_row"]): row for row in orders}
    quarantined = []
    for source_row in sorted(failed):
        row = rows_by_source[source_row]
        record = {"source_row": str(source_row)}
        for column in ORDER_COLUMNS:
            record[column] = row[column]
        record["failed_checks"] = "|".join(failed[source_row])
        quarantined.append(record)
    return quarantined


def _counted(name: str, bad: tuple[int, ...], phrase: str) -> CheckResult:
    if not bad:
        return CheckResult(name, "pass", "ok")
    return CheckResult(name, "fail", _count_phrase(len(bad), phrase), bad)


def _count_phrase(count: int, phrase: str) -> str:
    noun = "row" if count == 1 else "rows"
    return f"{count} {noun} {phrase}"


def _parse_iso(value: str) -> date | None:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
    if parsed.isoformat() != value:
        return None
    return parsed


def _with_source_row(row: dict[str, str]) -> dict[str, str]:
    normalized = dict(row)
    normalized["source_row"] = int(row["source_row"])
    return normalized


def _parse_amount(value: str) -> Decimal | None:
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return None
    if not amount.is_finite():
        return None
    return amount


def _parse_quantity(value: str) -> int | None:
    if value.startswith(("+", "-")):
        digits = value[1:]
    else:
        digits = value
    if not digits.isdigit():
        return None
    return int(value)
