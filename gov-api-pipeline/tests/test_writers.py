"""CSV is the default writer. Sheets is enabled only by env configuration."""

from __future__ import annotations

import csv
import json

import pytest

from bls import DEFAULT_SERIES_ID, parse_series
from writers import CSVWriter, SheetsWriter, build_writers
from writers.csv_writer import COLUMNS

from test_bls import _load


def _rows():
    return parse_series(_load("success.json"), DEFAULT_SERIES_ID)


def test_csv_writer_orders_rows_and_leaves_blanks_empty(tmp_path):
    path = tmp_path / "lns14000000.csv"
    CSVWriter(path).write(_rows())
    with path.open(encoding="utf-8", newline="") as handle:
        records = list(csv.DictReader(handle))
    assert list(records[0]) == COLUMNS
    assert records[0]["period"] == "M08"
    assert records[0]["year"] == "2023"
    june = next(row for row in records if row["year"] == "2024" and row["period"] == "M06")
    assert june["value"] == ""
    assert all(row["value"] != "-" for row in records)
    september = next(row for row in records if row["year"] == "2024" and row["period"] == "M09")
    assert september["value"] == "4.4"
    assert "M13" not in {row["period"] for row in records}


def test_sheets_writer_is_disabled_without_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    writers = build_writers(tmp_path, DEFAULT_SERIES_ID)
    assert len(writers) == 1
    assert isinstance(writers[0], CSVWriter)


def test_sheets_writer_is_enabled_when_credentials_are_set(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        json.dumps({"client_email": "sheets-writer@example.com", "token_uri": "https://oauth2.googleapis.com/token"}),
    )
    monkeypatch.setenv("GOOGLE_SHEETS_SPREADSHEET_ID", "PLACEHOLDER_SPREADSHEET_ID")
    monkeypatch.setenv("GOOGLE_SHEETS_WORKSHEET", "Unemployment")
    writers = build_writers(tmp_path, DEFAULT_SERIES_ID)
    sheets = [writer for writer in writers if isinstance(writer, SheetsWriter)]
    assert len(sheets) == 1
    assert sheets[0].spreadsheet_id == "PLACEHOLDER_SPREADSHEET_ID"
    assert sheets[0].worksheet == "Unemployment"


def test_sheets_writer_requires_spreadsheet_id(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
    monkeypatch.delenv("GOOGLE_SHEETS_SPREADSHEET_ID", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_SHEETS_SPREADSHEET_ID"):
        build_writers(tmp_path, DEFAULT_SERIES_ID)


def test_sheets_writer_clears_and_upserts_with_blank_cells():
    calls = []

    class _Exec:
        def execute(self):
            return {"ok": True}

    class _Values:
        def clear(self, **kwargs):
            calls.append(("clear", kwargs))
            return _Exec()

        def update(self, **kwargs):
            calls.append(("update", kwargs))
            return _Exec()

    class _Spreadsheets:
        def values(self):
            return _Values()

    class _Service:
        def spreadsheets(self):
            return _Spreadsheets()

    writer = SheetsWriter(
        "PLACEHOLDER_SPREADSHEET_ID",
        "Unemployment",
        {"client_email": "sheets-writer@example.com"},
        credentials_factory=lambda info: object(),
        service_builder=lambda *args, **kwargs: _Service(),
    )
    writer.write(_rows())
    assert calls[0][0] == "clear"
    assert calls[0][1]["range"] == "'Unemployment'!A:E"
    assert calls[1][0] == "update"
    grid = calls[1][1]["body"]["values"]
    assert grid[0] == COLUMNS
    june = next(row for row in grid[1:] if row[1] == "2024" and row[2] == "M06")
    assert june[4] == ""
    assert all(row[4] != "-" for row in grid[1:])
