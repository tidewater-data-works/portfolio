"""Mocked model calls. Nothing in this file opens a socket."""

from __future__ import annotations

import json
from decimal import Decimal
from urllib.error import HTTPError

import pytest
from pydantic import ValidationError

from extract import extract_document
from llm import (
    SYSTEM_PROMPT,
    BackendUnavailable,
    MockLLMBackend,
    OpenAIChatBackend,
    default_post,
)
from schema import FieldsIn

CLEAN = "\n".join(
    [
        "RECEIPT",
        "Merchant: Blue Heron Printworks",
        "Receipt number: R-88421",
        "Receipt date: 2026-03-18",
        "Currency: USD",
        "Description                          Amount",
        "Pallet labels, roll                  14.00",
        "Total: 14.00",
    ]
)

SCRIPTED = {
    "document_type": "receipt",
    "vendor_name": "Blue Heron Printworks",
    "document_number": "R-88421",
    "document_date": "2026-03-18",
    "bill_to": None,
    "currency": "USD",
    "subtotal": "14.00",
    "tax": "0.00",
    "total": "14.00",
    "line_items": [
        {
            "description": "Pallet labels, roll",
            "quantity": None,
            "unit_price": None,
            "amount": "$14.00",
        }
    ],
}


def _completion(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def test_fields_reject_extra_keys_and_a_short_year():
    with pytest.raises(ValidationError):
        FieldsIn.model_validate({**SCRIPTED, "notes": "call the vendor"})
    with pytest.raises(ValidationError):
        FieldsIn.model_validate({**SCRIPTED, "document_date": "03/18/26"})


def test_scripted_backend_is_schema_checked_and_scored_as_llm():
    backend = MockLLMBackend({"slip": SCRIPTED})
    document = extract_document(CLEAN, document_id="slip", backend=backend)
    assert backend.calls == ["slip"]
    assert document.extractor == "llm"
    assert document.confidence == Decimal("0.97")
    assert document.needs_review is False
    assert document.confidence_flags == []
    assert document.line_items[0].amount == Decimal("14.00")


def test_missing_mock_response_falls_back_to_rules():
    backend = MockLLMBackend({})
    document = extract_document(CLEAN, document_id="slip", backend=backend)
    assert document.extractor == "rules"
    assert "llm_fallback" in document.confidence_flags
    assert document.vendor_name == "Blue Heron Printworks"
    assert document.needs_review is False


def test_openai_post_is_mocked_and_fenced_json_is_accepted():
    calls = []

    def post(url, *, headers, json, timeout):
        calls.append((url, headers, json, timeout))
        fenced = "```json\n" + __import__("json").dumps(SCRIPTED) + "\n```"
        return _completion(fenced)

    backend = OpenAIChatBackend(api_key="test-key", post=post)
    document = extract_document(CLEAN, document_id="slip", backend=backend)
    assert document.extractor == "llm"
    assert document.confidence == Decimal("0.97")
    url, headers, body, timeout = calls[0]
    assert url == "https://api.openai.com/v1/chat/completions"
    assert headers["Authorization"] == "Bearer test-key"
    assert body["temperature"] == 0
    assert body["messages"][0]["content"] == SYSTEM_PROMPT
    assert body["messages"][1]["content"] == CLEAN
    assert timeout == 30.0


def test_invalid_model_json_and_schema_fall_back_to_rules():
    def bad_json(url, *, headers, json, timeout):
        return _completion("not json")

    def extra_key(url, *, headers, json, timeout):
        return _completion(__import__("json").dumps({**SCRIPTED, "notes": "extra"}))

    invalid = extract_document(
        CLEAN,
        document_id="slip",
        backend=OpenAIChatBackend(api_key="test-key", post=bad_json),
    )
    rejected = extract_document(
        CLEAN,
        document_id="slip",
        backend=OpenAIChatBackend(api_key="test-key", post=extra_key),
    )
    assert invalid.extractor == "rules"
    assert "llm_invalid_response" in invalid.confidence_flags
    assert "llm_fallback" in invalid.confidence_flags
    assert rejected.extractor == "rules"
    assert "llm_schema_invalid" in rejected.confidence_flags
    assert rejected.document_number == "R-88421"


def test_missing_api_key_does_not_call_post(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    calls = []

    def post(url, *, headers, json, timeout):
        calls.append(url)
        return _completion("{}")

    backend = OpenAIChatBackend(post=post)
    with pytest.raises(BackendUnavailable):
        backend.extract_fields(CLEAN, document_id="slip")
    assert calls == []


def test_default_post_reads_a_mocked_http_response(monkeypatch):
    captured = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(_completion(json.dumps(SCRIPTED))).encode()

    def urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode())
        captured["authorization"] = request.get_header("Authorization")
        return _Response()

    monkeypatch.setattr("llm.urllib.request.urlopen", urlopen)
    backend = OpenAIChatBackend(api_key="test-key")
    payload = backend.extract_fields(CLEAN, document_id="slip")
    assert payload["document_number"] == "R-88421"
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["timeout"] == 30.0
    assert captured["authorization"] == "Bearer test-key"
    assert captured["body"]["temperature"] == 0


def test_default_post_turns_http_errors_into_transport_failures(monkeypatch):
    def urlopen(request, timeout):
        raise HTTPError(request.full_url, 503, "unavailable", hdrs=None, fp=None)

    monkeypatch.setattr("llm.urllib.request.urlopen", urlopen)
    with pytest.raises(OSError):
        default_post(
            "https://api.openai.com/v1/chat/completions",
            headers={},
            json={"messages": []},
            timeout=30,
        )
