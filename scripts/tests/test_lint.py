"""Specification lint: the committed ruleset fails planted defects with actionable messages and passes the document."""

from __future__ import annotations

import json
import subprocess
import unittest
from unittest import mock

from support import SpecDir, replace_in_section, replace_once, replace_section, run_script, section_text, spec_text, with_probe_paths
import lint_spec


class LintCleanTest(unittest.TestCase):
    def test_probe_insertion_preserves_all_existing_operations_security_components_and_version(self) -> None:
        source = spec_text()
        combined = with_probe_paths(source)
        for name in ("info", "security", "components", "x-custom-destination-source", "x-dpop-protocol"):
            self.assertEqual(section_text(combined, (name,)), section_text(source, (name,)), name)
        self.assertEqual(section_text(combined, ("paths", "/ai/custom-destinations")),
                         section_text(source, ("paths", "/ai/custom-destinations")))
        restored = replace_section(combined, ("paths",), section_text(source, ("paths",)))
        restored = replace_section(restored, ("tags",), section_text(source, ("tags",)))
        self.assertEqual(restored, source)
        with self.assertRaises(AssertionError):
            with_probe_paths(combined)

    def test_named_mutation_changes_only_the_requested_header_when_shapes_repeat(self) -> None:
        source = spec_text()
        changed = replace_in_section(source, ("components", "parameters", "IdempotencyKey"),
                                     "      required: true\n", "      required: false\n")
        for name in ("DPoPProof", "StepUpToken"):
            self.assertEqual(section_text(changed, ("components", "parameters", name)),
                             section_text(source, ("components", "parameters", name)))
        self.assertNotEqual(source, changed)

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
        source = spec_text()
        version = next(line for line in section_text(source, ("info",)).splitlines(keepends=True) if line.startswith("  version:"))
        output = self.lint(replace_in_section(source, ("info",), version, ""))
        self.assertIn("[oas3-schema]", output)

    def test_non_semver_version(self) -> None:
        source = spec_text()
        version = next(line for line in section_text(source, ("info",)).splitlines(keepends=True) if line.startswith("  version:"))
        output = self.lint(replace_in_section(source, ("info",), version, "  version: '1.0'\n"))
        self.assertIn("[pl-info-version-semver]", output)
        output = self.lint(replace_in_section(source, ("info",), version, "  version: 1.0\n"))
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
        output = self.lint(replace_in_section(spec_text(), ("components", "parameters", "IdempotencyKey"),
                                             "      required: true\n", "      required: false\n"))
        self.assertIn("[pl-idempotency-key-parameter-binding]", output)
        output = self.lint(replace_in_section(spec_text(), ("components", "headers", "IdempotentReplayed"),
                                             "      schema: { type: boolean }\n", "      schema: { type: string }\n"))
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
        chain = ("    Entry:\n      type: object\n      properties:\n        lines:\n          type: array\n          items:\n            $ref: '#/components/schemas/Line'\n"
                 "        parent:\n          $ref: '#/components/schemas/Entry'\n    Line:\n      type: object\n      allOf:\n        - type: object\n          properties:\n"
                 "            value:\n              $ref: '#/components/schemas/Money'\n    Money:\n")
        text = replace_once(with_probe_paths(spec_text()), "    Money:\n", chain)
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
        anchor = ("        status:\n          type: integer\n          minimum: 400\n          maximum: 599\n"
                  "          description: HTTP status code of this response.\n")
        text = replace_once(spec_text(), anchor, anchor.replace("type: integer\n", "type: number\n          format: double\n"))
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
        text = replace_section(with_probe_paths(spec_text()), ("security",), "")
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


class LintRefusalBatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = SpecDir()
        self.addCleanup(self.spec.cleanup)
        self.files = [self.spec.write("{}", f"{index:02}.json") for index in range(3)]

    def finding(self, index: int) -> dict:
        return {
            "code": "synthetic-rule", "message": f"synthetic refusal {index}", "severity": 0,
            "path": ["components", "schemas", "Synthetic", index],
            "range": {"start": {"line": index, "character": 1}, "end": {"line": index, "character": 2}},
            "source": self.files[index].as_posix(),
        }

    def test_batch_preserves_full_findings_and_original_cli_flags(self) -> None:
        findings = [self.finding(index) for index in range(3)]
        findings[1]["severity"] = 1
        findings[1]["additional"] = {"retained": "not a conveniently filtered pair"}
        completed = subprocess.CompletedProcess([], 1, json.dumps(findings), "")
        requested = [self.files[2], self.files[0], self.files[1]]
        with mock.patch.object(lint_spec, "run", return_value=completed) as run, \
                mock.patch.object(lint_spec, "node_executable", return_value="synthetic-node"):
            batch = lint_spec.lint_refusals(requested)
        self.assertEqual(batch, {"cli_exit_code": 1, "documents": [
            {"source": self.files[index].as_posix(), "findings": [findings[index]]} for index in (2, 0, 1)
        ]})
        run.assert_called_once_with(
            ["synthetic-node", str(lint_spec.SPECTRAL), "lint", *(str(file) for file in requested),
             "--ruleset", str(lint_spec.RULESET), "--fail-severity", "warn",
             "--display-only-failures", "--format", "json"], capture=True, check=False,
        )

    def test_batch_rejects_missing_duplicate_glob_and_registry_inputs_before_launch(self) -> None:
        requests = [[], [self.files[0], self.files[0]], [self.spec.path / "glob[0].json"]]
        for index in range(3):
            files = list(self.files)
            files[index] = self.spec.path / "missing.json"
            requests.append(files)
        with mock.patch.object(lint_spec, "run") as run:
            for files in requests:
                with self.subTest(files=files), self.assertRaises(lint_spec.PipelineError):
                    lint_spec.lint_refusals(files)
            (self.spec.path / "currency-registry.v1.json").unlink()
            with self.assertRaisesRegex(lint_spec.PipelineError, "currency-registry.v1.json is missing"):
                lint_spec.lint_refusals(self.files)
            run.assert_not_called()

    def test_batch_rejects_bad_global_outcomes_or_json(self) -> None:
        cases = [(0, "[]"), (2, "[]"), (-9, "[]"), (1, ""), (1, "{}"), (1, "[]"),
                 (1, "[null]"), (1, "[NaN]"), (1, '[{"code":"first","code":"second"}]'),
                 (1, "[]\nUnexpected trailing output")]
        for code, stdout in cases:
            with self.subTest(code=code, stdout=stdout), \
                    mock.patch.object(lint_spec, "run", return_value=subprocess.CompletedProcess([], code, stdout, "synthetic stderr")), \
                    self.assertRaises(lint_spec.PipelineError):
                lint_spec.lint_refusals(self.files)

    def test_batch_rejects_missing_foreign_duplicate_and_malformed_findings(self) -> None:
        original = [self.finding(index) for index in range(3)]
        cases = [original[:index] + original[index + 1:] for index in range(3)]
        cases.append(original + [self.finding(0)])
        for field, value in [
            ("source", None), ("source", "00.json"), ("source", (self.spec.path / "foreign.json").as_posix()),
            ("code", None), ("message", None), ("severity", True), ("severity", 2), ("severity", "0"),
            ("path", None), ("path", [False]), ("path", [-1]), ("path", [{}]), ("range", None),
            ("range", {"start": {"line": -1, "character": 0}, "end": {"line": 0, "character": 0}}),
            ("range", {"start": {"line": 2, "character": 1}, "end": {"line": 2, "character": 0}}),
        ]:
            changed = [self.finding(index) for index in range(3)]
            changed[1][field] = value
            cases.append(changed)
        for findings in cases:
            with self.subTest(findings=findings), \
                    mock.patch.object(lint_spec, "run", return_value=subprocess.CompletedProcess([], 1, json.dumps(findings), "")), \
                    self.assertRaises(lint_spec.PipelineError):
                lint_spec.lint_refusals(self.files)

    def test_real_batch_matches_complete_single_findings_with_reordered_inputs(self) -> None:
        changes = [
            (("components", "schemas", "Money"), "          maxLength: 21\n", "          maxLength: 22\n"),
            (("components", "schemas", "Instant"), "      minLength: 24\n", "      minLength: 20\n"),
            (("components", "schemas", "TimeZone"), "      maxLength: 64\n", "      maxLength: 128\n"),
        ]
        files = [self.spec.write(replace_in_section(spec_text(), section, old, new), f"{index:02}.yaml")
                 for index, (section, old, new) in enumerate(changes)]
        originals = {file: file.read_bytes() for file in self.spec.path.iterdir()}
        singles = {}
        for file in files:
            code, findings = lint_spec.lint(file)
            self.assertEqual(code, 1)
            self.assertTrue(findings)
            singles[file.as_posix()] = findings
        requested = [files[2], files[0], files[1]]
        completed = run_script("lint_spec.py", "--refusal-specs", *(str(file) for file in requested))
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        batch = json.loads(completed.stdout)
        self.assertEqual(batch["cli_exit_code"], 1)
        self.assertEqual([item["source"] for item in batch["documents"]], [file.as_posix() for file in requested])
        for item in batch["documents"]:
            self.assertEqual(item["findings"], singles[item["source"]])
        self.assertEqual({file: file.read_bytes() for file in self.spec.path.iterdir()}, originals)

    def test_real_batch_rejects_clean_input_at_first_middle_and_last(self) -> None:
        negative = replace_in_section(spec_text(), ("components", "schemas", "TimeZone"),
                                      "      maxLength: 64\n", "      maxLength: 128\n")
        for position in range(3):
            with self.subTest(position=position):
                files = [self.spec.write(spec_text() if index == position else negative, f"{index:02}.yaml")
                         for index in range(3)]
                completed = run_script("lint_spec.py", "--refusal-specs", *(str(file) for file in reversed(files)))
                self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
                self.assertEqual(completed.stdout, "")
                self.assertIn("refusal batch has no findings for: " + str(files[position]), completed.stderr)

    def test_real_batch_retains_parser_refusals_at_first_middle_and_last(self) -> None:
        negative = replace_in_section(spec_text(), ("components", "schemas", "TimeZone"),
                                      "      maxLength: 64\n", "      maxLength: 128\n")
        malformed = spec_text() + "\n  this is: [not: valid\n"
        for position in range(3):
            with self.subTest(position=position):
                files = [self.spec.write(malformed if index == position else negative, f"{index:02}.yaml")
                         for index in range(3)]
                code, expected = lint_spec.lint(files[position])
                self.assertEqual(code, 1)
                completed = run_script("lint_spec.py", "--refusal-specs", *(str(file) for file in files))
                self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
                batch = json.loads(completed.stdout)
                self.assertEqual(batch["cli_exit_code"], 1)
                self.assertEqual(len(batch["documents"]), 3)
                self.assertTrue(all(item["findings"] for item in batch["documents"]))
                self.assertEqual(batch["documents"][position]["findings"], expected)
                self.assertTrue(any(item["code"] in ("parser", "oas3-schema") for item in expected))

    def test_batch_cli_keeps_spec_flags_exclusive_and_ruleset_errors(self) -> None:
        completed = run_script("lint_spec.py", "--spec", str(self.files[0]), "--refusal-specs", str(self.files[1]))
        self.assertEqual(completed.returncode, 2)
        self.assertIn("not allowed with argument", completed.stderr)
        self.assertEqual(completed.stdout, "")
        completed = run_script("lint_spec.py", "--refusal-specs", str(self.files[0]),
                               "--ruleset", str(self.spec.path / "missing-ruleset.yaml"))
        self.assertEqual(completed.returncode, 2)
        self.assertIn("Spectral could not lint", completed.stderr)
        self.assertEqual(completed.stdout, "")


if __name__ == "__main__":
    unittest.main()
