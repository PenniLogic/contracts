"""Compile exact recursive constraints, refusing unsupported or ambiguous declarations."""

from __future__ import annotations

import json
import subprocess
import unittest

from support import ROOT, SpecDir, replace_once, run_script, spec_text
from pl_contracts import node_executable


class ConstraintCompilationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = SpecDir()
        self.addCleanup(self.spec.cleanup)

    def test_local_item_refs_retain_identity_and_all_declared_constraints(self) -> None:
        model = """    ConstraintProbe:
      type: object
      x-pennilogic-strict-provider: true
      additionalProperties: false
      required: [values]
      properties:
        values:
          type: array
          uniqueItems: true
          items:
            type: array
            minItems: 1
            maxItems: 2
            items:
              $ref: '#/components/schemas/PublicCorrelationId'
"""
        source = self.spec.write(replace_once(spec_text(), "  schemas:\n", "  schemas:\n" + model))
        result = subprocess.run([node_executable(), str(ROOT / "scripts" / "provider_constraints.cjs"), str(source)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        declared = data["schemas"]["ConstraintProbe"]["properties"]["values"]
        self.assertTrue(declared["uniqueItems"])
        self.assertEqual(declared["items"]["minItems"], 1)
        self.assertEqual(declared["items"]["maxItems"], 2)
        self.assertEqual(declared["items"]["items"], {"ref": "PublicCorrelationId"})
        reference = data["schemas"]["PublicCorrelationId"]
        self.assertEqual(reference["minLength"], 40)
        self.assertEqual(reference["maxLength"], 40)
        self.assertIn("^cor_", reference["pattern"])

    def test_every_target_explicitly_refuses_unsupported_and_ambiguous_constraints_before_generation(self) -> None:
        anchor = """    UnsupportedProbe:
      type: object
      x-pennilogic-strict-provider: true
      additionalProperties: false
      required: [values]
      properties:
        values: """
        declarations = [
            ("unsupported scalar", {"type": "number"}),
            ("unknown keyword", {"type": "array", "items": {"type": "integer", "multipleOf": 2}}),
            ("unbound items", {"type": "array"}),
            ("untyped items", {"type": "array", "items": {}}),
            ("tuple items", {"type": "array", "items": [{"type": "integer"}]}),
            ("ambiguous union", {"type": ["string", "null"]}),
            ("external reference", {"$ref": "https://pennilogic.example/schema.json"}),
            ("unresolved reference", {"$ref": "#/components/schemas/AbsentProbe"}),
            ("unsupported pattern", {"type": "string", "pattern": r"^\w+$"}),
            ("contradictory bounds", {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 1}),
            ("unbound conditional", {"type": "integer", "then": {"minimum": 1}}),
            ("malformed enum", {"type": "string", "enum": ["a", "a"]}),
            ("unsupported scalar enum", {"type": "integer", "enum": [1, 2]}),
            ("ambiguous enum", {"type": "string", "enum": ["a", 1]}),
            ("unsafe bound", {"type": "integer", "maximum": 9007199254740992}),
            ("unsupported default", {"type": "integer", "default": 1}),
            ("exclusive contradictory bounds", {"type": "integer", "exclusiveMinimum": 0, "maximum": 0}),
            ("cycle", {"$ref": "#/components/schemas/UnsupportedProbe"}),
        ]
        for label, declaration in declarations:
            source = self.spec.write(replace_once(spec_text(), "  schemas:\n", "  schemas:\n" +
                                                 anchor + json.dumps(declaration) + "\n"))
            for target in ("python", "typescript", "kotlin"):
                with self.subTest(case=label, target=target):
                    result = run_script("generate_clients.py", "--language", target, "--spec", str(source),
                                        "--output-dir", str(self.spec.path / "generated"))
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("provider constraint generation rejected:", result.stderr)
                    self.assertNotIn("openapi-generator failed", result.stderr)

    def test_nonportable_class_syntax_fails_before_any_target_output_is_touched(self) -> None:
        anchor = """    PatternProbe:
      type: object
      x-pennilogic-strict-provider: true
      additionalProperties: false
      required: [text]
      properties:
        text: """
        for pattern in (
            "^[]$", "^[^]$", "^[a&&b]$", "^[a||b]$", "^[a~~b]$", "^[a--b]$", "^[a-b-c]$",
            "^a|b$", "^()$", "^(a|)$", "^a+?$", "^a++$", r"^\-$", r"^\u0041$",
            r"^(a\1)$", "^a{2,1}$", "^a{2147483648}$", "^[z-a]$", "^" + "(" * 65 + "a" + ")" * 65 + "$",
            r"^[\s-z]$", r"^[a-\s]$",
        ):
            source = self.spec.write(replace_once(spec_text(), "  schemas:\n", "  schemas:\n" +
                                                 anchor + json.dumps({"type": "string", "pattern": pattern}) + "\n"))
            for target in ("python", "typescript", "kotlin"):
                with self.subTest(pattern=pattern, target=target):
                    output = self.spec.path / "regex-output"
                    directory = output / target
                    directory.mkdir(parents=True, exist_ok=True)
                    sentinel = directory / "preflight-sentinel.txt"
                    sentinel.write_bytes(b"preserve-before-any-generator-output\n")
                    result = run_script("generate_clients.py", "--language", target, "--spec", str(source),
                                        "--output-dir", str(output))
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("provider constraint generation rejected:", result.stderr)
                    self.assertEqual(sentinel.read_bytes(), b"preserve-before-any-generator-output\n")

    def test_accepted_pattern_grammar_keeps_original_source_and_refuses_partial_outer_anchors(self) -> None:
        accepted = [
            "^$", "^.$", "^..$", "^[^a]+$", r"^a\.b$", "^x/y$", r"^\[a\]$",
            "^([a-z]{1,3}|[0-9]{2,4})$", "^[A-Za-z0-9_+-]+$", r"^\^\$$",
            "^a{0}$", "^a{0,2}$", "^a{2,}$", "^[.]$", r"^a\\b$", "^" + "(" * 64 + "a" + ")" * 64 + "$",
            r"^[\s]$", r"^[^\s:/\\?#@%\[\]]+$", r"^/[^\s?#\\]*$",
        ]
        for pattern in accepted:
            with self.subTest(pattern=pattern):
                model = """    PatternControl:
      type: object
      x-pennilogic-strict-provider: true
      additionalProperties: false
      required: [text]
      properties:
        text: """ + json.dumps({"type": "string", "pattern": pattern}) + "\n"
                source = self.spec.write(replace_once(spec_text(), "  schemas:\n", "  schemas:\n" + model))
                result = subprocess.run([node_executable(), str(ROOT / "scripts" / "provider_constraints.cjs"), str(source)],
                                        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["schemas"]["PatternControl"]["properties"]["text"]["pattern"], pattern)

    def test_destination_uuid_and_state_metadata_compile_without_losing_source_bounds(self) -> None:
        result = subprocess.run([node_executable(), str(ROOT / "scripts" / "provider_constraints.cjs"), str(ROOT / "spec" / "openapi.yaml")],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        schemas = json.loads(result.stdout)["schemas"]
        for name in ("CustomDestinationId", "CustomDestinationModelId", "CustomDestinationProviderKeyRef"):
            self.assertEqual(schemas[name]["format"], "uuid")
            self.assertIn("-4[0-9a-f]{3}-[89ab]", schemas[name]["pattern"])
        self.assertEqual(schemas["CustomDestinationRegistrationRequest"]["properties"]["models"]["uniqueItems"], True)

    def test_positive_enum_intersection_keeps_the_referenced_public_type_and_runtime_subset(self) -> None:
        model = """    ConstraintGlobalCode:
      type: string
      enum: [first, second]
    ConstraintSubsetProbe:
      type: object
      x-pennilogic-strict-provider: true
      additionalProperties: false
      required: [choice]
      properties:
        choice:
          $ref: '#/components/schemas/ConstraintGlobalCode'
          type: string
          enum: [first]
"""
        source = self.spec.write(replace_once(spec_text(), "  schemas:\n", "  schemas:\n" + model))
        result = subprocess.run([node_executable(), str(ROOT / "scripts" / "provider_constraints.cjs"), str(source)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        compiled = json.loads(result.stdout)
        self.assertEqual(compiled["schemas"]["ConstraintSubsetProbe"]["properties"]["choice"],
                         {"ref": "ConstraintGlobalCode", "type": "string", "enum": ["first"]})
        projection = compiled["generation_input"]["components"]["schemas"]
        self.assertEqual(projection["ConstraintSubsetProbe"]["properties"]["choice"],
                         {"$ref": "#/components/schemas/ConstraintGlobalCode", "type": "string"})
        self.assertEqual(projection["ConstraintGlobalCode"]["enum"], ["first", "second"])
        catalogue = json.loads((ROOT / "spec" / "error-catalogue.v1.json").read_text(encoding="utf-8"))
        self.assertEqual(compiled["schemas"]["ServiceProblemDetail"]["properties"]["code"]["enum"],
                         [entry["code"] for entry in catalogue["codes"]])
        self.assertEqual(projection["ServiceProblemDetail"]["properties"]["code"],
                         {"$ref": "#/components/schemas/ProblemCode", "type": "string"})

    def test_closed_model_projection_preserves_exact_fields_and_complete_runtime_compositions(self) -> None:
        result = subprocess.run([node_executable(), str(ROOT / "scripts" / "provider_constraints.cjs"), str(ROOT / "spec" / "openapi.yaml")],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        compiled = json.loads(result.stdout)
        schemas = compiled["schemas"]
        projection = compiled["generation_input"]["components"]["schemas"]
        for name in compiled["generation_model_projection"]:
            self.assertEqual(set(projection[name].get("properties", {})), set(schemas[name].get("properties", {})))
            self.assertEqual(projection[name].get("required", []), schemas[name].get("required", []))
            self.assertTrue(projection[name]["x-pennilogic-strict-provider"])
            self.assertFalse(projection[name]["additionalProperties"])
            if "allOf" in projection[name]:
                for member in projection[name]["allOf"]:
                    self.assertEqual(set(member), {"properties"})
                    self.assertIn(member, schemas[name]["allOf"])
            self.assertTrue(any(keyword in json.dumps(schemas[name]) for keyword in
                                ('"allOf"', '"anyOf"', '"oneOf"', '"if"', '"not"', '"const"')))
        self.assertNotIn("egress_denial_reason", projection["OperationProblemDetail"]["required"])
        self.assertEqual(schemas["OperationProblemDetail"]["allOf"][0]["oneOf"], [
            {"ref": "ServiceProblemDetail"}, {"ref": "EgressDeniedProblemDetail"}, {"ref": "AuthenticationProblemDetail"},
        ])
        self.assertEqual(projection["AuthenticationProblemDetail"]["properties"]["code"],
                         {"$ref": "#/components/schemas/ProblemCode"})
        self.assertTrue(schemas["DedupPrecedence"]["properties"]["user_confirmed_preserved"]["const"])
        self.assertEqual(projection["DedupPrecedence"]["properties"]["user_confirmed_preserved"]["type"], "boolean")
        self.assertNotIn("const", projection["DedupPrecedence"]["properties"]["user_confirmed_preserved"])

    def test_projection_preserves_unconditional_field_order_and_inline_helpers_without_branch_requiredness(self) -> None:
        model = """    LayoutPayload:
      type: object
      properties:
        choice: {type: string, enum: [first, second]}
    LayoutProbe:
      type: object
      x-pennilogic-strict-provider: true
      additionalProperties: false
      required: [position, payload]
      properties:
        position: {type: integer}
        note: {type: string}
        payload: {$ref: '#/components/schemas/LayoutPayload'}
      allOf:
        - properties:
            payload:
              properties:
                choice: {enum: [first]}
        - if:
            properties: {position: {const: 1}}
            required: [position]
          then:
            required: [note]
"""
        source = self.spec.write(replace_once(spec_text(), "  schemas:\n", "  schemas:\n" + model))
        result = subprocess.run([node_executable(), str(ROOT / "scripts" / "provider_constraints.cjs"), str(source)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        compiled = json.loads(result.stdout)
        projected = compiled["generation_input"]["components"]["schemas"]["LayoutProbe"]
        self.assertEqual(list(projected["properties"]), ["payload", "position", "note"])
        self.assertEqual(projected["required"], ["position", "payload"])
        self.assertEqual(projected["properties"]["payload"], {"$ref": "#/components/schemas/LayoutPayload"})
        self.assertEqual(projected["allOf"], [{"properties": {"payload": {"properties": {"choice": {"enum": ["first"]}}}}}])
        self.assertEqual(len(compiled["schemas"]["LayoutProbe"]["allOf"]), 2)
        self.assertEqual(compiled["schemas"]["LayoutProbe"]["allOf"][1]["then"], {"required": ["note"]})
        for constraint in (
            {"enum": ["first"], "maxLength": 10},
            {"anyOf": [{"enum": ["first"]}, {"enum": ["second"]}]},
            {"not": {"enum": ["second"]}},
            {"$ref": "#/components/schemas/ValidationReason"},
        ):
            with self.subTest(deeper_refinement=constraint):
                altered = replace_once(model, "choice: {enum: [first]}", "choice: " + json.dumps(constraint))
                source = self.spec.write(replace_once(spec_text(), "  schemas:\n", "  schemas:\n" + altered))
                result = subprocess.run(
                    [node_executable(), str(ROOT / "scripts" / "provider_constraints.cjs"), str(source)],
                    cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                compiled = json.loads(result.stdout)
                projected = compiled["generation_input"]["components"]["schemas"]["LayoutProbe"]
                self.assertNotIn("allOf", projected)
                self.assertEqual(list(projected["properties"]), ["payload", "position", "note"])
                self.assertEqual(projected["required"], ["position", "payload"])
                self.assertEqual(len(compiled["schemas"]["LayoutProbe"]["allOf"]), 2)


if __name__ == "__main__":
    unittest.main()
