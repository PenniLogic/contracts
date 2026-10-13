"""Actual bounded generator examples and transactional preservation, not giant-input tests."""

from __future__ import annotations

from copy import deepcopy
import ctypes
from ctypes import wintypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
from time import perf_counter
import unittest
from pathlib import Path
from unittest import mock

from support import ROOT, SpecDir, replace_once, spec_text
from pl_contracts import PipelineError, PROVIDER_SOURCE_NAMES, java_executable, node_executable, sha256_file, tree_hash
import generate_clients as gc
import toolchain


def cleanup_transaction_fixture(home: Path) -> None:
    if list(home.rglob(".contracts-generation-*")):
        print(f"Transaction recovery fixture retained at {home}", file=sys.stderr)
    else:
        shutil.rmtree(home)


def open_directory_handle(path: Path, share_delete: bool) -> int:
    """Open non-inheritable directory list access; metadata-only access does not constrain sharing."""
    if os.name != "nt":
        return os.open(path, os.O_RDONLY)
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    api.CreateFileW.restype = wintypes.HANDLE
    handle = api.CreateFileW(str(path), 0x1, 7 if share_delete else 3, None, 3, 0x02000000, None)
    if handle is None or handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def close_directory_handle(handle: int) -> None:
    if os.name != "nt":
        os.close(handle)
        return
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    api.CloseHandle.restype = wintypes.BOOL
    if not api.CloseHandle(handle):
        raise ctypes.WinError(ctypes.get_last_error())


def patterned(text: str, name: str, pattern: str, **constraints: object) -> str:
    shape = {"type": "string", "pattern": pattern, **constraints}
    model = ("    " + name + ":\n      type: object\n      x-pennilogic-strict-provider: true\n"
             "      additionalProperties: false\n      properties:\n        value: " + json.dumps(shape) + "\n")
    return replace_once(text, "  schemas:\n", "  schemas:\n" + model)


def fixture_inputs(tools: dict, spec: Path) -> dict:
    files = [spec, spec.parent / "currency-registry.v1.json",
             *(spec.parent / name for name in PROVIDER_SOURCE_NAMES),
             ROOT / "package.json", ROOT / "package-lock.json", ROOT / "toolchain" / "versions.json",
             ROOT / "spec" / "currency-registry.v1.json", Path(java_executable()),
             *(Path(value) for value in tools.values())]
    for directory in (ROOT / "generator", ROOT / "runtime", ROOT / "scripts",
                      ROOT / "spec" / "spectral-functions"):
        files.extend(path for path in directory.rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts)
    return {
        "spec": str(spec.resolve()), "tools": deepcopy(tools),
        "files": {str(path.resolve()): sha256_file(path) for path in sorted(set(files))},
        "options": {language: gc.version_properties(language, gc.spec_version(spec.read_text(encoding="utf-8")))
                    for language in gc.LANGUAGES},
        "environment": {name: os.environ.get(name) for name in
                        ("JAVA_HOME", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "NODE_OPTIONS")},
    }


class PristineTargets:
    """Only staged-output promotion tests reuse these freshly generated, fully bound bytes."""

    def __init__(self, baseline: Path, tools: dict, spec: Path, inputs: dict) -> None:
        if fixture_inputs(tools, spec) != inputs:
            raise PipelineError("pristine fixture inputs changed during generation")
        self.baseline = baseline
        self.inputs = deepcopy(inputs)
        self.maps = {}
        self.manifests = {}
        for language in gc.LANGUAGES:
            target = baseline / language
            manifest_path = target / gc.MANIFEST_NAME
            manifest_bytes = manifest_path.read_bytes()
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            digest, entries = tree_hash(target, exclude=(gc.MANIFEST_NAME,))
            if manifest["target"] != language or manifest["spec_sha256"] != gc.spec_digest(spec) or \
                    manifest["generator"]["jar_sha256"] != sha256_file(Path(tools["openapi_generator"])) or \
                    digest != manifest["tree_sha256"] or dict(entries) != {
                        row["path"]: row["sha256"] for row in manifest["files"]
                    }:
                raise PipelineError("pristine generated fixture is not completely bound")
            if manifest_path.read_bytes() != manifest_bytes:
                raise PipelineError("pristine fixture manifest changed during binding")
            files = {**dict(entries), gc.MANIFEST_NAME: gc.sha256_bytes(manifest_bytes)}
            self.maps[language] = (gc.combined_digest(files), sorted(files.items()))
            self.manifests[language] = deepcopy(manifest)

    def stage(self, language: str, output: Path, tools: dict, spec: Path) -> dict:
        if language not in self.maps:
            raise PipelineError("pristine fixture input/options/tools changed")
        if output.exists():
            raise PipelineError("pristine fixture staging target is not empty")
        if fixture_inputs(tools, spec) != self.inputs:
            raise PipelineError("pristine fixture input/options/tools changed")
        source = self.baseline / language
        if tree_hash(source) != self.maps[language]:
            raise PipelineError("pristine fixture bytes or manifest changed")
        if output.exists():
            raise PipelineError("pristine fixture staging target is not empty")
        shutil.copytree(source, output)
        if tree_hash(output) != self.maps[language]:
            raise PipelineError("pristine fixture copy changed")
        return deepcopy(self.manifests[language])


class PristineSnapshotUnitTest(unittest.TestCase):
    """Exercise snapshot bookkeeping with small files, without generating or compiling clients."""

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="pl-pristine-snapshot-unit-"))
        self.addCleanup(shutil.rmtree, self.home)
        self.spec = self.home / "synthetic.yaml"
        self.spec.write_bytes(b"synthetic source identity\n")
        self.jar = self.home / "synthetic-generator.bin"
        self.jar.write_bytes(b"synthetic tool identity\n")
        self.tools = {"openapi_generator": str(self.jar)}
        self.inputs = {"synthetic": "complete unit input identity"}
        self.baseline = self.home / "baseline"
        for language in gc.LANGUAGES:
            target = self.baseline / language
            (target / "nested").mkdir(parents=True)
            (target / "body.txt").write_bytes(b"synthetic target body\n")
            (target / "nested" / "binary.bin").write_bytes(b"\x00\r\n\xff")
            digest, entries = tree_hash(target)
            manifest = {
                "target": language, "spec_sha256": gc.spec_digest(self.spec),
                "generator": {"jar_sha256": sha256_file(self.jar)},
                "tree_sha256": digest,
                "files": [{"path": name, "sha256": value} for name, value in entries],
            }
            (target / gc.MANIFEST_NAME).write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        self.input_binding = mock.patch(__name__ + ".fixture_inputs", return_value=deepcopy(self.inputs))
        self.input_probe = self.input_binding.start()
        self.addCleanup(self.input_binding.stop)

    def snapshot(self) -> PristineTargets:
        return PristineTargets(self.baseline, self.tools, self.spec, self.inputs)

    def test_each_body_is_read_once_with_an_identical_full_manifest_tree_map(self) -> None:
        expected = {language: tree_hash(self.baseline / language) for language in gc.LANGUAGES}
        with mock.patch("pl_contracts.sha256_file", wraps=sha256_file) as hashes:
            fixture = self.snapshot()
        self.assertEqual(fixture.maps, expected)
        for language in gc.LANGUAGES:
            for relative in ("body.txt", "nested/binary.bin"):
                body = self.baseline / language / relative
                self.assertEqual(sum(call.args[0] == body for call in hashes.call_args_list), 1,
                                 "validated body bytes must not be reread for the full-map fold")

    def test_manifest_changed_during_body_binding_is_refused(self) -> None:
        target = self.baseline / "kotlin"
        manifest = target / gc.MANIFEST_NAME
        before = manifest.read_bytes()
        hash_tree = tree_hash

        def changed_manifest(path: Path, exclude: tuple[str, ...] = ()) -> tuple:
            result = hash_tree(path, exclude=exclude)
            if path == target:
                manifest.write_bytes(before + b"\n")
            return result

        with mock.patch(__name__ + ".tree_hash", side_effect=changed_manifest):
            with self.assertRaisesRegex(PipelineError, "manifest changed during binding"):
                self.snapshot()
        self.assertEqual(manifest.read_bytes(), before + b"\n")

    def test_constructor_still_refuses_body_manifest_and_tool_binding_changes(self) -> None:
        manifest = self.baseline / "typescript" / gc.MANIFEST_NAME
        body = manifest.parent / "body.txt"
        original_manifest, original_body = manifest.read_bytes(), body.read_bytes()
        for mutation in ("body", "body-map", "tool", "target", "spec"):
            with self.subTest(mutation=mutation):
                manifest.write_bytes(original_manifest)
                body.write_bytes(original_body)
                value = json.loads(original_manifest)
                if mutation == "body":
                    body.write_bytes(original_body + b"changed\n")
                elif mutation == "body-map":
                    value["files"][0]["sha256"] = "0" * 64
                elif mutation == "tool":
                    value["generator"]["jar_sha256"] = "0" * 64
                elif mutation == "target":
                    value["target"] = "python"
                else:
                    value["spec_sha256"] = "0" * 64
                if mutation != "body":
                    manifest.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaisesRegex(PipelineError, "not completely bound"):
                    self.snapshot()

    def test_live_manifest_body_members_inputs_and_independent_copies_remain_checked(self) -> None:
        fixture = self.snapshot()
        source = self.baseline / "python"
        manifest = source / gc.MANIFEST_NAME
        body = source / "body.txt"
        original_manifest, original_body = manifest.read_bytes(), body.read_bytes()
        for mutation in ("body", "manifest", "missing", "extra"):
            with self.subTest(mutation=mutation):
                output = self.home / ("refused-" + mutation)
                manifest.write_bytes(original_manifest)
                body.write_bytes(original_body)
                extra = source / "extra.txt"
                if extra.exists():
                    extra.unlink()
                if mutation == "body":
                    body.write_bytes(original_body + b"changed\n")
                elif mutation == "manifest":
                    manifest.write_bytes(original_manifest + b"\n")
                elif mutation == "missing":
                    body.unlink()
                else:
                    extra.write_bytes(b"unbound\n")
                with self.assertRaisesRegex(PipelineError, "bytes or manifest changed"):
                    fixture.stage("python", output, self.tools, self.spec)
                self.assertFalse(output.exists())
        manifest.write_bytes(original_manifest)
        body.write_bytes(original_body)
        (source / "extra.txt").unlink()
        self.input_probe.return_value = {"synthetic": "changed unit input"}
        with self.assertRaisesRegex(PipelineError, "input/options/tools changed"):
            fixture.stage("python", self.home / "refused-input", self.tools, self.spec)
        self.input_probe.return_value = deepcopy(self.inputs)
        first, second = self.home / "first", self.home / "second"
        returned = fixture.stage("python", first, self.tools, self.spec)
        returned["files"][0]["sha256"] = "0" * 64
        self.assertEqual(fixture.stage("python", second, self.tools, self.spec),
                         json.loads(original_manifest))
        self.assertEqual(tree_hash(first), tree_hash(second))
        self.assertEqual(tree_hash(source), fixture.maps["python"])

    def test_occupied_stage_has_priority_over_changed_inputs_without_hashing_or_copying(self) -> None:
        fixture = self.snapshot()
        output = self.home / "occupied"
        output.mkdir()
        (output / "existing.txt").write_bytes(b"previous complete output\n")
        before = tree_hash(output)
        body = self.baseline / "python" / "body.txt"
        body.write_bytes(body.read_bytes() + b"deliberately changed baseline\n")
        self.input_probe.return_value = {"synthetic": "changed unit input"}
        self.input_probe.reset_mock()
        with mock.patch(__name__ + ".tree_hash", side_effect=AssertionError("occupied stage must not scan")) as hashes, \
                mock.patch.object(shutil, "copytree", side_effect=AssertionError("occupied stage must not copy")) as copies:
            with self.assertRaisesRegex(PipelineError, "staging target is not empty"):
                fixture.stage("python", output, {"openapi_generator": str(self.home / "missing-tool")}, self.home / "missing-spec")
        self.input_probe.assert_not_called()
        hashes.assert_not_called()
        copies.assert_not_called()
        self.assertEqual(tree_hash(output), before)
        with mock.patch.object(Path, "exists", side_effect=AssertionError("unknown language must refuse first")), \
                mock.patch(__name__ + ".tree_hash", side_effect=AssertionError("unknown language must not scan")) as hashes, \
                mock.patch.object(shutil, "copytree", side_effect=AssertionError("unknown language must not copy")) as copies:
            with self.assertRaisesRegex(PipelineError, "input/options/tools changed"):
                fixture.stage("unknown", output, self.tools, self.spec)
        self.input_probe.assert_not_called()
        hashes.assert_not_called()
        copies.assert_not_called()

    def test_stage_retains_late_race_check_after_live_inputs_and_baseline_hash(self) -> None:
        fixture = self.snapshot()
        output = self.home / "racing-output"
        events = []
        hash_tree = tree_hash

        def inputs(tools: dict, spec: Path) -> dict:
            events.append("inputs")
            return deepcopy(self.inputs)

        def hashed(path: Path) -> tuple:
            self.assertEqual(path, self.baseline / "python")
            events.append("source")
            bound = hash_tree(path)
            output.mkdir()
            (output / "late.txt").write_bytes(b"owned racing output; retain\n")
            return bound

        self.input_probe.side_effect = inputs
        self.input_probe.reset_mock()
        with mock.patch(__name__ + ".tree_hash", side_effect=hashed) as hashes, \
                mock.patch.object(shutil, "copytree", side_effect=AssertionError("late output must not be replaced")) as copies:
            with self.assertRaisesRegex(PipelineError, "staging target is not empty"):
                fixture.stage("python", output, self.tools, self.spec)
        self.assertEqual(events, ["inputs", "source"])
        self.input_probe.assert_called_once_with(self.tools, self.spec)
        hashes.assert_called_once_with(self.baseline / "python")
        copies.assert_not_called()
        self.assertEqual((output / "late.txt").read_bytes(), b"owned racing output; retain\n")


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
                        env={**os.environ, "JAVA_TOOL_OPTIONS": os.environ.get("JAVA_TOOL_OPTIONS", "") + " -Xmx256m",
                             "PYTHONDONTWRITEBYTECODE": "1"},
                    )
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("unsupported example construction", result.stderr)
                    self.assertNotIn("OutOfMemoryError", result.stderr)
                    self.assertEqual(tree_hash(output), before)
                    self.assertEqual(list(self.spec.path.glob(".contracts-generation-*")), [])

    def test_huge_upper_counts_and_bounded_nested_alternatives_generate_all_three_real_targets(self) -> None:
        baseline = subprocess.run([node_executable(), str(ROOT / "scripts" / "provider_constraints.cjs"),
                                   str(ROOT / "spec" / "openapi.yaml")],
                                  cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        baseline_examples = len(json.loads(baseline.stdout).get("generation_examples", []))
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
            env={**os.environ, "JAVA_TOOL_OPTIONS": os.environ.get("JAVA_TOOL_OPTIONS", "") + " -Xmx256m",
                 "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for language in gc.LANGUAGES:
            manifest = json.loads((output / language / "contracts-manifest.json").read_bytes())
            self.assertTrue(manifest["generation_examples"]["validated_generation_only_annotations"])
            self.assertEqual(manifest["generation_examples"]["count"], baseline_examples + len(patterns) + 1)
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
        inputs = fixture_inputs(cls.tools, cls.good)
        gc.generate_all(list(gc.LANGUAGES), cls.baseline, cls.tools, cls.good)
        cls.pristine = PristineTargets(cls.baseline, cls.tools, cls.good, inputs)

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="pl-generation-case-"))
        self.addCleanup(cleanup_transaction_fixture, self.home)
        self.output = self.home / self.id().rsplit(".", 1)[-1]
        shutil.copytree(self.baseline, self.output)
        self.before = tree_hash(self.output)

    def test_actual_downstream_generator_failure_preserves_existing_tree_and_cleans_stage(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "generate_clients.py"), "--language", "python",
             "--spec", str(self.bad), "--output-dir", str(self.output)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=90,
            env={**os.environ, "JAVA_TOOL_OPTIONS": os.environ.get("JAVA_TOOL_OPTIONS", "") + " -Xmx256m",
                 "PYTHONDONTWRITEBYTECODE": "1"},
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
                                env={**os.environ, "JAVA_TOOL_OPTIONS": os.environ.get("JAVA_TOOL_OPTIONS", "") + " -Xmx256m",
                                     "PYTHONDONTWRITEBYTECODE": "1"})
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
        with mock.patch.object(gc, "_generate_target", side_effect=self.pristine.stage), \
                mock.patch.object(Path, "replace", refuse_one):
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
        controls = ROOT / "build" / "generation-transaction-controls"
        controls.mkdir(parents=True, exist_ok=True)
        evidence = Path(tempfile.mkdtemp(prefix="verify-phases-", dir=controls))
        started = perf_counter()

        def checkpoint(phase: str) -> None:
            with (evidence / (phase + ".json")).open("x", encoding="utf-8", newline="\n") as stream:
                json.dump({"phase": phase, "seconds": perf_counter() - started}, stream, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())

        def fail_second(language: str, output: Path, tools: dict, spec: Path = gc.SPEC) -> dict:
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                checkpoint("second-generate-refusal")
                raise PipelineError("synthetic second-generation failure")
            checkpoint("first-generate-entry")
            manifest = generate(language, output, tools, spec)
            checkpoint("first-generate-return")
            return manifest
        with mock.patch.object(gc, "BUILD", build_home), mock.patch.object(gc, "GOLDEN", golden), \
                mock.patch.object(gc, "generate", side_effect=fail_second):
            with self.assertRaisesRegex(PipelineError, "synthetic second-generation failure"):
                checkpoint("before-verify")
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
        with mock.patch.object(gc, "_generate_target", side_effect=self.pristine.stage), \
                mock.patch.object(Path, "replace", refuse_promotion_and_restore):
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

    def test_pristine_staging_is_exact_independent_and_refuses_changed_inputs_options_or_tools(self) -> None:
        before = tree_hash(self.baseline)
        for language in gc.LANGUAGES:
            first = self.output / "fixture-stages" / ("first-" + language)
            manifest = self.pristine.stage(language, first, self.tools, self.good)
            manifest["files"][0]["sha256"] = "0" * 64
            second = self.output / "fixture-stages" / ("second-" + language)
            result = self.pristine.stage(language, second, self.tools, self.good)
            self.assertEqual(result, json.loads((self.baseline / language / gc.MANIFEST_NAME).read_text()))
            self.assertEqual(tree_hash(first), tree_hash(second))
            with self.assertRaisesRegex(PipelineError, "not empty"):
                self.pristine.stage(language, first, self.tools, self.good)
        for spec, tools in ((self.bad, self.tools),
                            (self.good, {**self.tools, "openapi_generator": self.tools["oasdiff"]})):
            with self.subTest(spec=spec.name, tools=tools):
                destination = self.output / "refuse-fixture"
                with self.assertRaisesRegex(PipelineError, "input/options/tools"):
                    self.pristine.stage("typescript", destination, tools, spec)
                self.assertFalse(destination.exists())
        with mock.patch.dict(os.environ, {"JAVA_TOOL_OPTIONS": os.environ.get("JAVA_TOOL_OPTIONS", "") + " -Dfixture=changed"}):
            with self.assertRaisesRegex(PipelineError, "input/options/tools"):
                self.pristine.stage("typescript", self.output / "refuse-options", self.tools, self.good)
        self.assertEqual(tree_hash(self.baseline), before)

    def test_pristine_fixture_custody_refuses_tampered_body_manifest_missing_or_extra_files(self) -> None:
        before = tree_hash(self.baseline)
        for mutation in ("body", "manifest", "missing", "extra"):
            with self.subTest(mutation=mutation):
                baseline = self.output / ("fixture-" + mutation)
                shutil.copytree(self.baseline, baseline)
                fixture = PristineTargets(baseline, self.tools, self.good, self.pristine.inputs)
                target = baseline / "typescript"
                manifest = target / gc.MANIFEST_NAME
                body = target / "src" / "models" / "Money.ts"
                if mutation == "body":
                    body.write_bytes(body.read_bytes() + b"\n")
                elif mutation == "manifest":
                    manifest.write_bytes(manifest.read_bytes() + b"\n")
                elif mutation == "missing":
                    body.unlink()
                else:
                    (target / "extra-synthetic.txt").write_bytes(b"unexpected\n")
                destination = self.output / ("refused-" + mutation)
                with self.assertRaisesRegex(PipelineError, "bytes or manifest"):
                    fixture.stage("typescript", destination, self.tools, self.good)
                self.assertFalse(destination.exists())
        self.assertEqual(tree_hash(self.baseline), before)

    def test_failure_without_previous_outputs_leaves_no_partial_published_client(self) -> None:
        output = self.output / "new-client-set"
        with self.assertRaisesRegex(PipelineError, "openapi-generator failed"):
            gc.generate_all(list(gc.LANGUAGES), output, self.tools, self.bad)
        self.assertFalse(output.exists())
        self.assertEqual(tree_hash(self.output), self.before)
        self.assertEqual(list(self.output.glob(".contracts-generation-*")), [])


class CatchableRollbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_root = Path(tempfile.mkdtemp(prefix="pl-rollback-interrupt-"))
        self.addCleanup(cleanup_transaction_fixture, self.fixture_root)
        self.prepare("initial")

    def prepare(self, label: str) -> None:
        fixture = self.fixture_root / label
        self.home = fixture
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
                gc._replace(backup, output)
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


class SyntheticGenerationTransactionTest(unittest.TestCase):
    """Publication controls use fake target bytes, never a generator or SDK."""

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="pl-transaction-synthetic-"))
        self.addCleanup(self.cleanup)
        self.events: list[dict] = []
        self.output = self.home / "clients"
        for language in gc.LANGUAGES:
            target = self.output / language
            target.mkdir(parents=True)
            (target / "old.txt").write_bytes(b"previous valid synthetic client\n")
        self.before = tree_hash(self.output)

    def cleanup(self) -> None:
        retained = list(self.home.rglob(".contracts-generation-*"))
        controls = ROOT / "build" / "generation-transaction-controls"
        controls.mkdir(exist_ok=True)
        evidence = Path(tempfile.mkdtemp(prefix="synthetic-", dir=controls))
        (evidence / "trace.json").write_text(json.dumps({
            "test": self.id(), "fixture": str(self.home),
            "source_sha256": sha256_file(ROOT / "scripts" / "generate_clients.py"),
            "events": self.events, "retained_stages": [str(path) for path in retained],
        }, indent=2) + "\n", encoding="utf-8")
        if retained:
            print(f"Synthetic transaction recovery fixture retained at {self.home}", file=sys.stderr)
        else:
            shutil.rmtree(self.home)

    @staticmethod
    def fake_target(language: str, output: Path, tools: dict, spec: Path) -> dict:
        output.mkdir(parents=True)
        (output / "new.txt").write_bytes(b"new synthetic client\n")
        digest, entries = tree_hash(output)
        manifest = {
            "target": language, "tree_sha256": digest,
            "files": [{"path": name, "sha256": value} for name, value in entries],
        }
        (output / gc.MANIFEST_NAME).write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        return manifest

    def publish(self) -> dict:
        remove = gc.remove_tree

        def observed_remove(path: Path) -> None:
            self.events.append({"operation": "remove", "path": str(path),
                                "exists": path.exists(), "directory": path.is_dir()})
            remove(path)
            self.events.append({"operation": "removed", "path": str(path),
                                "exists": path.exists(), "directory": path.is_dir()})

        with mock.patch.object(gc, "remove_tree", observed_remove), gc._staging(self.output.parent) as stage:
            manifests = {}
            replacements = []
            for language in gc.LANGUAGES:
                ready = stage / "ready" / language
                manifests[language] = self.fake_target(language, ready, {}, self.home / "synthetic.yaml")
                replacements.append((ready, self.output / language))
            try:
                gc._publish(stage, replacements)
            except (PipelineError, OSError, KeyboardInterrupt) as error:
                self.events.append({
                    "operation": "publication failed", "type": type(error).__name__,
                    "error": str(error), "cause": repr(error.__cause__),
                })
                raise
        return manifests

    def test_fake_promotion_failure_restores_the_complete_previous_set(self) -> None:
        replace = Path.replace
        fired = False

        def fail_promotion(path: Path, target: Path) -> Path:
            nonlocal fired
            if path.parent.name == "ready" and path.name == "python":
                fired = True
                raise PermissionError("synthetic promotion failure")
            self.events.append({"operation": "replace", "path": str(path), "target": str(target),
                                "target_exists": target.exists(), "target_directory": target.is_dir()})
            return replace(path, target)

        with mock.patch.object(Path, "replace", fail_promotion):
            with self.assertRaisesRegex(PipelineError, "previous outputs restored"):
                self.publish()
        self.assertTrue(fired)
        self.assertEqual(tree_hash(self.output), self.before)
        self.assertEqual(list(self.home.glob(".contracts-generation-*")), [])

    def test_fake_success_publishes_complete_hash_bound_targets_without_a_stage(self) -> None:
        manifests = self.publish()
        self.assertEqual(set(manifests), set(gc.LANGUAGES))
        for language, manifest in manifests.items():
            target = self.output / language
            self.assertEqual(json.loads((target / gc.MANIFEST_NAME).read_bytes()), manifest)
            self.assertEqual(tree_hash(target, exclude=(gc.MANIFEST_NAME,))[0], manifest["tree_sha256"])
        self.assertEqual(list(self.home.glob(".contracts-generation-*")), [])

    def test_deep_directory_promotion_failure_restores_without_replacing_a_live_directory(self) -> None:
        parent = self.home
        while len(str(parent)) < 270:
            parent /= "bounded-synthetic-path"
        shutil.copytree(self.output, parent / "clients")
        self.output = parent / "clients"
        self.before = tree_hash(self.output)
        replace = Path.replace
        fired = False

        def fail_promotion(path: Path, target: Path) -> Path:
            nonlocal fired
            if path.parent.name == "ready" and path.name == "python":
                fired = True
                raise PermissionError("synthetic deep-path promotion failure")
            self.events.append({"operation": "replace", "path": str(path), "target": str(target),
                                "target_exists": target.exists(), "target_directory": target.is_dir()})
            return replace(path, target)

        with mock.patch.object(Path, "replace", fail_promotion):
            with self.assertRaisesRegex(PipelineError, "previous outputs restored"):
                self.publish()
        self.assertTrue(fired)
        self.assertEqual(tree_hash(self.output), self.before)
        self.assertEqual(list(parent.glob(".contracts-generation-*")), [])

    def test_shared_delete_directory_handle_does_not_weaken_transaction_rollback(self) -> None:
        replace = Path.replace
        handle = None
        held_denials = []
        post_close_denials = []
        closed_handles = []
        promotion_failed = False

        def close_handle() -> None:
            nonlocal handle
            if handle is not None:
                close_directory_handle(handle)
                closed_handles.append(handle)
                self.events.append({"operation": "owned shared-delete directory handle closed",
                                    "handle": handle, "checked_close": True})
                handle = None

        def held_directory(path: Path, target: Path) -> Path:
            nonlocal handle, promotion_failed
            if path.parent.name == "ready" and path.name == "python":
                promotion_failed = True
                raise PermissionError("synthetic promotion failure with an owned directory handle")
            try:
                result = replace(path, target)
            except OSError as error:
                if path.parent.name != "backups" or target.name != "typescript":
                    raise
                live = handle is not None
                (held_denials if live else post_close_denials).append(getattr(error, "winerror", None))
                self.events.append({"operation": "native delete-pending restore denied",
                                    "winerror": getattr(error, "winerror", None),
                                    "while_handle_live": live,
                                    "target_exists": target.exists(), "target": str(target)})
                if live:
                    close_handle()
                raise
            if path.parent.name == "ready" and path.name == "typescript":
                handle = open_directory_handle(target, share_delete=True)
                self.events.append({"operation": "owned shared-delete directory list handle opened",
                                    "handle": handle, "access": 1, "share": 7, "inheritable": False})
            return result

        try:
            with mock.patch.object(Path, "replace", held_directory):
                with self.assertRaisesRegex(PipelineError, "previous outputs restored"):
                    self.publish()
        finally:
            close_handle()
        self.assertTrue(promotion_failed)
        self.assertEqual(len(closed_handles), 1)
        self.assertTrue(all(code in (5, 32) for code in held_denials + post_close_denials))
        self.assertEqual(tree_hash(self.output), self.before)
        self.assertEqual(list(self.home.glob(".contracts-generation-*")), [])

    def test_native_directory_rename_denial_retries_after_only_its_owned_handle_is_closed(self) -> None:
        source, destination = self.output / "kotlin", self.home / "renamed-directory"
        handle = open_directory_handle(source, share_delete=False)
        replace = Path.replace
        held_denials = []
        post_close_denials = []
        closed_handles = []
        attempts = []
        successes = []
        self.events.append({"operation": "owned no-share-delete directory list handle opened",
                            "handle": handle, "access": 1, "share": 3, "inheritable": False})

        def close_handle() -> None:
            nonlocal handle
            if handle is not None:
                close_directory_handle(handle)
                closed_handles.append(handle)
                self.events.append({"operation": "owned no-share-delete directory handle closed",
                                    "handle": handle, "checked_close": True})
                handle = None

        def native_replace(path: Path, target: Path) -> Path:
            attempts.append((path, target))
            try:
                result = replace(path, target)
            except OSError as error:
                live = handle is not None
                (held_denials if live else post_close_denials).append(getattr(error, "winerror", None))
                self.events.append({"operation": "native non-share-delete directory rename denied",
                                    "winerror": getattr(error, "winerror", None),
                                    "while_handle_live": live,
                                    "source": str(path), "target": str(target)})
                if live:
                    close_handle()
                raise
            successes.append(handle is None)
            self.events.append({"operation": "native directory rename succeeded",
                                "after_handle_close": handle is None,
                                "source": str(path), "target": str(target)})
            return result

        try:
            with mock.patch.object(Path, "replace", native_replace):
                gc._replace(source, destination)
        finally:
            close_handle()
        if os.name == "nt":
            self.assertTrue(held_denials, "directory list access must produce a real held-handle denial")
            self.assertTrue(all(code in (5, 32) for code in held_denials + post_close_denials))
            self.assertEqual(successes, [True])
        else:
            self.assertEqual(held_denials + post_close_denials, [])
        self.assertEqual(len(closed_handles), 1)
        self.assertTrue(all(pair == (source, destination) for pair in attempts))
        self.assertEqual(len(attempts), len(held_denials) + len(post_close_denials) + len(successes))
        gc._replace(destination, source)
        self.assertEqual(tree_hash(self.output), self.before)
        self.events.append({"operation": "original synthetic directory restored", "bytes_match": True})

    def test_persistent_native_restore_denial_retains_mapping_and_every_previous_target(self) -> None:
        replace = Path.replace
        attempts = 0

        def denied_restore(path: Path, target: Path) -> Path:
            nonlocal attempts
            if path.parent.name == "ready" and path.name == "python":
                raise PermissionError("synthetic promotion failure")
            if path.parent.name == "backups":
                attempts += 1
                error = PermissionError("synthetic persistent native restoration denial")
                error.winerror = 5
                raise error
            return replace(path, target)

        with mock.patch.object(Path, "replace", denied_restore):
            with self.assertRaisesRegex(gc._RecoveryRequired, "recovery artifacts retained") as caught:
                self.publish()
        self.assertIsInstance(caught.exception.__cause__, PermissionError)
        self.assertGreater(attempts, 1 if os.name == "nt" else 0)
        stages = list(self.home.glob(".contracts-generation-*"))
        self.assertEqual(len(stages), 1)
        mapping = json.loads((stages[0] / "recovery.json").read_bytes())["previous_outputs"]
        self.assertEqual(len(mapping), len(gc.LANGUAGES))
        for entry in mapping:
            backup, output = Path(entry["backup"]), Path(entry["output"])
            self.assertEqual(output.parent, self.output.resolve())
            self.assertEqual((backup / "old.txt").read_bytes(), b"previous valid synthetic client\n")
            gc._replace(backup, output)
        self.assertEqual(tree_hash(self.output), self.before)
        gc.remove_tree(stages[0])

    def test_non_native_errors_and_interruption_are_not_retried(self) -> None:
        for error in (PermissionError("synthetic non-native denial"), KeyboardInterrupt("synthetic cancellation")):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(Path, "replace", side_effect=error) as replace, \
                        mock.patch.object(gc, "sleep") as pause:
                    with self.assertRaises(type(error)) as caught:
                        gc._replace(self.home / "unused", self.home / "unpublished")
                self.assertIs(caught.exception, error)
                replace.assert_called_once()
                pause.assert_not_called()

    def test_native_replacement_deadline_never_starts_a_late_retry(self) -> None:
        error = PermissionError("synthetic native replacement deadline")
        error.winerror = 5
        with mock.patch.object(Path, "replace", side_effect=error) as replace, \
                mock.patch.object(gc, "perf_counter", side_effect=[0.0, 0.05, 0.26]), \
                mock.patch.object(gc, "sleep") as pause:
            with self.assertRaises(PermissionError) as caught:
                gc._replace(self.home / "unused", self.home / "unpublished")
        self.assertIs(caught.exception, error)
        replace.assert_called_once()
        if os.name == "nt":
            pause.assert_called_once_with(0.01)
            self.assertIn("bounded retries", caught.exception.__notes__[0])
        else:
            pause.assert_not_called()

    def test_real_transaction_fixture_staging_is_disjoint_without_regenerating_shared_inputs(self) -> None:
        baseline = tree_hash(self.output)
        first, second = GenerationTransactionTest(), GenerationTransactionTest()
        with mock.patch.object(GenerationTransactionTest, "baseline", self.output, create=True):
            first.setUp()
            second.setUp()
        try:
            self.assertNotEqual(first.home, second.home)
            self.assertEqual(tree_hash(first.output), baseline)
            self.assertEqual(tree_hash(second.output), baseline)
            retained = first.home / ".contracts-generation-owned-control"
            retained.mkdir()
            marker = retained / "recovery.json"
            marker.write_bytes(b'{"retained":"synthetic recovery"}\n')
            self.assertEqual(list(second.home.glob(".contracts-generation-*")), [])
            self.assertTrue(second.doCleanups())
            self.assertEqual(marker.read_bytes(), b'{"retained":"synthetic recovery"}\n')
            self.assertEqual(tree_hash(self.output), baseline)
            gc.remove_tree(retained)
        finally:
            self.assertTrue(first.doCleanups())
            self.assertTrue(second.doCleanups())


if __name__ == "__main__":
    unittest.main()
