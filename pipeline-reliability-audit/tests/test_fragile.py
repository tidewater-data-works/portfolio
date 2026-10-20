"""The shipment load before the reliability check. These tests lock the failure modes."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from before.fragile import append_csv, fetch_events, load_and_append, main

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
EVENTS = SAMPLES / "events.json"
URL = "https://example.invalid/events"


class _Response:
    def __init__(self, status_code: int, payload=None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_fetch_does_not_retry_or_pass_a_timeout():
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(503)

    with pytest.raises(RuntimeError, match="HTTP 503"):
        fetch_events(get, URL)
    assert calls == [(URL, {})]


def test_fetch_returns_the_body_on_the_first_200():
    payload = [{"event_id": "NL-1001"}]

    def get(url):
        return _Response(200, payload)

    assert fetch_events(get, URL) == payload


def test_second_append_duplicates_rows_including_invalid_values(tmp_path, capsys):
    output = tmp_path / "shipments.csv"
    load_and_append(EVENTS, output)
    load_and_append(EVENTS, output)
    assert "writing 8 events" in capsys.readouterr().out
    text = output.read_text(encoding="utf-8")
    assert text == (SAMPLES / "before_twice.csv").read_text(encoding="utf-8")
    rows = list(csv.DictReader(text.splitlines()))
    assert len(rows) == 16
    assert [row["event_id"] for row in rows[:8]] == [row["event_id"] for row in rows[8:]]
    assert rows[7]["weight_lb"] == "-3"
    assert rows[6]["status"] == "lost"


def test_key_error_leaves_earlier_rows_in_the_destination(tmp_path):
    events = json.loads(EVENTS.read_text(encoding="utf-8"))[:2]
    events.append(
        {
            "event_id": "NL-1009",
            "shipped_on": "2026-03-06",
            "status": "booked",
            "weight_lb": "1",
        }
    )
    output = tmp_path / "shipments.csv"
    with pytest.raises(KeyError):
        append_csv(output, events)
    rows = list(csv.DictReader(output.open(encoding="utf-8")))
    assert [row["event_id"] for row in rows] == ["NL-1001", "NL-1002"]


def test_cli_rejects_a_payload_that_is_not_a_list(tmp_path):
    source = tmp_path / "events.json"
    source.write_text('{"event_id": "NL-1001"}\n', encoding="utf-8")
    code = main(["--input", str(source), "--output", str(tmp_path / "out.csv")])
    assert code == 1
    assert not (tmp_path / "out.csv").exists()
