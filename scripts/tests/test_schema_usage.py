"""Full valid-YAML and real generator/native controls for the bounded shared schema-use proof."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from support import ROOT, SpecDir, replace_once, section_text, replace_section, spec_text, run_script
from pl_contracts import node_executable
from test_core_cli_guards import parsed

REFERENCE = {"$ref": "#/components/schemas/UsageReceipt"}
RECEIPT = {
    "type": "object", "x-pennilogic-strict-provider": True, "additionalProperties": False,
    "required": ["result"], "properties": {"result": {"type": "string", "readOnly": True}},
}


def usage_source(shape: dict, declarations: dict | None = None) -> str:
    text = spec_text()
    models = {"UsageReceipt": RECEIPT, **(declarations or {})}
    text = replace_once(text, "  schemas:\n", "  schemas:\n" +
        "".join(f"    {name}: {json.dumps(value)}\n" for name, value in models.items()))
    tags = section_text(text, ("tags",))
    text = replace_section(text, ("tags",), tags +
        "  - name: RequestUsage\n    description: Synthetic schema-use boundary.\n")
    route = """  /schema-usage:
    get:
      operationId: readUsage
      tags: [RequestUsage]
      description: Synthetic received readonly result.
      responses:
        '200':
          description: A response-only marked receipt.
          content: {application/json: {schema: {$ref: '#/components/schemas/UsageReceipt'}}}
    post:
      operationId: postUsage
      tags: [RequestUsage]
      description: Synthetic request-usage proof, never a product endpoint.
      parameters: [{$ref: '#/components/parameters/IdempotencyKey'}]
      requestBody:
        required: true
        content: {application/json: {schema: SHAPE}}
      responses:
        '204': {description: Declared empty response.}
"""
    paths = section_text(text, ("paths",))
    return replace_section(text, ("paths",),
        "paths:\n" + route.replace("SHAPE", json.dumps(shape)) + paths.partition("\n")[2])


class SchemaUsageTest(unittest.TestCase):
    def test_shared_context_maps_references_annotations_and_budgets(self) -> None:
        result = subprocess.run(
            [node_executable(), "--test", str(ROOT / "scripts" / "tests" / "schema_usage.test.cjs")],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_full_yaml_map_and_direct_mixed_uses_refuse_lint_and_every_target_before_promotion(self) -> None:
        shapes = {
            "direct": REFERENCE,
            "additionalProperties": {"type": "object", "additionalProperties": REFERENCE},
            "patternProperties": {"type": "object", "additionalProperties": False,
                                  "patternProperties": {"^snapshot$": REFERENCE}},
            "dependentSchemas": {"type": "object", "additionalProperties": False, "properties": {
                "trigger": {"type": "boolean"},
                "snapshot": {"type": "object", "additionalProperties": False, "required": ["result"],
                             "properties": {"result": {"type": "string"}}},
            }, "dependentSchemas": {"trigger": {"properties": {"snapshot": REFERENCE}}}},
            "nestedMap": {"type": "array", "items": {"type": "object", "additionalProperties": REFERENCE}},
        }
        for label, shape in shapes.items():
            with self.subTest(case=label):
                spec = SpecDir()
                self.addCleanup(spec.cleanup)
                text = usage_source(shape)
                document = parsed(text)
                self.assertEqual(document["paths"]["/schema-usage"]["post"]["requestBody"]["content"]["application/json"]["schema"], shape)
                source = spec.write(text)
                lint = run_script("lint_spec.py", "--spec", str(source))
                self.assertEqual(lint.returncode, 1, lint.stdout + lint.stderr)
                self.assertIn("[pl-core-endpoint-contract]", lint.stderr)
                self.assertIn("readOnly response members", lint.stderr)
                for target in ("typescript", "kotlin", "python"):
                    directory = spec.path / "generated" / target
                    directory.mkdir(parents=True)
                    marker = directory / "preserve-output.txt"
                    marker.write_bytes(b"preserve-prior-valid-output\n")
                    result = run_script("generate_clients.py", "--language", target, "--spec", str(source),
                                        "--output-dir", str(spec.path / "generated"))
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("readOnly model must be response-only", result.stderr)
                    self.assertNotIn("info.version", result.stderr)
                    self.assertNotIn("openapi-generator failed", result.stderr)
                    self.assertEqual(marker.read_bytes(), b"preserve-prior-valid-output\n")
                self.assertEqual(sorted(path.name for path in spec.path.iterdir() if path.name.startswith(".contracts-generation-")), [])

    def test_real_existing_transaction_reference_in_pattern_map_is_an_owning_gate_failure(self) -> None:
        text = spec_text()
        at = ("paths", "/transactions", "post", "requestBody")
        shape = {"type": "object", "additionalProperties": False, "patternProperties": {
            "^scope_snapshot$": {"$ref": "#/components/schemas/Transaction"},
        }}
        text = replace_section(text, at, "      requestBody:\n        required: true\n"
            "        content: {application/json: {schema: " + json.dumps(shape) + "}}\n")
        self.assertEqual(parsed(text)["paths"]["/transactions"]["post"]["requestBody"]["content"]["application/json"]["schema"], shape)
        spec = SpecDir()
        self.addCleanup(spec.cleanup)
        source = spec.write(text)
        lint = run_script("lint_spec.py", "--spec", str(source))
        self.assertEqual(lint.returncode, 1, lint.stdout + lint.stderr)
        self.assertIn("[pl-core-endpoint-contract]", lint.stderr)
        self.assertIn("readOnly response members", lint.stderr)
        generated = run_script("generate_clients.py", "--language", "typescript", "--spec", str(source),
                               "--output-dir", str(spec.path / "generated"))
        self.assertEqual(generated.returncode, 1, generated.stdout + generated.stderr)
        self.assertIn("readOnly model must be response-only", generated.stderr)
        self.assertFalse((spec.path / "generated").exists())

    def test_real_callback_webhook_and_path_item_request_roles_do_not_grant_response_only_metadata(self) -> None:
        control = usage_source({"type": "object", "additionalProperties": False,
                                "properties": {"caption": {"type": "string"}}})
        callback = replace_once(control, "      operationId: readUsage\n",
            "      operationId: readUsage\n"
            "      callbacks:\n        signal:\n          '{$request.query.callback}':\n"
            "            post:\n              operationId: signalUsage\n              tags: [RequestUsage]\n"
            "              description: Synthetic callback schema-use role.\n"
            "              parameters: [{$ref: '#/components/parameters/IdempotencyKey'}]\n"
            "              requestBody:\n                required: true\n"
            "                content: {application/json: {schema: {$ref: '#/components/schemas/UsageReceipt'}}}\n"
            "              responses:\n                '204': {description: Empty callback response.}\n")
        webhook = control + (
            "webhooks:\n  signal:\n    post:\n      operationId: webhookUsage\n      tags: [RequestUsage]\n"
            "      description: Synthetic webhook schema-use role.\n"
            "      parameters: [{$ref: '#/components/parameters/IdempotencyKey'}]\n"
            "      requestBody:\n        required: true\n"
            "        content: {application/json: {schema: {$ref: '#/components/schemas/UsageReceipt'}}}\n"
            "      responses:\n        '204': {description: Empty webhook response.}\n"
        )
        path_item = replace_section(control, ("paths", "/schema-usage"),
                                    "  /schema-usage: {$ref: '#/components/pathItems/UsagePath'}\n")
        components = section_text(path_item, ("components",))
        path_item = replace_section(path_item, ("components",),
            "components:\n  pathItems:\n    UsagePath:\n      post:\n"
            "        operationId: aliasedUsage\n        tags: [RequestUsage]\n"
            "        description: Synthetic referenced request role.\n"
            "        parameters: [{$ref: '#/components/parameters/IdempotencyKey'}]\n"
            "        requestBody:\n          required: true\n"
            "          content: {application/json: {schema: {$ref: '#/components/schemas/UsageReceipt'}}}\n"
            "        responses:\n          '200':\n            description: Synthetic same-model response.\n"
            "            content: {application/json: {schema: {$ref: '#/components/schemas/UsageReceipt'}}}\n"
            + components.partition("\n")[2])
        for label, text in (("callback", callback), ("webhook", webhook), ("path-item", path_item)):
            with self.subTest(context=label):
                parsed(text)
                spec = SpecDir()
                self.addCleanup(spec.cleanup)
                source = spec.write(text)
                lint = run_script("lint_spec.py", "--spec", str(source))
                self.assertEqual(lint.returncode, 1, lint.stdout + lint.stderr)
                self.assertIn("[pl-core-endpoint-contract]", lint.stderr)
                self.assertIn("readOnly response members", lint.stderr)
                for target in ("typescript", "kotlin", "python"):
                    generated = run_script("generate_clients.py", "--language", target, "--spec", str(source),
                                           "--output-dir", str(spec.path / "generated"))
                    self.assertEqual(generated.returncode, 1, generated.stdout + generated.stderr)
                    self.assertIn("readOnly model must be response-only", generated.stderr)
                    self.assertNotIn("info.version", generated.stderr)
                    self.assertNotIn("openapi-generator failed", generated.stderr)
                    self.assertFalse((spec.path / "generated").exists())

    def test_unknown_request_clauses_and_wrong_reference_contexts_are_explicit_unproved_refusals(self) -> None:
        for shape in (
            {"type": "object", "unknownApplicator": REFERENCE},
            {"$ref": "#/components/schemas/UsageReceipt/example"},
            {"$dynamicRef": "#/components/schemas/UsageReceipt"},
        ):
            with self.subTest(schema=shape):
                spec = SpecDir()
                self.addCleanup(spec.cleanup)
                source = spec.write(usage_source(shape))
                compiler = run_script("generate_clients.py", "--language", "typescript", "--spec", str(source),
                                      "--output-dir", str(spec.path / "generated"))
                self.assertEqual(compiler.returncode, 1, compiler.stdout + compiler.stderr)
                self.assertIn("unproved readOnly usage", compiler.stderr)
                self.assertNotIn("info.version", compiler.stderr)
                self.assertFalse((spec.path / "generated").exists())

    def test_actual_response_only_and_annotation_positive_generates_all_targets_and_mock_transports(self) -> None:
        shape = {"type": "object", "additionalProperties": {"type": "string"},
                 "example": {"caption": "synthetic", "examples": "natural key"},
                 "examples": [{"caption": "synthetic"}],
                 "x-annotation": {"$ref": REFERENCE["$ref"], "additionalProperties": REFERENCE,
                                  "patternProperties": {"any": REFERENCE}, "readOnly": True}}
        text = usage_source(shape)
        spec = SpecDir()
        self.addCleanup(spec.cleanup)
        source = spec.write(text)
        lint = run_script("lint_spec.py", "--spec", str(source))
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
        home = Path(tempfile.mkdtemp(prefix="pl-response-usage-", dir=ROOT / "build"))
        self.addCleanup(shutil.rmtree, home)
        generated = run_script("generate_clients.py", "--spec", str(source), "--output-dir", str(home / "generated"))
        self.assertEqual(generated.returncode, 0, generated.stdout + generated.stderr)
        for language in ("typescript", "kotlin", "python"):
            self.assertTrue((home / "generated" / language / "contracts-manifest.json").is_file())
        fixture = ROOT / "scripts" / "tests" / "fixtures" / "schema_usage_response.ts"
        self.assertNotRegex(fixture.read_text(encoding="utf-8"), r"\bas\s+(?:any|unknown)\b|@ts-")
        shutil.copyfile(fixture, home / "consumer.ts")
        config = home / "tsconfig.json"
        config.write_text(json.dumps({
            "compilerOptions": {
                "strict": True, "noUncheckedIndexedAccess": True, "target": "es2022",
                "module": "es2022", "moduleResolution": "bundler", "lib": ["es2022", "dom", "dom.iterable"],
                "types": ["node"], "typeRoots": [str(ROOT / "node_modules" / "@types")],
                "skipLibCheck": False, "rootDir": str(home), "outDir": str(home / "compiled"),
            },
            "files": [str(home / "consumer.ts")],
        }), encoding="utf-8")
        compiled = subprocess.run([node_executable(), str(ROOT / "node_modules" / "typescript" / "bin" / "tsc"),
                                   "-p", str(config), "--pretty", "false"], cwd=ROOT, capture_output=True,
                                  text=True, encoding="utf-8", check=False)
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        (home / "compiled" / "package.json").write_bytes(b'{"type":"module"}\n')
        executed = subprocess.run([node_executable(), str(home / "compiled" / "consumer.js")],
                                  cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)
        self.assertIn("response-only readonly", executed.stdout)


if __name__ == "__main__":
    unittest.main()
