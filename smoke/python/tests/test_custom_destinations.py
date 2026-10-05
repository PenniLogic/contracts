"""Actual generated models and transports; failing T6 controls are not skipped."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import UUID

from urllib3 import HTTPResponse

from pennilogic_contracts.api.custom_destinations_api import CustomDestinationsApi
from pennilogic_contracts.api_client import ApiClient
from pennilogic_contracts.configuration import Configuration
from pennilogic_contracts.models.credential_header import CredentialHeader
from pennilogic_contracts.models.custom_destination import CustomDestination
from pennilogic_contracts.models.custom_destination_lifecycle_request import CustomDestinationLifecycleRequest
from pennilogic_contracts.models.custom_destination_registration_request import CustomDestinationRegistrationRequest
from pennilogic_contracts.models.custom_destination_state import CustomDestinationState
from pennilogic_contracts.models.destination_class import DestinationClass
from pennilogic_contracts.models.egress_denial_reason import EgressDenialReason

from fixtures import ROOT

FIXTURE = json.loads((ROOT / "spec" / "fixtures" / "custom-destination-wire.v1.json").read_text(encoding="utf-8"))


class CustomDestinationGeneratedTest(unittest.TestCase):
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

if __name__ == "__main__":
    unittest.main()
