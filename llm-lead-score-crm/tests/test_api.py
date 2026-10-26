"""FastAPI routes. The model and the CRM stay in-process."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import create_app
from crm import CrmError, HttpCrmClient, MemoryCrm
from llm import load_mock_file

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"

HOT = {
    "band": "Hot",
    "budget_stated": True,
    "company": "Cedar Wharf Logistics",
    "contact_role": "operations manager",
    "email": "mara.ellison@cedarwharf.example",
    "fit_reason": "Operations manager with an approved budget wants a nightly file in place within three weeks.",
    "intent": "ready_to_buy",
    "lead_id": "LD-2041",
    "model_confidence": "0.94",
    "score": 100,
    "timeline_days": 21,
}


def _leads() -> list[dict]:
    return json.loads((SAMPLES / "leads.json").read_text(encoding="utf-8"))


def test_qualify_accepts_four_and_quarantines_two():
    crm = MemoryCrm()
    client = TestClient(create_app(llm=load_mock_file(SAMPLES / "mock_responses.json"), crm=crm))
    first = client.post("/qualify", json={"leads": _leads()})
    assert first.status_code == 200
    body = first.json()
    assert [row["band"] for row in body["accepted"]] == ["Hot", "Warm", "Medium", "Cold"]
    assert [row["score"] for row in body["accepted"]] == [100, 75, 45, 0]
    assert [row["action"] for row in body["upserts"]] == ["created"] * 4
    assert [row["reason"] for row in body["quarantined"]] == [
        "low_model_confidence",
        "invalid_model_json",
    ]
    stored = set(crm.records)
    assert "riley.cho@harborpine.example" not in stored
    assert "sam.ortiz@blueheron.example" not in stored
    assert "note" not in crm.records["mara.ellison@cedarwharf.example"]

    second = client.post("/qualify", json={"leads": _leads()})
    assert second.status_code == 200
    assert [row["action"] for row in second.json()["upserts"]] == ["updated"] * 4
    assert len(crm.records) == 4


def test_unknown_lead_and_crm_outage_are_not_success_records():
    crm = MemoryCrm()
    client = TestClient(create_app(llm=load_mock_file(SAMPLES / "mock_responses.json"), crm=crm))
    missing = {
        "lead_id": "LD-4040",
        "email": "nobody@cedarwharf.example",
        "company": "Cedar Wharf Logistics",
        "note": "We need a nightly file next month.",
    }
    response = client.post("/qualify", json={"leads": [missing]})
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] == []
    assert body["upserts"] == []
    assert body["quarantined"][0]["reason"] == "model_unavailable"
    assert crm.records == {}

    class Boom:
        def upsert(self, contact):
            raise CrmError("down")

    outage = TestClient(create_app(llm=load_mock_file(SAMPLES / "mock_responses.json"), crm=Boom()))
    failed = outage.post("/qualify", json={"leads": _leads()})
    assert failed.status_code == 502
    assert "CRM upsert failed" in failed.json()["detail"]


def test_stub_upsert_is_idempotent_on_email():
    crm = MemoryCrm()
    client = TestClient(create_app(llm=load_mock_file(SAMPLES / "mock_responses.json"), crm=crm))
    created = client.post("/crm/contacts", json={**HOT, "idempotency_key": HOT["email"]})
    assert created.status_code == 200
    assert created.json() == {"action": "created", "email": HOT["email"]}
    changed = {**HOT, "band": "Warm", "score": 60, "idempotency_key": HOT["email"]}
    updated = client.post("/crm/contacts", json=changed)
    assert updated.json()["action"] == "updated"
    assert len(crm.records) == 1
    assert crm.records[HOT["email"]]["band"] == "Warm"
    assert "idempotency_key" not in crm.records[HOT["email"]]
    mismatch = client.post(
        "/crm/contacts",
        json={**HOT, "idempotency_key": "other@cedarwharf.example"},
    )
    assert mismatch.status_code == 400
    assert crm.records[HOT["email"]]["band"] == "Warm"


def test_http_client_posts_into_the_stub_without_a_socket():
    stub_crm = MemoryCrm()
    stub = TestClient(create_app(llm=load_mock_file(SAMPLES / "mock_responses.json"), crm=stub_crm))
    calls = []

    def post(url, *, headers, json, timeout):
        calls.append(json["email"])
        assert url == "https://crm.example.invalid/contacts"
        assert json["idempotency_key"] == json["email"]
        assert "note" not in json
        response = stub.post("/crm/contacts", json=json)
        assert response.status_code == 200
        return response.json()

    http = HttpCrmClient(url="https://crm.example.invalid/contacts", token="", post=post)
    client = TestClient(
        create_app(llm=load_mock_file(SAMPLES / "mock_responses.json"), crm=http)
    )
    first = client.post("/qualify", json={"leads": _leads()})
    second = client.post("/qualify", json={"leads": _leads()})
    assert first.status_code == 200
    assert [row["action"] for row in first.json()["upserts"]] == ["created"] * 4
    assert [row["action"] for row in second.json()["upserts"]] == ["updated"] * 4
    assert calls.count("riley.cho@harborpine.example") == 0
    assert calls.count("sam.ortiz@blueheron.example") == 0
    assert len(stub_crm.records) == 4


def test_health_and_a_spare_key_is_rejected():
    from app import app

    client = TestClient(create_app(llm=load_mock_file(SAMPLES / "mock_responses.json"), crm=MemoryCrm()))
    assert client.get("/health").json() == {"status": "ok"}
    response = client.post("/qualify", json={"leads": _leads(), "extra": True})
    assert response.status_code == 422
    default = TestClient(app)
    assert default.get("/health").json() == {"status": "ok"}
