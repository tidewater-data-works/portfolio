"""CRM upsert stub, keyed by email.

``MemoryCrm`` is the in-process store behind the FastAPI route.
``FileCrm`` rewrites a JSON object and is what the sample command writes.
``HttpCrmClient`` POSTs one contact. Tests pass a stand-in for that call.
A second upsert of the same email updates the stored contact.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

USER_AGENT = "TidewaterDataWorks/1.0 (+https://github.com/tidewater-data-works/portfolio)"
DEFAULT_URL = "https://crm.example.invalid/contacts"

Post = Callable[..., dict[str, Any]]


class CrmError(Exception):
    """The CRM upsert did not land."""


class MemoryCrm:
    """In-process contacts keyed by email."""

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}

    def upsert(self, contact: dict) -> dict:
        email = contact.get("email")
        if not isinstance(email, str) or not email:
            raise CrmError("contact is missing email")
        action = "updated" if email in self.records else "created"
        self.records[email] = dict(contact)
        return {"action": action, "email": email}


class FileCrm:
    """JSON object of contacts keyed by email. Replaced with ``os.replace``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.records: dict[str, dict] = {}
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise CrmError("CRM file must be a JSON object")
            self.records = payload

    def upsert(self, contact: dict) -> dict:
        email = contact.get("email")
        if not isinstance(email, str) or not email:
            raise CrmError("contact is missing email")
        action = "updated" if email in self.records else "created"
        self.records[email] = dict(contact)
        self._flush()
        return {"action": action, "email": email}

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.records, indent=2, sort_keys=True) + "\n"
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, self.path)


class HttpCrmClient:
    """POST one contact. ``post`` replaces urllib in tests.

    The body is the contact plus ``idempotency_key`` set to the email.
    ``CRM_API_TOKEN`` is sent only when it is non-empty. This repo does not
    contain a token.
    """

    def __init__(
        self,
        *,
        url: str = DEFAULT_URL,
        token: str | None = None,
        timeout: float = 10.0,
        post: Post | None = None,
    ) -> None:
        if token is None:
            token = os.environ.get("CRM_API_TOKEN", "")
        self.url = url
        self.token = token.strip()
        self.timeout = timeout
        self._post = post or default_post

    def upsert(self, contact: dict) -> dict:
        email = contact.get("email")
        if not isinstance(email, str) or not email:
            raise CrmError("contact is missing email")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = dict(contact)
        body["idempotency_key"] = email
        try:
            payload = self._post(self.url, headers=headers, json=body, timeout=self.timeout)
        except OSError as exc:
            raise CrmError(f"CRM upsert failed for {email}") from exc
        return _accepted(payload, email)


def _accepted(payload: object, email: str) -> dict:
    if not isinstance(payload, dict):
        raise CrmError(f"CRM upsert failed for {email}")
    if payload.get("email") != email:
        raise CrmError(f"CRM upsert failed for {email}")
    action = payload.get("action")
    if action not in ("created", "updated"):
        raise CrmError(f"CRM upsert failed for {email}")
    return {"action": action, "email": email}


def default_post(url: str, *, headers: dict[str, str], json: dict, timeout: float) -> dict:
    """POST a JSON body with urllib. ``json`` is the request body, matching the test double."""
    data = json.dumps(json).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise OSError(f"HTTP {exc.code}") from exc
    except json.JSONDecodeError as exc:
        raise CrmError("CRM response was not JSON") from exc
    if not isinstance(payload, dict):
        raise CrmError("CRM response was not a JSON object")
    return payload
