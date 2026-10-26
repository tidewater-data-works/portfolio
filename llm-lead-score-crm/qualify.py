"""Qualify inbound leads and upsert the ones that clear the gate.

The model returns structured JSON. This module scores that JSON. Invalid or
low-confidence output is quarantined and is not passed to the CRM. The
quarantine file is written before any upsert, so a CRM failure still leaves
the review file on disk.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from crm import CrmError, FileCrm
from llm import (
    BackendResponseError,
    BackendUnavailable,
    OpenAIChatBackend,
    load_mock_file,
)
from schema import (
    MIN_MODEL_CONFIDENCE,
    CrmContact,
    InboundLead,
    ModelQualification,
    format_confidence,
    normalize_email,
    score_qualification,
)

logger = logging.getLogger(__name__)

QUARANTINE_COLUMNS = ["lead_id", "email", "company", "reason", "detail"]


@dataclass(frozen=True)
class QuarantineRow:
    lead_id: str
    email: str
    company: str
    reason: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {
            "lead_id": self.lead_id,
            "email": self.email,
            "company": self.company,
            "reason": self.reason,
            "detail": self.detail,
        }


def qualify_one(lead: InboundLead, llm) -> CrmContact | QuarantineRow:
    """Return a contact, or a quarantine row. This does not call the CRM."""
    email = normalize_email(lead.email)
    if email is None:
        logger.info("quarantined %s (missing_lead_key)", lead.lead_id)
        return _row(lead, "missing_lead_key", "email is missing or not usable as an upsert key")
    try:
        payload = llm.qualify(lead)
    except BackendUnavailable as exc:
        logger.info("quarantined %s (model_unavailable)", lead.lead_id)
        return _row(lead, "model_unavailable", str(exc))
    except BackendResponseError as exc:
        logger.info("quarantined %s (invalid_model_json)", lead.lead_id)
        return _row(lead, "invalid_model_json", str(exc))
    try:
        fields = ModelQualification.model_validate(payload)
    except ValidationError:
        logger.info("quarantined %s (schema_invalid)", lead.lead_id)
        return _row(lead, "schema_invalid", "model payload did not match the qualification schema")
    if fields.model_confidence < MIN_MODEL_CONFIDENCE:
        shown = format_confidence(fields.model_confidence)
        floor = format_confidence(MIN_MODEL_CONFIDENCE)
        logger.info("quarantined %s (low_model_confidence)", lead.lead_id)
        return _row(
            lead,
            "low_model_confidence",
            f"model_confidence {shown} is below {floor}",
        )
    score, band = score_qualification(fields)
    contact = {
        "band": band,
        "budget_stated": fields.budget_stated,
        "company": lead.company,
        "contact_role": fields.contact_role,
        "email": email,
        "fit_reason": fields.fit_reason,
        "intent": fields.intent,
        "lead_id": lead.lead_id,
        "model_confidence": format_confidence(fields.model_confidence),
        "score": score,
        "timeline_days": fields.timeline_days,
    }
    return CrmContact.model_validate(contact)


def qualify_batch(leads: list[InboundLead], llm) -> tuple[list[dict], list[QuarantineRow]]:
    accepted: list[dict] = []
    quarantined: list[QuarantineRow] = []
    for lead in leads:
        outcome = qualify_one(lead, llm)
        if isinstance(outcome, QuarantineRow):
            quarantined.append(outcome)
            continue
        accepted.append(outcome.model_dump())
    quarantined.sort(key=lambda row: row.lead_id)
    return accepted, quarantined


def upsert_all(contacts: list[dict], crm) -> list[dict]:
    results = []
    for contact in contacts:
        email = contact["email"]
        try:
            result = crm.upsert(contact)
        except CrmError as exc:
            raise CrmError(f"CRM upsert failed for {email}") from exc
        if not isinstance(result, dict) or result.get("email") != email:
            raise CrmError(f"CRM upsert failed for {email}")
        if result.get("action") not in ("created", "updated"):
            raise CrmError(f"CRM upsert failed for {email}")
        results.append({"action": result["action"], "email": email})
    return results


def write_quarantine(path: Path, rows: list[QuarantineRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=lambda row: row.lead_id)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=QUARANTINE_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(row.as_dict() for row in ordered)


def load_leads(path: Path) -> list[InboundLead]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("lead file must be a JSON list")
    return [InboundLead.model_validate(item) for item in payload]


def execute(
    *,
    input_path: Path,
    crm_path: Path,
    quarantine_path: Path,
    llm,
    crm=None,
    strict: bool = False,
) -> int:
    if not input_path.is_file():
        logger.error("input file does not exist: %s", input_path)
        return 1
    try:
        leads = load_leads(input_path)
    except (OSError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        logger.error("%s", exc)
        return 1
    if not leads:
        logger.error("no leads in %s", input_path)
        return 1
    accepted, quarantined = qualify_batch(leads, llm)
    try:
        write_quarantine(quarantine_path, quarantined)
    except OSError as exc:
        logger.error("%s", exc)
        return 1
    try:
        results = _upsert(accepted, crm=crm, crm_path=crm_path)
    except (CrmError, OSError, json.JSONDecodeError) as exc:
        logger.error("%s", exc)
        return 1
    created = sum(1 for result in results if result["action"] == "created")
    updated = sum(1 for result in results if result["action"] == "updated")
    logger.info(
        "upserted %s (%s created, %s updated), quarantined %s",
        len(results),
        created,
        updated,
        len(quarantined),
    )
    if strict and quarantined:
        logger.error("%s lead(s) quarantined", len(quarantined))
        return 1
    return 0


def load_backend(name: str, mock_file: Path | None):
    if name == "mock":
        if mock_file is None:
            raise ValueError("--mock-file is required with --backend mock")
        return load_mock_file(mock_file)
    if name == "openai":
        return OpenAIChatBackend()
    raise ValueError(f"unknown backend: {name}")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Qualify leads and upsert a CRM stub.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--crm", type=Path, required=True)
    parser.add_argument("--quarantine", type=Path, required=True)
    parser.add_argument("--backend", choices=("mock", "openai"), default="mock")
    parser.add_argument("--mock-file", type=Path)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 when any lead is quarantined.",
    )
    args = parser.parse_args(argv)
    try:
        llm = load_backend(args.backend, args.mock_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.error("%s", exc)
        return 1
    return execute(
        input_path=args.input,
        crm_path=args.crm,
        quarantine_path=args.quarantine,
        llm=llm,
        strict=args.strict,
    )


def _upsert(accepted: list[dict], *, crm, crm_path: Path) -> list[dict]:
    if crm is None:
        if not accepted:
            return []
        crm = FileCrm(crm_path)
    return upsert_all(accepted, crm)


def _row(lead: InboundLead, reason: str, detail: str) -> QuarantineRow:
    return QuarantineRow(
        lead_id=lead.lead_id,
        email=lead.email.strip(),
        company=lead.company,
        reason=reason,
        detail=detail,
    )


if __name__ == "__main__":
    raise SystemExit(main())
