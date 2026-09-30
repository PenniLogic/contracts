"""Smoke consumer: imports the generated Python client and uses it the way the ai-service will.

Type-checked with `mypy --strict` and imported by the smoke tests. It carries no network call and no
real record; every value is synthetic.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from pennilogic_contracts import ApiClient, Configuration
from pennilogic_contracts.models import ProblemDetail
from pennilogic_contracts.models.instant import Instant
from pennilogic_contracts.models.local_date import LocalDate
from pennilogic_contracts.models.money import Money


class SyntheticEnvelope(BaseModel):
    """What a generated Money-bearing model looks like: the wrapper types are the field types (ADR-015 §2).

    `strict` and `hide_input_in_errors` mirror the generated model_config (generator/templates/python):
    a consumer that logs a ValidationError verbatim, or structured through `errors(include_input=False)`,
    never logs an amount.
    """

    model_config = ConfigDict(strict=True, hide_input_in_errors=True)

    total: Money
    recorded_at: Instant
    booked_on: LocalDate | None = None
    note: str | None = None


def client_for(host: str) -> ApiClient:
    configuration = Configuration(host=f"https://{host}/v1")
    return ApiClient(configuration)


def problem_from_json(text: str) -> ProblemDetail:
    problem = ProblemDetail.from_json(text)
    if problem is None:
        raise ValueError("problem body was empty")
    return problem


def envelope_from_wire(wire: object) -> SyntheticEnvelope:
    return SyntheticEnvelope.model_validate(wire)


def envelope_to_wire(envelope: SyntheticEnvelope) -> dict[str, object]:
    return envelope.model_dump(mode="json", exclude_none=True)


def total_in_minor_units(envelope: SyntheticEnvelope) -> int:
    return envelope.total.minor_units
