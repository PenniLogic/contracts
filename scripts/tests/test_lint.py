"""Specification lint: the committed ruleset fails planted defects with actionable messages and passes the document."""

from __future__ import annotations

import unittest

from support import SpecDir, replace_once, run_script, spec_text, with_probe_paths


class LintCleanTest(unittest.TestCase):
    def test_document_passes(self) -> None:
        completed = run_script("lint_spec.py")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("specification lint passed", completed.stdout)

    def test_probe_operations_pass_when_they_follow_the_rules(self) -> None:
        spec = SpecDir()
        try:
            completed = run_script("lint_spec.py", "--spec", str(spec.write(with_probe_paths(spec_text()))))
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        finally:
            spec.cleanup()


class PlantedDefectTest(unittest.TestCase):
    """Every rule fires on a planted defect and the failure names rule, location and fix."""

    def setUp(self) -> None:
        self.spec = SpecDir()
        self.addCleanup(self.spec.cleanup)

    def lint(self, text: str) -> str:
        completed = run_script("lint_spec.py", "--spec", str(self.spec.write(text)))
        self.assertEqual(completed.returncode, 1, "planted defect must exit 1\n" + completed.stdout + completed.stderr)
        self.assertIn("Specification lint failed", completed.stderr)
        self.assertIn("re-run python scripts/lint_spec.py", completed.stderr)
        return completed.stderr

    def test_malformed_yaml_fails_with_parser_error(self) -> None:
        output = self.lint(spec_text() + "\n  this is: [not: valid\n")
        self.assertRegex(output, r"\[(parser|oas3-schema)\]")

    def test_missing_info_version_fails_schema_rule(self) -> None:
        output = self.lint(replace_once(spec_text(), "  version: 0.1.0\n", ""))
        self.assertIn("[oas3-schema]", output)

    def test_non_semver_version(self) -> None:
        output = self.lint(replace_once(spec_text(), "  version: 0.1.0\n", "  version: '1.0'\n"))
        self.assertIn("[pl-info-version-semver]", output)
        output = self.lint(replace_once(spec_text(), "  version: 0.1.0\n", "  version: 1.0\n"))
        self.assertIn("[oas3-schema]", output)  # a YAML float is not a string version either

    def test_money_binding_shape_drift(self) -> None:
        output = self.lint(replace_once(spec_text(), "          maxLength: 21\n", "          maxLength: 22\n"))
        self.assertIn("[pl-money-component-binding]", output)
        self.assertIn("Money.properties.amount.maxLength must be 21", output)

    def test_money_binding_extra_key_and_missing_component(self) -> None:
        output = self.lint(replace_once(spec_text(), "      additionalProperties: false\n      required: [amount, currency]\n", "      additionalProperties: false\n      required: [amount, currency]\n      minProperties: 2\n"))
        self.assertIn("Money.minProperties is not part of the binding fragment", output)
        output = self.lint(replace_once(spec_text(), "    Money:\n      type: object\n", "    MoneyRenamed:\n      type: object\n"))
        self.assertIn("[pl-money-component-binding]", output)

    def test_instant_timezone_key_and_header_bindings(self) -> None:
        output = self.lint(replace_once(spec_text(), "      minLength: 24\n      maxLength: 24\n", "      minLength: 20\n      maxLength: 24\n"))
        self.assertIn("[pl-instant-component-binding]", output)
        output = self.lint(replace_once(spec_text(), "      pattern: '^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*$'\n      maxLength: 64\n", "      pattern: '^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*$'\n      maxLength: 128\n"))
        self.assertIn("[pl-timezone-component-binding]", output)
        output = self.lint(replace_once(spec_text(), "      in: header\n      required: true\n", "      in: header\n      required: false\n"))
        self.assertIn("[pl-idempotency-key-parameter-binding]", output)
        output = self.lint(replace_once(spec_text(), "      schema: { type: boolean }\n", "      schema: { type: string }\n"))
        self.assertIn("[pl-idempotent-replayed-header-binding]", output)

    def test_money_named_property_must_reference_money(self) -> None:
        text = replace_once(spec_text(), "        reason:\n          type: string\n", "        reason:\n          type: string\n        refund_amount:\n          type: string\n")
        output = self.lint(text)
        self.assertIn("[pl-money-bearing-property-references-money]", output)
        self.assertIn("refund_amount", output)
        self.assertIn("[pl-problem-detail-no-money]", output)

    def test_minor_unit_names_are_money_and_unit_counts_are_not(self) -> None:
        text = replace_once(spec_text(), "        reason:\n          type: string\n          pattern: '^[a-z][a-z0-9_]*$'\n          maxLength: 64\n",
                            "        reason:\n          type: string\n          pattern: '^[a-z][a-z0-9_]*$'\n          maxLength: 64\n"
                            "        balance_minor_units:\n          type: integer\n          format: int64\n        amountMinorUnits:\n          type: integer\n"
                            "        minor_units:\n          type: integer\n        quantity_units:\n          type: integer\n        unit_count:\n          type: integer\n")
        output = self.lint(text)
        for name in ("balance_minor_units", "amountMinorUnits", "minor_units"):
            self.assertIn(f"property '{name}' looks like money", output)
        for name in ("quantity_units", "unit_count"):
            self.assertNotIn(name, output)
        self.assertEqual(output.count("[pl-money-bearing-property-references-money]"), 3)

    def test_binding_fragment_refuses_an_extra_property_named_like_a_descriptive_key(self) -> None:
        text = replace_once(spec_text(), "          pattern: '^[A-Z]{3}$'\n          description: ISO 4217 alphabetic code present in the published currency registry.\n",
                            "          pattern: '^[A-Z]{3}$'\n          description: ISO 4217 alphabetic code present in the published currency registry.\n        title:\n          type: string\n")
        output = self.lint(text)
        self.assertIn("Money.properties.title is not part of the binding fragment", output)
        # A descriptive key on the schema object itself stays allowed.
        text = replace_once(spec_text(), "    Money:\n      type: object\n", "    Money:\n      title: Money\n      type: object\n")
        completed = run_script("lint_spec.py", "--spec", str(self.spec.write(text)))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_money_behind_a_reference_chain_cannot_be_exempt(self) -> None:
        # Entry -> lines[] -> Line -> allOf -> Money, plus a self-reference (Entry.parent) that must not loop the walk.
        chain = ("  schemas:\n    Entry:\n      type: object\n      properties:\n        lines:\n          type: array\n          items:\n            $ref: '#/components/schemas/Line'\n"
                 "        parent:\n          $ref: '#/components/schemas/Entry'\n    Line:\n      type: object\n      allOf:\n        - type: object\n          properties:\n"
                 "            value:\n              $ref: '#/components/schemas/Money'\n    Money:\n")
        text = replace_once(with_probe_paths(spec_text()), "  schemas:\n    Money:\n", chain)
        indirect = replace_once(text, "      parameters:\n        - $ref: '#/components/parameters/IdempotencyKey'\n      requestBody:\n        content:\n          application/json:\n            schema:\n              type: object\n              properties:\n                total:\n                  $ref: '#/components/schemas/Money'\n",
                                "      x-idempotency: not-applicable\n      requestBody:\n        content:\n          application/json:\n            schema:\n              $ref: '#/components/schemas/Entry'\n")
        output = self.lint(indirect)
        self.assertIn("carries Money (directly or through referenced schemas) can never be exempt", output)
        # Positive: the same indirect Money-bearing body with the key declared passes.
        keyed = replace_once(indirect, "      x-idempotency: not-applicable\n", "      parameters:\n        - $ref: '#/components/parameters/IdempotencyKey'\n")
        completed = run_script("lint_spec.py", "--spec", str(self.spec.write(keyed)))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        # Positive: an exemption on a body whose reference chain carries no Money passes.
        money_free = replace_once(indirect, "            value:\n              $ref: '#/components/schemas/Money'\n", "            value:\n              type: string\n")
        completed = run_script("lint_spec.py", "--spec", str(self.spec.write(money_free)))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
    def test_x_not_money_escape_hatch_needs_a_reason(self) -> None:
        text = with_probe_paths(spec_text())
        text = replace_once(text, "                total:\n                  $ref: '#/components/schemas/Money'\n      responses:\n        '204':",
                            "                total:\n                  $ref: '#/components/schemas/Money'\n                fee_label:\n                  type: string\n                total_count:\n                  type: integer\n                price_note:\n                  type: string\n                  x-not-money: free-text label, never a value\n      responses:\n        '204':")
        completed = run_script("lint_spec.py", "--spec", str(self.spec.write(text)))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_json_number_and_float_formats_refused(self) -> None:
        text = replace_once(spec_text(), "        status:\n          type: integer\n", "        status:\n          type: number\n          format: double\n")
        output = self.lint(text)
        self.assertEqual(output.count("[pl-no-json-number]"), 2)
        self.assertIn("type: number is refused", output)
        self.assertIn("format: double is refused", output)

    def test_mutating_operation_without_key_or_exemption(self) -> None:
        text = replace_once(with_probe_paths(spec_text()), "      parameters:\n        - $ref: '#/components/parameters/IdempotencyKey'\n", "")
        output = self.lint(text)
        self.assertIn("[pl-mutating-operation-idempotency]", output)
        self.assertIn("POST /probe", output)

    def test_money_bearing_operation_cannot_be_exempt_and_exemption_values_are_fixed(self) -> None:
        text = replace_once(with_probe_paths(spec_text()), "      parameters:\n        - $ref: '#/components/parameters/IdempotencyKey'\n", "      x-idempotency: because\n")
        output = self.lint(text)
        self.assertIn("x-idempotency must be one of", output)
        self.assertIn("can never be exempt", output)

    def test_valid_exemption_on_money_free_operation_passes(self) -> None:
        text = with_probe_paths(spec_text())
        text = replace_once(text, "      parameters:\n        - $ref: '#/components/parameters/IdempotencyKey'\n      requestBody:\n        content:\n          application/json:\n            schema:\n              type: object\n              properties:\n                total:\n                  $ref: '#/components/schemas/Money'\n",
                            "      x-idempotency: not-applicable\n")
        completed = run_script("lint_spec.py", "--spec", str(self.spec.write(text)))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_key_on_safe_method_refused(self) -> None:
        text = replace_once(with_probe_paths(spec_text()), "      operationId: getProbe\n      description: Probe read.\n      tags: [probe]\n", "      operationId: getProbe\n      description: Probe read.\n      tags: [probe]\n      parameters:\n        - $ref: '#/components/parameters/IdempotencyKey'\n")
        output = self.lint(text)
        self.assertIn("[pl-safe-method-no-idempotency-key]", output)

    def test_operation_without_security_refused(self) -> None:
        text = replace_once(with_probe_paths(spec_text()), "security:\n  - probe: []\n", "")
        output = self.lint(text)
        self.assertIn("[pl-operation-security]", output)
        text = replace_once(with_probe_paths(spec_text()), "      operationId: getProbe\n", "      operationId: getProbe\n      security: []\n")
        output = self.lint(text)
        self.assertIn("anonymous operations are not published", output)

    def test_money_example_scale_and_currency(self) -> None:
        output = self.lint(replace_once(spec_text(), 'examples: [{ amount: "-1234.56", currency: "INR" }]', 'examples: [{ amount: "-1234.5", currency: "INR" }]'))
        self.assertIn("[pl-money-example-registry-scale]", output)
        self.assertIn("scale_mismatch", output)
        output = self.lint(replace_once(spec_text(), 'examples: [{ amount: "-1234.56", currency: "INR" }]', 'examples: [{ amount: "-1234.56", currency: "XYZ" }]'))
        self.assertIn("currency_unknown", output)

    def test_missing_registry_next_to_the_document_is_an_explicit_failure(self) -> None:
        (self.spec.path / "currency-registry.v1.json").unlink()
        completed = run_script("lint_spec.py", "--spec", str(self.spec.write(spec_text())))
        self.assertEqual(completed.returncode, 2)
        self.assertIn("currency-registry.v1.json is missing", completed.stderr)


if __name__ == "__main__":
    unittest.main()
