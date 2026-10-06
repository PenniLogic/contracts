"""Real generic three-target code generation, compilation and typed transport conformance."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from support import ROOT, SpecDir, replace_once, section_text, replace_section, spec_text, with_probe_paths
from pl_contracts import node_executable


class NullableStatusGenerationTest(unittest.TestCase):
    def test_real_three_target_nullable_and_empty_status_consumers(self) -> None:
        spec = SpecDir()
        self.addCleanup(spec.cleanup)
        home = Path(tempfile.mkdtemp(prefix="pl-nullable-status-", dir=ROOT / "build"))
        self.addCleanup(shutil.rmtree, home)
        definitions = {
            "NullableLabel": {"type": "string", "minLength": 1, "maxLength": 8, "pattern": "^[a-z]+$"},
            "NullableLabelAlias": {"$ref": "#/components/schemas/NullableLabel"},
            "NullableChoice": {"type": "string", "enum": ["alpha", "beta"]},
            "NullableChoiceAlias": {"$ref": "#/components/schemas/NullableChoice"},
            "NullableProbe": {
                "type": "object", "x-pennilogic-strict-provider": True, "additionalProperties": False,
                "required": ["only_null", "number", "label", "values", "nested", "choice", "choice_alias",
                             "choices", "choices_nested", "nonnull_choice"],
                "properties": {
                    "only_null": {"type": "null"},
                    "number": {"type": ["integer", "null"], "minimum": -10, "maximum": 10},
                    "label": {"anyOf": [{"$ref": "#/components/schemas/NullableLabelAlias"}, {"type": "null"}]},
                    "values": {"type": ["array", "null"], "minItems": 1, "maxItems": 3,
                               "items": {"type": ["integer", "null"], "minimum": -10, "maximum": 10}},
                    "nested": {"type": "array", "maxItems": 3, "items": {
                        "type": ["array", "null"], "maxItems": 3, "items": {
                            "anyOf": [{"$ref": "#/components/schemas/NullableLabelAlias"}, {"type": "null"}],
                        },
                    }},
                    "optional_flag": {"type": ["boolean", "null"]},
                    "choice": {"anyOf": [{"$ref": "#/components/schemas/NullableChoice"}, {"type": "null"}]},
                    "choice_alias": {"anyOf": [{"$ref": "#/components/schemas/NullableChoiceAlias"}, {"type": "null"}]},
                    "choices": {"type": "array", "maxItems": 3, "items": {
                        "anyOf": [{"$ref": "#/components/schemas/NullableChoice"}, {"type": "null"}],
                    }},
                    "optional_choice": {"anyOf": [{"$ref": "#/components/schemas/NullableChoice"}, {"type": "null"}]},
                    "choices_nested": {"type": "array", "maxItems": 3, "items": {
                        "type": ["array", "null"], "maxItems": 3, "uniqueItems": True, "items": {
                            "anyOf": [{"$ref": "#/components/schemas/NullableChoice"}, {"type": "null"}],
                        },
                    }},
                    "nonnull_choice": {"$ref": "#/components/schemas/NullableChoice"},
                },
            },
        }
        declarations = "".join(f"    {name}: {json.dumps(value)}\n" for name, value in definitions.items())
        text = replace_once(with_probe_paths(spec_text()), "  schemas:\n", "  schemas:\n" + declarations)
        route = """  /nullable-probe:
    get:
      operationId: probeNullable
      tags: [probe]
      description: Synthetic generic conformance transport, never a product endpoint.
      responses:
        '200':
          description: Closed declared-null body.
          content: {application/json: {schema: {$ref: '#/components/schemas/NullableProbe'}}}
        '204': {description: Declared empty success.}
"""
        paths = section_text(text, ("paths",))
        text = replace_section(text, ("paths",), "paths:\n" + route + paths.partition("\n")[2])
        source = spec.write(text)
        self.run_checked([sys.executable, str(ROOT / "scripts" / "generate_clients.py"),
                          "--spec", str(source), "--output-dir", str(home / "generated")])
        for language in ("python", "typescript", "kotlin"):
            self.assertTrue((home / "generated" / language / "contracts-manifest.json").is_file(), language)
        fixtures = ROOT / "scripts" / "tests" / "fixtures"
        interpreter = ROOT / "build" / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.assertTrue(interpreter.is_file())
        self.run_checked([str(interpreter), str(fixtures / "nullable_status_probe.py")],
                         env={"PYTHONPATH": str(home / "generated" / "python"), "PYTHONDONTWRITEBYTECODE": "1"})
        shutil.copyfile(fixtures / "nullable_status_probe.ts", home / "probe.ts")
        configuration = json.loads((ROOT / "smoke" / "typescript" / "tsconfig.json").read_text(encoding="utf-8"))
        configuration["compilerOptions"].update(rootDir=str(home), outDir=str(home / "compiled"),
                                                 typeRoots=[str(ROOT / "node_modules" / "@types")])
        configuration["include"] = ["generated\\typescript\\src\\**\\*.ts", "probe.ts"]
        (home / "tsconfig.json").write_text(json.dumps(configuration), encoding="utf-8")
        self.run_checked([node_executable(), str(ROOT / "node_modules" / "typescript" / "bin" / "tsc"),
                          "-p", str(home / "tsconfig.json")])
        (home / "compiled" / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
        self.run_checked([node_executable(), str(home / "compiled" / "probe.js")])
        kotlin = home / "consumer-kotlin"
        kotlin.mkdir()
        project = ROOT / "smoke" / "kotlin"
        for name in ("settings.gradle.kts", "gradle.properties"):
            shutil.copyfile(project / name, kotlin / name)
        shutil.copytree(project / "gradle", kotlin / "gradle")
        build = (project / "build.gradle.kts").read_text(encoding="utf-8")
        absolute = str(home / "generated" / "kotlin").replace("\\", "\\\\")
        build = build.replace("../../build/generated/kotlin", absolute)
        build = build.replace('rootProject.projectDir.resolve("../../spec/fixtures")',
                              'file("' + str(ROOT / "spec" / "fixtures").replace("\\", "\\\\") + '")')
        (kotlin / "build.gradle.kts").write_text(build, encoding="utf-8")
        destination = kotlin / "src" / "test" / "kotlin" / "NullableStatusProbeTest.kt"
        destination.parent.mkdir(parents=True)
        shutil.copyfile(fixtures / "NullableStatusProbeTest.kt", destination)
        arguments = ["--project-dir", str(kotlin), "--no-daemon", "--no-configuration-cache",
                     "--console=plain", "--offline", "test"]
        wrapper = ["cmd.exe", "/c", str(project / "gradlew.bat")] if os.name == "nt" else ["sh", str(project / "gradlew")]
        cache = os.environ.get("GRADLE_USER_HOME")
        if cache is None and os.name == "nt":
            cache = str(ROOT / ".toolchain" / "gradle-user-home")
        self.run_checked([*wrapper, *arguments], env={"GRADLE_USER_HOME": cache} if cache else None)

    def run_checked(self, command: list[str], *, env: dict[str, str] | None = None) -> None:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
                                env={**os.environ, **(env or {})})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
