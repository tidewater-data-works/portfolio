"""Summary statistics and the PNG chart."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from bls import DEFAULT_SERIES_ID, parse_series
from summary import summarize, write_chart, write_summary

from test_bls import _load

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _rows():
    return parse_series(_load("success.json"), DEFAULT_SERIES_ID)


def test_summary_latest_mom_and_trailing_window():
    summary = summarize(_rows())
    assert summary.latest_period == "2024-09"
    assert summary.latest_value == Decimal("4.4")
    assert summary.mom_change == Decimal("0.2")
    assert summary.trailing_12_min == Decimal("3.7")
    assert summary.trailing_12_max == Decimal("4.4")
    assert summary.trailing_12_count == 11
    assert summary.numeric_rows == 13


def test_mom_is_blank_when_the_previous_month_is_blank():
    rows = [
        replace(row, value=None, value_text="") if row.period == "M08" and row.year == 2024 else row
        for row in _rows()
    ]
    summary = summarize(rows)
    assert summary.latest_value == Decimal("4.4")
    assert summary.mom_change is None


def test_summary_json_and_png(tmp_path):
    summary = summarize(_rows())
    json_path = tmp_path / "summary.json"
    chart_path = tmp_path / "lns14000000.png"
    write_summary(summary, json_path)
    write_chart(_rows(), chart_path)
    body = json_path.read_text(encoding="utf-8")
    assert '"latest_value": 4.4' in body
    assert '"mom_change": 0.2' in body
    assert '"trailing_12_min": 3.7' in body
    assert '"trailing_12_max": 4.4' in body
    data = chart_path.read_bytes()
    assert data.startswith(PNG_MAGIC)
    assert len(data) > 1000
