"""Real generated core models/transports; synthetic responses are not backend/cryptographic acceptance."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from uuid import UUID

from pydantic import TypeAdapter
from urllib3 import HTTPResponse

from fixtures import ROOT
from pennilogic_contracts import ApiClient, Configuration
from pennilogic_contracts.api.accounts_api import AccountsApi
from pennilogic_contracts.api.auth_api import AuthApi
from pennilogic_contracts.api.categories_api import CategoriesApi
from pennilogic_contracts.api.transactions_api import TransactionsApi
from pennilogic_contracts.models.auth_recovery_progress import AuthRecoveryProgress
from pennilogic_contracts.models.auth_recovery_state import AuthRecoveryState
from pennilogic_contracts.models.auth_enrollment_request import AuthEnrollmentRequest
from pennilogic_contracts.models.categorisation import Categorisation
from pennilogic_contracts.models.categorisation_view import CategorisationView
from pennilogic_contracts.models.categorisation_view_mode import CategorisationViewMode
from pennilogic_contracts.models.create_account_request import CreateAccountRequest
from pennilogic_contracts.models.instant import Instant
from pennilogic_contracts.models.post_transaction_request import PostTransactionRequest
from pennilogic_contracts.success_responses import PostTransactionSuccessStatus201, PostTransactionSuccessStatus202
from pennilogic_contracts.exceptions import ApiException
from pennilogic_contracts.models.application_problem_detail import ApplicationProblemDetail
from pennilogic_contracts.models.authentication_challenge_problem_detail import AuthenticationChallengeProblemDetail

CORE = json.loads((ROOT / "spec" / "fixtures" / "core-endpoints.v1.json").read_text(encoding="utf-8"))
AUTH = json.loads((ROOT / "spec" / "fixtures" / "auth-endpoints.v1.json").read_text(encoding="utf-8"))
IMPORT = json.loads((ROOT / "spec" / "fixtures" / "import-provider.v1.json").read_text(encoding="utf-8"))
KEY = "00000000-0000-4000-8000-000000000010"


def response(body: object, status: int = 200, media: str = "application/json") -> HTTPResponse:
    return HTTPResponse(body=json.dumps(body).encode(), status=status, headers={"Content-Type": media})


class CoreGeneratedEndpointTest(unittest.TestCase):
    def test_all_eight_authentication_contexts_use_real_non_2xx_transports_without_service_projection(self) -> None:
        catalogue = json.loads((ROOT / "spec" / "error-catalogue.v1.json").read_text(encoding="utf-8"))
        fixture = json.loads((ROOT / "spec" / "fixtures" / "authentication-errors.v1.json").read_text(encoding="utf-8"))
        entries = {row["code"]: row for row in catalogue["authentication_codes"]}
        client = self.client()
        api = AuthApi(client)
        self.assertEqual(len(fixture["examples"]), 8)
        for row in fixture["examples"]:
            code = row["code"]
            entry = entries[code]
            wire = {"type": "urn:pennilogic:problem:" + code, "title": entry["title"], "status": entry["status"],
                    "detail": entry["detail"], "code": code, "correlation_id": fixture["correlation_id"], **row["context"]}
            self.assertEqual(ApplicationProblemDetail.from_dict(wire).to_dict(), wire)
            with patch.object(client.rest_client.pool_manager, "request", return_value=response(wire, entry["status"], "application/problem+json")), \
                    self.assertRaises(ApiException) as caught:
                api.get_profile("synthetic.auth.signature")
            data = caught.exception.data
            if not isinstance(data, (ApplicationProblemDetail, AuthenticationChallengeProblemDetail)):
                self.fail("actual authentication transport must expose its declared typed error")
            self.assertEqual(data.to_dict(), wire)
            for field in catalogue["authentication_policies"][code]["required_context"]:
                missing = {name: value for name, value in wire.items() if name != field}
                with patch.object(client.rest_client.pool_manager, "request", return_value=response(missing, entry["status"], "application/problem+json")), \
                        self.assertRaises(ValueError):
                    api.get_profile("synthetic.invalid.signature")
            invalid = {**wire, "provider": "PRIVATE_SYNTHETIC_CANARY"}
            with patch.object(client.rest_client.pool_manager, "request", return_value=response(invalid, entry["status"], "application/problem+json")), \
                    self.assertRaises(ValueError) as denied:
                api.get_profile("synthetic.invalid.signature")
            self.assertNotIn("PRIVATE_SYNTHETIC_CANARY", str(denied.exception))

    def client(self) -> ApiClient:
        return ApiClient(Configuration(host="https://api.pennilogic.example/v1",
                                       api_key={"DPoP": "DPoP synthetic.access.signature"}))

    def test_native_required_null_optional_absence_and_nested_arrays_remain_distinct(self) -> None:
        current = CategorisationView(mode=CategorisationViewMode.CURRENT, as_of=None)
        self.assertTrue(current.to_dict() == {"mode": "CURRENT", "as_of": None}, "required null changed")
        self.assertTrue(json.loads(current.to_json())["as_of"] is None)
        for wire in ({"mode": "CURRENT"}, {"mode": "CURRENT", "as_of": "2026-10-01T00:00:00.000Z"},
                     {"mode": "AS_RECORDED", "as_of": None}):
            with self.assertRaises(ValueError):
                CategorisationView.from_dict(wire)
        pending = AuthRecoveryProgress(
            recovery_id=UUID("00000000-0000-7000-8000-000000000024"),
            state=AuthRecoveryState.NOTIFICATION_PENDING, recovery_proof="synthetic_recovery_instrument",
            window_ends_at=None, retry_after_seconds=30,
        )
        self.assertTrue(pending.to_dict() == AUTH["payloads"]["notification_pending"], "explicit null changed")
        with self.assertRaises(ValueError):
            AuthRecoveryProgress(recovery_id=pending.recovery_id, state=AuthRecoveryState.NOTIFICATION_PENDING,
                                 recovery_proof="synthetic_recovery_instrument", retry_after_seconds=30)
        unproven = AuthRecoveryProgress(recovery_id=pending.recovery_id, state=AuthRecoveryState.UNPROVEN)
        self.assertNotIn("window_ends_at", unproven.to_dict())
        nested = TypeAdapter(list[Categorisation]).validate_json(json.dumps([CORE["payloads"]["categorisation"]]))
        self.assertTrue(nested[0].to_dict() == CORE["payloads"]["categorisation"], "nested nullable wire changed")
        self.assertTrue(json.loads(TypeAdapter(list[Categorisation]).dump_json(nested))[0]["view"]["as_of"] is None)

    def test_actual_201_and_202_success_transports_are_nominal_typed_distinct_cases(self) -> None:
        client = self.client()
        api = TransactionsApi(client)
        request = PostTransactionRequest.from_dict(CORE["payloads"]["post_transaction"])
        with patch.object(client.rest_client.pool_manager, "request", side_effect=[
            response(CORE["payloads"]["transaction"], 201), response(IMPORT["screen"], 202),
            response(IMPORT["screen"], 202), response(IMPORT["screen"], 206),
        ]) as transport:
            created = api.post_transaction("synthetic.first.signature", KEY, request)
            duplicate = api.post_transaction("synthetic.second.signature", KEY, request)
            info = api.post_transaction_with_http_info("synthetic.third.signature", KEY, request)
            with self.assertRaises(ValueError):
                api.post_transaction("synthetic.fourth.signature", KEY, request)
        self.assertIsInstance(created, PostTransactionSuccessStatus201)
        self.assertIsInstance(duplicate, PostTransactionSuccessStatus202)
        self.assertIsInstance(info.data, PostTransactionSuccessStatus202)
        self.assertEqual(info.status_code, 202)
        self.assertTrue(created.body.to_dict() == CORE["payloads"]["transaction"], "created body changed")
        self.assertTrue(duplicate.body.to_dict() == IMPORT["screen"], "shared decision changed")
        self.assertEqual(transport.call_count, 4)
        for call in transport.call_args_list:
            self.assertEqual(call.kwargs["headers"]["Authorization"], "DPoP synthetic.access.signature")
            self.assertEqual(call.kwargs["headers"]["Idempotency-Key"], KEY)
        self.assertEqual(len({call.kwargs["headers"]["DPoP"] for call in transport.call_args_list}), 4)

    def test_actual_canonical_instant_query_and_account_request_transport(self) -> None:
        client = self.client()
        transactions = TransactionsApi(client)
        accounts = AccountsApi(client)
        start = Instant.parse("2026-10-01T00:00:00.000Z")
        with patch.object(client.rest_client.pool_manager, "request", side_effect=[
            response({"transactions": [], "page": CORE["payloads"]["cursor_end"]}),
            response(CORE["payloads"]["account"], 201),
        ]) as transport:
            transactions.list_transactions("synthetic.query.signature", occurred_from=start)
            accounts.create_account("synthetic.create.signature", KEY,
                                    CreateAccountRequest.from_dict(CORE["payloads"]["create_account"]))
        self.assertIn("2026-10-01T00%3A00%3A00.000Z", transport.call_args_list[0].args[1])
        self.assertTrue(json.loads(transport.call_args_list[1].kwargs["body"]) == CORE["payloads"]["create_account"],
                        "outgoing account body changed")

    def test_actual_nested_nullable_response_transport_preserves_null_and_refuses_disallowed_null(self) -> None:
        client = self.client()
        api = CategoriesApi(client)
        with patch.object(client.rest_client.pool_manager, "request", side_effect=[
            response(CORE["payloads"]["categorisation"]),
            response({**CORE["payloads"]["categorisation"], "assigned_at": None}),
        ]):
            value = api.get_categorisation("synthetic.read.signature",
                                          "rec_00000000-0000-4000-8000-000000000004")
            self.assertTrue(value.to_dict() == CORE["payloads"]["categorisation"], "nullable transport changed")
            with self.assertRaises(ValueError):
                api.get_categorisation("synthetic.invalid.signature",
                                       "rec_00000000-0000-4000-8000-000000000004")

    def test_bootstrap_never_uses_a_configured_global_proof(self) -> None:
        client = ApiClient(Configuration(host="https://api.pennilogic.example/v1",
                                         api_key={"DPoPBootstrap": "SYNTHETIC_REUSED_PROOF"}))
        api = AuthApi(client)
        with patch.object(client.rest_client.pool_manager, "request", return_value=response({
            "enrollment_id": "00000000-0000-7000-8000-000000000021", "expires_at": "2026-10-01T00:00:00.000Z",
        })) as transport:
            api.start_enrollment_without_preload_content("synthetic.fresh.signature",
                                                        AuthEnrollmentRequest.from_dict(AUTH["payloads"]["enrollment"]))
        self.assertEqual(transport.call_args.kwargs["headers"]["DPoP"], "synthetic.fresh.signature")


if __name__ == "__main__":
    unittest.main()
