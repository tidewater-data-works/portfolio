"""Batch qualification, quarantine, and the idempotent file upsert.

The model call and the CRM HTTP call are stand-ins. Nothing here opens a socket.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

import pytest

from crm import CrmError, HttpCrmClient, MemoryCrm
from llm import SYSTEM_PROMPT, MockLLMBackend, OpenAIChatBackend
from qualify import execute, load_backend, main, qualify_batch, qualify_one, upsert_all
from schema import InboundLead

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
PORTFOLIO = ROOT.parent / "README.md"

ACCEPTED = [
    "mara.ellison@cedarwharf.example",
    "jonah.peck@lanternmail.example",
    "priya.shah@ospreypress.example",
    "alex.kim@mailbox.example",
]
QUARANTINED = [
    "riley.cho@harborpine.example",
    "sam.ortiz@blueheron.example",
]


class SpyCrm:
    def __init__(self) -> None:
        self.emails: list[str] = []

    def upsert(self, contact: dict) -> dict:
        self.emails.append(contact["email"])
        if "note" in contact or "idempotency_key" in contact:
            raise AssertionError("CRM contact included a field it should not store")
        return {"action": "created", "email": contact["email"]}


def _boom(*args, **kwargs):
    raise AssertionError("network was called")


def _sample_args(tmp_path: Path, *, crm: Path | None = None, quarantine: Path | None = None):
    return [
        "--input",
        str(SAMPLES / "leads.json"),
        "--backend",
        "mock",
        "--mock-file",
        str(SAMPLES / "mock_responses.json"),
        "--crm",
        str(crm or (tmp_path / "crm.json")),
        "--quarantine",
        str(quarantine or (tmp_path / "quarantine.csv")),
    ]


def test_sample_run_matches_the_committed_files(tmp_path, monkeypatch):
    monkeypatch.setattr("llm.urllib.request.urlopen", _boom)
    monkeypatch.setattr("crm.urllib.request.urlopen", _boom)
    crm_path = tmp_path / "crm.json"
    quarantine_path = tmp_path / "quarantine.csv"
    code = main(_sample_args(tmp_path, crm=crm_path, quarantine=quarantine_path))
    assert code == 0
    assert crm_path.read_text(encoding="utf-8") == (SAMPLES / "crm_contacts.json").read_text(
        encoding="utf-8"
    )
    assert quarantine_path.read_text(encoding="utf-8") == (SAMPLES / "quarantine.csv").read_text(
        encoding="utf-8"
    )
    stored = json.loads(crm_path.read_text(encoding="utf-8"))
    assert list(stored) == sorted(ACCEPTED)
    assert [stored[email]["band"] for email in sorted(ACCEPTED)] == [
        "Cold",
        "Warm",
        "Hot",
        "Medium",
    ]
    assert stored["mara.ellison@cedarwharf.example"]["score"] == 100
    text = crm_path.read_text(encoding="utf-8")
    assert "6000 USD" not in text
    assert "class project" not in text
    for email in QUARANTINED:
        assert email not in stored
    rows = list(csv.DictReader(quarantine_path.open(encoding="utf-8")))
    assert [row["reason"] for row in rows] == ["low_model_confidence", "invalid_model_json"]
    assert "band" not in rows[0]


def test_second_run_updates_the_same_emails(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    crm_path = tmp_path / "crm.json"
    first = main(_sample_args(tmp_path, crm=crm_path))
    snapshot = crm_path.read_bytes()
    second = main(_sample_args(tmp_path, crm=crm_path))
    assert first == 0
    assert second == 0
    assert crm_path.read_bytes() == snapshot
    assert len(json.loads(snapshot)) == 4
    messages = [record.message for record in caplog.records if record.name == "qualify"]
    assert "upserted 4 (4 created, 0 updated), quarantined 2" in messages
    assert "upserted 4 (0 created, 4 updated), quarantined 2" in messages


def test_quarantine_rows_are_not_offered_to_the_crm():
    leads = [InboundLead.model_validate(item) for item in json.loads((SAMPLES / "leads.json").read_text())]
    llm = load_backend("mock", SAMPLES / "mock_responses.json")
    accepted, quarantined = qualify_batch(leads, llm)
    spy = SpyCrm()
    results = upsert_all(accepted, spy)
    assert spy.emails == ACCEPTED
    assert [row["action"] for row in results] == ["created"] * 4
    assert [row.reason for row in quarantined] == ["low_model_confidence", "invalid_model_json"]
    assert llm.calls == ["LD-2041", "LD-2042", "LD-2043", "LD-2044", "LD-2045", "LD-2046"]


def test_bad_email_does_not_call_the_model():
    class Exploding:
        def qualify(self, lead):
            raise AssertionError("model was called")

    lead = InboundLead(
        lead_id="LD-9",
        email="not-an-email",
        company="Cedar Wharf Logistics",
        note="We need a nightly file.",
    )
    outcome = qualify_one(lead, Exploding())
    assert outcome.reason == "missing_lead_key"
    assert outcome.email == "not-an-email"


def test_a_band_in_the_payload_is_quarantined_and_not_upserted():
    lead = InboundLead(
        lead_id="LD-8",
        email="Mara.Ellison@cedarwharf.example",
        company="Cedar Wharf Logistics",
        note="Budget is approved.",
    )
    payload = {
        "intent": "ready_to_buy",
        "budget_stated": True,
        "timeline_days": 21,
        "contact_role": "operations manager",
        "fit_reason": "Approved budget and a three-week start.",
        "model_confidence": 0.94,
        "band": "Hot",
    }
    outcome = qualify_one(lead, MockLLMBackend({"LD-8": payload}))
    assert outcome.reason == "schema_invalid"
    spy = SpyCrm()
    accepted, quarantined = qualify_batch([lead], MockLLMBackend({"LD-8": payload}))
    assert accepted == []
    assert quarantined[0].reason == "schema_invalid"
    assert upsert_all(accepted, spy) == []
    assert spy.emails == []


def test_low_confidence_is_quarantined_even_when_the_schema_is_valid():
    lead = InboundLead(
        lead_id="LD-7",
        email="riley.cho@harborpine.example",
        company="Harbor & Pine",
        note="hi",
    )
    payload = {
        "intent": "browsing",
        "budget_stated": False,
        "timeline_days": None,
        "contact_role": None,
        "fit_reason": "The note does not describe a project.",
        "model_confidence": "0.694",
    }
    outcome = qualify_one(lead, MockLLMBackend({"LD-7": payload}))
    assert outcome.reason == "low_model_confidence"
    assert outcome.detail == "model_confidence 0.69 is below 0.70"
    passed = qualify_one(
        lead,
        MockLLMBackend({"LD-7": {**payload, "model_confidence": "0.695"}}),
    )
    assert passed.email == "riley.cho@harborpine.example"
    assert passed.model_confidence == "0.70"


def test_crm_failure_keeps_the_quarantine_and_exits_1(tmp_path, monkeypatch):
    monkeypatch.setattr("crm.urllib.request.urlopen", _boom)
    crm_path = tmp_path / "crm.json"
    quarantine_path = tmp_path / "quarantine.csv"

    class Boom:
        def upsert(self, contact):
            raise CrmError("down")

    code = execute(
        input_path=SAMPLES / "leads.json",
        crm_path=crm_path,
        quarantine_path=quarantine_path,
        llm=load_backend("mock", SAMPLES / "mock_responses.json"),
        crm=Boom(),
    )
    assert code == 1
    assert not crm_path.exists()
    rows = list(csv.DictReader(quarantine_path.open(encoding="utf-8")))
    assert [row["lead_id"] for row in rows] == ["LD-2045", "LD-2046"]


def test_a_mid_batch_crm_failure_leaves_a_rerun_safe_file(tmp_path):
    from crm import FileCrm

    crm_path = tmp_path / "crm.json"
    store = FileCrm(crm_path)

    class FailAfter:
        def __init__(self) -> None:
            self.seen = 0

        def upsert(self, contact):
            if self.seen >= 1:
                raise CrmError("down")
            self.seen += 1
            return store.upsert(contact)

    code = execute(
        input_path=SAMPLES / "leads.json",
        crm_path=crm_path,
        quarantine_path=tmp_path / "quarantine.csv",
        llm=load_backend("mock", SAMPLES / "mock_responses.json"),
        crm=FailAfter(),
    )
    assert code == 1
    partial = json.loads(crm_path.read_text(encoding="utf-8"))
    assert list(partial) == ["mara.ellison@cedarwharf.example"]
    again = execute(
        input_path=SAMPLES / "leads.json",
        crm_path=crm_path,
        quarantine_path=tmp_path / "quarantine.csv",
        llm=load_backend("mock", SAMPLES / "mock_responses.json"),
    )
    assert again == 0
    stored = json.loads(crm_path.read_text(encoding="utf-8"))
    assert set(stored) == set(ACCEPTED)


def test_unavailable_model_does_not_rewrite_an_existing_crm(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("llm.urllib.request.urlopen", _boom)
    crm_path = tmp_path / "crm.json"
    crm_path.write_text('{"keep@example.com": {"email": "keep@example.com"}}\n', encoding="utf-8")
    code = main(
        [
            "--input",
            str(SAMPLES / "leads.json"),
            "--backend",
            "openai",
            "--crm",
            str(crm_path),
            "--quarantine",
            str(tmp_path / "quarantine.csv"),
        ]
    )
    assert code == 0
    assert json.loads(crm_path.read_text(encoding="utf-8")) == {
        "keep@example.com": {"email": "keep@example.com"}
    }
    rows = list(csv.DictReader((tmp_path / "quarantine.csv").open(encoding="utf-8")))
    assert len(rows) == 6
    assert {row["reason"] for row in rows} == {"model_unavailable"}


def test_fenced_model_json_is_upserted_without_a_socket():
    lead = InboundLead.model_validate(json.loads((SAMPLES / "leads.json").read_text())[0])
    scripted = json.loads((SAMPLES / "mock_responses.json").read_text())["LD-2041"]
    calls = []

    def post(url, *, headers, json, timeout):
        calls.append((url, headers, json, timeout))
        content = "```json\n" + __import__("json").dumps(scripted) + "\n```"
        return {"choices": [{"message": {"content": content}}]}

    backend = OpenAIChatBackend(api_key="test-key", post=post)
    crm = MemoryCrm()
    accepted, quarantined = qualify_batch([lead], backend)
    result = upsert_all(accepted, crm)
    assert quarantined == []
    assert result == [{"action": "created", "email": "mara.ellison@cedarwharf.example"}]
    assert crm.records["mara.ellison@cedarwharf.example"]["band"] == "Hot"
    url, headers, body, timeout = calls[0]
    assert url == "https://api.openai.com/v1/chat/completions"
    assert headers["Authorization"] == "Bearer test-key"
    assert body["temperature"] == 0
    assert body["messages"][0]["content"] == SYSTEM_PROMPT
    assert "Do not include a score or a band." in body["messages"][0]["content"]
    assert "6000 USD" in body["messages"][1]["content"]
    assert timeout == 30.0
    again = upsert_all(accepted, crm)
    assert again == [{"action": "updated", "email": "mara.ellison@cedarwharf.example"}]
    assert len(crm.records) == 1


def test_http_crm_client_sends_the_email_as_the_idempotency_key():
    contact = {
        "band": "Hot",
        "budget_stated": True,
        "company": "Cedar Wharf Logistics",
        "contact_role": "operations manager",
        "email": "mara.ellison@cedarwharf.example",
        "fit_reason": "Approved budget.",
        "intent": "ready_to_buy",
        "lead_id": "LD-2041",
        "model_confidence": "0.94",
        "score": 100,
        "timeline_days": 21,
    }
    calls = []

    def post(url, *, headers, json, timeout):
        calls.append((url, headers, json, timeout))
        if len(calls) == 1:
            return {"action": "created", "email": json["email"]}
        return {"action": "updated", "email": json["email"]}

    client = HttpCrmClient(url="https://crm.example.invalid/contacts", token="test-token", post=post)
    assert client.upsert(contact)["action"] == "created"
    assert client.upsert(contact)["action"] == "updated"
    url, headers, body, timeout = calls[0]
    assert url == "https://crm.example.invalid/contacts"
    assert headers["Authorization"] == "Bearer test-token"
    assert body["idempotency_key"] == contact["email"]
    assert "note" not in body
    assert timeout == 10.0

    def refused(url, *, headers, json, timeout):
        raise OSError("HTTP 500")

    def wrong_email(url, *, headers, json, timeout):
        return {"action": "created", "email": "other@example.com"}

    with pytest.raises(CrmError, match="mara.ellison@cedarwharf.example"):
        HttpCrmClient(token="", post=refused).upsert(contact)
    with pytest.raises(CrmError, match="mara.ellison@cedarwharf.example"):
        HttpCrmClient(token="", post=wrong_email).upsert(contact)


def test_strict_and_bad_input_exit_1(tmp_path):
    assert main(_sample_args(tmp_path) + ["--strict"]) == 1
    assert (tmp_path / "quarantine.csv").exists()
    empty = tmp_path / "empty.json"
    empty.write_text("[]\n", encoding="utf-8")
    assert (
        main(
            [
                "--input",
                str(empty),
                "--crm",
                str(tmp_path / "crm.json"),
                "--quarantine",
                str(tmp_path / "q.csv"),
            ]
        )
        == 1
    )
    assert (
        main(
            [
                "--input",
                str(SAMPLES / "leads.json"),
                "--backend",
                "mock",
                "--crm",
                str(tmp_path / "crm.json"),
                "--quarantine",
                str(tmp_path / "q.csv"),
            ]
        )
        == 1
    )


def test_readme_and_make_test_include_this_demo():
    demo = (ROOT / "README.md").read_text(encoding="utf-8")
    for heading in ("## Problem", "## Approach", "## Run", "## Sample output", "## Tests"):
        assert heading in demo
    assert "0.70" in demo
    assert "mara.ellison@cedarwharf.example" in demo
    assert "low_model_confidence" in demo
    assert "idempotent" in demo.casefold()
    assert "fictional" in demo.casefold()
    portfolio = PORTFOLIO.read_text(encoding="utf-8")
    assert "](llm-lead-score-crm/)" in portfolio
    assert "llm-lead-score-crm/README.md" in portfolio
    makefile = (ROOT.parent / "Makefile").read_text(encoding="utf-8")
    assert "llm-lead-score-crm/requirements-dev.txt" in makefile
    assert "llm-lead-score-crm &&" in makefile
