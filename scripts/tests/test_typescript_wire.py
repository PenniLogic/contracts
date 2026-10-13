"""Actual generated public writer types must agree with emitted wire data, not domain wrappers."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import ROOT, SpecDir, replace_once, replace_section, section_text, spec_text, run_script, with_probe_paths
from pl_contracts import node_executable


class TypeScriptPublicWireTest(unittest.TestCase):
    def test_strict_cast_free_public_consumers_and_paired_compile_negatives(self) -> None:
        home = Path(tempfile.mkdtemp(prefix="pl-public-wire-", dir=ROOT / "build"))
        self.addCleanup(shutil.rmtree, home)
        shutil.copytree(ROOT / "build" / "generated" / "typescript", home / "sdk")
        fixtures = ROOT / "scripts" / "tests" / "fixtures"
        for name in ("typescript_wire_public.ts", "typescript_wire_negative.ts"):
            source = (fixtures / name).read_text(encoding="utf-8")
            self.assertNotRegex(source, r"\bas\s+(?:any|unknown|Money|LedgerEntry)\b|@ts-|:\s*any\b")
            shutil.copyfile(fixtures / name, home / name)
        configuration = {
            "compilerOptions": {
                "strict": True, "noUncheckedIndexedAccess": True, "noImplicitReturns": True,
                "target": "es2022", "module": "es2022", "moduleResolution": "bundler",
                "lib": ["es2022", "dom", "dom.iterable"], "types": ["node"],
                "typeRoots": [str(ROOT / "node_modules" / "@types")],
                "skipLibCheck": False, "verbatimModuleSyntax": True,
                "rootDir": str(home), "outDir": str(home / "compiled"),
            },
            "files": [str(home / "typescript_wire_public.ts")],
        }
        config = home / "tsconfig.json"
        config.write_text(json.dumps(configuration), encoding="utf-8")
        command = [node_executable(), str(ROOT / "node_modules" / "typescript" / "bin" / "tsc"),
                   "--project", str(config), "--pretty", "false"]
        positive = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(positive.returncode, 0, positive.stdout + positive.stderr)
        (home / "compiled" / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
        runtime = subprocess.run([node_executable(), str(home / "compiled" / "typescript_wire_public.js")],
                                 cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(runtime.returncode, 0, runtime.stdout + runtime.stderr)
        self.assertIn("cast-free public", runtime.stdout)

        configuration["files"] = [str(home / "typescript_wire_negative.ts")]
        config.write_text(json.dumps(configuration), encoding="utf-8")
        negative = subprocess.run([*command, "--noEmit"], cwd=ROOT, capture_output=True,
                                  text=True, encoding="utf-8", check=False)
        self.assertNotEqual(negative.returncode, 0)
        diagnostics = negative.stdout + negative.stderr
        source = (home / "typescript_wire_negative.ts").read_text(encoding="utf-8").splitlines()
        expected = {number for number, line in enumerate(source, 1) if line.startswith("const ")}
        expected.update(number for number, line in enumerate(source, 1)
                        if re.match(r"(?:transaction|categorisation)(?:Wire)?\.", line))
        expected.add(next(number for number, line in enumerate(source, 1)
                          if "postTransactionRequest: PostTransactionRequestToJSON(post)" in line))
        expected.remove(next(number for number, line in enumerate(source, 1)
                             if line.startswith("const plainWireApiArgument")))
        actual = {int(line) for line in re.findall(r"typescript_wire_negative\.ts\((\d+),\d+\): error TS", diagnostics)}
        self.assertEqual(actual, expected, diagnostics)
        self.assertNotRegex(diagnostics, r"error TS(?:2305|2307|18003)")

    def test_actual_scratch_money_collections_nullable_arrays_and_optional_wire_fields(self) -> None:
        spec = SpecDir()
        self.addCleanup(spec.cleanup)
        home = Path(tempfile.mkdtemp(prefix="pl-wire-collections-", dir=ROOT / "build"))
        self.addCleanup(shutil.rmtree, home)
        definitions = {
            "WireLeaf": {
                "type": "object", "x-pennilogic-strict-provider": True, "additionalProperties": False,
                "required": ["ledger_amount"], "properties": {"ledger_amount": {"$ref": "#/components/schemas/Money"}},
            },
            "WireProbe": {
                "type": "object", "x-pennilogic-strict-provider": True, "additionalProperties": False,
                "required": ["total", "money_items", "money_set", "leaf", "leaves", "nullable_items", "number_or_null"],
                "properties": {
                    "total": {"$ref": "#/components/schemas/Money"},
                    "money_items": {"type": "array", "items": {"$ref": "#/components/schemas/Money"}},
                    "money_set": {"type": "array", "uniqueItems": True, "items": {"$ref": "#/components/schemas/Money"}},
                    "leaf": {"$ref": "#/components/schemas/WireLeaf"},
                    "leaves": {"type": "array", "items": {"$ref": "#/components/schemas/WireLeaf"}},
                    "nullable_items": {"type": ["array", "null"], "items": {"$ref": "#/components/schemas/Money"}},
                    "number_or_null": {"type": ["integer", "null"]},
                    "optional_money": {"$ref": "#/components/schemas/Money"},
                    "optional_items": {"type": "array", "items": {"$ref": "#/components/schemas/Money"}},
                },
            },
            "WireLegacy": {
                "type": "object", "required": ["total"],
                "properties": {"total": {"$ref": "#/components/schemas/Money"}},
            },
            "WireReceipt": {
                "type": "object", "x-pennilogic-strict-provider": True, "additionalProperties": False,
                "required": ["result_total"],
                "properties": {"result_total": {"$ref": "#/components/schemas/Money", "readOnly": True}},
            },
        }
        declarations = "".join(f"    {name}: {json.dumps(value)}\n" for name, value in definitions.items())
        text = replace_once(with_probe_paths(spec_text()), "  schemas:\n", "  schemas:\n" + declarations)
        response_path = ("paths", "/probe", "get", "responses")
        text = replace_section(text, response_path,
            "      responses:\n        '200':\n          description: Synthetic response-only receipt.\n"
            "          content: {application/json: {schema: {$ref: '#/components/schemas/WireReceipt'}}}\n")
        source = spec.write(text)
        generated = run_script("generate_clients.py", "--language", "typescript", "--spec", str(source),
                               "--output-dir", str(home / "generated"))
        self.assertEqual(generated.returncode, 0, generated.stdout + generated.stderr)
        shutil.move(str(home / "generated" / "typescript"), home / "sdk")
        fixture = ROOT / "scripts" / "tests" / "fixtures" / "typescript_wire_collections.ts"
        shutil.copyfile(fixture, home / "consumer.ts")
        configuration = {
            "compilerOptions": {
                "strict": True, "noUncheckedIndexedAccess": True,
                "target": "es2022", "module": "es2022", "moduleResolution": "bundler",
                "lib": ["es2022", "dom", "dom.iterable"], "types": ["node"],
                "typeRoots": [str(ROOT / "node_modules" / "@types")], "skipLibCheck": False,
                "rootDir": str(home), "outDir": str(home / "compiled"),
            },
            "files": [str(home / "consumer.ts")],
        }
        config = home / "tsconfig.json"
        config.write_text(json.dumps(configuration), encoding="utf-8")
        command = [node_executable(), str(ROOT / "node_modules" / "typescript" / "bin" / "tsc"),
                   "--project", str(config), "--pretty", "false"]
        compiled = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        (home / "compiled" / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
        native = subprocess.run([node_executable(), str(home / "compiled" / "consumer.js")],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(native.returncode, 0, native.stdout + native.stderr)
        negative = home / "negative.ts"
        negative.write_text(
            "import { Money, WireProbeToJSON, type WireProbe, type MoneyWire } from './sdk/src/index.js';\n"
            "declare const value: WireProbe;\n"
            "const total: Money = WireProbeToJSON(value).total;\n"
            "const nested: Money = WireProbeToJSON(value).leaf.ledger_amount;\n"
            "const items: Money[] = WireProbeToJSON(value).money_items;\n"
            "const moneySet: Set<Money> = WireProbeToJSON(value).money_set;\n"
            "const optional: MoneyWire = WireProbeToJSON(value).optional_money;\n"
            "const nullable: MoneyWire[] = WireProbeToJSON(value).nullable_items;\n", encoding="utf-8")
        configuration["files"] = [str(negative)]
        config.write_text(json.dumps(configuration), encoding="utf-8")
        refused = subprocess.run([*command, "--noEmit"], cwd=ROOT, capture_output=True,
                                 text=True, encoding="utf-8", check=False)
        self.assertNotEqual(refused.returncode, 0)
        lines = {int(line) for line in re.findall(r"negative\.ts\((\d+),\d+\): error TS",
                                                 refused.stdout + refused.stderr)}
        self.assertEqual(lines, set(range(3, 9)), refused.stdout + refused.stderr)
        for label, revised in (
            ("unbound readonly model", replace_section(text, response_path,
             "      responses:\n        '204': {description: Synthetic empty response.}\n")),
            ("request-bound readonly model", replace_section(text, ("paths", "/probe", "post", "requestBody"),
             "      requestBody:\n        content: {application/json: {schema: {$ref: '#/components/schemas/WireReceipt'}}}\n")),
        ):
            with self.subTest(case=label):
                rejected = run_script("generate_clients.py", "--language", "typescript",
                                      "--spec", str(spec.write(revised)), "--output-dir", str(home / "rejected"))
                self.assertEqual(rejected.returncode, 1, rejected.stdout + rejected.stderr)
                self.assertIn("readOnly model must be response-only", rejected.stderr)
                self.assertFalse((home / "rejected").exists())

    def test_every_generated_generic_writer_exports_a_truthful_wire_type_without_output_assertions(self) -> None:
        models = ROOT / "build" / "generated" / "typescript" / "src" / "models"
        seen = 0
        for path in models.glob("*.ts"):
            source = path.read_text(encoding="utf-8")
            if "export type " + path.stem + "Wire =" not in source:
                continue
            seen += 1
            writer = source[source.index("export function " + path.stem + "ToJSON"):]
            self.assertNotRegex(writer, r": any\b|\bas\s+(?:any|unknown|Array|Set|Money)\b")
            self.assertIn("WireOutput<Input, " + path.stem + "Wire", writer)
        self.assertGreaterEqual(seen, 113)


if __name__ == "__main__":
    unittest.main()
