"""Actually generate and compile closed DTO compositions with the accepted seams."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

from support import ROOT, SpecDir, replace_once, spec_text

import generate_clients as gc
import pl_contracts
import toolchain
from test_error_provider import schema_results


MODELS = """    SyntheticProviderRecord:
      type: object
      x-pennilogic-strict-provider: true
      additionalProperties: false
      required: [money, recorded_at, booked_on, currency_code, zone, public_id, values, flags, codes, preview, refusal]
      properties:
        money:
          $ref: '#/components/schemas/Money'
        recorded_at:
          $ref: '#/components/schemas/Instant'
        booked_on:
          $ref: '#/components/schemas/LocalDate'
        currency_code:
          $ref: '#/components/schemas/CurrencyCode'
        zone:
          $ref: '#/components/schemas/TimeZone'
        public_id:
          $ref: '#/components/schemas/PublicCorrelationId'
        values:
          type: array
          minItems: 1
          maxItems: 2
          items: {type: integer, minimum: 0, maximum: 10}
        flags:
          type: array
          items: {type: boolean}
        codes:
          type: array
          items:
            $ref: '#/components/schemas/ProblemCode'
        preview:
          $ref: '#/components/schemas/ImportPreview'
        refusal:
          $ref: '#/components/schemas/AiRefusal'
        problem:
          $ref: '#/components/schemas/ServiceProblemDetail'
    SyntheticProviderEnvelope:
      type: object
      x-pennilogic-strict-provider: true
      additionalProperties: false
      required: [record, records]
      properties:
        record:
          $ref: '#/components/schemas/SyntheticProviderRecord'
        records:
          type: array
          minItems: 1
          items:
            $ref: '#/components/schemas/SyntheticProviderRecord'
    SyntheticLegacyEnvelope:
      type: object
      properties:
        problem:
          $ref: '#/components/schemas/ServiceProblemDetail'
    SyntheticConstraintBundle:
      type: object
      x-pennilogic-strict-provider: true
      additionalProperties: false
      required: [grid, ids, aliases, tokens, flags, codes, interval, empty, glyphs, money_items]
      properties:
        grid:
          type: array
          minItems: 1
          maxItems: 2
          uniqueItems: true
          items:
            type: array
            minItems: 1
            maxItems: 2
            items: {type: integer, minimum: 0, maximum: 10}
        ids:
          type: array
          minItems: 1
          maxItems: 2
          uniqueItems: true
          items:
            $ref: '#/components/schemas/PublicCorrelationId'
        tokens:
          type: array
          items:
            type: array
            items: {type: string, pattern: '^ok_[a-z]{1,3}$', minLength: 4, maxLength: 6}
        flags:
          type: array
          items:
            type: array
            items: {type: boolean}
        codes:
          type: array
          items:
            $ref: '#/components/schemas/ProblemCode'
        aliases:
          type: array
          items:
            $ref: '#/components/schemas/SyntheticPublicAlias'
        interval:
          type: array
          items: {type: integer, exclusiveMinimum: 0, exclusiveMaximum: 10}
        empty:
          type: array
          maxItems: 0
          items: {type: integer}
        glyphs:
          type: array
          items: {type: string, minLength: 1, maxLength: 1}
        money_items:
          type: array
          items:
            $ref: '#/components/schemas/Money'
    SyntheticPublicAlias:
      $ref: '#/components/schemas/PublicCorrelationId'
    SyntheticConstraintEnvelope:
      type: object
      x-pennilogic-strict-provider: true
      additionalProperties: false
      required: [bundle]
      properties:
        bundle:
          $ref: '#/components/schemas/SyntheticConstraintBundle'
    SyntheticUnicodeRecord:
      type: object
      x-pennilogic-strict-provider: true
      additionalProperties: false
      required: [symbol, unit, wild, not_a, literal, choice, bounded, unbounded, empty, slash, brackets, backslash, anchors, class_dot, hyphen, range_text, double, zero_repeat]
      properties:
        symbol: {type: string, minLength: 1, maxLength: 1}
        unit: {type: string, pattern: '^.$', minLength: 1, maxLength: 1}
        wild:
          type: array
          items: {type: string, pattern: '^.$', minLength: 1, maxLength: 1}
        not_a:
          type: array
          items: {type: string, pattern: '^[^a]$', minLength: 1, maxLength: 1}
        literal:
          type: array
          items: {type: string, pattern: '^a\\.b$'}
        choice: {type: string, pattern: '^([a-z]{1,3}|[0-9]{2,4})$'}
        bounded: {type: string, pattern: '^a{1,3}$'}
        unbounded: {type: string, pattern: '^a{2,}$'}
        empty: {type: string, pattern: '^$', maxLength: 0}
        slash: {type: string, pattern: '^x/y$'}
        brackets: {type: string, pattern: '^\\[a\\]$'}
        backslash: {type: string, pattern: '^a\\\\b$'}
        anchors: {type: string, pattern: '^\\^\\$$'}
        class_dot: {type: string, pattern: '^[.]$'}
        hyphen: {type: string, pattern: '^-$'}
        range_text: {type: string, pattern: '^[A-Za-z0-9_+-]+$'}
        double: {type: string, pattern: '^..$', minLength: 2, maxLength: 2}
        zero_repeat: {type: string, pattern: '^a{0,2}$'}
    SyntheticUnicodeEnvelope:
      type: object
      x-pennilogic-strict-provider: true
      additionalProperties: false
      required: [record]
      properties:
        record:
          $ref: '#/components/schemas/SyntheticUnicodeRecord'
"""


class ProviderCompositionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = SpecDir()
        cls.addClassCleanup(cls.spec.cleanup)
        cls.spec_path = cls.spec.write(replace_once(spec_text(), "  schemas:\n", "  schemas:\n" + MODELS))
        cls.output = ROOT / "build" / "provider-composition"
        cls.output.mkdir(parents=True, exist_ok=True)
        cls.evidence = cls.output / "runs" / str(time.time_ns())
        cls.evidence.mkdir(parents=True)
        shutil.copyfile(cls.spec_path, cls.output / "openapi.yaml")
        tools = toolchain.ensure_installed()
        for language in gc.LANGUAGES:
            gc.generate(language, cls.output / language, tools, cls.spec_path)

    def run_probe(self, command: list[str], *, label: str, cwd: Path = ROOT,
                  env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        started = time.perf_counter()
        completed = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", **(env or {})},
            check=False, timeout=300,
        )
        elapsed = time.perf_counter() - started
        (self.evidence / f"{label}.stdout.log").write_text(completed.stdout, encoding="utf-8")
        (self.evidence / f"{label}.stderr.log").write_text(completed.stderr, encoding="utf-8")
        (self.evidence / f"{label}.json").write_text(json.dumps({
            "command": command, "cwd": str(cwd), "exit_code": completed.returncode,
            "duration_seconds": elapsed, "synthetic_only": True,
        }, indent=2) + "\n", encoding="utf-8")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return completed

    def test_python_ordinary_native_nested_generic_and_actual_generated_transport(self) -> None:
        interpreter = ROOT / "build" / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.assertTrue(interpreter.is_file(), "Run the documented Python smoke command to provide its pinned venv")
        probe = self.output / "provider_composition.py"
        shutil.copyfile(ROOT / "scripts" / "tests" / probe.name, probe)
        config = (ROOT / "smoke" / "python" / "mypy.ini").read_text(encoding="utf-8")
        config = replace_once(config,
            "mypy_path = $MYPY_CONFIG_FILE_DIR/../../build/generated/python:$MYPY_CONFIG_FILE_DIR:$MYPY_CONFIG_FILE_DIR/tests",
            "mypy_path = $MYPY_CONFIG_FILE_DIR/python",
        )
        config_path = self.output / "mypy.ini"
        config_path.write_text(config, encoding="utf-8", newline="\n")
        self.run_probe([
            str(interpreter), "-m", "mypy", "--config-file", str(config_path),
            str(self.output / "python" / "pennilogic_contracts"), str(probe),
        ], label="python-mypy")
        result = self.run_probe([str(interpreter), str(probe), "-v"], env={
            "PYTHONPATH": str(self.output / "python"), "PL_CONTRACTS_ROOT": str(ROOT),
        }, label="python-runtime")
        self.assertIn("OK", result.stderr)

    def test_recursive_negatives_fail_for_their_own_declared_schema_reason(self) -> None:
        data = json.loads((ROOT / "spec" / "fixtures" / "provider-recursive.v1.json").read_bytes())
        cases = [{"name": "control", "schema": "SyntheticConstraintBundle", "wire": data["control"]}]
        cases += [{"name": negative["name"], "schema": "SyntheticConstraintBundle",
                   "wire": {**data["control"], negative["field"]: negative["value"]}}
                  for negative in data["negatives"]]
        results = schema_results(cases, spec=self.spec_path)
        self.assertTrue(results[0]["valid"])
        for result, negative in zip(results[1:], data["negatives"]):
            self.assertFalse(result["valid"], negative["name"])
            self.assertIn(negative["keyword"], result["keywords"], negative["name"])

    def test_unicode_patterns_and_direct_lengths_match_the_actual_schema(self) -> None:
        data = json.loads((ROOT / "spec" / "fixtures" / "provider-unicode.v1.json").read_bytes())
        cases = [{"name": entry["name"], "schema": "SyntheticUnicodeRecord",
                  "wire": {key: value for key, value in entry.items() if key != "name"}}
                 for entry in data["positive"]]
        base = {key: value for key, value in data["positive"][0].items() if key != "name"}
        cases += [{"name": entry["name"], "schema": "SyntheticUnicodeRecord",
                   "wire": {**base, entry["field"]: entry["value"]}} for entry in data["negative"]]
        results = schema_results(cases, spec=self.spec_path)
        self.assertTrue(all(entry["valid"] for entry in results[:len(data["positive"])]))
        for result, negative in zip(results[len(data["positive"]):], data["negative"]):
            self.assertFalse(result["valid"], negative["name"])
            self.assertIn(negative["keyword"], result["keywords"], negative["name"])

    def test_typescript_strict_compile_optional_refs_dense_arrays_and_actual_baseapi(self) -> None:
        probe = self.output / "provider_composition.test.ts"
        shutil.copyfile(ROOT / "scripts" / "tests" / probe.name, probe)
        (self.output / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
        config_path = self.output / "tsconfig.json"
        options = json.loads((ROOT / "smoke" / "typescript" / "tsconfig.json").read_text(encoding="utf-8"))["compilerOptions"]
        config_path.write_text(json.dumps({
            "compilerOptions": {
                **options, "skipLibCheck": False, "noEmitOnError": True,
                "typeRoots": [str(ROOT / "node_modules" / "@types")], "types": ["node"],
                "outDir": "compiled", "rootDir": ".",
            },
            "include": [probe.name, "typescript/src/**/*.ts"],
        }, indent=2) + "\n", encoding="utf-8")
        node = pl_contracts.node_executable()
        self.run_probe([node, str(ROOT / "node_modules" / "typescript" / "bin" / "tsc"), "-p", str(config_path)],
                       label="typescript-compile")
        result = self.run_probe([node, "--test", str(self.output / "compiled" / probe.with_suffix(".js").name)],
                                env={"PL_CONTRACTS_ROOT": str(ROOT)}, label="typescript-runtime")
        self.assertIn("fail 0", result.stdout)

    def test_kotlin_registered_native_nested_and_actual_mockengine_transport(self) -> None:
        project = ROOT / "smoke" / "kotlin"
        pins = pl_contracts.versions()["gradle"]
        self.assertEqual(pl_contracts.sha256_file(project / "gradle" / "wrapper" / "gradle-wrapper.jar"),
                         pins["wrapper_jar_sha256"])
        properties = (project / "gradle" / "wrapper" / "gradle-wrapper.properties").read_text(encoding="utf-8")
        self.assertIn(f"distributionSha256Sum={pins['distribution_sha256']}", properties)
        self.assertIn(f"gradle-{pins['version']}-bin.zip", properties)
        source = self.output / "kotlin-probe"
        source.mkdir(exist_ok=True)
        shutil.copyfile(ROOT / "scripts" / "tests" / "ProviderCompositionTest.kt", source / "ProviderCompositionTest.kt")
        init = ROOT / "scripts" / "tests" / "provider_composition.gradle"
        args = [
            "--no-daemon", "--no-configuration-cache", "--console=plain", "--warning-mode=all",
            "--init-script", str(init), f"-Dpennilogic.composition.output={self.output}", "--rerun-tasks", "test",
        ]
        command = (["cmd.exe", "/c", str(project / "gradlew.bat")] if os.name == "nt"
                   else ["sh", str(project / "gradlew")]) + args
        self.run_probe(command, cwd=project, label="kotlin-compile-runtime")
        from xml.etree import ElementTree
        xml = ElementTree.parse(self.output / "kotlin-build" / "test-results" / "test" /
                                "TEST-com.pennilogic.contracts.smoke.ProviderCompositionTest.xml").getroot()
        self.assertEqual(xml.attrib["tests"], "7")
        for key in ("failures", "errors", "skipped"):
            self.assertEqual(xml.attrib[key], "0", key)


if __name__ == "__main__":
    unittest.main()
