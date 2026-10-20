"""Retries, idempotent writes, and the quality gate. No network."""

from __future__ import annotations

import csv
import json
import logging
from decimal import Decimal
from pathlib import Path

import pytest

from after.reliable import (
    FetchError,
    QualityError,
    assess,
    fetch_events,
    main,
    publish,
    run,
    urllib_get,
)

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
EVENTS = SAMPLES / "events.json"
URL = "https://example.invalid/events"


class _Response:
    def __init__(self, status_code: int, payload=None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else []

    def json(self):
        if self._payload is ValueError:
            raise ValueError("response was not JSON")
        return self._payload


def _events() -> list[dict]:
    return json.loads(EVENTS.read_text(encoding="utf-8"))


def _good(event_id: str = "NL-1001", **overrides) -> dict:
    row = {
        "event_id": event_id,
        "shipped_on": "2026-03-02",
        "status": "delivered",
        "weight_lb": "10",
        "destination": "Port City, VA",
    }
    row.update(overrides)
    return row


def test_retries_use_exponential_backoff_and_a_timeout():
    sleeps = []
    calls = []
    statuses = [503, 503, 200]

    def get(url, timeout):
        calls.append(timeout)
        status = statuses.pop(0)
        return _Response(status, [_good()] if status == 200 else None)

    rows = fetch_events(get, URL, sleep=sleeps.append, backoff_seconds=0.5)
    assert rows == [_good()]
    assert calls == [10.0, 10.0, 10.0]
    assert sleeps == [0.5, 1.0]


def test_timeout_is_retried_and_a_400_is_not():
    sleeps = []
    attempts = {"n": 0}

    def get(url, timeout):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise TimeoutError("timed out")
        return _Response(200, [_good()])

    assert fetch_events(get, URL, sleep=sleeps.append) == [_good()]
    assert sleeps == [0.5, 1.0]

    refused = []

    def deny(url, timeout):
        refused.append(timeout)
        return _Response(400)

    with pytest.raises(FetchError, match="HTTP 400"):
        fetch_events(deny, URL, sleep=sleeps.append)
    assert refused == [10.0]


def test_bad_json_and_a_non_list_fail_without_another_attempt():
    calls = []

    def bad_json(url, timeout):
        calls.append("json")
        return _Response(200, ValueError)

    def bad_shape(url, timeout):
        calls.append("shape")
        return _Response(200, {"events": []})

    with pytest.raises(FetchError, match="not JSON"):
        fetch_events(bad_json, URL, sleep=lambda delay: None)
    with pytest.raises(FetchError, match="must be a list"):
        fetch_events(bad_shape, URL, sleep=lambda delay: None)
    assert calls == ["json", "shape"]


def test_sample_batch_is_idempotent_and_matches_the_committed_files(tmp_path, caplog):
    output = tmp_path / "shipments.csv"
    quarantine = tmp_path / "quarantine.csv"
    with caplog.at_level(logging.INFO):
        first = run(input_path=EVENTS, output=output, quarantine=quarantine)
        second = run(input_path=EVENTS, output=output, quarantine=quarantine)
    assert first == {"written": 6, "accepted": 6, "quarantined": 2}
    assert second == first
    assert output.read_text(encoding="utf-8") == (SAMPLES / "shipments.csv").read_text(encoding="utf-8")
    assert quarantine.read_text(encoding="utf-8") == (SAMPLES / "quarantine.csv").read_text(
        encoding="utf-8"
    )
    assert "quarantined NL-1007: status" in caplog.text
    assert "quarantined NL-1008: weight_lb" in caplog.text
    assert "wrote 6 rows, quarantined 2" in caplog.text


def test_rerun_updates_one_event_in_place(tmp_path):
    output = tmp_path / "shipments.csv"
    quarantine = tmp_path / "quarantine.csv"
    run(input_path=EVENTS, output=output, quarantine=quarantine)
    changed = _good("NL-1001", weight_lb="430")
    result = publish([changed], output, quarantine)
    rows = list(csv.DictReader(output.open(encoding="utf-8")))
    assert result["written"] == 6
    assert len(rows) == 6
    assert rows[0]["weight_lb"] == "430.00"


def test_reading_the_destination_collapses_duplicate_ids(tmp_path):
    output = tmp_path / "shipments.csv"
    output.write_text(
        "event_id,shipped_on,status,weight_lb,destination\n"
        'NL-1001,2026-03-02,delivered,10.00,"Port City, VA"\n'
        'NL-1001,2026-03-02,delivered,10.00,"Port City, VA"\n',
        encoding="utf-8",
    )
    publish([_good("NL-2000")], output, tmp_path / "quarantine.csv")
    rows = list(csv.DictReader(output.open(encoding="utf-8")))
    assert [row["event_id"] for row in rows] == ["NL-1001", "NL-2000"]


def test_later_bad_copy_of_an_id_does_not_erase_the_good_row(tmp_path):
    output = tmp_path / "shipments.csv"
    quarantine = tmp_path / "quarantine.csv"
    publish(
        [
            _good("NL-1001", weight_lb="10"),
            _good("NL-1002"),
            _good("NL-1003"),
            _good("NL-1001", status="lost"),
        ],
        output,
        quarantine,
    )
    rows = list(csv.DictReader(output.open(encoding="utf-8")))
    assert [row["event_id"] for row in rows] == ["NL-1001", "NL-1002", "NL-1003"]
    assert rows[0]["weight_lb"] == "10.00"
    rejected = list(csv.DictReader(quarantine.open(encoding="utf-8")))
    assert [(row["event_id"], row["errors"]) for row in rejected] == [("NL-1001", "status")]


def test_quality_gate_and_empty_batch_leave_the_destination_unchanged(tmp_path, caplog):
    output = tmp_path / "shipments.csv"
    output.write_text("keep\n", encoding="utf-8")
    quarantine = tmp_path / "quarantine.csv"
    with caplog.at_level(logging.ERROR):
        with pytest.raises(QualityError, match="0 events"):
            publish([], output, quarantine)
        with pytest.raises(QualityError, match="invalid share 0.50 exceeds 0.25"):
            publish([_good(), _good("NL-1002", status="lost")], output, quarantine)
    assert output.read_text(encoding="utf-8") == "keep\n"
    assert not quarantine.exists()
    assert "destination left unchanged" in caplog.text


def test_one_bad_row_in_four_is_quarantined_and_the_rest_load(tmp_path):
    events = [_good(f"NL-100{index}") for index in range(1, 4)]
    events.append(_good("NL-1004", weight_lb="0"))
    result = publish(events, tmp_path / "shipments.csv", tmp_path / "quarantine.csv")
    assert result == {"written": 3, "accepted": 3, "quarantined": 1}


def test_failed_replace_keeps_the_previous_file(tmp_path, monkeypatch):
    output = tmp_path / "shipments.csv"
    quarantine = tmp_path / "quarantine.csv"
    publish([_good()], output, quarantine)
    original = output.read_bytes()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("after.reliable.os.replace", boom)
    with pytest.raises(OSError, match="disk full"):
        publish([_good(weight_lb="99")], output, quarantine)
    assert output.read_bytes() == original


def test_header_mismatch_does_not_replace_the_file(tmp_path):
    output = tmp_path / "shipments.csv"
    output.write_text("id,value\nNL-1001,1\n", encoding="utf-8")
    with pytest.raises(QualityError, match="header"):
        publish([_good()], output, tmp_path / "quarantine.csv")
    assert output.read_text(encoding="utf-8") == "id,value\nNL-1001,1\n"


def test_assess_boundaries():
    assert assess(_good(weight_lb="50000")) == []
    assert "weight_lb" in assess(_good(weight_lb="50000.01"))
    assert "weight_lb" in assess(_good(weight_lb="-3"))
    assert assess(_good(status="Delivered")) == ["status"]
    assert assess(_good(shipped_on="2026-02-31")) == ["shipped_on"]
    assert assess("NL-1001") == ["not_an_object"]


def test_cli_writes_the_sample_and_rejects_a_mixed_source(tmp_path):
    output = tmp_path / "shipments.csv"
    quarantine = tmp_path / "quarantine.csv"
    code = main(
        [
            "--input",
            str(EVENTS),
            "--output",
            str(output),
            "--quarantine",
            str(quarantine),
        ]
    )
    assert code == 0
    assert output.read_text(encoding="utf-8") == (SAMPLES / "shipments.csv").read_text(encoding="utf-8")
    assert main(["--output", str(output), "--quarantine", str(quarantine)]) == 1
    assert (
        main(
            [
                "--input",
                str(EVENTS),
                "--url",
                URL,
                "--output",
                str(output),
                "--quarantine",
                str(quarantine),
            ]
        )
        == 1
    )


def test_urllib_get_parses_a_mocked_response_and_keeps_http_errors(monkeypatch):
    captured = {}

    class _Body:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"[]"

    def urlopen(request, timeout):
        captured["timeout"] = timeout
        captured["agent"] = request.get_header("User-agent")
        return _Body()

    monkeypatch.setattr("after.reliable.urllib.request.urlopen", urlopen)
    response = urllib_get(URL, timeout=10)
    assert response.status_code == 200
    assert response.json() == []
    assert captured["timeout"] == 10
    assert "TidewaterDataWorks" in captured["agent"]

    def fail(request, timeout):
        import urllib.error

        raise urllib.error.HTTPError(request.full_url, 503, "unavailable", hdrs=None, fp=None)

    monkeypatch.setattr("after.reliable.urllib.request.urlopen", fail)
    assert urllib_get(URL).status_code == 503

    class _Garbage:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"not-json"

    monkeypatch.setattr("after.reliable.urllib.request.urlopen", lambda request, timeout: _Garbage())
    with pytest.raises(FetchError, match="not JSON"):
        urllib_get(URL)


def test_retry_log_names_the_status(caplog):
    def get(url, timeout):
        return _Response(503)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(FetchError, match="after 3 attempts"):
            fetch_events(get, URL, sleep=lambda delay: None, backoff_seconds=0)
    assert "attempt 1 retryable HTTP 503" in caplog.text
