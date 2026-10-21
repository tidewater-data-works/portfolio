"""Offline checks for the orders quality monitor."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from checks import evaluate
from monitor import main, run

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
PORTFOLIO = ROOT.parent / "README.md"
AS_OF = date(2026, 3, 20)
CUSTOMERS = {"C-100", "C-101", "C-102", "C-103", "C-104"}


def _order(**overrides) -> dict[str, str]:
    row = {
        "source_row": "1",
        "order_id": "ORD-1",
        "customer_id": "C-100",
        "ordered_on": "2026-03-18",
        "status": "shipped",
        "amount": "10.00",
        "quantity": "1",
        "ship_to_city": "Port City",
    }
    row.update(overrides)
    return row


def _report(rows, *, customers=None, previous=None, as_of=AS_OF, missing=None):
    if customers is None:
        customers = {"C-100"}
    if previous is None:
        previous = len(rows)
    return evaluate(
        rows,
        customer_ids=customers,
        previous_count=previous,
        as_of=as_of,
        missing_columns=missing,
    )


def _status(report, name: str) -> str:
    return next(check.status for check in report.checks if check.name == name)


def _detail(report, name: str) -> str:
    return next(check.detail for check in report.checks if check.name == name)


def test_sample_gate_fails_and_matches_committed_reports(tmp_path):
    code = run(
        orders_path=SAMPLES / "orders.csv",
        customers_path=SAMPLES / "customers.csv",
        previous_path=SAMPLES / "previous_count.json",
        as_of=AS_OF,
        report_md=tmp_path / "quality_report.md",
        report_json=tmp_path / "quality_report.json",
        quarantine_path=tmp_path / "quarantine.csv",
    )
    assert code == 1
    for name in ("quality_report.md", "quality_report.json", "quarantine.csv"):
        assert (tmp_path / name).read_text(encoding="utf-8") == (SAMPLES / name).read_text(
            encoding="utf-8"
        )


def test_sample_check_results_and_quarantine():
    report = _report(
        _read_sample_orders(),
        customers=CUSTOMERS,
        previous=20,
    )
    assert report.gate == "fail"
    assert report.row_count == 16
    assert _status(report, "freshness") == "pass"
    assert "2026-03-18" in _detail(report, "freshness")
    assert _status(report, "row_count_drift") == "warn"
    assert _detail(report, "row_count_drift") == "16 vs 20 (20.0%)"
    assert _status(report, "ship_to_city_present") == "warn"
    assert _status(report, "unique_order_id") == "fail"
    quarantined = [int(row["source_row"]) for row in report.quarantine]
    assert quarantined == [2, 8, 9, 10, 11, 12, 13, 14, 16]
    by_source = {int(row["source_row"]): row["failed_checks"] for row in report.quarantine}
    assert by_source[2] == "unique_order_id"
    assert by_source[8] == "unique_order_id"
    assert by_source[9] == "amount_not_null"
    assert by_source[10] == "amount_range"
    assert by_source[11] == "customer_id_known"
    assert by_source[12] == "status_allowed"
    assert by_source[13] == "amount_range"
    assert by_source[14] == "quantity_range"
    assert by_source[16] == "ordered_on_iso"
    assert _status(report, "amount_range") == "fail"
    assert _status(report, "amount_numeric") == "pass"


def test_clean_batch_passes():
    report = _report([_order()])
    assert report.gate == "pass"
    assert report.quarantine == ()


def test_warning_only_exits_zero(tmp_path):
    orders = tmp_path / "orders.csv"
    customers = tmp_path / "customers.csv"
    previous = tmp_path / "previous.json"
    _write_orders(orders, [_order(ship_to_city="")])
    _write_customers(customers, ["C-100"])
    previous.write_text('{"row_count": 1}\n', encoding="utf-8")
    code = run(
        orders_path=orders,
        customers_path=customers,
        previous_path=previous,
        as_of=AS_OF,
        report_md=tmp_path / "report.md",
        report_json=tmp_path / "report.json",
        quarantine_path=tmp_path / "quarantine.csv",
    )
    assert code == 0
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert payload["gate"] == "warn"
    with (tmp_path / "quarantine.csv").open(encoding="utf-8", newline="") as handle:
        assert list(csv.DictReader(handle)) == []


def test_blank_amount_is_not_a_range_failure():
    report = _report([_order(amount="")])
    assert _status(report, "amount_not_null") == "fail"
    assert _status(report, "amount_numeric") == "pass"
    assert _status(report, "amount_range") == "pass"
    assert report.quarantine[0]["failed_checks"] == "amount_not_null"


def test_unparseable_amount_is_numeric_not_range():
    report = _report([_order(amount="n/a")])
    assert _status(report, "amount_numeric") == "fail"
    assert _status(report, "amount_range") == "pass"
    assert report.quarantine[0]["failed_checks"] == "amount_numeric"


def test_duplicate_order_ids_quarantine_every_copy():
    rows = [
        _order(source_row="1", order_id="ORD-9"),
        _order(source_row="2", order_id="ORD-9", ordered_on="2026-03-17"),
    ]
    report = _report(rows, previous=2)
    assert [row["source_row"] for row in report.quarantine] == ["1", "2"]
    assert all(row["failed_checks"] == "unique_order_id" for row in report.quarantine)


def test_missing_required_column_short_circuits(tmp_path):
    orders = tmp_path / "orders.csv"
    orders.write_text("order_id,customer_id\nORD-1,C-100\n", encoding="utf-8")
    customers = tmp_path / "customers.csv"
    _write_customers(customers, ["C-100"])
    previous = tmp_path / "previous.json"
    previous.write_text('{"row_count": 1}\n', encoding="utf-8")
    code = run(
        orders_path=orders,
        customers_path=customers,
        previous_path=previous,
        as_of=AS_OF,
        report_md=tmp_path / "report.md",
        report_json=tmp_path / "report.json",
        quarantine_path=tmp_path / "quarantine.csv",
    )
    assert code == 1
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert [check["name"] for check in payload["checks"]] == ["required_columns"]
    assert payload["checks"][0]["status"] == "fail"
    with (tmp_path / "quarantine.csv").open(encoding="utf-8", newline="") as handle:
        assert list(csv.DictReader(handle)) == []


def test_freshness_boundaries():
    boundaries = (
        ("2026-03-13", "pass"),
        ("2026-03-12", "warn"),
        ("2026-02-18", "warn"),
        ("2026-02-17", "fail"),
        ("2026-03-21", "fail"),
        ("not-a-date", "fail"),
    )
    for ordered_on, expected in boundaries:
        report = _report([_order(ordered_on=ordered_on)])
        assert _status(report, "freshness") == expected, ordered_on


def test_row_count_drift_boundaries():
    rows = [_order(source_row=str(index), order_id=f"ORD-{index}") for index in range(1, 101)]
    assert _status(_report(rows[:85], previous=100), "row_count_drift") == "pass"
    assert _status(_report(rows[:84], previous=100), "row_count_drift") == "warn"
    assert _status(_report(rows[:50], previous=100), "row_count_drift") == "warn"
    assert _status(_report(rows[:49], previous=100), "row_count_drift") == "fail"
    assert _status(_report([], previous=100), "row_count") == "fail"


def test_future_date_detail_names_the_day():
    report = _report([_order(ordered_on="2026-03-21")])
    assert _detail(report, "freshness") == "newest 2026-03-21 is after 2026-03-20"


def test_reports_are_written_when_the_gate_fails(tmp_path):
    orders = tmp_path / "orders.csv"
    _write_orders(orders, [_order(amount="-1")])
    customers = tmp_path / "customers.csv"
    _write_customers(customers, ["C-100"])
    previous = tmp_path / "previous.json"
    previous.write_text('{"row_count": 1}\n', encoding="utf-8")
    code = run(
        orders_path=orders,
        customers_path=customers,
        previous_path=previous,
        as_of=AS_OF,
        report_md=tmp_path / "nested" / "report.md",
        report_json=tmp_path / "nested" / "report.json",
        quarantine_path=tmp_path / "nested" / "quarantine.csv",
    )
    assert code == 1
    assert (tmp_path / "nested" / "report.md").exists()
    assert (tmp_path / "nested" / "quarantine.csv").exists()


def test_missing_orders_file_exits_1(tmp_path):
    code = main(
        [
            "--orders",
            str(tmp_path / "missing.csv"),
            "--customers",
            str(SAMPLES / "customers.csv"),
            "--previous",
            str(SAMPLES / "previous_count.json"),
            "--report-md",
            str(tmp_path / "report.md"),
            "--report-json",
            str(tmp_path / "report.json"),
            "--quarantine",
            str(tmp_path / "quarantine.csv"),
        ]
    )
    assert code == 1
    assert not (tmp_path / "report.md").exists()


def test_readme_has_the_house_sections():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for heading in ("## Problem", "## Approach", "## Run", "## Sample output", "## Tests"):
        assert heading in text


def test_portfolio_readme_links_this_demo():
    text = PORTFOLIO.read_text(encoding="utf-8")
    assert "](data-quality-monitoring/)" in text
    assert "data-quality-monitoring/README.md" in text
    assert "independent data engineering practice" in text
    assert "weekdays at 14:30 UTC" in text
    assert "data studio" not in text.lower()
    assert "Weekday afternoons" not in text
    assert "Nothing here asks for a personal name" not in text


def test_gitignore_covers_local_cruft():
    text = (ROOT.parent / ".gitignore").read_text(encoding="utf-8")
    for entry in (".env", ".DS_Store", ".idea/", ".vscode/", "*.log", "*.tmp", "venv/"):
        assert entry in text.splitlines()


def _read_sample_orders():
    from monitor import read_orders

    rows, missing = read_orders(SAMPLES / "orders.csv")
    assert missing == []
    return rows


def _write_orders(path: Path, rows: list[dict[str, str]]) -> None:
    columns = [
        "order_id",
        "customer_id",
        "ordered_on",
        "status",
        "amount",
        "quantity",
        "ship_to_city",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_customers(path: Path, customer_ids: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["customer_id", "customer_name"],
            lineterminator="\n",
        )
        writer.writeheader()
        for customer_id in customer_ids:
            writer.writerow({"customer_id": customer_id, "customer_name": "Example Co."})
