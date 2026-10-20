"""Read orders and a customer list, run the quality checks, and write the reports."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from checks import ORDER_COLUMNS, QUARANTINE_COLUMNS, QualityReport, evaluate


def run(
    *,
    orders_path: Path,
    customers_path: Path,
    previous_path: Path,
    as_of: date,
    report_md: Path,
    report_json: Path,
    quarantine_path: Path,
) -> int:
    """Write the three report files. Return 1 when the gate is fail."""
    orders, missing = read_orders(orders_path)
    customer_ids = read_customers(customers_path)
    previous_count = read_previous_count(previous_path)
    report = evaluate(
        orders,
        customer_ids=customer_ids,
        previous_count=previous_count,
        as_of=as_of,
        missing_columns=missing,
    )
    write_report(report, report_md, report_json, quarantine_path)
    return 1 if report.gate == "fail" else 0


def read_orders(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    frame.columns = [str(column).strip() for column in frame.columns]
    missing = [column for column in ORDER_COLUMNS if column not in frame.columns]
    records: list[dict[str, str]] = []
    for offset, (_, row) in enumerate(frame.iterrows(), start=1):
        record = {
            column: str(row[column]).strip() if column in frame.columns else ""
            for column in ORDER_COLUMNS
        }
        record["source_row"] = str(offset)
        records.append(record)
    return records, missing


def read_customers(path: Path) -> set[str] | None:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    frame.columns = [str(column).strip() for column in frame.columns]
    if "customer_id" not in frame.columns:
        return None
    return {str(value).strip() for value in frame["customer_id"] if str(value).strip()}


def read_previous_count(path: Path) -> int | None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "row_count" not in payload:
        return None
    try:
        return int(payload["row_count"])
    except (TypeError, ValueError):
        return None


def write_report(
    report: QualityReport,
    report_md: Path,
    report_json: Path,
    quarantine_path: Path,
) -> None:
    for path in (report_md, report_json, quarantine_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(report.to_markdown(), encoding="utf-8")
    report_json.write_text(report.to_json(), encoding="utf-8")
    with quarantine_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(QUARANTINE_COLUMNS),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(report.quarantine)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check an orders file and write a pass/warn/fail quality report."
    )
    parser.add_argument("--orders", type=Path, default=Path("samples/orders.csv"))
    parser.add_argument("--customers", type=Path, default=Path("samples/customers.csv"))
    parser.add_argument("--previous", type=Path, default=Path("samples/previous_count.json"))
    parser.add_argument("--as-of", default="2026-03-20")
    parser.add_argument("--report-md", type=Path, default=Path("samples/quality_report.md"))
    parser.add_argument("--report-json", type=Path, default=Path("samples/quality_report.json"))
    parser.add_argument("--quarantine", type=Path, default=Path("samples/quarantine.csv"))
    args = parser.parse_args(argv)
    try:
        as_of = date.fromisoformat(args.as_of)
    except ValueError:
        print("as-of must be YYYY-MM-DD", file=sys.stderr)
        return 1
    try:
        code = run(
            orders_path=args.orders,
            customers_path=args.customers,
            previous_path=args.previous,
            as_of=as_of,
            report_md=args.report_md,
            report_json=args.report_json,
            quarantine_path=args.quarantine,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"gate: {json.loads(args.report_json.read_text(encoding='utf-8'))['gate']}")
    return code


if __name__ == "__main__":
    sys.exit(main())
