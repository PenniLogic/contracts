"""Actual generated models and transports; failing T6 controls are not skipped."""

from __future__ import annotations

import json
import copy
import unittest
from typing import Callable
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import UUID

from urllib3 import HTTPResponse
from pydantic import TypeAdapter, ValidationError

from pennilogic_contracts.api.custom_destinations_api import CustomDestinationsApi
from pennilogic_contracts.api_client import ApiClient
from pennilogic_contracts.configuration import Configuration
from pennilogic_contracts.models.credential_header import CredentialHeader
from pennilogic_contracts.models.custom_destination import CustomDestination
from pennilogic_contracts.models.custom_destination_lifecycle_request import CustomDestinationLifecycleRequest
from pennilogic_contracts.models.custom_destination_registration_request import CustomDestinationRegistrationRequest
from pennilogic_contracts.models.custom_destination_state import CustomDestinationState
from pennilogic_contracts.models.custom_destination_list import CustomDestinationList
from pennilogic_contracts.models.custom_destination_model import CustomDestinationModel
from pennilogic_contracts.models.custom_destination_validation_result import CustomDestinationValidationResult
from pennilogic_contracts.models.destination_class import DestinationClass
from pennilogic_contracts.models.egress_denial_reason import EgressDenialReason
from pennilogic_contracts.provider_model import ProviderModel

from fixtures import ROOT

FIXTURE = json.loads((ROOT / "spec" / "fixtures" / "custom-destination-wire.v1.json").read_text(encoding="utf-8"))


class CustomDestinationGeneratedTest(unittest.TestCase):
    def test_raw_invalid_header_type_is_rejected_before_transport_without_printing_input(self) -> None:
        client = ApiClient(Configuration(host="https://api.pennilogic.example/v1"))
        api = CustomDestinationsApi(client)
        raw = json.loads('{"proof":{"token":"PRIVATE_SYNTHETIC_CANARY"}}')
        calls: tuple[Callable[[], object], ...] = (
            lambda: api.list_custom_destinations(raw["proof"]),
            lambda: api.list_custom_destinations_with_http_info(raw["proof"]),
            lambda: api.list_custom_destinations_without_preload_content(raw["proof"]),
        )
        for call in calls:
            with patch.object(client.rest_client.pool_manager, "request") as transport:
                with self.assertRaises(ValidationError) as caught:
                    call()
                transport.assert_not_called()
            self.assertNotIn("PRIVATE_SYNTHETIC_CANARY", str(caught.exception))
            self.assertNotIn("PRIVATE_SYNTHETIC_CANARY", str(caught.exception.errors(include_input=False)))

    def test_generated_enums_equal_canonical_values(self) -> None:
        source = json.loads((ROOT / "spec" / "adr022" / "ai-egress-consequences.json").read_text(encoding="utf-8"))
        for enum in (DestinationClass, CredentialHeader, CustomDestinationState, EgressDenialReason):
            self.assertEqual([member.value for member in enum], source["enums"][enum.__name__])

    def test_six_real_generated_transports_preserve_raw_dpop_and_scope_headers(self) -> None:
        authorization = "DPoP synthetic.access.signature"
        configuration = Configuration(host="https://api.pennilogic.example/v1", api_key={"DPoP": authorization})
        client = ApiClient(configuration)
        api = CustomDestinationsApi(client)
        registration = CustomDestinationRegistrationRequest(
            host="models.pennilogic.example", credentialHeader=CredentialHeader.AUTHORIZATION_BEARER,
            models=["fixture/model-v1"],
        )
        lifecycle = CustomDestinationLifecycleRequest(version="1")
        destination = UUID("00000000-0000-4000-8000-000000000001")
        key = "00000000-0000-4000-8000-000000000010"
        with patch.object(client.rest_client.pool_manager, "request", return_value=HTTPResponse(
            body=b"{}", status=200, headers={"Content-Type": "application/json"},
        )) as transport:
            api.register_custom_destination_without_preload_content("header.register.signature", "synthetic-step-up", key, registration)
            api.list_custom_destinations_without_preload_content("header.list.signature")
            api.validate_custom_destination_without_preload_content("header.validate.signature", key, destination, lifecycle)
            api.activate_custom_destination_without_preload_content("header.activate.signature", "synthetic-step-up", key, destination, lifecycle)
            api.suspend_custom_destination_without_preload_content("header.suspend.signature", key, destination, lifecycle)
            api.revoke_custom_destination_without_preload_content("header.revoke.signature", key, destination, lifecycle)
        self.assertEqual(transport.call_count, 6)
        expected_calls = [
            ("POST", "", "register"), ("GET", "", "list"),
            ("POST", f"/{destination}/validate", "validate"), ("POST", f"/{destination}/activate", "activate"),
            ("POST", f"/{destination}/suspend", "suspend"), ("DELETE", f"/{destination}", "revoke"),
        ]
        proofs = set()
        for index, call in enumerate(transport.call_args_list):
            method, suffix, action = expected_calls[index]
            headers = call.kwargs["headers"]
            self.assertEqual(call.args[0], method)
            self.assertEqual(urlsplit(call.args[1]).path, f"/v1/ai/custom-destinations{suffix}")
            self.assertEqual(headers["DPoP"], f"header.{action}.signature")
            self.assertEqual(headers["Authorization"], authorization)
            self.assertFalse(headers["Authorization"].startswith(("Bearer ", "DPoP DPoP ")))
            self.assertEqual("Step-Up-Token" in headers, index in (0, 3))
            self.assertEqual("Idempotency-Key" in headers, index != 1)
            self.assertNotIn("DPoP-Nonce", headers)
            proofs.add(headers["DPoP"])
            self.assertTrue(call.args[1].startswith("https://api.pennilogic.example/v1/ai/custom-destinations"))
        self.assertEqual(len(proofs), 6)
        self.assertEqual(transport.call_args_list[-1].args[0], "DELETE")
        self.assertEqual(transport.call_args_list[-1].args[1], f"https://api.pennilogic.example/v1/ai/custom-destinations/{destination}")
        self.assertEqual(json.loads(transport.call_args_list[0].kwargs["body"]), {
            "host": "models.pennilogic.example", "credentialHeader": "authorization_bearer", "models": ["fixture/model-v1"],
        })
        self.assertEqual(json.loads(transport.call_args_list[3].kwargs["body"]), {"version": "1"})
        self.assertEqual(configuration.api_key_prefix, {})

    def test_valid_generated_json_helpers_round_trip_wire_enums_and_uuid(self) -> None:
        registration = CustomDestinationRegistrationRequest.from_json(json.dumps(FIXTURE["registration"]))
        self.assertIsNotNone(registration)
        assert registration is not None
        self.assertEqual(json.loads(registration.to_json()), FIXTURE["registration"])
        destination = CustomDestination.from_json(json.dumps(FIXTURE["destination"]))
        self.assertIsNotNone(destination)

    def test_t6_generated_model_rejects_every_invalid_registration(self) -> None:
        CustomDestinationRegistrationRequest.model_validate_json(json.dumps(FIXTURE["registration"]))
        for vector in FIXTURE["invalid_registration"]:
            with self.subTest(vector=vector["name"]), self.assertRaises(ValueError):
                CustomDestinationRegistrationRequest.model_validate_json(json.dumps({
                    **FIXTURE["registration"], **vector["add"],
                }))

    def test_t6_generated_model_rejects_every_invalid_lifecycle(self) -> None:
        CustomDestinationLifecycleRequest.model_validate_json(json.dumps(FIXTURE["lifecycle"]))
        for vector in FIXTURE["invalid_lifecycle"]:
            with self.subTest(vector=vector["name"]), self.assertRaises(ValueError):
                CustomDestinationLifecycleRequest.model_validate_json(json.dumps(vector["wire"]))

    def test_t6_generated_model_rejects_nested_address(self) -> None:
        CustomDestination.model_validate_json(json.dumps(FIXTURE["destination"]))
        wire = {**FIXTURE["destination"], "models": [{
            "custom_model_id": "00000000-0000-4000-8000-000000000003",
            "name": "fixture-model", "host": "blocked.example",
        }]}
        with self.assertRaises(ValueError):
            CustomDestination.model_validate_json(json.dumps(wire))

    def test_all_six_closed_models_and_native_generic_containers_reject_extras_before_conversion(self) -> None:
        cases: list[tuple[type[ProviderModel], dict[str, object]]] = [
            (CustomDestinationRegistrationRequest, FIXTURE["registration"]),
            (CustomDestinationLifecycleRequest, FIXTURE["lifecycle"]),
            (CustomDestination, FIXTURE["destination"]),
            (CustomDestinationValidationResult, FIXTURE["validation"]),
            (CustomDestinationModel, FIXTURE["destination"]["models"][0]),
            (CustomDestinationList, {"destinations": [FIXTURE["destination"]]}),
        ]
        for model, wire in cases:
            with self.subTest(model=model.__name__):
                self.assertEqual(model.from_dict(wire).to_dict(), wire)
                self.assertEqual(model.from_json(json.dumps(wire)).to_dict(), wire)
                self.assertEqual(TypeAdapter(model).validate_json(json.dumps(wire)).to_dict(), wire)
                invalid = {**wire, "PRIVATE_SYNTHETIC_CANARY": True}
                for read in (model.from_dict, model.model_validate):
                    with self.assertRaises(ValueError) as caught:
                        read(invalid)
                    self.assertNotIn("PRIVATE_SYNTHETIC_CANARY", str(caught.exception))
        adapter = TypeAdapter(list[CustomDestination])
        self.assertEqual(adapter.validate_json(json.dumps([FIXTURE["destination"]]))[0].to_dict(), FIXTURE["destination"])
        with self.assertRaises(ValueError):
            adapter.validate_json(json.dumps([{**FIXTURE["destination"], "PRIVATE_SYNTHETIC_CANARY": True}]))

    def test_whitespace_patterns_reject_exact_ecmascript_characters(self) -> None:
        for codepoint in FIXTURE["ecmascript_whitespace"]:
            character = chr(codepoint)
            for add in ({"host": f"models{character}.example"}, {"pathPrefix": f"/v1{character}/model"}):
                with self.subTest(codepoint=codepoint), self.assertRaises(ValueError):
                    CustomDestinationRegistrationRequest.from_dict({**FIXTURE["registration"], **add})
        CustomDestinationRegistrationRequest.from_dict(FIXTURE["registration"])

    def test_normal_and_http_info_generated_api_decode_typed_uuid_enum_and_nested_models(self) -> None:
        client = ApiClient(Configuration(host="https://api.pennilogic.example/v1",
                                         api_key={"DPoP": "DPoP synthetic.access.signature"}))
        api = CustomDestinationsApi(client)
        registration = CustomDestinationRegistrationRequest.from_dict(FIXTURE["registration"])
        key = "00000000-0000-4000-8000-000000000010"
        with patch.object(client.rest_client.pool_manager, "request", side_effect=[
            HTTPResponse(body=json.dumps(FIXTURE["destination"]).encode(), status=201,
                         headers={"Content-Type": "application/json"}),
            HTTPResponse(body=json.dumps(FIXTURE["destination"]).encode(), status=201,
                         headers={"Content-Type": "application/json"}),
            HTTPResponse(body=json.dumps({"destinations": [FIXTURE["destination"]]}).encode(), status=200,
                         headers={"Content-Type": "application/json"}),
            HTTPResponse(body=json.dumps(FIXTURE["validation"]).encode(), status=200,
                         headers={"Content-Type": "application/json"}),
        ]) as transport:
            normal = api.register_custom_destination("header.register.signature", "synthetic-step-up", key, registration)
            info = api.register_custom_destination_with_http_info("header.registerAgain.signature", "synthetic-step-up", key, registration)
            page = api.list_custom_destinations("header.list.signature")
            probe = api.validate_custom_destination("header.validate.signature", key, normal.destination_id,
                                                    CustomDestinationLifecycleRequest(version="1"))
        self.assertIsInstance(normal.destination_id, UUID)
        self.assertIsInstance(normal.state, CustomDestinationState)
        self.assertEqual(normal.to_dict(), FIXTURE["destination"])
        self.assertEqual(info.status_code, 201)
        self.assertEqual(info.data.to_dict(), FIXTURE["destination"])
        self.assertEqual(page.destinations[0].to_dict(), FIXTURE["destination"])
        self.assertTrue(probe.validated)
        self.assertEqual(probe.to_dict(), FIXTURE["validation"])
        self.assertEqual(json.loads(transport.call_args_list[0].kwargs["body"]), FIXTURE["registration"])

    def test_actual_api_rejects_nested_response_extras_and_mutated_requests_with_safe_errors(self) -> None:
        client = ApiClient(Configuration(host="https://api.pennilogic.example/v1",
                                         api_key={"DPoP": "DPoP synthetic.access.signature"}))
        api = CustomDestinationsApi(client)
        invalid = copy.deepcopy(FIXTURE["destination"])
        invalid["models"][0]["PRIVATE_SYNTHETIC_CANARY"] = True
        with patch.object(client.rest_client.pool_manager, "request", return_value=HTTPResponse(
            body=json.dumps({"destinations": [invalid]}).encode(), status=200,
            headers={"Content-Type": "application/json"},
        )) as transport:
            with self.assertRaises(ValueError) as caught:
                api.list_custom_destinations("header.list.signature")
            self.assertNotIn("PRIVATE_SYNTHETIC_CANARY", str(caught.exception))
            self.assertEqual(transport.call_count, 1)
            request = CustomDestinationRegistrationRequest.from_dict({**FIXTURE["registration"], "host": "PRIVATE_SYNTHETIC_CANARY"})
            request.models.append("PRIVATE SYNTHETIC CANARY")
            with self.assertRaises(ValueError) as caught:
                api.register_custom_destination("header.register.signature", "synthetic-step-up",
                                                "00000000-0000-4000-8000-000000000010", request)
            self.assertNotIn("PRIVATE SYNTHETIC CANARY", str(caught.exception))
            self.assertNotIn("PRIVATE_SYNTHETIC_CANARY", str(caught.exception))
            self.assertEqual(transport.call_count, 1, "invalid outbound state must not reach urllib3")

if __name__ == "__main__":
    unittest.main()
