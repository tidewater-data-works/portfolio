"""Structured qualification, point score, and the CRM contact contract.

The model does not choose the band. ``score_qualification`` does, from the
fields that survived schema checks. Confidence is rounded half up to two
decimals before it is compared with ``MIN_MODEL_CONFIDENCE``.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Intent = Literal["ready_to_buy", "evaluating", "browsing", "not_a_fit"]
Band = Literal["Hot", "Warm", "Medium", "Cold"]
QuarantineReason = Literal[
    "missing_lead_key",
    "model_unavailable",
    "invalid_model_json",
    "schema_invalid",
    "low_model_confidence",
]

INTENT_POINTS = {
    "ready_to_buy": 45,
    "evaluating": 30,
    "browsing": 15,
    "not_a_fit": 0,
}
BUDGET_POINTS = 25
ROLE_POINTS = 5
MIN_MODEL_CONFIDENCE = Decimal("0.70")
MAX_TIMELINE_DAYS = 3650
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CONFIDENCE_RE = re.compile(r"^(?:0\.\d{2}|1\.00)$")

# Inclusive lower bounds, highest first. Anything below the last floor is Cold.
BAND_FLOORS: tuple[tuple[int, Band], ...] = (
    (80, "Hot"),
    (55, "Warm"),
    (30, "Medium"),
)


def _blank(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def normalize_email(value: object) -> str | None:
    """Lowercase an email, or return None when it cannot be an upsert key."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if EMAIL_RE.fullmatch(text) is None:
        return None
    return text


def format_confidence(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}"


def timeline_points(days: int | None) -> int:
    if days is None:
        return 0
    if days <= 30:
        return 25
    if days <= 90:
        return 15
    if days <= 180:
        return 5
    return 0


def band_for(score: int) -> Band:
    for floor, name in BAND_FLOORS:
        if score >= floor:
            return name
    return "Cold"


def score_qualification(fields: ModelQualification) -> tuple[int, Band]:
    points = INTENT_POINTS[fields.intent]
    if fields.budget_stated:
        points += BUDGET_POINTS
    points += timeline_points(fields.timeline_days)
    if fields.contact_role:
        points += ROLE_POINTS
    if points > 100:
        points = 100
    if points < 0:
        points = 0
    return points, band_for(points)


class InboundLead(BaseModel):
    """One note from a form or an inbox. The email is checked later, per row."""

    model_config = ConfigDict(extra="forbid")

    lead_id: str = Field(min_length=1, max_length=40)
    email: str = Field(min_length=1, max_length=200)
    company: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=1, max_length=4000)

    @field_validator("lead_id", "company", "note", mode="before")
    @classmethod
    def _required_text(cls, value: object) -> str:
        text = _blank(value)
        if text is None:
            raise ValueError("empty")
        return text

    @field_validator("email", mode="before")
    @classmethod
    def _email_text(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("empty email")
        return value.strip()


class ModelQualification(BaseModel):
    """Fields the model is allowed to return. A band or a score is rejected."""

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    budget_stated: bool
    timeline_days: int | None = None
    contact_role: str | None = None
    fit_reason: str = Field(min_length=1, max_length=400)
    model_confidence: Decimal

    @field_validator("budget_stated", mode="before")
    @classmethod
    def _budget(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        raise ValueError("budget_stated must be a boolean")

    @field_validator("timeline_days", mode="before")
    @classmethod
    def _days(cls, value: object) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("timeline_days must be an integer or null")
        if value < 0 or value > MAX_TIMELINE_DAYS:
            raise ValueError("timeline_days is out of range")
        return value

    @field_validator("contact_role", mode="before")
    @classmethod
    def _role(cls, value: object) -> str | None:
        text = _blank(value)
        if text is not None and len(text) > 80:
            raise ValueError("contact_role is too long")
        return text

    @field_validator("fit_reason", mode="before")
    @classmethod
    def _reason(cls, value: object) -> str:
        text = _blank(value)
        if text is None:
            raise ValueError("fit_reason is empty")
        return text

    @field_validator("model_confidence", mode="before")
    @classmethod
    def _confidence_in(cls, value: object) -> Decimal:
        if isinstance(value, bool) or value is None:
            raise ValueError("model_confidence must be a number")
        if isinstance(value, str) and not value.strip():
            raise ValueError("model_confidence must be a number")
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("model_confidence must be a number") from exc
        return parsed

    @field_validator("model_confidence")
    @classmethod
    def _confidence_range(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("model_confidence must be finite")
        quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if quantized == 0:
            quantized = Decimal("0.00")
        if quantized < 0 or quantized > 1:
            raise ValueError("model_confidence must be between 0 and 1")
        return quantized


class CrmContact(BaseModel):
    """A contact the CRM stub is willing to store. There is no raw note."""

    model_config = ConfigDict(extra="forbid")

    band: Band
    budget_stated: bool
    company: str
    contact_role: str | None
    email: str
    fit_reason: str
    intent: Intent
    lead_id: str
    model_confidence: str
    score: int = Field(ge=0, le=100)
    timeline_days: int | None

    @field_validator("model_confidence")
    @classmethod
    def _two_decimals(cls, value: str) -> str:
        if CONFIDENCE_RE.fullmatch(value) is None:
            raise ValueError("model_confidence must be two decimal places")
        return value

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        normalized = normalize_email(value)
        if normalized is None or normalized != value:
            raise ValueError("email is not a normalized upsert key")
        return value


class CrmUpsertRequest(CrmContact):
    idempotency_key: str


class QuarantineRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lead_id: str
    email: str
    company: str
    reason: QuarantineReason
    detail: str


class UpsertResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["created", "updated"]
    email: str


class QualifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    leads: list[InboundLead] = Field(min_length=1)


class QualifyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: list[CrmContact]
    quarantined: list[QuarantineRecord]
    upserts: list[UpsertResult]
