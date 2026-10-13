"""Original #16's ADR-019 extension retains the old service/auth/egress partitions."""

from __future__ import annotations

import copy
import hashlib
import json
import unittest

from support import ROOT, SpecDir, run_script, spec_text
from test_error_provider import schema_results
from test_import_provider import schema_document

CATALOGUE = json.loads((ROOT / "spec" / "error-catalogue.v1.json").read_text(encoding="utf-8"))
FIXTURE = json.loads((ROOT / "spec" / "fixtures" / "authentication-errors.v1.json").read_text(encoding="utf-8"))


def authentication_examples() -> dict[str, dict]:
    entries = {row["code"]: row for row in CATALOGUE["authentication_codes"]}
    return {
        row["code"]: {
            "type": "urn:pennilogic:problem:" + row["code"],
            "title": entries[row["code"]]["title"], "status": entries[row["code"]]["status"],
            "detail": entries[row["code"]]["detail"], "code": row["code"],
            "correlation_id": FIXTURE["correlation_id"], **row["context"],
        } for row in FIXTURE["examples"]
    }


class AuthenticationProviderTest(unittest.TestCase):
    def test_complete_machine_parameter_mirror_matches_the_accepted_adr_and_wire_constants(self) -> None:
        document = schema_document()
        schemas = document["components"]["schemas"]
        identity = schemas["AuthenticationParameters"]
        mirror = identity["x-adr-019-auth-parameters"]
        digest = hashlib.sha256(json.dumps(mirror, sort_keys=True, separators=(",", ":"),
                                          ensure_ascii=True).encode()).hexdigest()
        self.assertEqual(digest, "84ca3e5ecebbdaed4df90ebe6b6f847ac8f2d68d70536e82e735ec8dc5258300")
        self.assertEqual(identity["x-adr-source"]["canonicalJsonSha256"], digest)
        self.assertEqual(identity["x-adr-source"]["commit"], "a700e639585c61a4610e7b99dbd02b2dab28bdcc")
        self.assertEqual(identity["const"], mirror["artifact"] + "@1")
        self.assertEqual(mirror["tokens"]["refresh_grace_window_seconds"], 30)
        self.assertIn("inclusive at 30.000 s", mirror["tokens"]["refresh_grace_window_boundary"])
        self.assertEqual(schemas["AuthTokenSet"]["properties"]["expires_in"]["const"],
                         mirror["tokens"]["access_token_ttl_seconds"])
        for name, key in (("AuthDeviceTrust", "trust"), ("AuthAttestation", "attestation"),
                          ("AuthRecoveryState", "recovery_state"), ("AuthRevocationReason", "revocation_reason")):
            self.assertEqual(schemas[name]["enum"], mirror["enums"][key])
        self.assertEqual(schemas["AuthRecoveryRoute"]["enum"], mirror["recovery"]["routes"])
        self.assertEqual(schemas["AuthCredentialList"]["properties"]["credentials"]["maxItems"],
                         mirror["passkeys"]["max_credentials_per_account"])
        self.assertEqual(schemas["AuthCreationOptions"]["properties"]["timeout"]["const"],
                         mirror["passkeys"]["challenge_ttl_seconds"] * 1000)
        self.assertIsNone(mirror["owner_inputs"]["product_domain"])
        self.assertFalse(mirror["owner_inputs"]["email_provider_approved"])
        self.assertEqual(schemas["AuthChannelInput"]["properties"]["type"]["const"], "email")

    def test_each_required_code_has_typed_context_and_an_excluded_flow_policy(self) -> None:
        examples = authentication_examples()
        self.assertEqual(set(examples), {row["code"] for row in CATALOGUE["authentication_codes"]})
        self.assertEqual(set(examples), set(CATALOGUE["authentication_policies"]))
        cases = []
        for code, wire in examples.items():
            old = code in ("authentication_required", "step_up_required")
            selected = "AuthenticationProblemDetail" if old else "AuthenticationContextProblemDetail"
            for family in (selected, "ApplicationProblemDetail"):
                cases.append({"name": code, "schema": family, "wire": wire})
            for row in CATALOGUE["authentication_codes"]:
                self.assertIsNone(row["state"])
                self.assertEqual(row["flow"], "authentication_required")
        self.assertTrue(all(row["valid"] for row in schema_results(cases)), "auth context family rejected")
        denied = [{"name": code, "schema": "ServiceProblemDetail", "wire": wire} for code, wire in examples.items()]
        self.assertTrue(all(not row["valid"] for row in schema_results(denied)), "auth leaked into service policy")

    def test_missing_null_nonnull_wrong_context_and_disclosure_vectors_refuse(self) -> None:
        examples = authentication_examples()
        cases = []
        for row in FIXTURE["invalid"]:
            wire = {**copy.deepcopy(examples[row["base"]]), **row.get("set", {})}
            for member in row.get("remove", []):
                wire.pop(member)
            family = ("AuthenticationProblemDetail" if row["base"] in ("authentication_required", "step_up_required")
                      else "AuthenticationContextProblemDetail")
            cases.append({"name": row["name"], "schema": family, "wire": wire})
        for row in schema_results(cases):
            self.assertFalse(row["valid"], row["name"])

    def test_new_context_codes_cannot_be_emitted_by_an_unchanged_legacy_operation_response(self) -> None:
        new = {code: wire for code, wire in authentication_examples().items()
               if code not in ("authentication_required", "step_up_required")}
        cases = [{"name": code, "schema": name, "wire": wire}
                 for code, wire in new.items() for name in
                 ("AuthenticationProblemDetail", "AuthenticationRequiredProblemDetail", "OperationProblemDetail")]
        self.assertFalse(any(row["valid"] for row in schema_results(cases)))

    def test_missing_classification_or_auth_financial_replay_is_a_real_cli_failure(self) -> None:
        for mutation in (
            lambda value: value["authentication_policies"].pop("session_revoked"),
            lambda value: value["authentication_policies"]["session_revoked"].update(idempotency="reuse_unchanged"),
            lambda value: value["authentication_policies"]["recovery_locked"].update(retryable=True),
        ):
            with self.subTest(), SpecDirContext() as spec:
                path = spec.path / "error-catalogue.v1.json"
                value = json.loads(path.read_text(encoding="utf-8"))
                mutation(value)
                path.write_text(json.dumps(value), encoding="utf-8")
                result = run_script("lint_spec.py", "--spec", str(spec.write(spec_text())))
                self.assertEqual(result.returncode, 1)
                self.assertIn("[pl-error-provider]", result.stderr)


class SpecDirContext(SpecDir):
    def __enter__(self) -> SpecDirContext:
        return self

    def __exit__(self, *_args: object) -> None:
        self.cleanup()


if __name__ == "__main__":
    unittest.main()
