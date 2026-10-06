"""Real generated shared error models and non-2xx API paths; synthetic transport only."""

from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from pydantic import TypeAdapter
from urllib3 import HTTPResponse
from pennilogic_contracts import ApiClient
from pennilogic_contracts.api.custom_destinations_api import CustomDestinationsApi
from pennilogic_contracts.configuration import Configuration
from pennilogic_contracts.error_catalogue import authentication_policy, egress_problem_code, error_policy
from pennilogic_contracts.exceptions import ApiException
from pennilogic_contracts.models.authentication_problem_detail import AuthenticationProblemDetail
from pennilogic_contracts.models.authentication_required_problem_detail import AuthenticationRequiredProblemDetail
from pennilogic_contracts.models.custom_destination_registration_request import CustomDestinationRegistrationRequest
from pennilogic_contracts.models.egress_denial_reason import EgressDenialReason
from pennilogic_contracts.models.egress_denied_problem_detail import EgressDeniedProblemDetail
from pennilogic_contracts.models.operation_problem_detail import OperationProblemDetail
from pennilogic_contracts.models.problem_code import ProblemCode
from pennilogic_contracts.models.service_problem_detail import ServiceProblemDetail
from pennilogic_contracts.provider_model import ProviderModel

from fixtures import ROOT, load
from test_error_provider import examples as service_examples


FIXTURE = load("egress-errors.v1.json")
CATALOGUE = json.loads((ROOT / "spec" / "error-catalogue.v1.json").read_text(encoding="utf-8"))
ENTRIES = {entry["code"]: entry for entry in CATALOGUE["codes"] + CATALOGUE["authentication_codes"]}


def wire(case: dict[str, object]) -> dict[str, object]:
    entry = ENTRIES[case["code"]]
    value = {"type": "urn:pennilogic:problem:" + entry["code"], "title": entry["title"], "status": entry["status"],
             "detail": entry["detail"], "code": entry["code"], "correlation_id": FIXTURE["correlation_id"]}
    extra = case.get("extra", {})
    if not isinstance(extra, dict):
        raise AssertionError("synthetic error extras must be an object")
    value.update(extra)
    if "reason" in case:
        value["egress_denial_reason"] = case["reason"]
    return value


class EgressErrorGeneratedTest(unittest.TestCase):
    def test_service_subset_retains_global_code_type_constructor_and_safe_family_partition(self) -> None:
        value = wire({"code": "egress_denied"})
        model = ServiceProblemDetail.model_validate_json(json.dumps(value))
        code: ProblemCode = model.code
        self.assertIs(type(code), ProblemCode)
        self.assertEqual(code, ProblemCode.EGRESS_DENIED)
        constructed = ServiceProblemDetail(type=model.type, title=model.title, status=model.status,
                                           detail=model.detail, code=code, correlation_id=model.correlation_id)
        self.assertEqual(constructed.to_dict(), value)
        for update in ({"code": ProblemCode.AUTHENTICATION_REQUIRED}, {"status": 401},
                       {"detail": "PRIVATE_SYNTHETIC_CANARY"}):
            with self.assertRaises(ValueError) as caught:
                ApiClient().sanitize_for_serialization(model.model_copy(update=update))
            self.assertNotIn("PRIVATE_SYNTHETIC_CANARY", str(caught.exception))
        for family in (OperationProblemDetail, EgressDeniedProblemDetail, AuthenticationProblemDetail):
            with self.assertRaises(ValueError):
                family.from_dict(value)
        for case in FIXTURE["authentication"]:
            with self.assertRaises(ValueError):
                ServiceProblemDetail.from_dict(wire(case))

    def test_all_four_closed_families_round_trip_native_generics_and_shared_codes(self) -> None:
        for case in FIXTURE["cases"]:
            value = wire(case)
            family: type[ProviderModel] = AuthenticationProblemDetail if case["reason"] == "step_up_required" else EgressDeniedProblemDetail
            reason = EgressDenialReason(case["reason"])
            self.assertEqual(egress_problem_code(reason), ProblemCode(case["code"]))
            self.assertEqual(value["status"], case["status"])
            for model in (family, OperationProblemDetail):
                with self.subTest(reason=case["reason"], model=model.__name__):
                    self.assertEqual(model.from_dict(value).to_dict(), value)
                    self.assertEqual(model.model_validate_json(json.dumps(value)).to_dict(), value)
                    self.assertEqual(TypeAdapter(model).validate_json(json.dumps(value)).to_dict(), value)
                    self.assertEqual(ApiClient().deserialize(json.dumps(value), model.__name__, "application/problem+json").to_dict(), value)
            with self.assertRaises(ValueError):
                ServiceProblemDetail.from_dict(value)
        for value in service_examples().values():
            self.assertEqual(OperationProblemDetail.from_dict(value).to_dict(), value)
        for case in FIXTURE["authentication"]:
            value = wire(case)
            self.assertEqual(AuthenticationProblemDetail.from_dict(value).to_dict(), value)
            code = ProblemCode(case["code"])
            self.assertIsNone(authentication_policy(code).state)
            with self.assertRaises(TypeError):
                error_policy(code)
            if case["status"] == 401:
                self.assertEqual(AuthenticationRequiredProblemDetail.from_dict(value).to_dict(), value)
            else:
                with self.assertRaises(ValueError):
                    AuthenticationRequiredProblemDetail.from_dict(value)
                with self.assertRaises(ValueError):
                    OperationProblemDetail.from_dict(value)

    def test_error_negatives_and_mutated_outbound_models_fail_without_private_diagnostics(self) -> None:
        originals = {case["reason"]: wire(case) for case in FIXTURE["cases"]}
        for negative in FIXTURE["invalid"]:
            value = {**copy.deepcopy(originals[negative["base"]]), **negative.get("set", {})}
            for member in negative.get("remove", []):
                value.pop(member)
            with self.subTest(name=negative["name"]):
                with self.assertRaises(ValueError) as caught:
                    OperationProblemDetail.model_validate_json(json.dumps(value))
                self.assertNotIn("PRIVATE_SYNTHETIC_CANARY", str(caught.exception))
        model = OperationProblemDetail.from_dict(originals["destination_denied"])
        with self.assertRaises(ValueError):
            ApiClient().sanitize_for_serialization(model.model_copy(update={"egress_denial_reason": "PRIVATE_SYNTHETIC_CANARY"}))
        nested = TypeAdapter(list[OperationProblemDetail])
        self.assertEqual(nested.validate_json(json.dumps([originals["destination_denied"]]))[0].to_dict(),
                         originals["destination_denied"])
        with self.assertRaises(ValueError):
            nested.validate_json(json.dumps([{**originals["destination_denied"], "provider": "PRIVATE_SYNTHETIC_CANARY"}]))

    def test_actual_generated_non_2xx_path_returns_typed_auth_and_every_egress_reason(self) -> None:
        client = ApiClient(Configuration(host="https://api.pennilogic.example/v1",
                                         api_key={"DPoP": "DPoP synthetic.access.signature"}))
        api = CustomDestinationsApi(client)
        registration = CustomDestinationRegistrationRequest.from_dict(load("custom-destination-wire.v1.json")["registration"])
        for case in FIXTURE["cases"] + FIXTURE["authentication"][:1]:
            value = wire(case)
            headers = dict(FIXTURE["safe_headers"])
            if case["status"] == 401:
                headers.update(FIXTURE["nonce_headers"])
            if "retry_after_seconds" in value:
                headers["Retry-After"] = str(value["retry_after_seconds"])
            with patch.object(client.rest_client.pool_manager, "request", return_value=HTTPResponse(
                body=json.dumps(value).encode("utf-8"), status=case["status"], headers=headers,
            )) as transport, self.assertRaises(ApiException) as caught:
                api.register_custom_destination("header.register.signature", "synthetic-step-up",
                                                "00000000-0000-4000-8000-000000000010", registration)
            self.assertEqual(transport.call_count, 1)
            data = caught.exception.data
            if not isinstance(data, (OperationProblemDetail, AuthenticationRequiredProblemDetail)):
                self.fail("actual API error must carry the strict generated shared model")
            self.assertEqual(data.to_dict(), value)
            self.assertEqual(data.status, caught.exception.status)
            received_headers = caught.exception.headers
            if received_headers is None:
                self.fail("actual API error must expose its response headers")
            self.assertEqual(received_headers["Cache-Control"], "no-store")
            if case["status"] == 401:
                self.assertEqual(received_headers["WWW-Authenticate"], 'DPoP error="use_dpop_nonce"')
                self.assertEqual(received_headers["DPoP-Nonce"], "synthetic-response-nonce")
            if "retry_after_seconds" in value:
                self.assertEqual(received_headers["Retry-After"], str(value["retry_after_seconds"]))

    def test_actual_invalid_error_response_propagates_safe_validation_not_raw_api_exception(self) -> None:
        client = ApiClient(Configuration(host="https://api.pennilogic.example/v1"))
        api = CustomDestinationsApi(client)
        for status, value in [
            (403, {**wire(FIXTURE["cases"][0]), "host": "PRIVATE_SYNTHETIC_CANARY"}),
            (401, wire(FIXTURE["cases"][0])),
            (200, None), (401, None), (403, None),
        ]:
            with patch.object(client.rest_client.pool_manager, "request", return_value=HTTPResponse(
                body=json.dumps(value).encode("utf-8"), status=status, headers=FIXTURE["safe_headers"],
            )), self.assertRaises(ValueError) as caught:
                api.list_custom_destinations("header.list.signature")
            self.assertNotIn("PRIVATE_SYNTHETIC_CANARY", str(caught.exception))

    def test_unknown_foreign_fixture_cannot_add_existence_or_resource_information(self) -> None:
        control = FIXTURE["unknown_foreign_control"]
        value = wire(control)
        for _context in control["contexts"]:
            self.assertEqual(OperationProblemDetail.from_dict(value).to_dict(), value)
        for member in control["forbidden"]:
            with self.assertRaises(ValueError):
                OperationProblemDetail.from_dict({**value, member: "PRIVATE_SYNTHETIC_CANARY"})


if __name__ == "__main__":
    unittest.main()
