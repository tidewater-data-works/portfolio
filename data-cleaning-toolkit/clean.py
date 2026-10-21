"""Normalize a messy vendor export and merge fuzzy duplicate rows.

Dates, phones, streets, cities, regions, categories, currency, and weights
are cleaned in a fixed order. A change is logged only when the stored value
changes. Rows whose match keys score 90 or above on a token sort are merged.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from openpyxl import Workbook, load_workbook
from rapidfuzz import fuzz

RAW_COLUMNS = (
    "vendor_name",
    "phone",
    "street",
    "city",
    "region",
    "category",
    "since",
    "contract_value",
    "weight",
)
CLEAN_COLUMNS = (
    "vendor_id",
    "vendor_name",
    "phone",
    "street",
    "city",
    "region",
    "category",
    "since",
    "contract_amount",
    "contract_currency",
    "weight_kg",
    "source_row",
)
CHANGE_COLUMNS = ("source_row", "vendor_name", "field", "before", "after", "reason")
MERGE_THRESHOLD = 90
POUND_TO_KG = Decimal("0.45359237")
SMALL_WORDS = {"and", "of", "the"}
SUFFIXES = {"co": "Co.", "company": "Co.", "llc": "LLC", "inc": "Inc.", "ltd": "Ltd."}
KEY_DROPPED = {"co", "company", "llc", "inc", "ltd"}
STREET_WORDS = {
    "st": "Street",
    "street": "Street",
    "ln": "Lane",
    "lane": "Lane",
    "rd": "Road",
    "road": "Road",
    "ave": "Avenue",
    "avenue": "Avenue",
    "ste": "Suite",
    "suite": "Suite",
    "ct": "Court",
    "court": "Court",
    "way": "Way",
}
REGIONS = {
    "va": "VA",
    "virginia": "VA",
    "nc": "NC",
    "north carolina": "NC",
}
MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
WEIGHT_MISSING = {"n/a", "na", "none", "-", "--"}
WEIGHT_RE = re.compile(
    r"^(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>lb|lbs|pound|pounds|g|kg|kilogram|kilograms)$",
    re.IGNORECASE,
)
CODE_RE = re.compile(r"\b(USD|EUR|GBP)\b", re.IGNORECASE)
FILL_FIELDS = (
    "phone",
    "street",
    "city",
    "region",
    "category",
    "since",
    "weight_kg",
)


class Change:
    def __init__(
        self,
        source_row: int,
        vendor_name: str,
        field: str,
        before: str,
        after: str,
        reason: str,
    ) -> None:
        self.source_row = source_row
        self.vendor_name = vendor_name
        self.field = field
        self.before = before
        self.after = after
        self.reason = reason

    def as_dict(self) -> dict[str, str]:
        return {
            "source_row": str(self.source_row),
            "vendor_name": self.vendor_name,
            "field": self.field,
            "before": self.before,
            "after": self.after,
            "reason": self.reason,
        }


def clean_records(
    rows: list[dict[str, str]],
    categories: dict[str, str],
    *,
    score_of=None,
) -> tuple[list[dict[str, str]], list[Change], dict]:
    """Return clean rows, the change log, and a small summary mapping."""
    score_of = score_of or name_score
    prepared: list[dict] = []
    changes: list[tuple[int, int, Change]] = []

    def log(
        source_row: int,
        vendor_name: str,
        field: str,
        before: str,
        after: str,
        reason: str,
        *,
        force: bool = False,
    ) -> None:
        if not force and before == after:
            return
        changes.append(
            (
                source_row,
                len(changes),
                Change(source_row, vendor_name, field, before, after, reason),
            )
        )

    for row in rows:
        source_row = int(row["source_row"])
        raw_name = row["vendor_name"]
        vendor_name, name_changes = clean_vendor_name(raw_name)
        for field, before, after, reason in name_changes:
            log(source_row, raw_name, field, before, after, reason)
        phone, phone_reason = clean_phone(row["phone"])
        if phone_reason:
            log(source_row, raw_name, "phone", row["phone"], phone, phone_reason)
        street, street_reason = clean_street(row["street"])
        if street_reason:
            log(source_row, raw_name, "street", row["street"], street, street_reason)
        city, city_reason = clean_city(row["city"])
        if city_reason:
            log(source_row, raw_name, "city", row["city"], city, city_reason)
        region, region_reason = clean_region(row["region"])
        if region_reason:
            log(source_row, raw_name, "region", row["region"], region, region_reason)
        category, category_reason = clean_category(row["category"], categories)
        if category_reason:
            log(source_row, raw_name, "category", row["category"], category, category_reason)
        since, date_reason = clean_date(row["since"])
        if date_reason:
            log(source_row, raw_name, "since", row["since"], since, date_reason)
        amount, currency, money_reason = clean_contract(row["contract_value"])
        if money_reason:
            after = f"{amount} {currency}".strip()
            log(source_row, raw_name, "contract_value", row["contract_value"], after, money_reason)
        weight, weight_reason = clean_weight(row["weight"])
        if weight_reason:
            log(source_row, raw_name, "weight_kg", row["weight"], weight, weight_reason)
        prepared.append(
            {
                "source_row": source_row,
                "vendor_name_raw": raw_name,
                "vendor_name": vendor_name,
                "phone": phone,
                "street": street,
                "city": city,
                "region": region,
                "category": category,
                "since": since,
                "contract_amount": amount,
                "contract_currency": currency,
                "weight_kg": weight,
            }
        )

    groups = cluster_indices(
        [match_key(row["vendor_name"]) for row in prepared],
        score_of,
    )
    survivor_for: dict[int, int] = {}
    for group in groups:
        survivor = _pick_survivor(prepared, group)
        for index in group:
            survivor_for[index] = survivor
        _fill_blanks(prepared, group, survivor, log)

    for index, row in enumerate(prepared):
        survivor = survivor_for[index]
        if index == survivor:
            continue
        kept = prepared[survivor]
        score = score_of(match_key(row["vendor_name"]), match_key(kept["vendor_name"]))
        log(
            row["source_row"],
            row["vendor_name_raw"],
            "vendor_name",
            row["vendor_name_raw"],
            kept["vendor_name"],
            f"merged_duplicate score={format_score(score)} into_row={kept['source_row']}",
            force=True,
        )

    survivors = [prepared[index] for index in range(len(prepared)) if survivor_for[index] == index]
    survivors.sort(key=lambda row: (row["vendor_name"].casefold(), row["source_row"]))
    clean_rows = []
    for offset, row in enumerate(survivors, start=1):
        clean_rows.append(
            {
                "vendor_id": f"V-{1000 + offset}",
                "vendor_name": row["vendor_name"],
                "phone": row["phone"],
                "street": row["street"],
                "city": row["city"],
                "region": row["region"],
                "category": row["category"],
                "since": row["since"],
                "contract_amount": row["contract_amount"],
                "contract_currency": row["contract_currency"],
                "weight_kg": row["weight_kg"],
                "source_row": str(row["source_row"]),
            }
        )

    ordered_changes = [item[2] for item in sorted(changes, key=lambda item: (item[0], item[1]))]
    summary = _summary(prepared, survivor_for, ordered_changes)
    return clean_rows, ordered_changes, summary


def run(input_path: Path, output_dir: Path, category_map: Path, *, score_of=None) -> int:
    rows = read_table(input_path)
    categories = load_categories(category_map)
    clean_rows, changes, summary = clean_records(rows, categories, score_of=score_of)
    write_outputs(output_dir, clean_rows, changes, summary)
    return 0


def read_table(path: Path) -> list[dict[str, str]]:
    if path.suffix.lower() == ".xlsx":
        return _read_xlsx(path)
    return _read_csv(path)


def load_categories(path: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "alias" not in reader.fieldnames or "category" not in reader.fieldnames:
            raise ValueError(f"{path} must have alias and category columns")
        for row in reader:
            alias = (row.get("alias") or "").strip().lower()
            category = (row.get("category") or "").strip()
            if alias and category:
                mapping[alias] = category
    return mapping


def write_outputs(
    output_dir: Path,
    clean_rows: list[dict[str, str]],
    changes: list[Change],
    summary: dict,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "vendors_clean.csv", CLEAN_COLUMNS, clean_rows)
    _write_csv(
        output_dir / "change_log.csv",
        CHANGE_COLUMNS,
        [change.as_dict() for change in changes],
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary["json"], indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "summary.md").write_text(summary["markdown"], encoding="utf-8")


def write_xlsx(path: Path, rows: list[dict[str, str]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "vendors"
    for column, name in enumerate(RAW_COLUMNS, start=1):
        cell = sheet.cell(row=1, column=column, value=name)
        cell.number_format = "@"
    for row_index, row in enumerate(rows, start=2):
        for column, name in enumerate(RAW_COLUMNS, start=1):
            cell = sheet.cell(row=row_index, column=column, value=row.get(name, ""))
            cell.number_format = "@"
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


def match_key(name: str) -> str:
    text = name.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(token for token in text.split() if token not in KEY_DROPPED)


def name_score(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return float(fuzz.token_sort_ratio(left, right))


def format_score(score: float) -> str:
    rounded = round(float(score), 1)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.1f}"


def cluster_indices(keys: list[str], score_of) -> list[list[int]]:
    parent = list(range(len(keys)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left in range(len(keys)):
        if not keys[left]:
            continue
        for right in range(left + 1, len(keys)):
            if not keys[right]:
                continue
            if score_of(keys[left], keys[right]) >= MERGE_THRESHOLD:
                union(left, right)
    groups: dict[int, list[int]] = defaultdict(list)
    for index in range(len(keys)):
        groups[find(index)].append(index)
    return list(groups.values())


def clean_vendor_name(raw: str) -> tuple[str, list[tuple[str, str, str, str]]]:
    changes: list[tuple[str, str, str, str]] = []
    collapsed = re.sub(r"\s+", " ", raw.strip())
    current = raw
    if collapsed != raw:
        changes.append(("vendor_name", raw, collapsed, "whitespace"))
        current = collapsed
    letters = [character for character in current if character.isalpha()]
    uniform = bool(letters) and (
        all(character.isupper() for character in letters)
        or all(character.islower() for character in letters)
    )
    if uniform:
        titled = _title_vendor(current)
        if titled != current:
            changes.append(("vendor_name", current, titled, "name_case"))
            current = titled
    return current, changes


def clean_phone(raw: str) -> tuple[str, str | None]:
    text = raw.strip()
    if not text:
        return "", None
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return "", "phone_unparsed"
    formatted = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    if formatted == text:
        return formatted, None
    return formatted, "phone_normalized"


def clean_street(raw: str) -> tuple[str, str | None]:
    collapsed = re.sub(r"\s+", " ", raw.strip())
    if not collapsed:
        return "", None
    parts: list[str] = []
    for part in collapsed.split(","):
        tokens: list[str] = []
        for token in part.strip().split(" "):
            if not token:
                continue
            bare = token[:-1] if token.endswith(".") else token
            if bare.lower() in STREET_WORDS:
                tokens.append(STREET_WORDS[bare.lower()])
            elif re.fullmatch(r"\d+[A-Za-z]?", token):
                tokens.append(token)
            else:
                word = token.strip(".")
                tokens.append(word[:1].upper() + word[1:].lower() if word else word)
        if tokens:
            parts.append(" ".join(tokens))
    cleaned = ", ".join(parts)
    if cleaned == raw:
        return cleaned, None
    return cleaned, "street_normalized"


def clean_city(raw: str) -> tuple[str, str | None]:
    collapsed = re.sub(r"\s+", " ", raw.strip())
    if not collapsed:
        return "", None
    letters = [character for character in collapsed if character.isalpha()]
    if letters and (
        all(character.isupper() for character in letters)
        or all(character.islower() for character in letters)
    ):
        cleaned = " ".join(part.capitalize() for part in collapsed.split(" "))
    else:
        cleaned = collapsed
    if cleaned == raw:
        return cleaned, None
    return cleaned, "city_case"


def clean_region(raw: str) -> tuple[str, str | None]:
    collapsed = re.sub(r"\s+", " ", raw.strip())
    if not collapsed:
        return "", None
    mapped = REGIONS.get(collapsed.lower())
    if mapped:
        cleaned = mapped
    elif re.fullmatch(r"[A-Za-z]{2}", collapsed):
        cleaned = collapsed.upper()
    else:
        cleaned = collapsed
    if cleaned == raw:
        return cleaned, None
    return cleaned, "region_normalized"


def clean_category(raw: str, categories: dict[str, str]) -> tuple[str, str | None]:
    text = raw.strip()
    if not text:
        return "", None
    found = categories.get(text.lower())
    if found is None:
        return "", "category_unmapped"
    if found == text:
        return found, None
    return found, "category_mapped"


def clean_date(raw: str) -> tuple[str, str | None]:
    text = re.sub(r"\s+", " ", raw.strip())
    if not text:
        return "", None
    parsed = parse_date(text)
    if parsed is None:
        return "", "date_unparsed"
    iso = parsed.isoformat()
    if iso == text:
        return iso, None
    return iso, "date_normalized"


def parse_date(text: str) -> date | None:
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        return _safe_date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    slashed_ymd = re.fullmatch(r"(\d{4})/(\d{2})/(\d{2})", text)
    if slashed_ymd:
        return _safe_date(
            int(slashed_ymd.group(1)),
            int(slashed_ymd.group(2)),
            int(slashed_ymd.group(3)),
        )
    us = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if us:
        return _safe_date(int(us.group(3)), int(us.group(1)), int(us.group(2)))
    named_day = re.fullmatch(r"(\d{1,2})-([A-Za-z]+)-(\d{4})", text)
    if named_day:
        month = MONTHS.get(named_day.group(2).lower())
        if month is None:
            return None
        return _safe_date(int(named_day.group(3)), month, int(named_day.group(1)))
    named = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", text)
    if named:
        month = MONTHS.get(named.group(1).lower())
        if month is None:
            return None
        return _safe_date(int(named.group(3)), month, int(named.group(2)))
    return None


def clean_contract(raw: str) -> tuple[str, str, str | None]:
    text = raw.strip()
    if not text:
        return "", "", None
    code_match = CODE_RE.search(text)
    assumed = False
    if code_match:
        code = code_match.group(1).upper()
    elif "$" in text:
        code = "USD"
    elif "€" in text:
        code = "EUR"
    elif "£" in text:
        code = "GBP"
    else:
        code = "USD"
        assumed = True
    number_text = CODE_RE.sub("", text)
    for symbol in ("$", "€", "£", ","):
        number_text = number_text.replace(symbol, "")
    number_text = number_text.strip()
    try:
        amount = Decimal(number_text)
    except InvalidOperation:
        return "", "", "currency_unparsed"
    quantized = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    amount_text = f"{quantized:.2f}"
    combined = f"{amount_text} {code}"
    if combined == text:
        return amount_text, code, None
    reason = "currency_assumed_usd" if assumed else "currency_parsed"
    return amount_text, code, reason


def clean_weight(raw: str) -> tuple[str, str | None]:
    text = re.sub(r"\s+", " ", raw.strip())
    if not text:
        return "", None
    if text.lower() in WEIGHT_MISSING:
        return "", "weight_missing"
    match = WEIGHT_RE.fullmatch(text)
    if match is None:
        return "", "weight_unparsed"
    quantity = Decimal(match.group("qty"))
    unit = match.group("unit").lower()
    if unit in {"lb", "lbs", "pound", "pounds"}:
        kilograms = quantity * POUND_TO_KG
    elif unit == "g":
        kilograms = quantity / Decimal(1000)
    else:
        kilograms = quantity
    quantized = kilograms.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    rendered = f"{quantized:.3f}"
    if rendered == raw:
        return rendered, None
    return rendered, "weight_normalized"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Clean a vendor CSV or Excel file and write a change log."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--category-map", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("samples"))
    args = parser.parse_args(argv)
    category_map = args.category_map or Path(__file__).resolve().parent / "category_map.csv"
    try:
        return run(args.input, args.output_dir, category_map)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _title_vendor(text: str) -> str:
    tokens = []
    for index, token in enumerate(text.split(" ")):
        bare = token.strip(".").lower()
        if bare in SUFFIXES:
            tokens.append(SUFFIXES[bare])
        elif index > 0 and bare in SMALL_WORDS:
            tokens.append(bare)
        else:
            word = token.strip(".")
            tokens.append(word.capitalize() if word else word)
    return " ".join(tokens)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _completeness(row: dict) -> int:
    fields = (
        "vendor_name",
        "phone",
        "street",
        "city",
        "region",
        "category",
        "since",
        "contract_amount",
        "contract_currency",
        "weight_kg",
    )
    return sum(1 for field in fields if str(row[field]).strip())


def _pick_survivor(rows: list[dict], group: list[int]) -> int:
    return min(group, key=lambda index: (-_completeness(rows[index]), rows[index]["source_row"]))


def _fill_blanks(rows: list[dict], group: list[int], survivor: int, log) -> None:
    kept = rows[survivor]
    donors = sorted(
        (index for index in group if index != survivor),
        key=lambda index: rows[index]["source_row"],
    )
    for field in FILL_FIELDS:
        if str(kept[field]).strip():
            continue
        for donor_index in donors:
            donor = rows[donor_index]
            if not str(donor[field]).strip():
                continue
            before = kept[field]
            kept[field] = donor[field]
            log(
                kept["source_row"],
                kept["vendor_name_raw"],
                field,
                before,
                donor[field],
                f"filled_from_duplicate from_row={donor['source_row']}",
            )
            break
    if str(kept["contract_amount"]).strip():
        return
    for donor_index in donors:
        donor = rows[donor_index]
        if not str(donor["contract_amount"]).strip():
            continue
        before = ""
        after = f"{donor['contract_amount']} {donor['contract_currency']}".strip()
        kept["contract_amount"] = donor["contract_amount"]
        kept["contract_currency"] = donor["contract_currency"]
        log(
            kept["source_row"],
            kept["vendor_name_raw"],
            "contract_value",
            before,
            after,
            f"filled_from_duplicate from_row={donor['source_row']}",
        )
        break


def _summary(rows: list[dict], survivor_for: dict[int, int], changes: list[Change]) -> dict:
    reasons: dict[str, int] = defaultdict(int)
    for change in changes:
        reasons[change.reason.split()[0]] += 1
    merged_rows = sum(1 for index, survivor in survivor_for.items() if index != survivor)
    lines = [
        "# Vendor cleaning summary",
        "",
        f"Input rows: {len(rows)}",
        f"Output rows: {len(rows) - merged_rows}",
        f"Rows merged as duplicates: {merged_rows}",
        f"Logged changes: {len(changes)}",
        "",
        "| Source row | Vendor before | Vendor after | In the clean file |",
        "| --- | --- | --- | --- |",
    ]
    for index, row in enumerate(rows):
        survivor = rows[survivor_for[index]]
        if index == survivor_for[index]:
            kept = "yes"
        else:
            kept = f"merged into row {survivor['source_row']}"
        lines.append(
            f"| {row['source_row']} | {row['vendor_name_raw']} | {survivor['vendor_name']} | {kept} |"
        )
    lines.extend(["", "| Reason | Count |", "| --- | --- |"])
    for reason in sorted(reasons):
        lines.append(f"| {reason} | {reasons[reason]} |")
    lines.append("")
    payload = {
        "input_rows": len(rows),
        "output_rows": len(rows) - merged_rows,
        "merged_rows": merged_rows,
        "change_count": len(changes),
        "reasons": {reason: reasons[reason] for reason in sorted(reasons)},
    }
    return {"json": payload, "markdown": "\n".join(lines)}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{path} is empty")
        missing = [column for column in RAW_COLUMNS if column not in reader.fieldnames]
        if missing:
            raise ValueError(f"missing columns: {', '.join(missing)}")
        records = []
        for source_row, row in enumerate(reader, start=1):
            record = {column: row.get(column) or "" for column in RAW_COLUMNS}
            record["source_row"] = str(source_row)
            records.append(record)
        return records


def _read_xlsx(path: Path) -> list[dict[str, str]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.active
        iterator = sheet.iter_rows(values_only=True)
        header = next(iterator, None)
        if header is None:
            raise ValueError(f"{path} is empty")
        names = ["" if cell is None else str(cell).strip() for cell in header]
        missing = [column for column in RAW_COLUMNS if column not in names]
        if missing:
            raise ValueError(f"missing columns: {', '.join(missing)}")
        positions = {name: index for index, name in enumerate(names)}
        records = []
        for source_row, values in enumerate(iterator, start=1):
            record = {}
            for column in RAW_COLUMNS:
                cell = values[positions[column]] if positions[column] < len(values) else None
                record[column] = "" if cell is None else str(cell)
            if all(not record[column] for column in RAW_COLUMNS):
                continue
            record["source_row"] = str(source_row)
            records.append(record)
        return records
    finally:
        workbook.close()


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    sys.exit(main())
