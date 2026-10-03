"""Strict T-CON-12 seam; the permissive scaffold ProblemDetail remains unchanged."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from pennilogic_contracts.error_catalogue import error_policy, error_status
from pennilogic_contracts.models.allowance import Allowance
from pennilogic_contracts.models.entitlement_denial import EntitlementDenial
from pennilogic_contracts.models.instant import Instant
from pennilogic_contracts.models.problem_code import ProblemCode
from pennilogic_contracts.models.problem_field import ProblemField
from pennilogic_contracts.models.validation_issue import ValidationIssue
from pennilogic_contracts.models.validation_reason import ValidationReason

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_REQUIRED = frozenset(("type", "title", "status", "detail", "code", "correlation_id"))
_OPTIONAL = frozenset(("instance", "field", "reason", "validation_errors", "idempotency_key",
                       "retry_after_seconds", "allowance", "entitlement"))
_VALIDATION = frozenset((ProblemCode.VALIDATION_REJECTED, ProblemCode.IMPORT_MAPPING_REQUIRED,
                         ProblemCode.IDEMPOTENCY_KEY_INVALID))
_DELAY = frozenset((ProblemCode.DEPENDENCY_UNAVAILABLE, ProblemCode.IDEMPOTENCY_IN_PROGRESS,
                   ProblemCode.RATE_LIMITED, ProblemCode.REQUEST_FAILED))


class ProblemWireError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"problem rejected: {reason}")


def _object(value: object, required: frozenset[str], optional: frozenset[str] = frozenset()) -> Mapping[str, Any]:
    if not isinstance(value, dict) or not required <= value.keys() or not value.keys() <= required | optional:
        raise ProblemWireError("shape")
    if any(item is None for item in value.values()):
        raise ProblemWireError("shape")
    return value


def validate_problem_wire(value: object) -> None:
    wire = _object(value, _REQUIRED, _OPTIONAL)
    code = ProblemCode.from_wire(wire["code"])
    policy = error_policy(code)
    for member, expected in (("type", f"urn:pennilogic:problem:{code.value}"), ("title", policy.title),
                             ("detail", policy.detail), ("status", error_status(
                                 code, ProblemField.from_wire(wire["field"]) if "field" in wire else None))):
        if wire[member] != expected or type(wire[member]) is not type(expected):
            raise ProblemWireError("catalogue")
    correlation = wire["correlation_id"]
    if not isinstance(correlation, str) or re.fullmatch("cor_" + _UUID, correlation) is None:
        raise ProblemWireError("correlation")
    if "instance" in wire and (not isinstance(wire["instance"], str) or
                              re.fullmatch("urn:pennilogic:problem-instance:" + _UUID, wire["instance"]) is None):
        raise ProblemWireError("instance")
    if code in _VALIDATION:
        if "field" not in wire or "reason" not in wire:
            raise ProblemWireError("validation")
        field, reason = ProblemField.from_wire(wire["field"]), ValidationReason.from_wire(wire["reason"])
        if code == ProblemCode.IDEMPOTENCY_KEY_INVALID and (
                field != ProblemField.IDEMPOTENCY_KEY or reason not in (ValidationReason.REQUIRED, ValidationReason.MALFORMED)):
            raise ProblemWireError("validation")
        if code == ProblemCode.IMPORT_MAPPING_REQUIRED and (
                field != ProblemField.COLUMN_MAPPING or reason not in (ValidationReason.MAPPING_UNMAPPED, ValidationReason.MAPPING_CONFLICT)):
            raise ProblemWireError("validation")
        if field == ProblemField.DUPLICATE_OVERRIDE and code == ProblemCode.VALIDATION_REJECTED and reason not in (
                ValidationReason.MALFORMED, ValidationReason.NOT_AVAILABLE):
            raise ProblemWireError("validation")
        if "validation_errors" in wire:
            issues = wire["validation_errors"]
            if not isinstance(issues, list) or not 1 <= len(issues) <= 20:
                raise ProblemWireError("validation")
            seen: set[tuple[ProblemField, ValidationReason]] = set()
            for item in issues:
                issue = _object(item, frozenset(("field", "reason")))
                pair = ProblemField.from_wire(issue["field"]), ValidationReason.from_wire(issue["reason"])
                if pair in seen:
                    raise ProblemWireError("validation")
                seen.add(pair)
    elif any(member in wire for member in ("field", "reason", "validation_errors")):
        raise ProblemWireError("validation")
    if code == ProblemCode.IDEMPOTENCY_PAYLOAD_MISMATCH:
        key = wire.get("idempotency_key")
        if not isinstance(key, str) or re.fullmatch(_UUID, key) is None:
            raise ProblemWireError("key")
    elif "idempotency_key" in wire:
        raise ProblemWireError("key")
    if "retry_after_seconds" in wire:
        delay = wire["retry_after_seconds"]
        if code not in _DELAY or type(delay) is not int or not 1 <= delay <= 86400:
            raise ProblemWireError("retry")
    elif code in (ProblemCode.IDEMPOTENCY_IN_PROGRESS, ProblemCode.RATE_LIMITED):
        raise ProblemWireError("retry")
    if code in (ProblemCode.QUOTA_EXHAUSTED, ProblemCode.RATE_LIMITED):
        allowance = _object(wire.get("allowance"), frozenset(("limit", "unit", "window")), frozenset(("resets_at",)))
        if type(allowance["limit"]) is not int or not 0 <= allowance["limit"] <= 2147483647:
            raise ProblemWireError("allowance")
        if allowance["unit"] not in ("requests", "tokens") or allowance["window"] not in (
                "minute", "hour", "day", "month", "rolling_7_days", "lifetime"):
            raise ProblemWireError("allowance")
        if "resets_at" in allowance:
            if allowance["limit"] == 0 or allowance["window"] == "lifetime":
                raise ProblemWireError("allowance")
            Instant.from_wire(allowance["resets_at"])
    elif "allowance" in wire:
        raise ProblemWireError("allowance")
    if code == ProblemCode.ENTITLEMENT_DENIED:
        entitlement = _object(wire.get("entitlement"), frozenset(("upgrade_available",)))
        if type(entitlement["upgrade_available"]) is not bool:
            raise ProblemWireError("entitlement")
    elif "entitlement" in wire:
        raise ProblemWireError("entitlement")


class ServiceProblemDetail(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, hide_input_in_errors=True,
                              validate_by_alias=True, validate_by_name=False)

    type: StrictStr
    title: StrictStr
    status: StrictInt
    detail: StrictStr
    code: ProblemCode
    correlation_id: StrictStr
    instance: StrictStr | None = None
    var_field: ProblemField | None = Field(default=None, alias="field")
    reason: ValidationReason | None = None
    validation_errors: list[ValidationIssue] | None = None
    idempotency_key: StrictStr | None = None
    retry_after_seconds: StrictInt | None = None
    allowance: Allowance | None = None
    entitlement: EntitlementDenial | None = None

    @model_validator(mode="before")
    @classmethod
    def check_wire(cls, value: Any) -> Any:
        validate_problem_wire(value)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        return cls.model_validate(value)

    @classmethod
    def from_json(cls, text: str) -> Self:
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            raise ProblemWireError("json") from None
        return cls.model_validate(value)

    def to_dict(self) -> dict[str, Any]:
        value = self.model_dump(mode="json", by_alias=True, exclude_none=True,
                                exclude={"allowance", "entitlement", "validation_errors"})
        if self.allowance is not None:
            value["allowance"] = self.allowance.to_dict()
        if self.entitlement is not None:
            value["entitlement"] = self.entitlement.to_dict()
        if self.validation_errors is not None:
            value["validation_errors"] = [issue.to_dict() for issue in self.validation_errors]
        validate_problem_wire(value)
        return value

    def to_json(self) -> str:
        return json.dumps(self.to_dict())
