"""End-to-end pipeline runs against fixture payloads. No network."""

from __future__ import annotations

import csv
import json
import logging
from datetime import date

import pytest

from bls import DEFAULT_SERIES_ID
from pipeline import main, run

from test_bls import _load
from test_summary import PNG_MAGIC


@pytest.fixture(autouse=True)
def _clear_optional_env(monkeypatch):
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_SHEETS_SPREADSHEET_ID", raising=False)
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)


def test_pipeline_writes_csv_summary_and_chart(tmp_path, caplog):
    code = run(
        output_dir=tmp_path,
        today=date(2024, 10, 20),
        poster=lambda *args, **kwargs: _Response(_load("success.json")),
        start_year=2023,
        end_year=2024,
    )
    assert code == 0
    with (tmp_path / "lns14000000.csv").open(encoding="utf-8", newline="") as handle:
        values = [row["value"] for row in csv.DictReader(handle)]
    assert "" in values
    assert "-" not in values
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["latest_period"] == "2024-09"
    assert summary["latest_value"] == 4.4
    assert summary["mom_change"] == 0.2
    assert summary["trailing_12_min"] == 3.7
    assert summary["trailing_12_max"] == 4.4
    assert (tmp_path / "lns14000000.png").read_bytes().startswith(PNG_MAGIC)


def test_pipeline_alerts_and_fails_on_zero_rows(tmp_path, caplog):
    alerts = []

    def alert_poster(url, *, json, timeout):
        alerts.append(json)
        return _Response({})

    with caplog.at_level("ERROR"):
        code = run(
            output_dir=tmp_path,
            today=date(2024, 10, 20),
            poster=lambda *args, **kwargs: _Response(_load("empty.json")),
            alert_poster=alert_poster,
        )
    assert code == 1
    assert alerts == []
    assert "0 rows" in caplog.text
    assert "ALERT_WEBHOOK_URL is not set" in caplog.text
    assert not (tmp_path / "lns14000000.csv").exists()


def test_pipeline_posts_alert_when_webhook_is_set(tmp_path, monkeypatch):
    alerts = []

    class _Hook:
        status_code = 200

    def alert_poster(url, *, json, timeout):
        alerts.append((url, json))
        return _Hook()

    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://example.com/hooks/alerts")
    code = run(
        output_dir=tmp_path,
        today=date(2024, 10, 20),
        poster=lambda *args, **kwargs: _Response(_load("schema_mismatch.json")),
        alert_poster=alert_poster,
    )
    assert code == 1
    assert alerts[0][0] == "https://example.com/hooks/alerts"
    assert "schema mismatch" in alerts[0][1]["text"]
    assert not (tmp_path / "lns14000000.csv").exists()


def test_stale_series_warns_and_still_writes(tmp_path, caplog):
    alerts = []

    def alert_poster(url, *, json, timeout):
        alerts.append(json)
        return _Response({})

    with caplog.at_level(logging.WARNING):
        code = run(
            output_dir=tmp_path,
            today=date(2025, 1, 15),
            poster=lambda *args, **kwargs: _Response(_load("success.json")),
            alert_poster=alert_poster,
        )
    assert code == 0
    assert alerts == []
    assert "older than 60 days" in caplog.text
    assert (tmp_path / "lns14000000.csv").exists()
    assert (tmp_path / "summary.json").exists()


def test_cli_uses_fixture_transport(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pipeline.fetch_payload",
        lambda *args, **kwargs: _load("success.json"),
    )
    code = main(
        [
            "--output-dir",
            str(tmp_path),
            "--as-of",
            "2024-10-20",
            "--series-id",
            DEFAULT_SERIES_ID,
        ]
    )
    assert code == 0
    assert (tmp_path / "summary.json").exists()


def test_schema_mismatch_fails_the_pipeline(tmp_path, caplog):
    with caplog.at_level("ERROR"):
        code = run(
            output_dir=tmp_path,
            today=date(2024, 10, 20),
            poster=lambda *args, **kwargs: _Response(_load("schema_mismatch.json")),
        )
    assert code == 1
    assert "schema mismatch" in caplog.text


class _Response:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload
