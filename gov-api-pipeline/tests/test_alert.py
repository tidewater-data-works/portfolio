"""alert.py posts when a webhook is set and exits non-zero otherwise."""

from __future__ import annotations

import logging

from alert import main, send_alert


def test_missing_webhook_logs_and_returns_nonzero(monkeypatch, caplog):
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    with caplog.at_level(logging.ERROR):
        code = send_alert("series check failed")
    assert code == 1
    assert "ALERT_WEBHOOK_URL is not set" in caplog.text
    assert "series check failed" in caplog.text


def test_cli_exits_nonzero_without_webhook(monkeypatch, caplog):
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    with caplog.at_level(logging.ERROR):
        code = main(["--message", "pipeline failed"])
    assert code == 1
    assert "pipeline failed" in caplog.text


def test_webhook_receives_json_and_returns_zero(monkeypatch):
    captured = {}

    class _Response:
        status_code = 204

    def poster(url, *, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://example.com/hooks/alerts")
    code = send_alert("data older than 60 days", series_id="LNS14000000", poster=poster)
    assert code == 0
    assert captured["url"] == "https://example.com/hooks/alerts"
    assert captured["json"]["text"] == "data older than 60 days"
    assert captured["json"]["source"] == "tidewater-data-works"
    assert captured["json"]["series_id"] == "LNS14000000"


def test_webhook_http_error_returns_nonzero(monkeypatch):
    class _Response:
        status_code = 500

    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://example.com/hooks/alerts")
    code = send_alert("schema mismatch", poster=lambda url, *, json, timeout: _Response())
    assert code == 1
