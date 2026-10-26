"""Pluggable model backends.

``MockLLMBackend`` replays scripted JSON and is what the sample and the tests
use. ``OpenAIChatBackend`` posts a chat completion only when ``OPENAI_API_KEY``
is set. Tests pass a stand-in for the HTTP call.
"""

from __future__ import annotations

import json as jsonlib
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from schema import InboundLead

SYSTEM_PROMPT = (
    "Qualify one inbound lead. Respond with a JSON object only, with keys "
    "intent, budget_stated, timeline_days, contact_role, fit_reason, and "
    "model_confidence. "
    "intent is one of ready_to_buy, evaluating, browsing, not_a_fit. "
    "budget_stated is true only when the note states a budget or an approved amount. "
    "timeline_days is an integer count of days until they want to start, or null. "
    "contact_role is a short role, or null. "
    "fit_reason is one sentence grounded in the note. "
    "model_confidence is a number from 0 to 1 for how complete the note is. "
    "Do not include a score or a band. Do not invent a budget, a date, or a role."
)
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_URL = "https://api.openai.com/v1/chat/completions"
USER_AGENT = "TidewaterDataWorks/1.0 (+https://github.com/tidewater-data-works/portfolio)"

Post = Callable[..., dict[str, Any]]


class BackendUnavailable(Exception):
    """The model backend cannot be called."""


class BackendResponseError(Exception):
    """The model backend returned a payload that is not a JSON object."""


def parse_model_text(text: str) -> dict:
    raw = text.strip()
    if not raw:
        raise BackendResponseError("model response was empty")
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        payload = jsonlib.loads(raw)
    except jsonlib.JSONDecodeError as exc:
        raise BackendResponseError("model response was not JSON") from exc
    if not isinstance(payload, dict):
        raise BackendResponseError("model response JSON must be an object")
    return payload


def parse_completion(envelope: object) -> dict:
    if not isinstance(envelope, dict):
        raise BackendResponseError("chat completion had no message content")
    try:
        content = envelope["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise BackendResponseError("chat completion had no message content") from exc
    if not isinstance(content, str):
        raise BackendResponseError("chat completion content was empty")
    return parse_model_text(content)


class MockLLMBackend:
    """Deterministic stand-in keyed by lead id.

    An object value is the qualification. A string value is raw model text,
    so a fixture can script a non-JSON reply.
    """

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def qualify(self, lead: InboundLead) -> dict:
        self.calls.append(lead.lead_id)
        if lead.lead_id not in self.responses:
            raise BackendUnavailable(f"no mock response for {lead.lead_id}")
        payload = self.responses[lead.lead_id]
        if isinstance(payload, str):
            return parse_model_text(payload)
        if not isinstance(payload, dict):
            raise BackendResponseError("mock response must be an object or a string")
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

    def qualify(self, lead: InboundLead) -> dict:
        if not self.api_key:
            raise BackendUnavailable("OPENAI_API_KEY is not set")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _user_message(lead)},
            ],
        }
        try:
            envelope = self._post(self.url, headers=headers, json=body, timeout=self.timeout)
        except OSError as exc:
            raise BackendUnavailable("model request failed") from exc
        return parse_completion(envelope)


def load_mock_file(path: Path) -> MockLLMBackend:
    payload = jsonlib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("mock file must be a JSON object")
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, (dict, str)):
            raise ValueError("mock values must be objects or strings")
    return MockLLMBackend(payload)


def default_post(url: str, *, headers: dict[str, str], json: dict, timeout: float) -> dict:
    """POST a JSON body with urllib. ``json`` is the request body, matching the test double."""
    data = jsonlib.dumps(json).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise OSError(f"HTTP {exc.code}") from exc
    try:
        payload = jsonlib.loads(raw)
    except jsonlib.JSONDecodeError as exc:
        raise BackendResponseError("chat completion was not JSON") from exc
    if not isinstance(payload, dict):
        raise BackendResponseError("chat completion envelope must be an object")
    return payload


def _user_message(lead: InboundLead) -> str:
    return (
        f"lead_id: {lead.lead_id}\n"
        f"email: {lead.email}\n"
        f"company: {lead.company}\n"
        f"note:\n{lead.note}"
    )
