"""Pluggable model backends.

``MockLLMBackend`` returns scripted JSON and is what tests and the offline
sample use. ``OpenAIChatBackend`` posts a chat completion only when
``OPENAI_API_KEY`` is set. Tests pass a stand-in for the HTTP call.
"""

from __future__ import annotations

import json as jsonlib
import os
import re
import urllib.error
import urllib.request
from typing import Any, Callable

SYSTEM_PROMPT = (
    "Extract one invoice or receipt into a JSON object with keys "
    "document_type, vendor_name, document_number, document_date, bill_to, "
    "currency, subtotal, tax, total, and line_items. "
    "document_type is invoice or receipt. "
    "document_date is YYYY-MM-DD or null. "
    "Money is a JSON string with two decimals and no currency symbol. "
    "line_items is a list of objects with description, quantity, unit_price, and amount. "
    "Use null when a value is not on the document. "
    "Do not invent line items. Respond with JSON only."
)
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_URL = "https://api.openai.com/v1/chat/completions"

Post = Callable[..., dict[str, Any]]


class BackendUnavailable(Exception):
    """The model backend cannot be called."""


class BackendResponseError(Exception):
    """The model backend returned a payload that is not JSON."""


class MockLLMBackend:
    """Deterministic stand-in keyed by document id."""

    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def extract_fields(self, document_text: str, *, document_id: str) -> dict:
        self.calls.append(document_id)
        if document_id not in self.responses:
            raise BackendUnavailable(f"no mock response for {document_id}")
        payload = self.responses[document_id]
        if not isinstance(payload, dict):
            raise BackendResponseError("mock response must be an object")
        return payload


class OpenAIChatBackend:
    """Chat-completions client. ``post`` replaces urllib in tests."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        url: str = DEFAULT_URL,
        timeout: float = 30.0,
        post: Post | None = None,
    ) -> None:
        if api_key is None:
            api_key = os.environ.get("OPENAI_API_KEY")
        self.api_key = (api_key or "").strip()
        self.model = model
        self.url = url
        self.timeout = timeout
        self._post = post or default_post

    def extract_fields(self, document_text: str, *, document_id: str) -> dict:
        del document_id
        if not self.api_key:
            raise BackendUnavailable("OPENAI_API_KEY is not set")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": document_text},
            ],
        }
        try:
            envelope = self._post(self.url, headers=headers, json=body, timeout=self.timeout)
        except OSError as exc:
            raise BackendUnavailable("model request failed") from exc
        return parse_completion(envelope)


def parse_completion(envelope: object) -> dict:
    if not isinstance(envelope, dict):
        raise BackendResponseError("chat completion had no message content")
    try:
        content = envelope["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise BackendResponseError("chat completion had no message content") from exc
    if not isinstance(content, str) or not content.strip():
        raise BackendResponseError("chat completion content was empty")
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = jsonlib.loads(text)
    except jsonlib.JSONDecodeError as exc:
        raise BackendResponseError("chat completion was not JSON") from exc
    if not isinstance(payload, dict):
        raise BackendResponseError("chat completion JSON must be an object")
    return payload


def default_post(url: str, *, headers: dict[str, str], json: dict, timeout: float) -> dict:
    """POST a JSON body with urllib. ``json`` is the request body, matching the test double."""
    data = jsonlib.dumps(json).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = jsonlib.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise OSError(f"HTTP {exc.code}") from exc
    if not isinstance(payload, dict):
        raise BackendResponseError("chat completion envelope must be an object")
    return payload
