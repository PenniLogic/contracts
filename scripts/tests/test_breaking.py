"""Breaking-change detection: a planted removal fails the check unless explicitly acknowledged."""

from __future__ import annotations

import json
import copy
import subprocess
import unittest

from support import ROOT, SPEC, SpecDir, replace_in_section, replace_once, replace_section, run_script, section_text, spec_text, with_probe_paths
import toolchain

import check_breaking_changes as cbc


def published_tags_exist() -> bool:
    completed = subprocess.run(["git", "tag", "--list", "v*"], cwd=str(ROOT), capture_output=True, text=True, check=False)
    return bool(completed.stdout.strip())


def with_inheritance_probe(text: str) -> str:
    """Isolate the supported allOf proof without deleting live union consumers or their responses."""
    base = section_text(text, ("components", "schemas", "ProblemDetail"))
    base = base.replace("    ProblemDetail:\n", "    BreakingProbeBase:\n", 1)
    provider = """    BreakingProbeProvider:
      type: object
      additionalProperties: false
      required: [type, title, status]
      allOf:
        - $ref: '#/components/schemas/BreakingProbeBase'
      properties:
        type: {type: string}
        title: {type: string}
        status: {type: integer}
        detail: {type: string}
        correlation_id: {type: string}
"""
    response = """    BreakingProbeResponse:
      description: Synthetic inherited optional-property probe.
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/BreakingProbeProvider'
"""
    text = replace_section(text, ("components", "schemas"),
                           section_text(text, ("components", "schemas")) + base + provider)
    text = replace_section(text, ("components", "responses"),
                           section_text(text, ("components", "responses")) + response)
    return replace_section(with_probe_paths(text), ("paths", "/probe", "get", "responses", "200"),
                           "        '200':\n          $ref: '#/components/responses/BreakingProbeResponse'\n")


class BaselineTest(unittest.TestCase):
    def test_identity_has_no_breaking_change(self) -> None:
        completed = run_script("check_breaking_changes.py", "--base", str(SPEC), "--revision", str(SPEC))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("no breaking change", completed.stdout)

    def test_default_run_uses_the_highest_tag_or_reports_the_missing_baseline(self) -> None:
        completed = run_script("check_breaking_changes.py")
        if published_tags_exist():
            self.assertIn("against v", completed.stdout + completed.stderr)
        else:
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertIn("no published v* tag exists yet", completed.stdout)
            completed = run_script("check_breaking_changes.py", "--require-baseline")
            self.assertEqual(completed.returncode, 1)

    def test_tag_ordering_is_semantic(self) -> None:
        tags = sorted(["v1.10.0", "v1.2.0", "v0.9.9"], key=lambda t: tuple(int(x) for x in t[1:].split(".")))
        self.assertEqual(tags, ["v0.9.9", "v1.2.0", "v1.10.0"])


class PlantedRemovalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = SpecDir()
        self.addCleanup(self.spec.cleanup)
        self.base = self.spec.write(spec_text(), "base.yaml")

    def check(self, revision_text: str, *args: str) -> subprocess.CompletedProcess:
        revision = self.spec.write(revision_text, "revision.yaml")
        return run_script("check_breaking_changes.py", "--base", str(self.base), "--revision", str(revision), "--acknowledgement", str(self.spec.path / "ack.json"), *args)

    def test_removed_component_property_fails_with_actionable_message(self) -> None:
        removed = replace_once(spec_text(), "        correlation_id:\n          type: string\n          pattern: '^[A-Za-z0-9._-]{1,128}$'\n", "        correlation_id_renamed:\n          type: string\n          pattern: '^[A-Za-z0-9._-]{1,128}$'\n")
        completed = self.check(removed)
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertIn("[component-schema-property-removed] components/schemas/ProblemDetail/properties/correlation_id", completed.stdout)
        self.assertIn("expand-and-contract", completed.stderr)
        self.assertIn("naming every finding (id + location + reason)", completed.stderr)

    def test_removed_schema_parameter_header_and_narrowed_constraints_are_detected(self) -> None:
        text = spec_text()
        text = replace_once(text, "    TimeZone:\n      type: string\n      pattern: '^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*$'\n      maxLength: 64\n      examples: [Asia/Kolkata]\n", "")
        text = replace_once(text, "          maxLength: 21\n", "          maxLength: 20\n")
        text = replace_once(text, "      required: [amount, currency]\n", "      required: [amount, currency, scale]\n")
        text = replace_in_section(text, ("components", "headers", "IdempotentReplayed"),
                                  "    IdempotentReplayed:\n", "    IdempotentReplayedRenamed:\n")
        text = text.replace("#/components/headers/IdempotentReplayed'", "#/components/headers/IdempotentReplayedRenamed'")
        completed = self.check(text, "--format", "json")
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertIn('"findings"', completed.stdout, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout[completed.stdout.index("{"):])
        ids = {(f["id"], f["location"]) for f in report["findings"]}
        self.assertIn(("component-schemas-removed", "components/schemas/TimeZone"), ids)
        self.assertIn(("component-schema-maxlength-decreased", "components/schemas/Money/properties/amount"), ids)
        self.assertIn(("component-schema-required-added", "components/schemas/Money/required/scale"), ids)
        self.assertIn(("component-headers-removed", "components/headers/IdempotentReplayed"), ids)
        self.assertEqual(completed.returncode, 1)

    def test_additive_change_passes(self) -> None:
        base = with_inheritance_probe(spec_text())
        self.base = self.spec.write(base, "base.yaml")
        added = replace_in_section(base, ("components", "schemas", "BreakingProbeBase"),
                                   "      examples:\n        - type: about:blank\n",
                                   "        hint:\n          type: string\n          description: Additive optional member.\n      examples:\n        - type: about:blank\n")
        completed = self.check(added)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_operation_removal_is_detected_by_oasdiff(self) -> None:
        base = self.spec.write(with_probe_paths(spec_text()), "base.yaml")
        revision = replace_section(with_probe_paths(spec_text()), ("paths", "/probe", "post"), "")
        completed = run_script("check_breaking_changes.py", "--base", str(base), "--revision", str(self.spec.write(revision, "revision.yaml")), "--acknowledgement", str(self.spec.path / "ack.json"))
        self.assertEqual(completed.returncode, 1)
        self.assertIn("[api-removed-without-deprecation] POST /probe", completed.stdout)
        self.assertNotIn("component-parameters-name-changed", completed.stdout, "header names are case-insensitive")

    def test_acknowledgement_lets_a_named_break_pass(self) -> None:
        removed = replace_once(spec_text(), "        correlation_id:\n          type: string\n          pattern: '^[A-Za-z0-9._-]{1,128}$'\n", "        correlation_id_renamed:\n          type: string\n          pattern: '^[A-Za-z0-9._-]{1,128}$'\n")
        detected = self.check(removed, "--format", "json")
        self.assertEqual(detected.returncode, 1)
        report = json.loads(detected.stdout[detected.stdout.index("{"):])
        self.assertIn(("component-schema-property-removed", "components/schemas/ProblemDetail/properties/correlation_id"),
                      {(finding["id"], finding["location"]) for finding in report["findings"]})
        (self.spec.path / "ack.json").write_text(json.dumps({
            "baseline": str(self.base),
            "acknowledged": [{"id": finding["id"], "location": finding["location"],
                              "reason": "Synthetic expand-and-contract fixture: every affected reference was explicitly migrated"}
                             for finding in report["findings"]],
        }), encoding="utf-8")
        completed = self.check(removed)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("every finding is acknowledged", completed.stdout)

    def test_partial_stale_and_blanket_acknowledgements_fail(self) -> None:
        removed = replace_once(spec_text(), "        correlation_id:\n          type: string\n          pattern: '^[A-Za-z0-9._-]{1,128}$'\n          description: Opaque request correlation identifier for support and logs; not a secret and never an idempotency key.\n", "")
        removed = replace_once(removed, "          maxLength: 21\n", "          maxLength: 20\n")
        ack = {"baseline": str(self.base), "acknowledged": [{"id": "component-schema-property-removed", "location": "components/schemas/ProblemDetail/properties/correlation_id", "reason": "documented"}]}
        (self.spec.path / "ack.json").write_text(json.dumps(ack), encoding="utf-8")
        completed = self.check(removed)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("unacknowledged: [component-schema-maxlength-decreased]", completed.stderr)
        ack["baseline"] = "v0.0.1"
        (self.spec.path / "ack.json").write_text(json.dumps(ack), encoding="utf-8")
        completed = self.check(removed)
        self.assertIn("stale acknowledgement never carries over", completed.stderr)
        ack["baseline"] = str(self.base)
        ack["acknowledged"].append({"id": "component-schema-maxlength-decreased", "location": "components/schemas/Money/properties/amount", "reason": "ok"})
        ack["acknowledged"].append({"id": "component-schemas-removed", "location": "components/schemas/Nothing", "reason": "blanket"})
        (self.spec.path / "ack.json").write_text(json.dumps(ack), encoding="utf-8")
        completed = self.check(removed)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("blanket acknowledgements are refused", completed.stderr)

    def test_acknowledgement_without_a_break_is_refused(self) -> None:
        (self.spec.path / "ack.json").write_text(json.dumps({"baseline": str(self.base), "acknowledged": []}), encoding="utf-8")
        completed = self.check(spec_text())
        self.assertEqual(completed.returncode, 1)
        self.assertIn("no breaking change was detected", completed.stderr)


class VersionBumpRuleTest(unittest.TestCase):
    def test_pre_1_needs_minor_and_post_1_needs_major(self) -> None:
        self.assertTrue(cbc.version_bump_ok("v0.1.0", "0.2.0")[0])
        self.assertFalse(cbc.version_bump_ok("v0.1.0", "0.1.1")[0])
        self.assertTrue(cbc.version_bump_ok("v0.1.0", "1.0.0")[0])
        self.assertTrue(cbc.version_bump_ok("v1.4.2", "2.0.0")[0])
        self.assertFalse(cbc.version_bump_ok("v1.4.2", "1.5.0")[0])

    def test_evaluate_requires_the_bump_for_a_tag_baseline(self) -> None:
        finding = cbc.Finding("component-schemas-removed", "components/schemas/TimeZone", "removed", "component-guard")
        ack = {"baseline": "v1.0.0", "target_version": "1.1.0", "acknowledged": [{"id": finding.id, "location": finding.location, "reason": "documented"}]}
        problems = cbc.evaluate([finding], ack, "v1.0.0", "1.1.0", ROOT / "spec" / "breaking-change-acknowledgement.json")
        self.assertTrue(any("MAJOR bump" in p for p in problems))
        self.assertEqual(cbc.evaluate([finding], {**ack, "target_version": "2.0.0"}, "v1.0.0", "2.0.0", ROOT / "spec" / "x.json"), [])


class CompositionRegressionTest(unittest.TestCase):
    def test_malformed_or_ambiguous_record_and_diff_data_never_proves_optional_inheritance(self) -> None:
        valid = {"base": {"index": 0, "component": "ProblemDetail"},
                 "revision": {"index": 0, "component": "ProblemDetail"},
                 "diff": {"properties": {"added": ["hint"]}}}
        defects = [
            {**valid, "metadata": {}}, {**valid, "unknownConstraint": True},
            {**valid, "base": {}, "revision": {}},
            {**valid, "base": {"index": True}, "revision": {"index": True}},
            {**valid, "base": {"index": "0"}, "revision": {"index": "0"}},
            {**valid, "base": {"index": -1}, "revision": {"index": -1}},
            {**valid, "base": {"index": 0, "extra": True}, "revision": {"index": 0, "extra": True}},
            {**valid, "base": {"index": 0, "component": ""}, "revision": {"index": 0, "component": ""}},
            {**valid, "revision": {"index": 1, "component": "ProblemDetail"}},
            {**valid, "diff": None}, {**valid, "diff": []},
            {**valid, "diff": {"properties": {"added": ["hint"]}, "metadata": {}}},
            {**valid, "diff": {"properties": {"added": []}}},
            {**valid, "diff": {"properties": {"added": "hint"}}},
            {**valid, "diff": {"properties": {"added": ["hint", "hint"]}}},
            {**valid, "diff": {"properties": {"modified": []}}},
        ]
        for record in defects:
            with self.subTest(record=record):
                composition = {"allOf": {"modified": [record]}}
                self.assertFalse(cbc._only_optional_property_additions(composition))
                self.assertTrue(cbc.component_findings({"components": {"schemas": {"modified": {"Provider": composition}}}}))
        for records in ([], {}, None, [valid, valid]):
            composition = {"allOf": {"modified": records}}
            self.assertFalse(cbc._only_optional_property_additions(composition))
            self.assertTrue(cbc.component_findings({"components": {"schemas": {"modified": {"Provider": composition}}}}))
        for change in ({"added": None}, {"deleted": "ambiguous"}, {}, {"metadata": True}):
            self.assertTrue(cbc.component_findings({"components": {"schemas": {"modified": {"Provider": {"allOf": change}}}}}))
        self.assertFalse(cbc._only_additive_response_schema({"content": {"modified": "ambiguous"}}))
        self.assertTrue(cbc._only_optional_property_additions({"allOf": {"modified": [valid]}}))

    def test_real_pinned_oasdiff_pair_matrix_preserves_proofs_and_refuses_constraints(self) -> None:
        specs = SpecDir()
        self.addCleanup(specs.cleanup)
        base_text = with_inheritance_probe(spec_text())
        base = specs.write(base_text, "base.yaml")
        tools = toolchain.ensure_installed()
        findings, _ = cbc.compare(SPEC, base, tools["oasdiff"])
        self.assertEqual(findings, [], "the named probe must preserve every genuine operation, security and shared response")
        probe = ("components", "schemas", "BreakingProbeBase")
        added = replace_in_section(base_text, probe, "      examples:\n        - type: about:blank\n",
                                   "        diagnostic_hint:\n          type: string\n      examples:\n        - type: about:blank\n")
        matrix = [
            ("optional inherited addition", added, False),
            ("required inherited detail", replace_in_section(base_text, probe, "      required: [type, title, status]\n",
                                                            "      required: [type, title, status, detail]\n"), True),
            ("narrowed inherited title", replace_in_section(base_text, probe, "          maxLength: 200\n          description: Short human-readable summary",
                                                            "          maxLength: 100\n          description: Short human-readable summary"), True),
            ("removed inherited correlation", replace_in_section(base_text, probe, "        correlation_id:\n          type: string\n          pattern: '^[A-Za-z0-9._-]{1,128}$'\n",
                                                                 "        correlation_renamed:\n          type: string\n          pattern: '^[A-Za-z0-9._-]{1,128}$'\n"), True),
            ("new allOf constraint", replace_in_section(base_text, ("components", "schemas", "BreakingProbeProvider"),
                                                        "        - $ref: '#/components/schemas/BreakingProbeBase'\n",
                                                        "        - $ref: '#/components/schemas/BreakingProbeBase'\n        - required: [instance]\n"), True),
            ("response metadata", replace_in_section(base_text, ("components", "responses", "ServiceProblem"),
                                                     "      description: Published safe problem detail; the HTTP status equals the catalogue status for its typed code.\n",
                                                     "      description: Changed response metadata requiring review.\n"), True),
        ]
        for name, text, should_break in matrix:
            with self.subTest(pair=name):
                revision = specs.write(text, "revision.yaml")
                findings, _ = cbc.compare(base, revision, tools["oasdiff"])
                self.assertEqual(bool(findings), should_break, name)
        revision = specs.write(added, "addition.yaml")
        real_diff = cbc.oasdiff_json(tools["oasdiff"], "diff", base, revision)
        self.assertEqual(cbc.component_findings(real_diff), [])
        for key in ("metadata", "unknownConstraint"):
            mutated = copy.deepcopy(real_diff)
            record = mutated["components"]["schemas"]["modified"]["BreakingProbeProvider"]["allOf"]["modified"][0]
            record[key] = {"from": False, "to": True}
            self.assertTrue(cbc.component_findings(mutated))

    def test_live_oneof_inheritance_is_not_silently_promoted_to_an_optional_addition_proof(self) -> None:
        specs = SpecDir()
        self.addCleanup(specs.cleanup)
        added = replace_in_section(spec_text(), ("components", "schemas", "ProblemDetail"),
                                   "      examples:\n        - type: about:blank\n",
                                   "        hint:\n          type: string\n      examples:\n        - type: about:blank\n")
        findings, _ = cbc.compare(SPEC, specs.write(added), toolchain.ensure_installed()["oasdiff"])
        self.assertEqual({finding.key() for finding in findings}, {
            ("component-schema-allof-changed", "components/schemas/OperationProblemDetail"),
            ("component-responses-changed", "components/responses/CustomDestinationProblem"),
        })

    def test_unknown_record_metadata_is_not_an_optional_addition_proof(self) -> None:
        for key in ("unknownConstraint", "metadata"):
            record = {"base": {"index": 0, "component": "ProblemDetail"},
                      "revision": {"index": 0, "component": "ProblemDetail"},
                      "diff": {"properties": {"added": ["hint"]}}, key: {"from": False, "to": True}}
            composition = {"allOf": {"modified": [record]}}
            with self.subTest(member=key):
                self.assertFalse(cbc._only_optional_property_additions(composition))
                self.assertTrue(cbc.component_findings({"components": {"schemas": {"modified": {"Provider": composition}}}}))

    def test_only_proven_optional_additions_are_nonbreaking_through_allof_and_responses(self) -> None:
        addition = {"properties": {"added": ["hint"]}}
        composition = {"allOf": {"modified": [{"base": {"index": 0, "component": "ProblemDetail"},
                                              "revision": {"index": 0, "component": "ProblemDetail"}, "diff": addition}]}}
        diff = {"components": {
            "schemas": {"modified": {"Provider": composition}},
            "responses": {"modified": {"Problem": {"content": {"modified": {"application/problem+json": {"schema": composition}}}}}},
        }}
        self.assertEqual(cbc.component_findings(diff), [])
        for defect in (
            {"required": {"added": ["hint"]}},
            {"properties": {"deleted": ["hint"]}},
            {"properties": {"modified": {"hint": {"maxLength": {"from": 20, "to": 10}}}}},
            {"unknownConstraint": {"from": False, "to": True}},
        ):
            with self.subTest(defect=defect):
                narrowed = {"allOf": {"modified": [{"base": {"index": 0}, "revision": {"index": 0}, "diff": defect}]}}
                findings = cbc.component_findings({"components": {"schemas": {"modified": {"Provider": narrowed}}}})
                self.assertEqual([finding.id for finding in findings], ["component-schema-allof-changed"])

    def test_new_allof_constraint_and_changed_response_metadata_remain_breaking(self) -> None:
        findings = cbc.component_findings({"components": {
            "schemas": {"modified": {"Provider": {"allOf": {"added": [{"required": ["code"]}]}}}},
            "responses": {"modified": {"Problem": {"headers": {"deleted": ["Retry-After"]}}}},
        }})
        self.assertEqual({finding.id for finding in findings}, {"component-schema-allof-added", "component-responses-changed"})


class EnumResponseRegressionTest(unittest.TestCase):
    def test_only_complete_scalar_enum_additions_and_inert_descriptions_prove_response_compatibility(self) -> None:
        def response(leaf: object) -> dict:
            return {"content": {"modified": {"application/json": {
                "schema": {"properties": {"modified": {"choice": leaf}}},
            }}}}

        valid = {"enum": {"added": ["next"]}, "description": {"from": "Before.", "to": "After."}}
        self.assertTrue(cbc._only_additive_response_schema(response(valid)))
        self.assertFalse(cbc._only_optional_property_additions(valid))
        self.assertTrue(cbc._only_additive_response_schema(response({"enum": {"added": [1, True, None, "1"]}})))
        for change in (
            None, [], {}, {"added": []}, {"added": "next"}, {"added": ["next", "next"]},
            {"added": [1, 1.0]}, {"added": [float("nan")]}, {"added": [float("inf")]},
            {"added": [{}]}, {"added": [["next"]]}, {"added": ["next"], "deleted": ["old"]},
            {"added": ["next"], "deleted": []}, {"added": ["next"], "enumAdded": True},
            {"added": ["next"], "metadata": {}}, {"deleted": ["old"]}, {"modified": ["next"]},
        ):
            with self.subTest(enum=change):
                self.assertFalse(cbc._only_additive_response_schema(response({**valid, "enum": change})))
        for description in (
            None, "", {}, {"to": "After."}, {"from": "Before."}, {"from": 1, "to": "After."},
            {"from": "Same.", "to": "Same."}, {"from": None, "to": None},
            {"from": "Before.", "to": "After.", "metadata": {}},
        ):
            self.assertFalse(cbc._only_additive_response_schema(response({**valid, "description": description})))
        for defect in (
            {"required": {"added": ["choice"]}}, {"required": {"deleted": ["choice"]}},
            {"type": {"from": "string", "to": "integer"}}, {"format": {"from": None, "to": "uuid"}},
            {"pattern": {"from": None, "to": "^next$"}}, {"maxLength": {"from": 10, "to": 5}},
            {"$ref": {"from": "#/components/schemas/Before", "to": "#/components/schemas/After"}},
            {"not": {"schemaAdded": True}}, {"if": {"schemaAdded": True}}, {"then": {"required": ["choice"]}},
            {"allOf": {"added": [{"required": ["choice"]}]}}, {"oneOf": {"modified": []}},
            {"anyOf": {"deleted": [0]}}, {"unknownConstraint": True},
        ):
            self.assertFalse(cbc._only_additive_response_schema(response({**valid, **defect})), defect)
        self.assertFalse(cbc._only_additive_response_schema({**response(valid), "description": {"from": "a", "to": "b"}}))
        self.assertFalse(cbc._only_additive_response_schema({**response(valid), "headers": {"deleted": ["Retry-After"]}}))

    def test_real_generic_enum_response_expansion_passes_without_a_model_or_value_whitelist(self) -> None:
        specs = SpecDir()
        self.addCleanup(specs.cleanup)
        text = with_inheritance_probe(spec_text())
        text = replace_section(text, ("components", "schemas"),
                               section_text(text, ("components", "schemas")) + """    BreakingProbeChoice:
      type: string
      enum: [first]
      description: First explanation.
""")
        text = replace_section(text, ("components", "schemas", "BreakingProbeProvider", "properties", "detail"),
                               "        detail:\n          $ref: '#/components/schemas/BreakingProbeChoice'\n")
        base = specs.write(text, "base.yaml")
        enum = ("components", "schemas", "BreakingProbeChoice")
        added = replace_in_section(text, enum, "      enum: [first]\n      description: First explanation.\n",
                                   "      enum: [first, second]\n      description: Second explanation.\n")
        tools = toolchain.ensure_installed()
        findings, _ = cbc.compare(base, specs.write(added, "addition.yaml"), tools["oasdiff"])
        self.assertEqual(findings, [])
        for declaration in (
            "      enum: [second]\n", "      enum: [first, second]\n      pattern: '^first$'\n",
            "      enum: [first, second]\n      not: {const: first}\n",
        ):
            narrowed = replace_in_section(text, enum, "      enum: [first]\n", declaration)
            findings, _ = cbc.compare(base, specs.write(narrowed, "narrowed.yaml"), tools["oasdiff"])
            self.assertTrue(findings, declaration)

    def test_actual_accepted_enum_delta_and_source_backed_branch_preserve_diff_only_red_history(self) -> None:
        specs = SpecDir()
        self.addCleanup(specs.cleanup)
        accepted = subprocess.run(
            ["git", "show", "5b41d4580c85be3cc1617074c0f3052b1f7b02cd:spec/openapi.yaml"],
            cwd=ROOT, capture_output=True, check=True,
        ).stdout.decode("utf-8")
        enum_only = replace_section(accepted, ("components", "schemas", "ProblemCode"),
                                    section_text(spec_text(), ("components", "schemas", "ProblemCode")))
        code = ("components", "schemas", "ServiceProblemDetail", "properties", "code")
        enum_only = replace_section(enum_only, code, section_text(spec_text(), code))
        base = specs.write(accepted, "accepted.yaml")
        revision = specs.write(enum_only, "enum-only.yaml")
        tools = toolchain.ensure_installed()
        findings, _ = cbc.compare(base, revision, tools["oasdiff"])
        self.assertEqual(findings, [])
        diff = cbc.oasdiff_json(tools["oasdiff"], "diff", base, revision)
        response = diff["components"]["responses"]["modified"]["ServiceProblem"]
        self.assertTrue(cbc._only_additive_response_schema(response))
        self.assertFalse(cbc._only_optional_property_additions(response["content"]["modified"]["application/problem+json"]["schema"]))
        actual_diff = cbc.oasdiff_json(tools["oasdiff"], "diff", base, SPEC)
        findings = cbc.component_findings(actual_diff)
        self.assertEqual(len(findings), 7, "the preserved diff-only classifier has no authority to waive these records")
        self.assertIn(("component-schema-allof-added", "components/schemas/ServiceProblemDetail"),
                      {finding.key() for finding in findings})
        self.assertIn(("component-responses-changed", "components/responses/ServiceProblem"),
                      {finding.key() for finding in findings})
        findings, _ = cbc.compare(base, SPEC, tools["oasdiff"])
        self.assertEqual(findings, [], "complete sources and exact records, not an acknowledgement, prove this expansion")


if __name__ == "__main__":
    unittest.main()
