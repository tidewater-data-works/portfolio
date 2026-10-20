"""Offline tests for the BLS v1 client, parser, and validators."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import requests

from bls import (
    API_URL,
    DEFAULT_SERIES_ID,
    EmptySeriesError,
    FetchError,
    SchemaError,
    fetch_payload,
    freshness_warning,
    parse_series,
    validate_observations,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class _Response:
    def __init__(self, status: int, payload) -> None:
        self.status_code = status
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_fetch_sends_v1_query_without_an_api_key():
    captured = {}

    def poster(url, *, json, timeout, headers):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _Response(200, _load("success.json"))

    payload = fetch_payload(DEFAULT_SERIES_ID, 2023, 2024, poster=poster, sleep=lambda _seconds: None)
    assert payload["status"] == "REQUEST_SUCCEEDED"
    assert captured["url"] == API_URL
    assert captured["json"] == {
        "seriesid": ["LNS14000000"],
        "startyear": "2023",
        "endyear": "2024",
    }
    assert "registrationkey" not in {key.lower() for key in captured["json"]}
    assert "TidewaterDataWorks" in captured["headers"]["User-Agent"]


def test_retries_transient_failures_then_succeeds():
    calls = {"n": 0}
    sleeps: list[float] = []

    def poster(url, *, json, timeout, headers):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ConnectionError("reset")
        if calls["n"] == 2:
            return _Response(503, {"status": "REQUEST_FAILED"})
        return _Response(200, _load("success.json"))

    payload = fetch_payload(
        DEFAULT_SERIES_ID,
        2023,
        2024,
        poster=poster,
        max_attempts=3,
        backoff_seconds=0.5,
        sleep=sleeps.append,
    )
    assert payload["Results"]["series"][0]["seriesID"] == DEFAULT_SERIES_ID
    assert calls["n"] == 3
    assert sleeps == [0.5, 1.0]


def test_retry_exhaustion_raises_fetch_error():
    def poster(url, *, json, timeout, headers):
        raise requests.Timeout("slow")

    with pytest.raises(FetchError, match="failed after 2 attempts"):
        fetch_payload(
            DEFAULT_SERIES_ID,
            2024,
            2024,
            poster=poster,
            max_attempts=2,
            sleep=lambda _seconds: None,
        )


def test_http_client_error_is_not_retried():
    calls = {"n": 0}

    def poster(url, *, json, timeout, headers):
        calls["n"] += 1
        return _Response(400, {"status": "REQUEST_FAILED"})

    with pytest.raises(FetchError, match="HTTP 400"):
        fetch_payload(DEFAULT_SERIES_ID, 2024, 2024, poster=poster, sleep=lambda _seconds: None)
    assert calls["n"] == 1


def test_parse_drops_annual_rows_and_treats_dashes_as_blanks():
    rows = parse_series(_load("success.json"), DEFAULT_SERIES_ID)
    periods = [row.period for row in rows]
    assert "M13" not in periods
    june = next(row for row in rows if row.year == 2024 and row.period == "M06")
    assert june.value is None
    assert june.value_text == ""
    september = next(row for row in rows if row.year == 2024 and row.period == "M09")
    assert september.value == Decimal("4.4")
    assert september.value_text == "4.4"
    assert september.period_end == date(2024, 9, 30)


def test_parse_strips_spaced_dash_to_blank():
    rows = parse_series(_load("all_blanks.json"), DEFAULT_SERIES_ID)
    assert len(rows) == 2
    assert all(row.value is None and row.value_text == "" for row in rows)


def test_schema_mismatch_when_value_is_missing():
    with pytest.raises(SchemaError, match="schema mismatch"):
        parse_series(_load("schema_mismatch.json"), DEFAULT_SERIES_ID)


def test_schema_mismatch_when_payload_is_not_an_object():
    with pytest.raises(SchemaError, match="schema mismatch"):
        parse_series(["not-an-object"], DEFAULT_SERIES_ID)  # type: ignore[arg-type]


def test_request_failed_status_is_a_fetch_error():
    with pytest.raises(FetchError, match="REQUEST_FAILED"):
        parse_series(_load("request_failed.json"), DEFAULT_SERIES_ID)


def test_empty_series_fails_validation():
    rows = parse_series(_load("empty.json"), DEFAULT_SERIES_ID)
    with pytest.raises(EmptySeriesError, match="0 rows"):
        validate_observations(rows, today=date(2024, 10, 20))


def test_all_blank_values_count_as_zero_rows():
    rows = parse_series(_load("all_blanks.json"), DEFAULT_SERIES_ID)
    with pytest.raises(EmptySeriesError, match="0 rows"):
        validate_observations(rows, today=date(2024, 10, 20))


def test_fresh_series_passes_and_stale_series_warns():
    rows = parse_series(_load("success.json"), DEFAULT_SERIES_ID)
    validate_observations(rows, today=date(2024, 11, 30))
    assert freshness_warning(rows, today=date(2024, 10, 20)) is None
    assert freshness_warning(rows, today=date(2024, 11, 29)) is None
    warning = freshness_warning(rows, today=date(2024, 11, 30))
    assert warning is not None
    assert "older than 60 days" in warning
