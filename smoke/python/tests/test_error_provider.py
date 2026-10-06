"""Generated typed codes and the strict T-CON-12 seam, using only synthetic fixtures."""

from __future__ import annotations

import copy
import json
import unittest
from typing import Any, Callable
from pennilogic_contracts import ApiClient
from pennilogic_contracts.models.validation_issue import ValidationIssue
from pennilogic_contracts.models.allocation_mismatch_direction import AllocationMismatchDirection

from fixtures import ROOT, load
from pennilogic_contracts.error_catalogue import AUTHENTICATION_POLICIES, ERROR_POLICIES, authentication_policy, error_policy, new_correlation_id
from pennilogic_contracts.models.ai_refusal import AiRefusal
from pennilogic_contracts.models.ai_refusal_code import AiRefusalCode
from pennilogic_contracts.models.client_state import ClientState
from pennilogic_contracts.models.idempotency_treatment import IdempotencyTreatment
from pennilogic_contracts.models.problem_code import ProblemCode
from pennilogic_contracts.models.service_problem_detail import ServiceProblemDetail

FIXTURE = load("error-provider.v1.json")
CATALOGUE: dict[str, Any] = json.loads((ROOT / "spec" / "error-catalogue.v1.json").read_text(encoding="utf-8"))


def examples() -> dict[str, dict[str, Any]]:
    entries = {entry["code"]: entry for entry in CATALOGUE["codes"]}
    return {
        example["code"]: {
            "type": "urn:pennilogic:problem:" + example["code"],
            "title": entries[example["code"]]["title"], "status": entries[example["code"]]["status"],
            "detail": entries[example["code"]]["detail"], "correlation_id": FIXTURE["correlation_id"], **example,
        }
        for example in FIXTURE["examples"]
    }


class ErrorProviderTest(unittest.TestCase):
    def test_allocation_mismatch_requires_the_accepted_safe_direction(self) -> None:
        data = load("allocation-refusal.v1.json")
        with self.assertRaises(ValueError):
            ServiceProblemDetail.from_dict(data["problem"])
        for direction in data["directions"]:
            problem = ServiceProblemDetail.from_dict({**data["problem"], "direction": direction})
            self.assertEqual(problem.to_dict()["direction"], direction)

    def test_allocation_direction_is_typed_safe_conditional_and_nested_on_actual_generated_paths(self) -> None:
        data = load("allocation-refusal.v1.json")
        serialize: Callable[[object], object] = ApiClient().sanitize_for_serialization
        for direction in data["directions"]:
            issue = {"field": "allocation", "reason": "allocation_sum_mismatch", "direction": direction}
            wire = {**data["problem"], "direction": direction, "validation_errors": [issue]}
            problem = ServiceProblemDetail.from_dict(wire)
            self.assertIsInstance(problem.direction, AllocationMismatchDirection)
            self.assertIsInstance(ValidationIssue.from_dict(issue).direction, AllocationMismatchDirection)
            self.assertEqual(serialize(problem), wire)
            self.assertEqual(ApiClient().deserialize(json.dumps(wire), "ServiceProblemDetail", "application/problem+json").to_dict(), wire)
            broken = copy.deepcopy(wire)
            broken["validation_errors"][0].pop("direction")
            with self.assertRaises(ValueError):
                ApiClient().deserialize(json.dumps(broken), "ServiceProblemDetail", "application/problem+json")
        for direction in data["invalid_directions"]:
            wire = {**data["problem"], "direction": direction}
            with self.assertRaises(ValueError) as caught:
                ApiClient().deserialize(json.dumps(wire), "ServiceProblemDetail", "application/problem+json")
            self.assertNotIn("PRIVATE_SYNTHETIC_CANARY", str(caught.exception))
            self.assertNotIn("12.34", str(caught.exception))
        with self.assertRaises(ValueError):
            ServiceProblemDetail.from_dict({**data["problem"], "reason": "shape", "direction": "shortfall"})
        with self.assertRaises(ValueError):
            ServiceProblemDetail.from_dict({**data["problem"], "direction": "shortfall", "amount": "12.34"})
        good = ServiceProblemDetail.from_dict({**data["problem"], "direction": "shortfall"})
        for changed in (good.model_copy(update={"direction": "PRIVATE_SYNTHETIC_CANARY"}),
                        good.model_copy(update={"direction": None})):
            with self.assertRaises(ValueError):
                serialize(changed)

    def test_generated_codes_and_catalogue_have_one_typed_state_and_key_policy(self) -> None:
        self.assertEqual({code.value for code in ProblemCode},
                         {entry["code"] for entry in CATALOGUE["codes"] + CATALOGUE["authentication_codes"]})
        self.assertEqual(set(ERROR_POLICIES) | set(AUTHENTICATION_POLICIES), set(ProblemCode))
        self.assertFalse(set(ERROR_POLICIES) & set(AUTHENTICATION_POLICIES))
        for code in ERROR_POLICIES:
            self.assertIsInstance(error_policy(code).state, ClientState)
            self.assertIsInstance(error_policy(code).idempotency, IdempotencyTreatment)
        for code in AUTHENTICATION_POLICIES:
            self.assertIsNone(authentication_policy(code).state)
            self.assertEqual(authentication_policy(code).flow, "authentication_required")
            with self.assertRaisesRegex(TypeError, "problem code rejected"):
                error_policy(code)
        mismatch = error_policy(ProblemCode.IDEMPOTENCY_PAYLOAD_MISMATCH)
        self.assertFalse(mismatch.retryable)
        self.assertEqual(mismatch.idempotency, IdempotencyTreatment.NEVER_REPLACE_TO_ESCAPE_MISMATCH)
        self.assertEqual(error_policy(ProblemCode.DEPENDENCY_UNAVAILABLE).state, ClientState.ERROR)

    def test_every_error_example_imports_with_enum_codes_and_round_trips(self) -> None:
        for code, wire in examples().items():
            problem = ServiceProblemDetail.from_dict(wire)
            typed: ProblemCode = problem.code
            self.assertIsInstance(typed, ProblemCode)
            self.assertEqual(typed.value, code)
            self.assertEqual(json.loads(problem.to_json()), wire)
            self.assertEqual(ServiceProblemDetail.from_json(problem.to_json()), problem)

    def test_all_negative_bodies_fail_without_echoing_canaries(self) -> None:
        originals = examples()
        for negative in FIXTURE["invalid"]:
            with self.subTest(name=negative["name"]):
                wire = copy.deepcopy(originals[negative["base"]])
                wire.update(negative.get("set", {}))
                for member in negative.get("remove", []):
                    wire.pop(member)
                with self.assertRaises(ValueError) as caught:
                    ServiceProblemDetail.from_dict(wire)
                self.assertNotIn("SYNTHETIC_", str(caught.exception))
                self.assertNotIn("12.34", str(caught.exception))
                self.assertNotIn("internal-42", str(caught.exception))

    def test_enum_wire_rejects_case_coercion_numbers_and_unknown_values(self) -> None:
        for value in ("VALIDATION_REJECTED", "unknown", 422, True, None):
            with self.assertRaises(ValueError) as caught:
                ProblemCode.from_wire(value)
            self.assertEqual(str(caught.exception), "enum value rejected")

    def test_refusal_is_separate_successful_typed_content(self) -> None:
        refusal = AiRefusal.from_dict(FIXTURE["refusal"])
        self.assertIsNotNone(refusal)
        assert refusal is not None
        self.assertEqual(refusal.code, AiRefusalCode.AI_REFUSAL)
        self.assertNotIn(refusal.code.value, {code.value for code in ProblemCode})
        self.assertEqual(json.loads(refusal.to_json()), FIXTURE["refusal"])

    def test_correlation_factory_is_public_random_not_an_internal_record_input(self) -> None:
        identifiers = {new_correlation_id() for _ in range(100)}
        self.assertEqual(len(identifiers), 100)
        for identifier in identifiers:
            self.assertRegex(identifier, r"^cor_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


if __name__ == "__main__":
    unittest.main()
