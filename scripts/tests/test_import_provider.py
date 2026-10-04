"""T-CON-10 source/schema verification; actual service adoption remains consumer work."""

from __future__ import annotations

import copy
import json
import subprocess
import unittest

from support import ROOT, SpecDir, replace_once, run_script, spec_text
from test_error_provider import schema_results
from pl_contracts import node_executable

TARGET_SCHEMAS = {
    "request": "ImportCommitRequest", "preview": "ImportPreview", "result": "ImportCommitResult",
    "screen": "DedupOutcome", "suspected": "DedupOutcome", "linked": "DedupOutcome",
    "reversed": "DedupOutcome", "review": "DedupOutcome", "clear": "DedupOutcome",
    "override_rejection": "ServiceProblemDetail",
}


def fixture() -> dict:
    return json.loads((ROOT / "spec" / "fixtures" / "import-provider.v1.json").read_text(encoding="utf-8"))


def mutate(value: dict, change: dict) -> dict:
    result = copy.deepcopy(value)
    target = result
    for member in change["path"][:-1]:
        target = target[member]
    if change.get("remove"):
        del target[change["path"][-1]]
    else:
        target[change["path"][-1]] = change["value"]
    return result


def schema_document() -> dict:
    code = "process.stdout.write(JSON.stringify(require('@stoplight/spectral-parsers').Yaml.parse(require('node:fs').readFileSync(process.argv[1],'utf8')).data))"
    completed = subprocess.run([node_executable(), "-e", code, str(ROOT / "spec" / "openapi.yaml")],
                               cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
    if completed.returncode:
        raise AssertionError(completed.stderr)
    return json.loads(completed.stdout)


class ImportSchemaTest(unittest.TestCase):
    def test_every_documented_provider_example_matches_the_shared_schema(self) -> None:
        data = fixture()
        cases = [{"name": name, "schema": schema, "wire": data[name]} for name, schema in TARGET_SCHEMAS.items()]
        for result in schema_results(cases):
            self.assertTrue(result["valid"], result)

    def test_exact_schema_negatives_and_semantic_only_boundaries(self) -> None:
        data = fixture()
        cases = [{"name": case["name"], "schema": TARGET_SCHEMAS[case["target"]],
                  "wire": mutate(data[case["target"]], case)} for case in data["invalid"]]
        for result, case in zip(schema_results(cases), data["invalid"], strict=True):
            self.assertEqual(result["valid"], not case["schema_rejected"], case["name"])

    def test_row_limit_is_exactly_10000_not_an_unrun_claim(self) -> None:
        data = fixture()
        preview = copy.deepcopy(data["preview"])
        row = copy.deepcopy(preview["rows"][0])
        preview["rows"] = [{**row, "source_row": index + 1} for index in range(10000)]
        preview["counts"] = {"row_count": 10000, "create_count": 10000, "skip_count": 0, "reject_count": 0, "review_count": 0}
        overflow = copy.deepcopy(preview)
        overflow["rows"].append({**row, "source_row": 10001})
        self.assertEqual([result["valid"] for result in schema_results([
            {"name": "10000 rows", "schema": "ImportPreview", "wire": preview},
            {"name": "10001 rows", "schema": "ImportPreview", "wire": overflow},
        ])], [True, False])

    def test_base_components_remain_binding_equal_to_accepted_scaffold(self) -> None:
        completed = subprocess.run(["git", "show", "ea56c63d5c9b679537bd9205b04626049c20c572:spec/openapi.yaml"],
                                   cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        parser = "process.stdout.write(JSON.stringify(require('@stoplight/spectral-parsers').Yaml.parse(require('node:fs').readFileSync(0,'utf8')).data))"
        parsed = subprocess.run([node_executable(), "-e", parser], cwd=ROOT, input=completed.stdout,
                                capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(parsed.returncode, 0, parsed.stderr)
        base, current = json.loads(parsed.stdout), schema_document()
        for kind, members in base["components"].items():
            for name, schema in members.items():
                self.assertEqual(current["components"][kind][name], schema, f"existing {kind}/{name} changed")
        self.assertEqual(current["paths"], {})

    def test_source_pins_and_taxonomy_projection_are_explicit(self) -> None:
        policy = json.loads((ROOT / "spec" / "import-group.v1.json").read_text(encoding="utf-8"))
        bindings = json.loads((ROOT / "spec" / "client-state-bindings.v1.json").read_text(encoding="utf-8"))
        self.assertEqual({source["commit"] for source in policy["sources"]}, {"a700e639585c61a4610e7b99dbd02b2dab28bdcc"})
        self.assertEqual(bindings["taxonomy_version"], "1.1.0")
        self.assertEqual(bindings["source"]["sha256"], "040d2f0c27332ce3f6794a90139e714b3c50afb5b7340aa596d47b8866f2b3fb")
        self.assertEqual(policy["confidence"]["vocabulary"], ["high", "low"])
        self.assertEqual(policy["classification"], "new_provider_source_not_accepted_or_released")


class ImportLintNegativeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = SpecDir()
        self.addCleanup(self.spec.cleanup)

    def reject(self, text: str) -> str:
        completed = run_script("lint_spec.py", "--spec", str(self.spec.write(text)))
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertIn("[pl-import-provider]", completed.stderr)
        return completed.stderr

    def test_inlined_confidence_is_rejected(self) -> None:
        text = replace_once(spec_text(), "    ConfidenceBand:\n", "    LocalConfidence:\n      type: string\n      enum: [high, low]\n    ConfidenceBand:\n")
        self.assertIn("equivalent inline/local", self.reject(text))

    def test_inlined_preview_and_dedup_are_rejected(self) -> None:
        document = schema_document()
        for name in ("ImportPreview", "DedupOutcome"):
            with self.subTest(component=name):
                alias = "    LocalShape: " + json.dumps(document["components"]["schemas"][name]) + "\n"
                self.assertIn("inline/local", self.reject(replace_once(spec_text(), "    ImportGroupVersion:\n", alias + "    ImportGroupVersion:\n")))

    def test_local_confidence_member_must_reference_provider(self) -> None:
        text = replace_once(spec_text(), "    ImportGroupVersion:\n",
                            "    ConsumerProbe:\n      type: object\n      properties:\n        confidence_band:\n          type: string\n          enum: [high, low, medium]\n    ImportGroupVersion:\n")
        self.assertIn("shared component", self.reject(text))

    def test_override_cannot_bind_another_identifier(self) -> None:
        text = replace_once(spec_text(), "      schema:\n        $ref: '#/components/schemas/DedupRecordId'\n",
                            "      schema:\n        $ref: '#/components/schemas/ImportPreviewRef'\n")
        self.assertIn("same normative DedupRecordId", self.reject(text))

    def test_raw_content_and_open_maps_are_rejected_at_source(self) -> None:
        anchor = ("    ImportPreview:\n      type: object\n      x-pennilogic-strict-provider: true\n"
                  "      x-pennilogic-provider-validator: com.pennilogic.contracts.imports.ImportContract.verifyPreview\n"
                  "      additionalProperties: false\n")
        text = replace_once(spec_text(), anchor, anchor.replace("additionalProperties: false", "additionalProperties: true"))
        self.assertIn("objects must be closed", self.reject(text))
        text = replace_once(spec_text(), "        column_index:\n          $ref: '#/components/schemas/ImportColumnIndex'\n        problem:\n",
                            "        raw_row:\n          type: string\n        column_index:\n          $ref: '#/components/schemas/ImportColumnIndex'\n        problem:\n")
        self.assertIn("Raw file/row/account", self.reject(text))

    def test_unknown_confidence_and_missing_source_pair_policy_fail(self) -> None:
        self.reject(replace_once(spec_text(), "      enum: [high, low]\n", "      enum: [high, low, medium]\n"))
        policy_path = self.spec.path / "import-group.v1.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["source_pair_windows"].pop()
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        self.assertIn("Every source pair", self.reject(spec_text()))


if __name__ == "__main__":
    unittest.main()
