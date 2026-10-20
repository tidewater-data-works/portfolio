"""Offline tests for vendor cleaning and fuzzy duplicate merge."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from clean import (
    clean_category,
    clean_contract,
    clean_date,
    clean_phone,
    clean_records,
    clean_region,
    clean_street,
    clean_vendor_name,
    clean_weight,
    cluster_indices,
    load_categories,
    main,
    match_key,
    name_score,
    parse_date,
    read_table,
    run,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
CATEGORIES = load_categories(ROOT / "category_map.csv")
PORTFOLIO = ROOT.parent / "README.md"


def test_sample_csv_matches_committed_output(tmp_path):
    code = run(SAMPLES / "vendors_raw.csv", tmp_path, ROOT / "category_map.csv")
    assert code == 0
    for name in ("vendors_clean.csv", "change_log.csv", "summary.json", "summary.md"):
        assert (tmp_path / name).read_text(encoding="utf-8") == (SAMPLES / name).read_text(
            encoding="utf-8"
        )


def test_sample_outcomes():
    rows, changes, summary = clean_records(read_table(SAMPLES / "vendors_raw.csv"), CATEGORIES)
    assert summary["json"]["input_rows"] == 7
    assert summary["json"]["output_rows"] == 6
    assert summary["json"]["merged_rows"] == 1
    by_name = {row["vendor_name"]: row for row in rows}
    assert list(by_name) == [
        "Blue Heron Printworks",
        "Cedar Wharf Logistics",
        "Harbor & Pine Supply Co.",
        "Lantern Street Clinic",
        "Northbank Analytics LLC",
        "Osprey Municipal Library",
    ]
    harbor = by_name["Harbor & Pine Supply Co."]
    assert harbor["vendor_id"] == "V-1003"
    assert harbor["source_row"] == "1"
    assert harbor["phone"] == "(757) 555-0142"
    assert harbor["street"] == "18 Wharf Lane"
    assert harbor["city"] == "Port City"
    assert harbor["region"] == "VA"
    assert harbor["category"] == "Packaging"
    assert harbor["since"] == "2024-03-12"
    assert harbor["contract_amount"] == "1240.50"
    assert harbor["contract_currency"] == "USD"
    assert harbor["weight_kg"] == "5.443"

    lantern = by_name["Lantern Street Clinic"]
    assert lantern["phone"] == "(757) 555-0188"
    assert lantern["street"] == "4 Lantern Street"
    assert lantern["region"] == "NC"
    assert lantern["category"] == "Healthcare"
    assert lantern["since"] == "2025-03-12"
    assert lantern["contract_amount"] == "800.00"
    assert lantern["contract_currency"] == "USD"
    assert lantern["weight_kg"] == "2.500"

    cedar = by_name["Cedar Wharf Logistics"]
    assert cedar["phone"] == "(757) 555-0177"
    assert cedar["street"] == "400 Harbor Lane, Suite 2"
    assert cedar["contract_amount"] == "0.00"
    assert cedar["contract_currency"] == "EUR"
    assert cedar["since"] == "2025-01-20"
    assert cedar["weight_kg"] == "0.500"
    assert cedar["category"] == "Freight"

    heron = by_name["Blue Heron Printworks"]
    assert heron["since"] == "2026-02-02"
    assert heron["weight_kg"] == "1.361"
    assert heron["city"] == "Osprey"
    assert heron["category"] == "Print"
    assert heron["contract_amount"] == "515.16"

    osprey = by_name["Osprey Municipal Library"]
    assert osprey["phone"] == ""
    assert osprey["category"] == "Public"
    assert osprey["weight_kg"] == ""
    assert osprey["contract_amount"] == "0.00"
    assert osprey["contract_currency"] == "USD"
    assert osprey["street"] == "1 Reading Way"
    assert osprey["region"] == "VA"

    northbank = by_name["Northbank Analytics LLC"]
    assert northbank["category"] == ""
    assert northbank["street"] == "12 Northbank Road"
    assert northbank["city"] == "Northbank"
    assert northbank["contract_amount"] == "2250.00"
    assert northbank["weight_kg"] == "1.000"
    assert northbank["since"] == "2026-02-02"

    assert not any(change.source_row == 1 and change.reason == "name_case" for change in changes)
    assert any(change.source_row == 1 and change.reason == "whitespace" for change in changes)
    assert any(
        change.source_row == 2 and change.reason == "merged_duplicate score=100 into_row=1"
        for change in changes
    )
    assert any(change.source_row == 2 and change.reason == "name_case" for change in changes)
    assert any(change.source_row == 6 and change.reason == "phone_unparsed" for change in changes)
    assert any(change.source_row == 6 and change.reason == "weight_missing" for change in changes)
    assert any(change.source_row == 7 and change.reason == "category_unmapped" for change in changes)
    assert any(change.source_row == 3 and change.reason == "currency_assumed_usd" for change in changes)
    assert any(change.source_row == 4 and change.reason == "currency_parsed" for change in changes)
    logged_pairs = {(change.source_row, change.field, change.reason) for change in changes}
    assert (2, "since", "date_normalized") not in logged_pairs
    assert (2, "contract_value", "currency_parsed") not in logged_pairs


def test_only_the_harbor_rows_score_above_the_merge_threshold():
    rows = read_table(SAMPLES / "vendors_raw.csv")
    keys = [match_key(clean_vendor_name(row["vendor_name"])[0]) for row in rows]
    close = []
    for left in range(len(keys)):
        for right in range(left + 1, len(keys)):
            if name_score(keys[left], keys[right]) >= 90:
                close.append((left + 1, right + 1))
    assert close == [(1, 2)]


def test_xlsx_input_matches_csv_input(tmp_path):
    csv_dir = tmp_path / "csv"
    xlsx_dir = tmp_path / "xlsx"
    csv_dir.mkdir()
    xlsx_dir.mkdir()
    run(SAMPLES / "vendors_raw.csv", csv_dir, ROOT / "category_map.csv")
    run(SAMPLES / "vendors_raw.xlsx", xlsx_dir, ROOT / "category_map.csv")
    for name in ("vendors_clean.csv", "change_log.csv", "summary.json", "summary.md"):
        assert (csv_dir / name).read_text(encoding="utf-8") == (xlsx_dir / name).read_text(
            encoding="utf-8"
        )


def test_filled_from_duplicate_keeps_the_more_complete_row():
    rows = [
        _raw(
            source_row="1",
            vendor_name="Acme Supply Co.",
            phone="",
            street="1 main st.",
            city="Port City",
            region="VA",
            category="packaging",
            since="2024-01-01",
            contract_value="10",
            weight="1 kg",
        ),
        _raw(
            source_row="2",
            vendor_name="ACME SUPPLY",
            phone="7575550142",
            street="",
            city="",
            region="",
            category="",
            since="",
            contract_value="",
            weight="",
        ),
    ]
    clean_rows, changes, summary = clean_records(rows, CATEGORIES)
    assert summary["json"]["output_rows"] == 1
    assert clean_rows[0]["vendor_name"] == "Acme Supply Co."
    assert clean_rows[0]["phone"] == "(757) 555-0142"
    assert clean_rows[0]["source_row"] == "1"
    assert any(
        change.source_row == 1 and change.reason == "filled_from_duplicate from_row=2"
        for change in changes
    )


def test_equal_completeness_keeps_the_earlier_row():
    rows = [
        _raw(source_row="1", vendor_name="Acme Supply Co."),
        _raw(source_row="2", vendor_name="ACME SUPPLY COMPANY"),
    ]
    clean_rows, _, summary = clean_records(rows, CATEGORIES)
    assert summary["json"]["merged_rows"] == 1
    assert clean_rows[0]["source_row"] == "1"
    assert clean_rows[0]["vendor_name"] == "Acme Supply Co."


def test_dissimilar_names_stay_separate():
    rows = [
        _raw(source_row="1", vendor_name="Harbor & Pine Supply Co."),
        _raw(source_row="2", vendor_name="Cedar Wharf Logistics"),
    ]
    clean_rows, changes, _ = clean_records(rows, CATEGORIES)
    assert len(clean_rows) == 2
    assert not any(change.reason.startswith("merged_duplicate") for change in changes)


def test_mixed_case_name_is_not_retitled():
    cleaned, changes = clean_vendor_name("Harbor & Pine Supply Co.")
    assert cleaned == "Harbor & Pine Supply Co."
    assert changes == []
    cleaned, changes = clean_vendor_name("Harbor  &  Pine Supply Co.")
    assert cleaned == "Harbor & Pine Supply Co."
    assert [change[3] for change in changes] == ["whitespace"]


def test_uniform_case_name_is_titled_and_small_words_stay_lower():
    cleaned, changes = clean_vendor_name("HARBOR AND PINE SUPPLY")
    assert cleaned == "Harbor and Pine Supply"
    assert changes[-1][3] == "name_case"


def test_dates_phones_weights_regions_and_categories():
    assert parse_date("03/12/2024") == date(2024, 3, 12)
    assert parse_date("2025/01/20") == date(2025, 1, 20)
    assert parse_date("12-Mar-2025") == date(2025, 3, 12)
    assert parse_date("Feb 2, 2026") == date(2026, 2, 2)
    assert parse_date("2024-02-31") is None
    assert clean_date("03/12/24") == ("", "date_unparsed")
    assert clean_date("2024-03-12") == ("2024-03-12", None)
    assert clean_phone("(757) 555-0142") == ("(757) 555-0142", None)
    assert clean_phone("757.555.0142") == ("(757) 555-0142", "phone_normalized")
    assert clean_phone("+1 757 555 0177") == ("(757) 555-0177", "phone_normalized")
    assert clean_phone("call the desk") == ("", "phone_unparsed")
    assert clean_weight("12 lb") == ("5.443", "weight_normalized")
    assert clean_weight("3 lb") == ("1.361", "weight_normalized")
    assert clean_weight("500 g") == ("0.500", "weight_normalized")
    assert clean_weight("2.5kg") == ("2.500", "weight_normalized")
    assert clean_weight("n/a") == ("", "weight_missing")
    assert clean_region("Virginia") == ("VA", "region_normalized")
    assert clean_region("North Carolina") == ("NC", "region_normalized")
    assert clean_region("TX") == ("TX", None)
    assert clean_region("texas") == ("texas", None)
    assert clean_category("dashboards", CATEGORIES) == ("", "category_unmapped")
    assert clean_category("pkg", CATEGORIES) == ("Packaging", "category_mapped")
    assert clean_street("400 harbor lane, suite 2") == (
        "400 Harbor Lane, Suite 2",
        "street_normalized",
    )
    assert clean_contract("$1,240.50") == ("1240.50", "USD", "currency_parsed")
    assert clean_contract("800") == ("800.00", "USD", "currency_assumed_usd")
    assert clean_contract("€0") == ("0.00", "EUR", "currency_parsed")
    assert clean_contract("1240.50 USD") == ("1240.50", "USD", None)


def test_transitive_cluster_merges_a_chain():
    def score_of(left: str, right: str) -> float:
        pair = tuple(sorted((left, right)))
        return {("a", "b"): 95, ("b", "c"): 92, ("a", "c"): 10}[pair]

    groups = cluster_indices(["a", "b", "c"], score_of)
    assert [sorted(group) for group in groups] == [[0, 1, 2]]


def test_missing_input_exits_1(tmp_path):
    code = main(["--input", str(tmp_path / "missing.csv"), "--output-dir", str(tmp_path)])
    assert code == 1


def test_readme_has_the_house_sections():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for heading in ("## Problem", "## Approach", "## Run", "## Sample output", "## Tests"):
        assert heading in text


def test_portfolio_readme_links_this_demo():
    text = PORTFOLIO.read_text(encoding="utf-8")
    assert "](data-cleaning-toolkit/)" in text
    assert "data-cleaning-toolkit/README.md" in text


def _raw(**overrides) -> dict[str, str]:
    row = {
        "source_row": "1",
        "vendor_name": "Example Co.",
        "phone": "",
        "street": "",
        "city": "",
        "region": "",
        "category": "",
        "since": "",
        "contract_value": "",
        "weight": "",
    }
    row.update(overrides)
    return row
