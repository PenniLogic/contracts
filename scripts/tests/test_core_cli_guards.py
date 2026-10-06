"""Real full-YAML CLI controls for distinct financial decisions and structural metadata equality."""

from __future__ import annotations

import hashlib
import json
import subprocess
import unittest

from support import ROOT, SpecDir, replace_once, replace_section, section_text, spec_text, run_script
from pl_contracts import node_executable


def parsed(text: str) -> dict:
    result = subprocess.run(
        [node_executable(), "-e",
         "const{Yaml}=require('@stoplight/spectral-parsers');"
         "const doc=Yaml.parse(require('node:fs').readFileSync(0,'utf8'));"
         "if(doc.diagnostics.length)throw Error('full YAML parse failed');"
         "process.stdout.write(JSON.stringify(doc.data));"],
        cwd=ROOT, input=text, capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return json.loads(result.stdout)


class CoreCliGuardTest(unittest.TestCase):
    def assert_lint(self, text: str, expected: int, rule: str | None = None) -> None:
        parsed(text)
        spec = SpecDir()
        self.addCleanup(spec.cleanup)
        result = run_script("lint_spec.py", "--spec", str(spec.write(text)))
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        if rule:
            self.assertIn("[" + rule + "]", result.stderr)

    def test_actual_complete_source_requires_own_shared_202_for_every_declared_screen(self) -> None:
        text = spec_text()
        self.assert_lint(text, 0)
        document = parsed(text)
        screens = [(route, method) for route, item in document["paths"].items()
                   for method, operation in item.items()
                   if isinstance(operation, dict) and operation.get("x-duplicate-screen")]
        self.assertGreaterEqual(len(screens), 4)
        for route, method in screens:
            with self.subTest(route=route, method=method):
                at = ("paths", route, method, "responses")
                revised = replace_section(text, at, replace_once(section_text(text, at),
                    "        '202': {$ref: '#/components/responses/DuplicateSuspected'}\n", ""))
                self.assert_lint(revised, 1, "pl-core-endpoint-contract")

    def test_actual_wrong_or_merged_decision_and_reference_alias_creation_responses_refuse(self) -> None:
        text = spec_text()
        at = ("paths", "/transactions", "post", "responses")
        original = section_text(text, at)
        wrong = replace_section(text, at, replace_once(original,
            "        '202': {$ref: '#/components/responses/DuplicateSuspected'}\n",
            "        '202': {description: Wrong financial decision body, content: {application/json: {schema: {$ref: '#/components/schemas/Transaction'}}}}\n"))
        merged = replace_section(text, at, replace_once(replace_once(original,
            "        '201': {$ref: '#/components/responses/CoreTransactionCreated'}\n",
            "        '201': {description: Merged effect and decision, content: {application/json: {schema: {oneOf: [{$ref: '#/components/schemas/Transaction'}, {$ref: '#/components/schemas/DedupOutcome'}]}}}}\n"),
            "        '202': {$ref: '#/components/responses/DuplicateSuspected'}\n", ""))
        alias = replace_once(text, "  schemas:\n",
            "  schemas:\n    SyntheticDecisionAlias: {$ref: '#/components/schemas/DedupOutcome'}\n")
        alias = replace_section(alias, at, replace_once(section_text(alias, at),
            "        '201': {$ref: '#/components/responses/CoreTransactionCreated'}\n",
            "        '201': {description: Wrong named creation branch, content: {application/json: {schema: {$ref: '#/components/schemas/SyntheticDecisionAlias'}}}}\n"))
        for name, revised in (("wrong202", wrong), ("merged201", merged), ("aliased201", alias)):
            with self.subTest(name=name):
                self.assert_lint(revised, 1, "pl-core-endpoint-contract")

    def test_only_idempotency_object_key_order_changes_are_real_full_source_positives(self) -> None:
        text = spec_text()
        at = ("paths", "/accounts", "post")
        policy = parsed(text)["paths"]["/accounts"]["post"]["x-idempotency-policy"]
        original = "      x-idempotency-policy: " + section_text(text, at).split("      x-idempotency-policy: ", 1)[1].split("\n", 1)[0] + "\n"
        reordered = dict(reversed(list(policy.items())))
        revised = replace_section(text, at, replace_once(section_text(text, at),
            original, "      x-idempotency-policy: " + json.dumps(reordered) + "\n"))
        self.assertEqual(parsed(revised), parsed(text), "positive must preserve every complete parsed value")
        self.assert_lint(revised, 0)
        mutations = [
            {**policy, "scope": list(reversed(policy["scope"]))},
            {**policy, "lifetime": "P1D"},
            {**policy, "retryHorizon": 14},
            {**policy, "extra": True},
        ]
        for index, changed in enumerate(mutations):
            with self.subTest(mutation=index):
                altered = replace_section(text, at, replace_once(section_text(text, at),
                    original, "      x-idempotency-policy: " + json.dumps(changed) + "\n"))
                self.assert_lint(altered, 1, "pl-core-endpoint-contract")

    def test_exact_seed_binding_object_order_is_supported_by_real_lint_and_generation(self) -> None:
        text = spec_text()
        binding = parsed(text)["x-category-seed-source"]
        reordered = replace_section(text, ("x-category-seed-source",),
            "x-category-seed-source: " + json.dumps(dict(reversed(list(binding.items())))) + "\n")
        self.assertEqual(parsed(reordered), parsed(text))
        self.assert_lint(reordered, 0)
        spec = SpecDir()
        self.addCleanup(spec.cleanup)
        source = spec.write(reordered)
        before = (spec.path / "category-seed.v1.json").read_bytes()
        result = run_script("generate_clients.py", "--language", "typescript", "--spec", str(source),
                            "--output-dir", str(spec.path / "generated"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = spec.path / "generated" / "typescript"
        self.assertEqual((output / "category-seed.v1.json").read_bytes(), before)
        self.assertEqual(hashlib.sha256(before).hexdigest(),
                         "3fcf568abcfc7da7409e463ea6bbda9d952af739cfa9ef42a8e9fe90b993390c")
        manifest = json.loads((output / "contracts-manifest.json").read_bytes())
        self.assertEqual(manifest["provider_sources_sha256"]["category-seed.v1.json"], hashlib.sha256(before).hexdigest())
        for changed in ({**binding, "commit": "0" * 40}, {**binding, "size": "14237"},
                        {**binding, "extra": True}):
            altered = replace_section(text, ("x-category-seed-source",),
                "x-category-seed-source: " + json.dumps(changed) + "\n")
            self.assert_lint(altered, 1, "pl-category-seed-binding")
            rejected = run_script("generate_clients.py", "--language", "typescript",
                                  "--spec", str(spec.write(altered)), "--output-dir", str(spec.path / "rejected"))
            self.assertEqual(rejected.returncode, 1, rejected.stdout + rejected.stderr)
            self.assertIn("category source binding", rejected.stderr)
            self.assertFalse((spec.path / "rejected").exists())

    def test_readonly_projection_requires_explicit_nullable_response_metadata_on_real_cli(self) -> None:
        text = spec_text()
        for name in ("Transaction", "Categorisation"):
            at = ("components", "schemas", name)
            for old, new in (
                ("          readOnly: true\n", ""),
                ("          anyOf: [{$ref: '#/components/schemas/ResourceId'}, {type: 'null'}]\n",
                 "          $ref: '#/components/schemas/ResourceId'\n"),
            ):
                with self.subTest(model=name, change=old):
                    self.assert_lint(replace_section(text, at,
                        replace_once(section_text(text, at), old, new)), 1, "pl-core-endpoint-contract")


if __name__ == "__main__":
    unittest.main()
