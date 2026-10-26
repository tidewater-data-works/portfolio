"""Point table, band edges, and schema rejections. No CRM and no model call."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from schema import (
    BUDGET_POINTS,
    INTENT_POINTS,
    ROLE_POINTS,
    ModelQualification,
    band_for,
    score_qualification,
    timeline_points,
)

GOOD = {
    "intent": "ready_to_buy",
    "budget_stated": True,
    "timeline_days": 21,
    "contact_role": "operations manager",
    "fit_reason": "Operations manager with an approved budget wants to start within three weeks.",
    "model_confidence": "0.94",
}


def test_point_table_and_band_edges():
    assert timeline_points(None) == 0
    assert timeline_points(0) == 25
    assert timeline_points(30) == 25
    assert timeline_points(31) == 15
    assert timeline_points(90) == 15
    assert timeline_points(91) == 5
    assert timeline_points(180) == 5
    assert timeline_points(181) == 0
    assert (
        INTENT_POINTS["ready_to_buy"] + BUDGET_POINTS + timeline_points(0) + ROLE_POINTS == 100
    )
    assert [band_for(score) for score in (100, 80, 79, 55, 54, 30, 29, 0)] == [
        "Hot",
        "Hot",
        "Warm",
        "Warm",
        "Medium",
        "Medium",
        "Cold",
        "Cold",
    ]


def test_sample_archetypes_land_on_the_four_bands():
    hot = score_qualification(ModelQualification.model_validate(GOOD))
    warm = score_qualification(
        ModelQualification.model_validate(
            {**GOOD, "intent": "evaluating", "timeline_days": 60, "model_confidence": "0.88"}
        )
    )
    medium = score_qualification(
        ModelQualification.model_validate(
            {
                **GOOD,
                "intent": "browsing",
                "timeline_days": 300,
                "contact_role": "buyer",
                "model_confidence": "0.86",
            }
        )
    )
    cold = score_qualification(
        ModelQualification.model_validate(
            {
                **GOOD,
                "intent": "not_a_fit",
                "budget_stated": False,
                "timeline_days": None,
                "contact_role": None,
                "model_confidence": "0.91",
            }
        )
    )
    assert hot == (100, "Hot")
    assert warm == (75, "Warm")
    assert medium == (45, "Medium")
    assert cold == (0, "Cold")


def test_confidence_is_rounded_half_up_before_the_range_check():
    high = ModelQualification.model_validate({**GOOD, "model_confidence": "0.695"})
    assert high.model_confidence == Decimal("0.70")
    top = ModelQualification.model_validate({**GOOD, "model_confidence": "0.995"})
    assert top.model_confidence == Decimal("1.00")
    with pytest.raises(ValidationError):
        ModelQualification.model_validate({**GOOD, "model_confidence": "1.005"})
    with pytest.raises(ValidationError):
        ModelQualification.model_validate({**GOOD, "model_confidence": "-0.02"})
    with pytest.raises(ValidationError):
        ModelQualification.model_validate({**GOOD, "model_confidence": "NaN"})
    with pytest.raises(ValidationError):
        ModelQualification.model_validate({**GOOD, "model_confidence": True})


def test_model_cannot_set_the_band_or_loosen_types():
    with pytest.raises(ValidationError):
        ModelQualification.model_validate({**GOOD, "band": "Hot"})
    with pytest.raises(ValidationError):
        ModelQualification.model_validate({**GOOD, "score": 100})
    with pytest.raises(ValidationError):
        ModelQualification.model_validate({**GOOD, "budget_stated": "yes"})
    with pytest.raises(ValidationError):
        ModelQualification.model_validate({**GOOD, "timeline_days": 21.0})
    with pytest.raises(ValidationError):
        ModelQualification.model_validate({**GOOD, "timeline_days": -1})
    with pytest.raises(ValidationError):
        ModelQualification.model_validate({**GOOD, "intent": "hot"})
    with pytest.raises(ValidationError):
        ModelQualification.model_validate({**GOOD, "fit_reason": "   "})
