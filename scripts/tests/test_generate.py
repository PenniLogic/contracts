"""Client generation: determinism, manifest content, registry rendering, seam wiring and template drift."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from support import ROOT, REGISTRY, SpecDir, replace_once, run_script, spec_text

import generate_clients as gc
import pl_contracts
import toolchain

MONEY_BEARING_MODEL = """    SyntheticEnvelope:
      type: object
      required: [total, recorded_at]
      properties:
        total:
          $ref: '#/components/schemas/Money'
        recorded_at:
          $ref: '#/components/schemas/Instant'
        booked_on:
          $ref: '#/components/schemas/LocalDate'
        note:
          type: string
"""


class TreeHashTest(unittest.TestCase):
    def test_tree_hash_depends_on_content_and_paths_only(self) -> None:
        first = Path(tempfile.mkdtemp())
        second = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, first, True)
        self.addCleanup(shutil.rmtree, second, True)
        for root in (first, second):
            (root / "a").mkdir()
            (root / "a" / "x.txt").write_bytes(b"one\n")
            (root / "b.txt").write_bytes(b"two\n")
        self.assertEqual(pl_contracts.tree_hash(first)[0], pl_contracts.tree_hash(second)[0])
        (second / "b.txt").write_bytes(b"two!\n")
        self.assertNotEqual(pl_contracts.tree_hash(first)[0], pl_contracts.tree_hash(second)[0])
        digest, entries = pl_contracts.tree_hash(first, exclude=("b.txt",))
        self.assertEqual([path for path, _ in entries], ["a/x.txt"])
        self.assertEqual(len(digest), 64)

    def test_spec_version_regex(self) -> None:
        self.assertEqual(pl_contracts.spec_version(spec_text()), "0.1.0")
        self.assertEqual(pl_contracts.spec_version("openapi: 3.1.0\ninfo:\n  title: x\n  version: '2.10.3'\npaths: {}\n"), "2.10.3")
        with self.assertRaises(pl_contracts.PipelineError):
            pl_contracts.spec_version("openapi: 3.1.0\ninfo:\n  title: x\n  version: 1.0\n")


class RegistryRenderingTest(unittest.TestCase):
    def test_rendered_registries_carry_every_entry_of_the_json_file(self) -> None:
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))["currencies"]
        for language in gc.LANGUAGES:
            path, content = gc.render_registry(language)
            self.assertIn("do not edit", content)
            for entry in registry:
                self.assertIn(f'"{entry["code"]}"', content, language)
                self.assertIn(str(entry["exponent"]), content, language)
                self.assertIn(entry["minor_unit_name"], content, language)
            self.assertTrue(path.endswith((".py", ".ts", ".kt")))


class GoldenAndManifestTest(unittest.TestCase):
    def test_golden_file_matches_the_generator_configuration(self) -> None:
        golden = json.loads((ROOT / "generator" / "golden.json").read_text(encoding="utf-8"))
        self.assertEqual(set(golden), set(gc.LANGUAGES))
        for language, record in golden.items():
            self.assertEqual(record["spec_version"], pl_contracts.spec_version(), language)
            self.assertEqual(record["spec_sha256"], gc.spec_digest(gc.SPEC), f"{language}: golden was recorded for another specification; run --update-golden")
            self.assertEqual(len(record["tree_sha256"]), 64)
            self.assertEqual(record["file_count"], len(record["files"]))

    def test_manifest_of_a_generated_output_records_versions(self) -> None:
        manifest_path = ROOT / "build" / "generated" / "python" / "contracts-manifest.json"
        if not manifest_path.is_file():
            self.skipTest("build/generated/python is absent; run python scripts/generate_clients.py first")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        pins = pl_contracts.versions()
        self.assertEqual(manifest["spec_version"], pl_contracts.spec_version())
        self.assertEqual(manifest["generator"]["version"], pins["openapi_generator"]["version"])
        self.assertEqual(manifest["generator"]["jar_sha256"], pins["openapi_generator"]["sha256"])
        self.assertEqual(manifest["spec_sha256"], gc.spec_digest(gc.SPEC))
        self.assertEqual({f["path"] for f in manifest["files"]} & {"contracts-manifest.json"}, set())
        self.assertIn("pennilogic_contracts/models/money.py", {f["path"] for f in manifest["files"]})


class SeamWiringTest(unittest.TestCase):
    """A Money-bearing model generated from a scratch specification uses the hand-written seams."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = SpecDir()
        text = replace_once(spec_text(), "  parameters:\n    IdempotencyKey:", MONEY_BEARING_MODEL + "  parameters:\n    IdempotencyKey:")
        cls.spec_path = cls.spec.write(text)
        cls.output = Path(tempfile.mkdtemp(prefix="pl-gen-"))
        cls.tools = toolchain.ensure_installed()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.spec.cleanup()
        shutil.rmtree(cls.output, ignore_errors=True)

    def test_kotlin_model_uses_the_money_wrapper_and_contextual_dates(self) -> None:
        gc.generate("kotlin", self.output / "kotlin", self.tools, self.spec_path)
        model = (self.output / "kotlin" / "src/main/kotlin/com/pennilogic/contracts/models/SyntheticEnvelope.kt").read_text(encoding="utf-8")
        self.assertIn("val total: com.pennilogic.contracts.money.Money", model)
        self.assertIn("@Contextual", model)
        self.assertIn("java.time.OffsetDateTime", model)
        self.assertIn("java.time.LocalDate", model)
        self.assertFalse((self.output / "kotlin" / "src/main/kotlin/com/pennilogic/contracts/models/Money.kt").exists(), "the raw wire shape must not be generated as a model")
        self.assertTrue((self.output / "kotlin" / "src/main/kotlin/com/pennilogic/contracts/money/Money.kt").is_file())

    def test_typescript_model_calls_the_seam_functions(self) -> None:
        gc.generate("typescript", self.output / "typescript", self.tools, self.spec_path)
        model = (self.output / "typescript" / "src/models/SyntheticEnvelope.ts").read_text(encoding="utf-8")
        self.assertIn("MoneyFromJSON(json['total'])", model)
        self.assertIn("MoneyToJSON(value['total'])", model)
        self.assertIn("InstantFromJSON(json['recorded_at'])", model)
        self.assertIn("total: Money;", model)
        self.assertIn("recordedAt: Instant;", model)
        index = (self.output / "typescript" / "src/models/index.ts").read_text(encoding="utf-8")
        self.assertIn("./Money.js", index)
        self.assertTrue((self.output / "typescript" / "src/models/Money.ts").is_file())
        self.assertTrue((self.output / "typescript" / "src/models/Instant.ts").is_file())
        for forbidden in ("git_push.sh", ".travis.yml", ".gitlab-ci.yml"):
            self.assertFalse((self.output / "typescript" / forbidden).exists(), forbidden)

    def test_python_model_uses_the_wrappers_and_imports_the_instant_seam(self) -> None:
        gc.generate("python", self.output / "python", self.tools, self.spec_path)
        model = (self.output / "python" / "pennilogic_contracts/models/synthetic_envelope.py").read_text(encoding="utf-8")
        self.assertIn("from pennilogic_contracts.models.money import Money", model)
        self.assertIn("from pennilogic_contracts.models.instant import Instant", model)
        self.assertIn("total: Money", model)
        self.assertIn("recorded_at: Instant", model)
        self.assertIn("Money.from_dict(obj[\"total\"])", model)
        self.assertIn("Instant.from_dict(obj[\"recorded_at\"])", model)
        self.assertIn("def recorded_at_validate_regular_expression(cls, value: Any) -> Any", model)
        for forbidden in ("git_push.sh", ".travis.yml", ".gitlab-ci.yml", "tox.ini", ".github/workflows/python.yml"):
            self.assertFalse((self.output / "python" / forbidden).exists(), forbidden)
        self.assertFalse((self.output / "python" / "test").exists(), "generated test stubs are replaced by the smoke consumer")


class TemplateOverrideDriftTest(unittest.TestCase):
    """The committed Python template equals the pinned generator's stock template plus the documented edits."""

    def test_override_is_stock_plus_known_edits(self) -> None:
        pins = pl_contracts.versions()
        jar = toolchain.generator_jar_path(pins)
        if not jar.is_file():
            self.skipTest("generator jar not installed; run python scripts/toolchain.py install")
        with zipfile.ZipFile(jar) as archive:
            stock = archive.read("python/model_generic.mustache").decode("utf-8")
        override = (ROOT / "generator" / "templates" / "python" / "model_generic.mustache").read_text(encoding="utf-8")
        marker = "{{#vendorExtensions.x-py-model-imports}}\n{{{.}}}\n{{/vendorExtensions.x-py-model-imports}}\n"
        expected = stock.replace(
            marker,
            marker + "# PenniLogic: the ADR-015 Instant seam is type-mapped for date-time and is not a generated model, so its import is added by generator/templates/python (see docs/development.md#generator-templates).\n"
            "from pennilogic_contracts.models.instant import Instant  # noqa: F401\n",
        ).replace(
            "    def {{{name}}}_validate_regular_expression(cls, value):\n",
            "    def {{{name}}}_validate_regular_expression(cls, value: Any) -> Any:  # PenniLogic: annotated for mypy --strict\n",
        ).replace(
            "    def {{{name}}}_validate_enum(cls, value):\n",
            "    def {{{name}}}_validate_enum(cls, value: Any) -> Any:  # PenniLogic: annotated for mypy --strict\n",
        )
        self.assertEqual(override, expected, "generator/templates/python/model_generic.mustache drifted from the pinned generator's template; re-apply the documented edits on the new stock template")


class DeterminismTest(unittest.TestCase):
    def test_verify_passes_on_the_committed_golden(self) -> None:
        completed = run_script("generate_clients.py", "--verify")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("byte-identical double generation", completed.stdout)


if __name__ == "__main__":
    unittest.main()
