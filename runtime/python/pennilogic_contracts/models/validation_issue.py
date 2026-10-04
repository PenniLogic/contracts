"""Accepted ADR-016 field/reason/direction diagnostics, with no monetary values."""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from pennilogic_contracts.models.allocation_mismatch_direction import AllocationMismatchDirection
from pennilogic_contracts.models.problem_field import ProblemField
from pennilogic_contracts.models.validation_reason import ValidationReason
from pennilogic_contracts.provider_model import ProviderModel, ProviderWireError


def validate_direction(reason: ValidationReason, value: object, present: bool) -> None:
    if reason == ValidationReason.ALLOCATION_SUM_MISMATCH:
        if not present:
            raise ProviderWireError()
        AllocationMismatchDirection.from_wire(value)
    elif present:
        raise ProviderWireError()


class ValidationIssue(ProviderModel):
    var_field: ProblemField = Field(alias="field")
    reason: ValidationReason
    direction: AllocationMismatchDirection | None = None

    @model_validator(mode="before")
    @classmethod
    def check_direction(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            raise ProviderWireError()
        validate_direction(ValidationReason.from_wire(value.get("reason")), value.get("direction"), "direction" in value)
        return value
