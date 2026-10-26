"""FastAPI wrapper around ``qualify_batch``.

``POST /qualify`` scores a batch and upserts the contacts that clear the gate.
``POST /crm/contacts`` is the stub contract: one contact, matched on email.
The default app loads the sample mock and an empty memory store, so importing
it does not need an API key and does not open a socket.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException

from crm import CrmError, MemoryCrm
from llm import load_mock_file
from qualify import qualify_batch, upsert_all
from schema import CrmUpsertRequest, QualifyRequest, QualifyResponse

SAMPLE_MOCK = Path(__file__).resolve().parent / "samples" / "mock_responses.json"


def create_app(*, llm=None, crm=None) -> FastAPI:
    if llm is None:
        llm = load_mock_file(SAMPLE_MOCK)
    if crm is None:
        crm = MemoryCrm()

    app = FastAPI(
        title="Lead qualification",
        summary="Score structured model JSON and upsert a CRM stub by email.",
    )
    app.state.llm = llm
    app.state.crm = crm

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/qualify", response_model=QualifyResponse)
    def qualify(body: QualifyRequest) -> QualifyResponse:
        accepted, quarantined = qualify_batch(body.leads, app.state.llm)
        try:
            upserts = upsert_all(accepted, app.state.crm)
        except CrmError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return QualifyResponse.model_validate(
            {
                "accepted": accepted,
                "quarantined": [row.as_dict() for row in quarantined],
                "upserts": upserts,
            }
        )

    @app.post("/crm/contacts")
    def crm_contacts(body: CrmUpsertRequest) -> dict[str, str]:
        if body.idempotency_key != body.email:
            raise HTTPException(status_code=400, detail="idempotency_key must match email")
        contact = body.model_dump(exclude={"idempotency_key"})
        try:
            result = app.state.crm.upsert(contact)
        except CrmError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return result

    return app


app = create_app()
