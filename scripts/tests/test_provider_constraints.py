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


if __name__ == "__main__":
    unittest.main()
