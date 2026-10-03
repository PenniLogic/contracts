"""T-CON-12 source/schema conformance; no running service or rendered UI is claimed."""

from __future__ import annotations

import copy
import json
import subprocess
import unittest

from support import ROOT, SPEC, SpecDir, replace_once, run_script, spec_text, with_probe_paths
from pl_contracts import node_executable


def error_examples() -> dict[str, dict]:
    catalogue = json.loads((ROOT / "spec" / "error-catalogue.v1.json").read_text(encoding="utf-8"))
    fixture = json.loads((ROOT / "spec" / "fixtures" / "error-provider.v1.json").read_text(encoding="utf-8"))
    entries = {entry["code"]: entry for entry in catalogue["codes"]}
    return {
        example["code"]: {
            "type": "urn:pennilogic:problem:" + example["code"],
            "title": entries[example["code"]]["title"], "status": entries[example["code"]]["status"],
            "detail": entries[example["code"]]["detail"], "correlation_id": fixture["correlation_id"], **example,
        }
        for example in fixture["examples"]
    }


def schema_results(cases: list[dict]) -> list[dict]:
    completed = subprocess.run(
        [node_executable(), str(ROOT / "scripts" / "tests" / "provider_schema.cjs"), str(SPEC)],
        cwd=ROOT, input=json.dumps(cases), capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if completed.returncode:
        raise AssertionError(completed.stdout + completed.stderr)
    return json.loads(completed.stdout)


class ErrorSchemaTest(unittest.TestCase):
    def test_every_catalogue_code_has_a_valid_documented_example(self) -> None:
        examples = error_examples()
        catalogue = json.loads((ROOT / "spec" / "error-catalogue.v1.json").read_text(encoding="utf-8"))
        self.assertEqual(set(examples), {entry["code"] for entry in catalogue["codes"]})
        cases = [{"name": code, "schema": "ServiceProblemDetail", "wire": wire} for code, wire in examples.items()]
        for result in schema_results(cases):
            self.assertTrue(result["valid"], result)

    def test_schema_rejects_disclosure_and_retry_shape_negatives(self) -> None:
        fixture = json.loads((ROOT / "spec" / "fixtures" / "error-provider.v1.json").read_text(encoding="utf-8"))
        examples = error_examples()
        cases = []
        for negative in fixture["invalid"]:
            wire = copy.deepcopy(examples[negative["base"]])
            wire.update(negative.get("set", {}))
            for member in negative.get("remove", []):
                wire.pop(member)
            cases.append({"name": negative["name"], "schema": "ServiceProblemDetail", "wire": wire})
        for result in schema_results(cases):
            self.assertFalse(result["valid"], result["name"])

    def test_ai_refusal_is_safe_successful_content_not_a_problem(self) -> None:
        fixture = json.loads((ROOT / "spec" / "fixtures" / "error-provider.v1.json").read_text(encoding="utf-8"))
        refusal = fixture["refusal"]
        cases = [
            {"name": "safe refusal", "schema": "AiRefusal", "wire": refusal},
            {"name": "refusal is not problem", "schema": "ServiceProblemDetail", "wire": refusal},
            {"name": "raw refusal", "schema": "AiRefusal", "wire": {**refusal, "message": "SYNTHETIC_PROVIDER_TEXT"}},
            {"name": "extra refusal content", "schema": "AiRefusal", "wire": {**refusal, "account": "SYNTHETIC_ACCOUNT"}},
        ]
        self.assertEqual([result["valid"] for result in schema_results(cases)], [True, False, False, False])


class ErrorCatalogueNegativeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = SpecDir()
        self.addCleanup(self.spec.cleanup)

    def reject_catalogue(self, mutate) -> str:
        path = self.spec.path / "error-catalogue.v1.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        mutate(value)
        path.write_text(json.dumps(value), encoding="utf-8")
        completed = run_script("lint_spec.py", "--spec", str(self.spec.write(spec_text())))
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertIn("[pl-error-provider]", completed.stderr)
        return completed.stderr

    def test_missing_mapping_fails(self) -> None:
        self.reject_catalogue(lambda value: value["codes"][0].pop("state"))

    def test_wrong_or_multiple_state_fails(self) -> None:
        self.reject_catalogue(lambda value: value["codes"][0].update(state=["error", "degraded"]))

    def test_missing_retry_classification_fails(self) -> None:
        self.reject_catalogue(lambda value: value["codes"][0].pop("retry_class"))

    def test_retry_that_changes_key_fails(self) -> None:
        self.reject_catalogue(lambda value: value["codes"][0].update(idempotency="new_after_edit"))

    def test_new_code_without_catalogue_fails(self) -> None:
        text = replace_once(spec_text(), "        - dependency_unavailable\n", "        - unclassified_code\n        - dependency_unavailable\n")
        completed = run_script("lint_spec.py", "--spec", str(self.spec.write(text)))
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertIn("adding an unclassified code fails", completed.stderr)

    def test_unconstrained_or_mismatched_diagnostic_fails(self) -> None:
        text = replace_once(spec_text(), "              detail: {const: The request could not be completed.}\n",
                            "              detail: {type: string}\n")
        completed = run_script("lint_spec.py", "--spec", str(self.spec.write(text)))
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertIn("catalogue type/title/status/detail constants", completed.stderr)

    def test_inline_service_error_fails(self) -> None:
        text = replace_once(with_probe_paths(spec_text()), "        '204':\n          description: ok\n",
                            "        '204':\n          description: ok\n        '422':\n          description: rejected\n"
                            "          content:\n            application/problem+json:\n              schema:\n                type: object\n")
        completed = run_script("lint_spec.py", "--spec", str(self.spec.write(text)))
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertIn("service error responses must reference".lower(), completed.stderr.lower())


if __name__ == "__main__":
    unittest.main()
