"""Actual bounded generator examples and transactional preservation, not giant-input tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import ROOT, SpecDir, replace_once, spec_text
from pl_contracts import PipelineError, node_executable, tree_hash
import generate_clients as gc
import toolchain


def patterned(text: str, name: str, pattern: str, **constraints: object) -> str:
    shape = {"type": "string", "pattern": pattern, **constraints}
    model = ("    " + name + ":\n      type: object\n      x-pennilogic-strict-provider: true\n"
             "      additionalProperties: false\n      properties:\n        value: " + json.dumps(shape) + "\n")
    return replace_once(text, "  schemas:\n", "  schemas:\n" + model)


class ConstructiveExampleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = SpecDir()
        self.addCleanup(self.spec.cleanup)

    def test_aggregate_unconstructible_patterns_are_refused_before_every_target_output(self) -> None:
        for pattern, constraints in [
            ("^a{2147483647}$", {}), ("^(a{65}){64}$", {}),
            ("^a{2048}b{2049}$", {}), ("^(a{2147483647}|b{2147483647})$", {}),
            ("^(a{0}){2147483647}$", {}), ("^a{4097,}$", {}),
            ("^(a?){2147483647}$", {}), ("^((a?){2147483647}|b)$", {}),
            ("^a{0,2147483647}$", {"minLength": 4097}),
            ("^(a{2147483647}|null)$", {}),
        ]:
            source = self.spec.write(patterned(spec_text(), "UnsafeConstruction", pattern, **constraints))
            for language in gc.LANGUAGES:
                with self.subTest(pattern=pattern, language=language):
                    parent = self.spec.path / "existing"
                    output = parent / language
                    output.mkdir(parents=True, exist_ok=True)
                    sentinel = output / "existing.txt"
                    sentinel.write_bytes(b"previous-valid-content\n")
                    before = tree_hash(output)
                    result = subprocess.run(
                        [sys.executable, str(ROOT / "scripts" / "generate_clients.py"), "--language", language,
                         "--spec", str(source), "--output-dir", str(parent)],
                        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=90,
                        env={**os.environ, "JAVA_TOOL_OPTIONS": "-Xmx256m", "PYTHONDONTWRITEBYTECODE": "1"},
                    )
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("unsupported example construction", result.stderr)
                    self.assertNotIn("OutOfMemoryError", result.stderr)
                    self.assertEqual(tree_hash(output), before)
                    self.assertEqual(list(self.spec.path.glob(".contracts-generation-*")), [])

    def test_huge_upper_counts_and_bounded_nested_alternatives_generate_all_three_real_targets(self) -> None:
        patterns = {
            "WideMaximum": "^a{0,2147483647}$",
            "WideNearlyMaximum": "^a{0,2147483646}$",
            "NestedWide": "^(a{0,2147483647}){0,2147483647}$",
            "ShortAlternative": "^(a{2147483647}|b)$",
            "BoundedProduct": "^(a{64}){64}$",
            "BoundedConcatenation": "^a{2048}b{2048}$",
            "BoundedExact": "^a{4096}$",
            "SafeSentinelAlternative": "^(a{2147483647}|null|z)$",
        }
        text = spec_text()
        for name, pattern in patterns.items():
            text = patterned(text, name, pattern)
        text = patterned(text, "WideBlankExample", "^a{0,2147483647}$", example="")
        text = patterned(text, "WideBlankEnum", "^a{0,2147483647}$", enum=[""])
        text = patterned(text, "WideUnicodeExample", "^.{0,2147483647}$", example="\u00a0")
        source = self.spec.write(text)
        output = self.spec.path / "generated"
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "generate_clients.py"),
             "--spec", str(source), "--output-dir", str(output)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180,
            env={**os.environ, "JAVA_TOOL_OPTIONS": "-Xmx256m", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for language in gc.LANGUAGES:
            manifest = json.loads((output / language / "contracts-manifest.json").read_bytes())
            self.assertTrue(manifest["generation_examples"]["validated_generation_only_annotations"])
            self.assertEqual(manifest["generation_examples"]["count"], len(patterns) + 1)
            self.assertEqual(manifest["generation_examples"]["budget"], 4096)
            digest, files = tree_hash(output / language, exclude=("contracts-manifest.json",))
            self.assertEqual(digest, manifest["tree_sha256"])
            self.assertEqual(dict(files), {entry["path"]: entry["sha256"] for entry in manifest["files"]})
        compiled = subprocess.run([node_executable(), str(ROOT / "scripts" / "provider_constraints.cjs"), str(source)],
                                  cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        declarations = json.loads(compiled.stdout)
        for name, pattern in patterns.items():
            self.assertEqual(declarations["schemas"][name]["properties"]["value"]["pattern"], pattern)
        # Actual schema validation checks generation-only examples, not invented dummy values.
        cases = [{"name": name, "schema": name, "wire": {"value": schema["properties"]["value"]["example"]}}
                 for name, schema in declarations["generation_input"]["components"]["schemas"].items()
                 if name in patterns or name == "WideBlankExample"]
        from test_error_provider import schema_results
        self.assertTrue(all(result["valid"] for result in schema_results(cases, spec=source)))
        interpreter = ROOT / "build" / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.assertTrue(interpreter.is_file(), "Run the existing Python smoke command first")
        probe = """
import importlib, json
from pydantic import TypeAdapter
from pennilogic_contracts import ApiClient
controls = {
    'wide_maximum': ('WideMaximum', 'a'),
    'wide_nearly_maximum': ('WideNearlyMaximum', ''),
    'nested_wide': ('NestedWide', 'a'),
    'short_alternative': ('ShortAlternative', 'b'),
    'bounded_product': ('BoundedProduct', 'a' * 4096),
    'bounded_concatenation': ('BoundedConcatenation', 'a' * 2048 + 'b' * 2048),
    'bounded_exact': ('BoundedExact', 'a' * 4096),
    'wide_blank_example': ('WideBlankExample', ''),
    'wide_blank_enum': ('WideBlankEnum', ''),
    'wide_unicode_example': ('WideUnicodeExample', '\\u00a0'),
    'safe_sentinel_alternative': ('SafeSentinelAlternative', 'z'),
}
for module, (name, text) in controls.items():
    model = getattr(importlib.import_module('pennilogic_contracts.models.' + module), name)
    absent = model.from_dict({})
    assert absent.to_dict() == {} and ApiClient().sanitize_for_serialization(absent) == {}
    value = model.from_dict({'value': text})
    assert json.loads(TypeAdapter(model).dump_json(value)) == {'value': text}
    assert ApiClient().sanitize_for_serialization(value) == {'value': text}
print('11 actual generated Python optional-model, generic and transport controls; no giant input')
"""
        runtime = subprocess.run([str(interpreter), "-c", probe], cwd=ROOT, capture_output=True, text=True,
                                 encoding="utf-8", timeout=90, env={**os.environ, "PYTHONPATH": str(output / "python"),
                                                                  "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(runtime.returncode, 0, runtime.stdout + runtime.stderr)


class GenerationTransactionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = SpecDir()
        cls.addClassCleanup(cls.spec.cleanup)
        cls.home = Path(tempfile.mkdtemp(prefix="pl-generation-safety-"))
        cls.addClassCleanup(shutil.rmtree, cls.home)
        cls.tools = toolchain.ensure_installed()
        cls.good = cls.spec.write(spec_text(), "valid.yaml")
        cls.bad = cls.spec.write(replace_once(spec_text(), "  schemas:\n",
            "  schemas:\n    UnmarkedDownstreamFailure:\n      $ref: '#/components/schemas/MissingDownstreamSchema'\n"), "invalid.yaml")
        cls.baseline = cls.home / "baseline"
        gc.generate_all(list(gc.LANGUAGES), cls.baseline, cls.tools, cls.good)

    def setUp(self) -> None:
        self.output = self.home / self.id().rsplit(".", 1)[-1]
        shutil.copytree(self.baseline, self.output)
        self.before = tree_hash(self.output)

    def test_actual_downstream_generator_failure_preserves_existing_tree_and_cleans_stage(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "generate_clients.py"), "--language", "python",
             "--spec", str(self.bad), "--output-dir", str(self.output)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=90,
            env={**os.environ, "JAVA_TOOL_OPTIONS": "-Xmx256m", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("openapi-generator failed", result.stderr)
        self.assertNotIn("generated ", result.stdout)
        self.assertEqual(tree_hash(self.output), self.before)
        self.assertEqual(list(self.home.glob(".contracts-generation-*")), [])

    def test_later_real_generator_failure_does_not_publish_earlier_targets(self) -> None:
        build = gc._generate_target
        def fail_last(language: str, output: Path, tools: dict, spec: Path = gc.SPEC) -> dict:
            return build(language, output, tools, self.bad if language == "python" else spec)
        with mock.patch.object(gc, "_generate_target", side_effect=fail_last):
            with self.assertRaisesRegex(PipelineError, "openapi-generator failed"):
                gc.generate_all(["kotlin", "typescript", "python"], self.output, self.tools, self.good)
        self.assertEqual(tree_hash(self.output), self.before)
        self.assertEqual(list(self.home.glob(".contracts-generation-*")), [])

    def test_actual_cli_late_failure_reports_no_partial_client_set(self) -> None:
        probe = """
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'scripts'))
import generate_clients as gc
valid, invalid, output = map(Path, sys.argv[1:])
build = gc._generate_target
def choose_source(language, output, tools, spec=gc.SPEC):
    return build(language, output, tools, invalid if language == 'python' else spec)
gc._generate_target = choose_source
sys.argv = ['generate_clients.py', '--spec', str(valid), '--output-dir', str(output)]
raise SystemExit(gc.main())
"""
        result = subprocess.run([sys.executable, "-c", probe, str(self.good), str(self.bad), str(self.output)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180,
                                env={**os.environ, "JAVA_TOOL_OPTIONS": "-Xmx256m", "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("openapi-generator failed for python", result.stderr)
        self.assertNotIn("generated ", result.stdout)
        self.assertEqual(tree_hash(self.output), self.before)
        self.assertEqual(list(self.home.glob(".contracts-generation-*")), [])

    def test_post_generator_companion_failure_keeps_previous_valid_output(self) -> None:
        with mock.patch.object(gc, "render_registry", side_effect=PipelineError("synthetic companion failure")):
            with self.assertRaisesRegex(PipelineError, "synthetic companion failure"):
                gc.generate("typescript", self.output / "typescript", self.tools, self.good)
        self.assertEqual(tree_hash(self.output), self.before)
        self.assertEqual(list(self.output.glob(".contracts-generation-*")), [])

    def test_promotion_failure_rolls_back_the_whole_previously_valid_set(self) -> None:
        replace = Path.replace
        fired = False
        def refuse_one(path: Path, target: Path) -> Path:
            nonlocal fired
            if not fired and path.parent.name == "ready" and path.name == "python":
                fired = True
                raise PermissionError("synthetic promotion failure")
            return replace(path, target)
        with mock.patch.object(Path, "replace", refuse_one):
            with self.assertRaisesRegex(PipelineError, "previous outputs restored"):
                gc.generate_all(["kotlin", "typescript", "python"], self.output, self.tools, self.good)
        self.assertTrue(fired)
        self.assertEqual(tree_hash(self.output), self.before)
        self.assertEqual(list(self.home.glob(".contracts-generation-*")), [])

    def test_verify_failure_preserves_client_set_and_golden(self) -> None:
        build_home = self.output / "build"
        shutil.copytree(self.baseline, build_home / "generated")
        golden = self.output / "golden.json"
        golden.write_bytes((ROOT / "generator" / "golden.json").read_bytes())
        before = tree_hash(self.output)
        generate = gc.generate
        attempts = 0
        def fail_second(language: str, output: Path, tools: dict, spec: Path = gc.SPEC) -> dict:
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise PipelineError("synthetic second-generation failure")
            return generate(language, output, tools, spec)
        with mock.patch.object(gc, "BUILD", build_home), mock.patch.object(gc, "GOLDEN", golden), \
                mock.patch.object(gc, "generate", side_effect=fail_second):
            with self.assertRaisesRegex(PipelineError, "synthetic second-generation failure"):
                gc.verify(["python"], self.tools, update_golden=True)
        self.assertEqual(tree_hash(self.output), before)
        self.assertEqual(attempts, 2)
        self.assertEqual(list(build_home.glob(".contracts-generation-*")), [])

    def test_success_publishes_only_complete_hash_bound_targets(self) -> None:
        manifests = gc.generate_all(list(gc.LANGUAGES), self.output, self.tools, self.good)
        self.assertEqual(set(manifests), set(gc.LANGUAGES))
        for language, manifest in manifests.items():
            self.assertEqual(json.loads((self.output / language / "contracts-manifest.json").read_bytes()), manifest)
            self.assertEqual(tree_hash(self.output / language, exclude=("contracts-manifest.json",))[0], manifest["tree_sha256"])
        self.assertEqual(tree_hash(self.output), self.before)
        self.assertEqual(list(self.home.glob(".contracts-generation-*")), [])

    def test_failed_rollback_retains_all_original_bytes_with_recovery_mapping(self) -> None:
        replace = Path.replace
        def refuse_promotion_and_restore(path: Path, target: Path) -> Path:
            if path.parent.name == "ready" and path.name == "python":
                raise PermissionError("synthetic promotion failure")
            if path.parent.name == "backups":
                raise PermissionError("synthetic restoration failure")
            return replace(path, target)
        with mock.patch.object(Path, "replace", refuse_promotion_and_restore):
            with self.assertRaisesRegex(PipelineError, "recovery artifacts retained"):
                gc.generate_all(["kotlin", "typescript", "python"], self.output, self.tools, self.good)
        retained = list(self.home.glob(".contracts-generation-*"))
        self.assertEqual(len(retained), 1)
        mapping = json.loads((retained[0] / "recovery.json").read_bytes())
        for entry in mapping["previous_outputs"]:
            backup = Path(entry["backup"])
            target = Path(entry["output"])
            self.assertEqual(target.parent, self.output.resolve())
            self.assertEqual(tree_hash(backup), tree_hash(self.baseline / target.name))
        self.assertFalse(any((self.output / language / "contracts-manifest.json").exists() for language in gc.LANGUAGES))
        for entry in mapping["previous_outputs"]:
            Path(entry["backup"]).replace(Path(entry["output"]))
        self.assertEqual(tree_hash(self.output), self.before)
        gc.remove_tree(retained[0])

    def test_failure_without_previous_outputs_leaves_no_partial_published_client(self) -> None:
        output = self.output / "new-client-set"
        with self.assertRaisesRegex(PipelineError, "openapi-generator failed"):
            gc.generate_all(list(gc.LANGUAGES), output, self.tools, self.bad)
        self.assertFalse(output.exists())
        self.assertEqual(tree_hash(self.output), self.before)
        self.assertEqual(list(self.output.glob(".contracts-generation-*")), [])


class CatchableRollbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="pl-rollback-interrupt-"))
        self.addCleanup(shutil.rmtree, self.home)
        self.prepare("initial")

    def prepare(self, label: str) -> None:
        fixture = self.home / label
        self.outputs = [fixture / "clients" / "kotlin", fixture / "clients" / "python", fixture / "golden.json"]
        for output in self.outputs[:2]:
            output.mkdir(parents=True)
            (output / "old.txt").write_bytes(b"previous valid synthetic client\n")
        self.outputs[2].write_bytes(b'{"previous":"synthetic golden"}\n')
        self.before = [self.snapshot(output) for output in self.outputs]

    @staticmethod
    def snapshot(path: Path) -> tuple:
        return ("directory", tree_hash(path)) if path.is_dir() else ("file", path.read_bytes())

    def promote(self) -> None:
        with gc._staging(self.home) as stage:
            ready = stage / "ready"
            ready.mkdir()
            replacements = []
            for index, output in enumerate(self.outputs):
                generated = ready / str(index)
                if index < 2:
                    generated.mkdir()
                    (generated / "new.txt").write_bytes(b"new synthetic client\n")
                else:
                    generated.write_bytes(b'{"new":"synthetic golden"}\n')
                replacements.append((generated, output))
            gc._publish(stage, replacements)

    def assert_recoverable(self) -> None:
        stages = list(self.home.glob(".contracts-generation-*"))
        self.assertEqual(len(stages), 1)
        mapping = json.loads((stages[0] / "recovery.json").read_bytes())["previous_outputs"]
        self.assertEqual(len(mapping), len(self.outputs))
        for index, entry in enumerate(mapping):
            output, backup = Path(entry["output"]), Path(entry["backup"])
            self.assertEqual(output, self.outputs[index].resolve())
            self.assertEqual(backup.parent, stages[0] / "backups")
            surviving = backup if backup.exists() else output
            self.assertTrue(surviving.exists(), f"previous destination {index} must remain recoverable")
            self.assertEqual(self.snapshot(surviving), self.before[index])
        for entry in mapping:
            backup, output = Path(entry["backup"]), Path(entry["output"])
            if backup.exists():
                backup.replace(output)
        self.assertEqual([self.snapshot(output) for output in self.outputs], self.before)
        gc.remove_tree(stages[0])

    def test_oserror_and_interrupt_during_first_or_partial_restore_retain_every_previous_byte(self) -> None:
        replace = Path.replace
        for failure in (PermissionError, KeyboardInterrupt):
            for fail_at in (1, 2, 3):
                with self.subTest(exception=failure.__name__, restore_position=fail_at):
                    self.prepare(f"{failure.__name__}-{fail_at}")
                    attempts = 0
                    def fail_restore(path: Path, target: Path) -> Path:
                        nonlocal attempts
                        if path.parent.name == "ready" and path.name == "2":
                            raise PermissionError("synthetic golden promotion failure")
                        if path.parent.name == "backups":
                            attempts += 1
                            if attempts == fail_at:
                                raise failure("synthetic restoration interruption")
                        return replace(path, target)
                    caught: BaseException | None = None
                    with mock.patch.object(Path, "replace", fail_restore):
                        try:
                            self.promote()
                        except (PipelineError, KeyboardInterrupt) as error:
                            caught = error
                    self.assertIsInstance(caught, gc._RecoveryRequired)
                    self.assertIsInstance(caught.__cause__ if caught else None, failure)
                    self.assertEqual(attempts, fail_at)
                    self.assert_recoverable()

    def test_successful_rollback_after_promotion_interrupt_restores_and_reraises(self) -> None:
        replace = Path.replace
        def interrupt_promotion(path: Path, target: Path) -> Path:
            if path.parent.name == "ready" and path.name == "2":
                raise KeyboardInterrupt("synthetic promotion interruption")
            return replace(path, target)
        with mock.patch.object(Path, "replace", interrupt_promotion):
            with self.assertRaises(KeyboardInterrupt):
                self.promote()
        self.assertEqual([self.snapshot(output) for output in self.outputs], self.before)
        self.assertEqual(list(self.home.glob(".contracts-generation-*")), [])

    def test_rollback_cleanup_interrupt_retains_targets_golden_and_mapping(self) -> None:
        replace, remove = Path.replace, gc.remove_tree
        def fail_promotion(path: Path, target: Path) -> Path:
            if path.parent.name == "ready" and path.name == "2":
                raise PermissionError("synthetic golden promotion failure")
            return replace(path, target)
        def interrupt_cleanup(path: Path) -> None:
            if path == self.outputs[1]:
                raise KeyboardInterrupt("synthetic installed-target cleanup interruption")
            remove(path)
        caught: BaseException | None = None
        with mock.patch.object(Path, "replace", fail_promotion), mock.patch.object(gc, "remove_tree", interrupt_cleanup):
            try:
                self.promote()
            except (PipelineError, KeyboardInterrupt) as error:
                caught = error
        self.assertIsInstance(caught, gc._RecoveryRequired)
        self.assertIsInstance(caught.__cause__ if caught else None, KeyboardInterrupt)
        stages = list(self.home.glob(".contracts-generation-*"))
        self.assertEqual(len(stages), 1)
        mapping = json.loads((stages[0] / "recovery.json").read_bytes())["previous_outputs"]
        for index, entry in enumerate(mapping):
            self.assertEqual(self.snapshot(Path(entry["backup"])), self.before[index])
        for output in self.outputs[:2]:
            if output.exists():
                remove(output)
        self.assert_recoverable()


if __name__ == "__main__":
    unittest.main()
