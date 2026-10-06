"""Owning shared error composition, with finite source and real Spectral controls."""

from __future__ import annotations

import copy
import json
import subprocess
import unittest

from support import ROOT, SPEC, SpecDir, replace_in_section, run_script, spec_text
from pl_contracts import node_executable
from test_error_provider import error_examples, schema_results


FIXTURE = json.loads((ROOT / "spec" / "fixtures" / "egress-errors.v1.json").read_text(encoding="utf-8"))
CATALOGUE = json.loads((ROOT / "spec" / "error-catalogue.v1.json").read_text(encoding="utf-8"))
ENTRIES = {entry["code"]: entry for entry in CATALOGUE["codes"] + CATALOGUE["authentication_codes"]}


def wire(case: dict) -> dict:
    entry = ENTRIES[case["code"]]
    result = {"type": "urn:pennilogic:problem:" + entry["code"], "title": entry["title"],
              "status": entry["status"], "detail": entry["detail"], "code": entry["code"],
              "correlation_id": FIXTURE["correlation_id"], **case.get("extra", {})}
    if "reason" in case:
        result["egress_denial_reason"] = case["reason"]
    return result


class EgressErrorSchemaTest(unittest.TestCase):
    def test_positive_service_subset_preserves_old_shapes_and_binds_new_safe_diagnostics_without_auth(self) -> None:
        ordinary = error_examples()
        self.assertEqual(len(ordinary), 14)
        value = wire({"code": "egress_denied"})
        controls = [{"name": code, "schema": "ServiceProblemDetail", "wire": old}
                    for code, old in ordinary.items()]
        controls.append({"name": "new service code", "schema": "ServiceProblemDetail", "wire": value})
        for result in schema_results(controls):
            self.assertTrue(result["valid"], result)
        invalid = [{"name": family, "schema": family, "wire": value} for family in
                   ("EgressDeniedProblemDetail", "OperationProblemDetail", "AuthenticationProblemDetail")]
        for member, changed in (("status", 401), ("status", 503), ("detail", "PRIVATE_SYNTHETIC_CANARY"),
                                ("title", "PRIVATE_SYNTHETIC_CANARY"), ("egress_denial_reason", "destination_denied")):
            invalid.append({"name": member, "schema": "ServiceProblemDetail", "wire": {**value, member: changed}})
        for case in FIXTURE["authentication"]:
            invalid.append({"name": case["code"], "schema": "ServiceProblemDetail", "wire": wire(case)})
        for result in schema_results(invalid):
            self.assertFalse(result["valid"], result)

    def test_every_canonical_reason_has_exactly_one_global_code_status_and_typed_family(self) -> None:
        consequence = json.loads((ROOT / "spec" / "adr022" / "ai-egress-consequences.json").read_text(encoding="utf-8"))
        cases = FIXTURE["cases"]
        self.assertEqual([case["reason"] for case in cases], consequence["enums"]["EgressDenialReason"])
        self.assertEqual({case["reason"]: case["code"] for case in cases}, CATALOGUE["egress_binding"]["reason_codes"])
        controls = []
        for case in cases:
            self.assertEqual(case["status"], ENTRIES[case["code"]]["status"])
            family = "AuthenticationProblemDetail" if case["reason"] == "step_up_required" else "EgressDeniedProblemDetail"
            controls.extend([
                {"name": case["reason"], "schema": family, "wire": wire(case)},
                {"name": case["reason"], "schema": "OperationProblemDetail", "wire": wire(case)},
            ])
        for result in schema_results(controls):
            self.assertTrue(result["valid"], result)

    def test_all_original_service_examples_survive_the_union_without_an_egress_member(self) -> None:
        for result in schema_results([{"name": code, "schema": "OperationProblemDetail", "wire": value}
                                      for code, value in error_examples().items()]):
            self.assertTrue(result["valid"], result)
        self.assertEqual(set(error_examples()) | {case["code"] for case in FIXTURE["cases"] + FIXTURE["authentication"]},
                         set(ENTRIES))

    def test_authentication_is_null_state_and_unrelated_confirmation_needs_no_egress_member(self) -> None:
        bindings = json.loads((ROOT / "spec" / "client-state-bindings.v1.json").read_text(encoding="utf-8"))
        self.assertEqual(len(bindings["states"]), 8)
        controls = []
        for case in FIXTURE["authentication"]:
            entry = ENTRIES[case["code"]]
            self.assertIsNone(entry["state"])
            self.assertEqual(entry["classification"], "authentication_owned")
            self.assertEqual(entry["flow"], "authentication_required")
            self.assertIn(entry["flow"], bindings["excluded_conditions"])
            controls.extend([
                {"name": case["code"], "schema": "AuthenticationProblemDetail", "wire": wire(case)},
                {"name": case["code"], "schema": "ServiceProblemDetail", "wire": wire(case)},
                {"name": case["code"], "schema": "EgressDeniedProblemDetail", "wire": wire(case)},
                {"name": case["code"], "schema": "AuthenticationRequiredProblemDetail", "wire": wire(case)},
            ])
        self.assertEqual([result["valid"] for result in schema_results(controls)],
                         [True, False, False, True, True, False, False, False])

    def test_wrong_family_code_status_reason_missing_members_and_private_nested_fields_fail(self) -> None:
        originals = {case["reason"]: wire(case) for case in FIXTURE["cases"]}
        controls = []
        for negative in FIXTURE["invalid"]:
            value = {**copy.deepcopy(originals[negative["base"]]), **negative.get("set", {})}
            for member in negative.get("remove", []):
                value.pop(member)
            controls.append({"name": negative["name"], "schema": "OperationProblemDetail", "wire": value})
        for case in FIXTURE["cases"]:
            controls.append({"name": case["reason"], "schema": "ServiceProblemDetail", "wire": wire(case)})
            for code in ENTRIES:
                if code == case["code"]:
                    continue
                value = {**wire(case), **wire({"code": code})}
                controls.append({"name": case["reason"] + " -> " + code, "schema": "OperationProblemDetail", "wire": value})
        for result in schema_results(controls):
            self.assertFalse(result["valid"], result)

    def test_real_spectral_rejects_a_permissive_default_response_on_the_named_operation(self) -> None:
        directory = SpecDir()
        self.addCleanup(directory.cleanup)
        text = replace_in_section(spec_text(),
                                  ("components", "responses", "CustomDestinationProblem"),
                                  "#/components/schemas/OperationProblemDetail", "#/components/schemas/ProblemDetail")
        result = run_script("lint_spec.py", "--spec", str(directory.write(text)))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("[pl-error-provider]", result.stderr)
        self.assertIn("resolved strict shared error family", result.stderr)

    def test_resolved_rule_rejects_family_media_header_and_nonce_mutations_without_raw_exemptions(self) -> None:
        mutations = [
            ("default body", ["components", "responses", "CustomDestinationProblem", "content", "application/problem+json", "schema"],
             {"$ref": "#/components/schemas/ProblemDetail"}),
            ("raw union branch", ["components", "responses", "CustomDestinationProblem", "content", "application/problem+json", "schema"],
             {"oneOf": [{"$ref": "#/components/schemas/OperationProblemDetail"}, {"type": "object"}]}),
            ("wrong 401 family", ["components", "responses", "CustomDestinationDPoPChallenge", "content", "application/problem+json", "schema"],
             {"$ref": "#/components/schemas/ServiceProblemDetail"}),
            ("wrong media", ["components", "responses", "CustomDestinationProblem", "content", "text/plain"],
             {"schema": {"type": "string"}}),
            ("missing authenticate", ["components", "responses", "CustomDestinationDPoPChallenge", "headers", "WWW-Authenticate"], None),
            ("optional authenticate", ["components", "headers", "DPoPAuthenticate", "required"], False),
            ("wrong nonce challenge", ["components", "responses", "CustomDestinationDPoPChallenge", "x-dpop-nonce-challenge", "authenticate"],
             'DPoP error="invalid_token"'),
            ("missing nonce", ["components", "responses", "CustomDestinationDPoPChallenge", "headers", "DPoP-Nonce"], None),
            ("cacheable error", ["components", "headers", "CustomDestinationNoStore", "schema", "const"], "public"),
            ("optional no-store", ["components", "headers", "CustomDestinationNoStore", "required"], False),
            ("missing delay header", ["components", "responses", "CustomDestinationProblem", "headers", "Retry-After"], None),
            ("unbound delay", ["components", "responses", "CustomDestinationProblem", "x-response-header-bindings", "retry_after_seconds"], "Unknown"),
            ("open family", ["components", "schemas", "EgressDeniedProblemDetail", "additionalProperties"], True),
            ("forked reason", ["components", "schemas", "EgressDeniedProblemDetail", "properties", "egress_denial_reason", "$ref"],
             "#/components/schemas/ValidationReason"),
            ("missing positive service subset", ["components", "schemas", "ServiceProblemDetail", "properties", "code", "enum"], None),
            ("auth in service subset", ["components", "schemas", "ServiceProblemDetail", "properties", "code", "enum"],
             [entry["code"] for entry in CATALOGUE["codes"]] + ["authentication_required"]),
            ("duplicate service enum", ["components", "schemas", "ServiceProblemDetail", "properties", "code", "enum"],
             [entry["code"] for entry in CATALOGUE["codes"]] + ["egress_denied"]),
            ("missing old service code", ["components", "schemas", "ServiceProblemDetail", "properties", "code", "enum"],
             [entry["code"] for entry in CATALOGUE["codes"] if entry["code"] != "request_failed"]),
            ("negative service partition", ["components", "schemas", "ServiceProblemDetail", "properties", "code", "not"],
             {"const": "authentication_required"}),
            ("untyped egress operation", ["components", "schemas", "OperationProblemDetail", "allOf", 1, "if", "properties", "code"],
             {"const": "step_up_required"}),
        ]
        script = r"""
const fs = require('node:fs');
const {Yaml} = require('@stoplight/spectral-parsers');
const rule = require('./spec/spectral-functions/providerErrors.js');
const source = process.argv[1];
const document = JSON.parse(JSON.stringify(Yaml.parse(fs.readFileSync(source, 'utf8')).data));
const results = JSON.parse(fs.readFileSync(0, 'utf8')).map(([name, path, value]) => {
  const changed = JSON.parse(JSON.stringify(document));
  const parent = path.slice(0, -1).reduce((node, key) => node[key], changed);
  if (value === null) delete parent[path.at(-1)]; else parent[path.at(-1)] = value;
  return {name, messages: rule(changed, null, {document: {source}}).map((result) => result.message)};
});
process.stdout.write(JSON.stringify(results));
"""
        result = subprocess.run([node_executable(), "-e", script, str(SPEC)], cwd=ROOT, input=json.dumps(mutations),
                                capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        for control in json.loads(result.stdout):
            self.assertTrue(control["messages"], control["name"])


if __name__ == "__main__":
    unittest.main()
