"""The accepted ADR-016 direction is a current shared provider obligation."""

from __future__ import annotations

import json
import unittest

from support import ROOT, SpecDir, replace_once, run_script, spec_text
from test_error_provider import schema_results


class AllocationRefusalSchemaTest(unittest.TestCase):
    def test_missing_direction_fails_and_both_safe_directions_pass(self) -> None:
        data = json.loads((ROOT / "spec" / "fixtures" / "allocation-refusal.v1.json").read_text(encoding="utf-8"))
        cases = [{"name": "missing", "schema": "ServiceProblemDetail", "wire": data["problem"]}]
        cases += [{"name": direction, "schema": "ServiceProblemDetail", "wire": {**data["problem"], "direction": direction}}
                  for direction in data["directions"]]
        self.assertEqual([result["valid"] for result in schema_results(cases)], [False, True, True])

    def test_unsafe_unrelated_and_nested_direction_cases_are_closed(self) -> None:
        data = json.loads((ROOT / "spec" / "fixtures" / "allocation-refusal.v1.json").read_text(encoding="utf-8"))
        cases = [{"name": "bad direction", "schema": "ServiceProblemDetail", "wire": {**data["problem"], "direction": value}}
                 for value in data["invalid_directions"]]
        cases += [
            {"name": "other reason", "schema": "ServiceProblemDetail", "wire": {**data["problem"], "reason": "shape", "direction": "shortfall"}},
            {"name": "amount forbidden", "schema": "ServiceProblemDetail", "wire": {**data["problem"], "direction": "shortfall", "amount": "12.34"}},
            {"name": "missing field", "schema": "ServiceProblemDetail", "wire": {key: value for key, value in {**data["problem"], "direction": "shortfall"}.items() if key != "field"}},
            {"name": "issue missing", "schema": "ValidationIssue", "wire": {"field": "allocation", "reason": "allocation_sum_mismatch"}},
            {"name": "issue unsafe", "schema": "ValidationIssue", "wire": {"field": "allocation", "reason": "allocation_sum_mismatch", "direction": {"amount": "12.34"}}},
        ]
        self.assertFalse(any(result["valid"] for result in schema_results(cases)))
        nested = {**data["problem"], "direction": "shortfall",
                  "validation_errors": [{"field": "allocation", "reason": "allocation_sum_mismatch", "direction": "shortfall"}]}
        self.assertTrue(schema_results([{"name": "nested valid", "schema": "ServiceProblemDetail", "wire": nested}])[0]["valid"])
        del nested["validation_errors"][0]["direction"]
        self.assertFalse(schema_results([{"name": "nested missing", "schema": "ServiceProblemDetail", "wire": nested}])[0]["valid"])

    def test_the_accepted_direction_binding_cannot_be_removed_or_reinvented(self) -> None:
        specs = SpecDir()
        self.addCleanup(specs.cleanup)
        for index, text in enumerate((
            replace_once(spec_text(), "      enum: [shortfall, excess]\n", "      enum: [shortfall, excess, unknown]\n"),
            replace_once(spec_text(), "            required: [field, direction]\n", "            required: [field]\n"),
        )):
            with self.subTest(mutation=index):
                result = run_script("lint_spec.py", "--spec", str(specs.write(text)))
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("ADR-016 allocation_sum_mismatch", result.stderr)


if __name__ == "__main__":
    unittest.main()
