"""Client generation: determinism, manifest content, registry rendering, seam wiring and template drift."""

from __future__ import annotations

import io
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

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
        self.assertEqual(pl_contracts.spec_version("openapi: 3.1.0\ninfo:\n  title: x\n  version: 0.2.0\npaths: {}\n"), "0.2.0")
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
            # The per-file map is not diagnostic only: its fold must reproduce the recorded tree digest.
            self.assertEqual(pl_contracts.combined_digest(record["files"]), record["tree_sha256"], language)
            self.assertIsNone(gc.golden_inconsistency(record), language)

    def test_a_tampered_per_file_hash_is_refused(self) -> None:
        golden = json.loads((ROOT / "generator" / "golden.json").read_text(encoding="utf-8"))
        record = json.loads(json.dumps(golden["python"]))
        first = sorted(record["files"])[0]
        record["files"][first] = "0" * 64
        problem = gc.golden_inconsistency(record)
        self.assertIsNotNone(problem)
        self.assertIn("edited inconsistently", problem or "")
        # verify() refuses the record before comparing it with a generation.
        with mock.patch.object(gc, "load_golden", return_value={"python": record}), \
                mock.patch.object(gc, "generate", return_value={"tree_sha256": golden["python"]["tree_sha256"], "file_count": 1, "files": [], "spec_version": "0.1.0", "spec_sha256": "x"}), \
                mock.patch("sys.stdout", new_callable=io.StringIO), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(gc.verify(["python"], {"openapi_generator": "", "oasdiff": ""}, update_golden=False), 1)
        self.assertIn("edited inconsistently", stderr.getvalue())
        dropped = json.loads(json.dumps(golden["python"]))
        del dropped["files"][first]
        self.assertIn("file_count", gc.golden_inconsistency(dropped) or "")

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
        text = replace_once(spec_text(), "  schemas:\n", "  schemas:\n" + MONEY_BEARING_MODEL)
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
        # The generated client registers the seams itself (ADR-015 s2): converter + dependency come from the template override.
        client = (self.output / "kotlin" / "src/main/kotlin/com/pennilogic/contracts/infrastructure/ApiClient.kt").read_text(encoding="utf-8")
        self.assertIn("json(PennilogicJson.json)", client)
        self.assertIn("import com.pennilogic.contracts.serialization.PennilogicJson", client)
        self.assertNotIn("install(ContentNegotiation) {\n            }", client)
        self.assertIn('implementation "io.ktor:ktor-serialization-kotlinx-json:$ktor_version"', (self.output / "kotlin" / "build.gradle").read_text(encoding="utf-8"))
        self.assertTrue((self.output / "kotlin" / "src/main/kotlin/com/pennilogic/contracts/serialization/PennilogicJson.kt").is_file())

    def test_typescript_model_calls_the_seam_functions(self) -> None:
        gc.generate("typescript", self.output / "typescript", self.tools, self.spec_path)
        model = (self.output / "typescript" / "src/models/SyntheticEnvelope.ts").read_text(encoding="utf-8")
        self.assertIn("MoneyFromJSON(json['total'])", model)
        self.assertIn("MoneyToJSON(value['total'])", model)
        self.assertIn("InstantFromJSON(json['recorded_at'])", model)
        self.assertIn("LocalDateFromJSON(json['booked_on'])", model)
        self.assertIn("total: Money;", model)
        self.assertIn("recordedAt: Instant;", model)
        self.assertIn("bookedOn?: LocalDate;", model)
        self.assertTrue((self.output / "typescript" / "src/models/LocalDate.ts").is_file())
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
        self.assertIn("booked_on: Optional[LocalDate]", model)
        self.assertIn("LocalDate.from_dict(obj[\"booked_on\"])", model)
        self.assertIn("def recorded_at_validate_regular_expression(cls, value: Any) -> Any", model)
        self.assertIn("strict=True,", model)
        self.assertIn("hide_input_in_errors=True,", model)
        for forbidden in ("git_push.sh", ".travis.yml", ".gitlab-ci.yml", "tox.ini", ".github/workflows/python.yml"):
            self.assertFalse((self.output / "python" / forbidden).exists(), forbidden)
        self.assertFalse((self.output / "python" / "test").exists(), "generated test stubs are replaced by the smoke consumer")

    def test_generated_python_money_model_never_echoes_the_offending_value(self) -> None:
        """Runtime proof on a GENERATED Money-bearing model, executed in the smoke venv (created by scripts/smoke.py python)."""
        interpreter = ROOT / "build" / "venv" / ("Scripts/python.exe" if sys.platform.startswith("win") else "bin/python")
        if not interpreter.is_file():
            self.skipTest("smoke venv missing; run python scripts/smoke.py python first (CI runs it before this suite)")
        gc.generate("python", self.output / "python", self.tools, self.spec_path)
        probe = (
            "import json, sys\n"
            "from pydantic import ValidationError\n"
            "from pennilogic_contracts.models.synthetic_envelope import SyntheticEnvelope\n"
            "from pennilogic_contracts.models.money import Money\n"
            "assert SyntheticEnvelope.model_config['strict'] is True and SyntheticEnvelope.model_config['hide_input_in_errors'] is True\n"
            "ok = SyntheticEnvelope.from_dict({'total': {'amount': '-1234.56', 'currency': 'INR'}, 'recorded_at': '2026-09-30T04:52:08.439Z', 'booked_on': '2026-09-30'})\n"
            "assert isinstance(ok.total, Money) and ok.total.minor_units == -123456 and ok.booked_on.to_wire() == '2026-09-30'\n"
            "assert json.loads(ok.to_json()) == {'total': {'amount': '-1234.56', 'currency': 'INR'}, 'recorded_at': '2026-09-30T04:52:08.439Z', 'booked_on': '2026-09-30'}\n"
            "for bad in ({'amount': '12.5', 'currency': 'INR'}, {'amount': 12.5, 'currency': 'INR'}):\n"
            "    try:\n"
            "        SyntheticEnvelope.model_validate({'total': bad, 'recorded_at': '2026-09-30T04:52:08.439Z'})\n"
            "    except ValidationError as error:\n"
            "        assert '12.5' not in str(error), str(error)\n"
            "        assert '12.5' not in error.json(include_input=False)\n"
            "    else:\n"
            "        raise SystemExit('accepted an invalid amount')\n"
            "try:\n"
            "    SyntheticEnvelope.model_validate({'total': {'amount': '0.00', 'currency': 'INR'}, 'recorded_at': '2026-09-30T04:52:08.439Z', 'note': 5})\n"
            "except ValidationError as error:\n"
            "    assert '5' not in str(error).split('[')[-1]\n"
            "else:\n"
            "    raise SystemExit('strict model coerced a non-string note')\n"
            "print('generated model: strict, no echo')\n"
        )
        completed = subprocess.run([str(interpreter), "-c", probe], capture_output=True, text=True, encoding="utf-8",
                                   env={**os.environ, "PYTHONPATH": str(self.output / "python"), "PYTHONDONTWRITEBYTECODE": "1"}, check=False)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("generated model: strict, no echo", completed.stdout)


class TemplateOverrideDriftTest(unittest.TestCase):
    """Every committed template override equals the pinned generator's stock template plus the documented edits."""

    def stock(self, name: str) -> str:
        pins = pl_contracts.versions()
        jar = toolchain.generator_jar_path(pins)
        if not jar.is_file():
            self.skipTest("generator jar not installed; run python scripts/toolchain.py install")
        with zipfile.ZipFile(jar) as archive:
            return archive.read(name).decode("utf-8")

    def test_every_template_uses_exact_lf_bytes_before_native_generation(self) -> None:
        for path in (ROOT / "generator" / "templates").rglob("*.mustache"):
            with self.subTest(template=path.relative_to(ROOT)):
                self.assertNotIn(b"\r", path.read_bytes(),
                                 "native templates must match committed LF bytes before golden generation")

    def replace_once(self, text: str, old: str, new: str) -> str:
        self.assertEqual(text.count(old), 1, f"stock template anchor changed: {old[:60]!r}")
        return text.replace(old, new)

    def core_adapter(self, text: str, name: str) -> str:
        record = json.loads((ROOT / "scripts" / "tests" / "template-adapter-edits.v1.json").read_text(encoding="utf-8"))[name]
        self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), record["base_sha256"])
        lines = text.splitlines(keepends=True)
        for edit in reversed(record["edits"]):
            self.assertEqual("".join(lines[edit["start"]:edit["end"]]), edit["old"])
            lines[edit["start"]:edit["end"]] = edit["new"].splitlines(keepends=True)
        return "".join(lines)

    def nullable_enum_items(self, text: str) -> str:
        start = text.index("{{^items.isEnum}}")
        suffix = "{{#items.isNullable}}?{{/items.isNullable}}"
        end = text.index(suffix, start) + len(suffix)
        items = text[start:end]
        return self.replace_once(
            text, items,
            "{{#vendorExtensions.x-pennilogic-nullable-enum-items}}{{#items}}{{>provider_type}}{{/items}}"
            "{{/vendorExtensions.x-pennilogic-nullable-enum-items}}"
            "{{^vendorExtensions.x-pennilogic-nullable-enum-items}}" + items +
            "{{/vendorExtensions.x-pennilogic-nullable-enum-items}}",
        )

    def test_python_api_override_adds_precise_types_typed_responses_and_private_diagnostics(self) -> None:
        expected = self.stock("python/api.mustache")
        response_type = "ApiResponse[{{{returnType}}}{{^returnType}}None{{/returnType}}]"
        edits = [
            ("    @validate_call\n", '    @validate_call(config={"hide_input_in_errors": True})\n', 10),
            ("def __init__(self, api_client=None) -> None:",
             "def __init__(self, api_client: Optional[ApiClient] = None) -> None:", 1),
            ("return self.api_client.response_deserialize(",
             f"return {response_type}.model_validate(self.api_client.response_deserialize(", 4),
            ("            response_types_map=_response_types_map,\n        ).data",
             "            response_types_map=_response_types_map,\n        ), from_attributes=True).data", 2),
            ("            response_types_map=_response_types_map,\n        )\n",
             "            response_types_map=_response_types_map,\n        ), from_attributes=True)\n", 2),
            ("        {{paramName}},\n", "        {{paramName}}: {{{vendorExtensions.x-py-typing}}},\n", 1),
            ("        _request_auth,\n", "        _request_auth: Optional[Dict[str, object]],\n", 1),
            ("        _content_type,\n", "        _content_type: Optional[str],\n", 1),
            ("        _headers,\n", "        _headers: Optional[Dict[str, object]],\n", 1),
            ("        _host_index,\n", "        _host_index: int,\n", 1),
            ("_path_params: Dict[str, str]", "_path_params: Dict[str, object]", 1),
            ("_query_params: List[Tuple[str, str]]", "_query_params: List[Tuple[str, object]]", 1),
            ("_header_params: Dict[str, Optional[str]]", "_header_params: Dict[str, object]", 1),
            ("_form_params: List[Tuple[str, str]]", "_form_params: List[Tuple[str, object]]", 1),
            ("_body_params: Optional[bytes]", "_body_params: object", 1),
        ]
        for old, new, count in edits:
            self.assertEqual(expected.count(old), count, old)
            expected = expected.replace(old, new)
        actual = (ROOT / "generator" / "templates" / "python" / "api.mustache").read_text(encoding="utf-8")
        self.assertEqual(actual, self.core_adapter(expected, "python/api.mustache"))

    def test_python_api_client_override_types_the_existing_transport_not_string_named_models(self) -> None:
        expected = self.stock("python/api_client.mustache")
        edits = [
            ("from {{packageName}}.api_response import ApiResponse, T as ApiResponseT",
             "from {{packageName}}.api_response import ApiResponse"),
            ("RequestSerialized = Tuple[str, str, Dict[str, str], Optional[str], List[str]]",
             "RequestSerialized = Tuple[str, str, Dict[str, str], object, List[Tuple[str, object]]]"),
            ("def get_default(cls):", 'def get_default(cls) -> "ApiClient":'),
            ("response_types_map: Optional[Dict[str, ApiResponseT]]=None\n    ) -> ApiResponse[ApiResponseT]:",
             "response_types_map: Optional[Dict[str, Optional[str]]]=None\n    ) -> ApiResponse[object]:"),
            ("        assert response_data.data is not None, msg\n",
             "        assert response_data.data is not None, msg\n"
             '        if response_types_map is None:\n            raise ValueError("response type binding required")\n'),
            ("def select_header_content_type(self, content_types):",
             "def select_header_content_type(self, content_types: List[str]) -> Optional[str]:"),
            ("def sanitize_for_serialization(self, obj):",
             "def sanitize_for_serialization(self, obj: object) -> object:"),
            ("        if data is None:\n            return None\n",
             "        if data is None:\n"
             "            from {{packageName}}.provider_model import ProviderModel, ProviderWireError\n"
             "            candidate: object = getattr({{modelPackage}}, klass, None) if isinstance(klass, str) else klass\n"
             "            if isinstance(candidate, type) and issubclass(candidate, ProviderModel):\n"
             "                raise ProviderWireError()\n"
             "            return None\n"),
        ]
        for old, new in edits:
            expected = self.replace_once(expected, old, new)
        start = expected.index('        try:\n            if response_type in ("bytearray", "bytes"):')
        end = expected.index("\n        return ApiResponse(", start)
        block = expected[start:end]
        self.assertEqual(block.count("        finally:\n"), 1)
        lines = block.replace("        try:\n", "", 1).replace("        finally:\n", "", 1).splitlines(keepends=True)
        expected = expected[:start] + "".join(line[4:] if line.startswith("            ") else line for line in lines) + expected[end:]
        actual = (ROOT / "generator" / "templates" / "python" / "api_client.mustache").read_text(encoding="utf-8")
        self.assertEqual(actual, expected)

    def test_python_rest_override_types_the_actual_urllib3_base_response_and_bytes(self) -> None:
        expected = self.stock("python/rest.mustache")
        for old, new in [
            ("import ssl\n", "import ssl\nfrom typing import Optional\n"),
            ("RESTResponseType = urllib3.HTTPResponse", "RESTResponseType = urllib3.response.BaseHTTPResponse"),
            ("def __init__(self, resp) -> None:", "def __init__(self, resp: RESTResponseType) -> None:"),
            ("        self.data = None\n", "        self.data: Optional[bytes] = None\n"),
            ("    def read(self):", "    def read(self) -> bytes:"),
        ]:
            expected = self.replace_once(expected, old, new)
        actual = (ROOT / "generator" / "templates" / "python" / "rest.mustache").read_text(encoding="utf-8")
        self.assertEqual(actual, self.core_adapter(expected, "python/rest.mustache"))

    def test_python_model_override_is_stock_plus_known_edits(self) -> None:
        stock = self.stock("python/model_generic.mustache")
        override = (ROOT / "generator" / "templates" / "python" / "model_generic.mustache").read_text(encoding="utf-8")
        marker = "{{#vendorExtensions.x-py-model-imports}}\n{{{.}}}\n{{/vendorExtensions.x-py-model-imports}}\n"
        expected = self.replace_once(
            stock, marker,
            marker + "# PenniLogic: the ADR-015 Instant seam is type-mapped for date-time and is not a generated model, so its import is added by generator/templates/python (see docs/development.md#generator-templates).\n"
            "from pennilogic_contracts.models.instant import Instant  # noqa: F401\n"
            "from pennilogic_contracts.models.local_date import LocalDate  # noqa: F401\n",
        )
        expected = self.replace_once(
            expected, "    def {{{name}}}_validate_regular_expression(cls, value):\n",
            "    def {{{name}}}_validate_regular_expression(cls, value: Any) -> Any:  # PenniLogic: annotated for mypy --strict\n",
        )
        expected = self.replace_once(
            expected, "    def {{{name}}}_validate_enum(cls, value):\n",
            "    def {{{name}}}_validate_enum(cls, value: Any) -> Any:  # PenniLogic: annotated for mypy --strict\n",
        )
        config = "    model_config = ConfigDict(\n        validate_by_name=True,\n        validate_by_alias=True,\n        validate_assignment=True,\n"
        expected = self.replace_once(
            expected, config,
            config + "        # PenniLogic: ADR-015 s2.2 strict models; diagnostics never echo the offending value (s1.5, s5).\n        strict=True,\n        hide_input_in_errors=True,\n",
        )
        expected = ("{{#vendorExtensions.x-pennilogic-strict-provider}}\n{{>model_provider}}\n"
                    "{{/vendorExtensions.x-pennilogic-strict-provider}}\n"
                    "{{^vendorExtensions.x-pennilogic-strict-provider}}\n" + expected +
                    "{{/vendorExtensions.x-pennilogic-strict-provider}}\n")
        self.assertEqual(override, expected, "generator/templates/python/model_generic.mustache drifted from the pinned generator's template; re-apply the documented edits on the new stock template")

    def test_kotlin_api_client_override_is_stock_plus_known_edits(self) -> None:
        stock = self.stock("kotlin-client/libraries/jvm-ktor/infrastructure/ApiClient.kt.mustache")
        override = (ROOT / "generator" / "templates" / "kotlin" / "libraries" / "jvm-ktor" / "infrastructure" / "ApiClient.kt.mustache").read_text(encoding="utf-8")
        imports = "{{#jackson}}\nimport io.ktor.serialization.jackson.*\n"
        expected = self.replace_once(
            stock, imports,
            "{{#kotlinx_serialization}}\n// PenniLogic: register the ADR-015 seams (Money, @Contextual instants and dates) on the client (generator/templates/kotlin).\n"
            "import io.ktor.serialization.kotlinx.json.json\nimport {{packageName}}.serialization.PennilogicJson\n{{/kotlinx_serialization}}\n" + imports,
        )
        install = "                {{#jackson}}\n                  jackson { jsonBlock() }\n                {{/jackson}}\n"
        expected = self.replace_once(expected, install, install + "                {{#kotlinx_serialization}}\n                  json(PennilogicJson.json)\n                {{/kotlinx_serialization}}\n")
        self.assertEqual(override, expected, "generator/templates/kotlin/.../ApiClient.kt.mustache drifted from the pinned generator's template; re-apply the documented edits")

    def test_kotlin_build_gradle_override_is_stock_plus_known_edits(self) -> None:
        stock = self.stock("kotlin-client/build.gradle.mustache")
        override = (ROOT / "generator" / "templates" / "kotlin" / "build.gradle.mustache").read_text(encoding="utf-8")
        anchor = (
            "    {{#jackson}}\n    implementation \"io.ktor:ktor-client-jackson:$ktor_version\"\n"
            "    implementation \"io.ktor:ktor-serialization-jackson:$ktor_version\"\n    {{/jackson}}\n    {{/jvm-ktor}}\n"
        )
        expected = self.replace_once(
            stock, anchor,
            anchor.replace(
                "    {{/jvm-ktor}}\n",
                "    {{#kotlinx_serialization}}\n    // PenniLogic: converter for the ADR-015 seams registered by ApiClient (generator/templates/kotlin).\n"
                "    implementation \"io.ktor:ktor-serialization-kotlinx-json:$ktor_version\"\n    {{/kotlinx_serialization}}\n    {{/jvm-ktor}}\n",
            ),
        )
        self.assertEqual(override, expected, "generator/templates/kotlin/build.gradle.mustache drifted from the pinned generator's template; re-apply the documented edits")

    def test_status_api_overrides_are_exact_anchored_edits_of_pinned_stock(self) -> None:
        for name, stock in (
            ("typescript/apis.mustache", "typescript-fetch/apis.mustache"),
            ("kotlin/libraries/jvm-ktor/api.mustache", "kotlin-client/libraries/jvm-ktor/api.mustache"),
        ):
            with self.subTest(template=name):
                actual = (ROOT / "generator" / "templates" / name).read_text(encoding="utf-8")
                self.assertEqual(actual, self.core_adapter(self.stock(stock), name))

    def test_small_null_query_and_return_partials_have_exact_finite_bindings(self) -> None:
        root = ROOT / "generator" / "templates"
        optional = (root / "kotlin" / "data_class_opt_var.mustache").read_text(encoding="utf-8")
        marker = "{{^vendorExtensions.x-pennilogic-null-presence}}\n"
        self.assertEqual(optional.count(marker), 1)
        fallback = optional.split(marker, 1)[1].removesuffix("{{/vendorExtensions.x-pennilogic-null-presence}}\n")
        self.assertEqual(fallback, self.nullable_enum_items(self.stock("kotlin-client/data_class_opt_var.mustache")) + "\n")
        expected = {
            "kotlin/data_class_opt_var.mustache": "5691f16143389ce56506592ee7009fad19166fbe73f6770718ce6bac79b4ecdd",
            "kotlin/provider_type.mustache": "1f62966332688ca916a8422b31dd9a1fdd08a469d234a20b20e061434771c6e6",
            "python/provider_type.mustache": "5a8fbdab26f3babe49da0985a19c1398605eaccf34327e6dab5cf5931271114b",
            "typescript/apisAssignQueryParam.mustache": "3bc65469c980da7cecb24b45461c271a4b7f089e035fb7de964ecf67cd47d917",
            "typescript/provider_type.mustache": "6384986536cbe34f4bcd72c0ff94517529742c59944be92a80e6550d1eda9bb9",
            "typescript/provider_enum_read.mustache": "a69ab168a5abb26851699082eb5d3f88e3368134def948e142b7f454740091c0",
            "typescript/provider_enum_write.mustache": "9e2f18dd273b713a12d9ca07461f508ed908800aeb6ec4de96047e460e854719",
            "typescript/providerField.mustache": "378ee2817d00d90e254c90b66eda4ad6ff0f56a2104b690112f63572249217b3",
            "python/success_return_type.mustache": "cec162ef157593fd357de58d0240c16a58b44ab67ec99f6bb04ed621264d41d9",
        }
        for name, digest in expected.items():
            text = (root / name).read_text(encoding="utf-8")
            self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), digest, name)
        self.assertNotIn("\n", (root / "python" / "success_return_type.mustache").read_text(encoding="utf-8"))

    def test_nullable_enum_partial_overrides_are_exact_edits_of_pinned_stock(self) -> None:
        for name, stock, old, new in (
            ("kotlin/data_class_req_var.mustache", "kotlin-client/data_class_req_var.mustache",
             "{{#isNullable}}?{{/isNullable}}{{#defaultValue}}",
             "{{#isNullable}}?{{/isNullable}}{{^isNullable}}{{#vendorExtensions.x-pennilogic-nullable-enum}}"
             "?{{/vendorExtensions.x-pennilogic-nullable-enum}}{{/isNullable}}{{#defaultValue}}"),
            ("typescript/modelGenericInterfaces.mustache", "typescript-fetch/modelGenericInterfaces.mustache",
             "{{#isNullable}} | null{{/isNullable}};",
             "{{#isNullable}} | null{{/isNullable}}{{^isNullable}}{{#vendorExtensions.x-pennilogic-nullable-enum}}"
             " | null{{/vendorExtensions.x-pennilogic-nullable-enum}}{{/isNullable}};"),
        ):
            with self.subTest(template=name):
                expected = self.replace_once(self.stock(stock), old, new)
                if name.startswith("kotlin/"):
                    expected = self.nullable_enum_items(expected)
                else:
                    expected = self.replace_once(expected, "{{{datatypeWithEnum}}}{{#isNullable}}",
                        "{{#vendorExtensions.x-pennilogic-nullable-enum-items}}"
                        "{{#uniqueItems}}Set{{/uniqueItems}}{{^uniqueItems}}Array{{/uniqueItems}}"
                        "<{{#items}}{{>provider_type}}{{/items}}>"
                        "{{/vendorExtensions.x-pennilogic-nullable-enum-items}}"
                        "{{^vendorExtensions.x-pennilogic-nullable-enum-items}}{{{datatypeWithEnum}}}"
                        "{{/vendorExtensions.x-pennilogic-nullable-enum-items}}{{#isNullable}}")
                actual = (ROOT / "generator" / "templates" / name).read_text(encoding="utf-8")
                self.assertEqual(actual, expected + "\n")

    def test_no_other_template_is_overridden(self) -> None:
        overrides = sorted(p.relative_to(ROOT / "generator" / "templates").as_posix() for p in (ROOT / "generator" / "templates").rglob("*.mustache"))
        self.assertEqual(overrides, ["kotlin/build.gradle.mustache", "kotlin/data_class.mustache",
                                    "kotlin/data_class_opt_var.mustache",
                                    "kotlin/data_class_req_var.mustache",
                                    "kotlin/libraries/jvm-ktor/api.mustache",
                                    "kotlin/libraries/jvm-ktor/infrastructure/ApiClient.kt.mustache",
                                    "kotlin/provider_type.mustache",
                                    "python/api.mustache", "python/api_client.mustache",
                                    "python/model_enum.mustache", "python/model_generic.mustache", "python/model_provider.mustache",
                                    "python/provider_type.mustache",
                                    "python/rest.mustache",
                                    "python/success_return_type.mustache",
                                    "typescript/apis.mustache", "typescript/apisAssignQueryParam.mustache",
                                    "typescript/modelEnum.mustache", "typescript/modelGeneric.mustache", "typescript/modelGenericInterfaces.mustache",
                                    "typescript/providerField.mustache", "typescript/provider_enum_read.mustache",
                                    "typescript/provider_enum_write.mustache", "typescript/provider_type.mustache"])

    def test_kotlin_data_class_override_is_stock_plus_provider_only_registration(self) -> None:
        stock = self.stock("kotlin-client/data_class.mustache")
        override = (ROOT / "generator" / "templates" / "kotlin" / "data_class.mustache").read_text(encoding="utf-8")
        serializable = "{{#multiplatform}}@Serializable{{/multiplatform}}{{#kotlinx_serialization}}{{#serializableModel}}@KSerializable{{/serializableModel}}{{^serializableModel}}@Serializable{{/serializableModel}}{{/kotlinx_serialization}}{{#moshi}}{{#moshiCodeGen}}@JsonClass(generateAdapter = true){{/moshiCodeGen}}{{/moshi}}{{#jackson}}{{#discriminator}}{{>typeInfoAnnotation}}{{/discriminator}}{{/jackson}}\n"
        expected = self.replace_once(
            stock, serializable,
            "{{#vendorExtensions.x-pennilogic-strict-provider}}\n"
            "@OptIn(kotlinx.serialization.ExperimentalSerializationApi::class)\n@kotlinx.serialization.KeepGeneratedSerializer\n"
            "@Serializable(with = {{classname}}ProviderSerializer::class)\n"
            "{{/vendorExtensions.x-pennilogic-strict-provider}}\n{{^vendorExtensions.x-pennilogic-strict-provider}}\n" +
            serializable + "{{/vendorExtensions.x-pennilogic-strict-provider}}\n",
        )
        expected = self.replace_once(
            expected, "{{/vendorExtensions.x-has-data-class-body}}\n{{#generateRoomModels}}\n",
            "{{/vendorExtensions.x-has-data-class-body}}\n"
            "{{#vendorExtensions.x-pennilogic-strict-provider}}\n"
            "    init { {{classname}}ProviderSerializer.validateValue(this) }\n"
            "{{/vendorExtensions.x-pennilogic-strict-provider}}\n{{#generateRoomModels}}\n",
        )
        marker = "{{#vendorExtensions.x-pennilogic-strict-provider}}\n\n@OptIn(kotlinx.serialization.ExperimentalSerializationApi::class)\n"
        self.assertEqual(override.split(marker, 1)[0], expected)
        added = override.split(marker, 1)[1]
        self.assertIn('StrictProviderSerializer<{{classname}}>({{classname}}.generatedSerializer(), "{{name}}")', added)
        self.assertIn("{{#isEnumRef}}", added)
        self.assertIn('ProviderConstraints.patternMatches("{{{pattern}}}", member.content)', added)
        self.assertIn("member.content.codePointCount(0, member.content.length)", added)
        for constraint in ("minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems", "uniqueItems"):
            self.assertIn("{{#" + constraint + "}}", added)
        self.assertIn("override fun verify(value: {{classname}})", added)
        self.assertNotIn("ignoreUnknownKeys", added)

    def test_typescript_generic_override_keeps_stock_conversion_inside_provider_guards(self) -> None:
        stock = self.stock("typescript-fetch/modelGeneric.mustache")
        override = (ROOT / "generator" / "templates" / "typescript" / "modelGeneric.mustache").read_text(encoding="utf-8")
        expected = (
            "{{#vendorExtensions.x-pennilogic-strict-provider}}\n"
            "import { providerObject, providerPattern, providerWire, ProviderWireError, type ProviderField } from '../providerGuard{{importFileExtension}}';\n"
            "{{/vendorExtensions.x-pennilogic-strict-provider}}\n" + stock
        )
        field_block = override.split("{{>modelGenericInterfaces}}\n", 1)[1].split("\n\n/**", 1)[0]
        self.assertTrue(field_block.startswith("{{#vendorExtensions.x-pennilogic-strict-provider}}"))
        self.assertTrue(field_block.rstrip().endswith("{{/vendorExtensions.x-pennilogic-strict-provider}}"))
        expected = self.replace_once(expected, "{{>modelGenericInterfaces}}\n", "{{>modelGenericInterfaces}}\n" + field_block)
        from_json = "export function {{classname}}FromJSONTyped(json: any, ignoreDiscriminator: boolean): {{classname}} {\n"
        expected = self.replace_once(expected, from_json, from_json +
                                     "    {{#vendorExtensions.x-pennilogic-strict-provider}}\n"
                                     "    providerObject(json, providerFields, true);\n"
                                     '    providerWire(json, "{{name}}");\n'
                                     "    {{/vendorExtensions.x-pennilogic-strict-provider}}\n")
        to_json = "export function {{classname}}ToJSONTyped(value?: {{#hasReadOnly}}Omit<{{classname}}, {{#readOnlyVars}}'{{name}}'{{^-last}}|{{/-last}}{{/readOnlyVars}}>{{/hasReadOnly}}{{^hasReadOnly}}{{classname}}{{/hasReadOnly}} | null, ignoreDiscriminator: boolean = false): any {\n"
        expected = self.replace_once(expected, to_json, to_json +
                                     "    {{#vendorExtensions.x-pennilogic-strict-provider}}\n"
                                     "    if (value === null) throw new ProviderWireError();\n"
                                     "    if (value !== undefined) providerObject(value, providerFields, false);\n"
                                     "    {{/vendorExtensions.x-pennilogic-strict-provider}}\n")
        expected = self.replace_once(
            expected, "        '{{baseName}}': {{datatype}}ToJSON(value['{{name}}']),\n",
            "        '{{baseName}}': {{^required}}value['{{name}}'] === undefined ? undefined : {{/required}}"
            "{{datatype}}ToJSON(value['{{name}}']),\n",
        )
        expected = self.replace_once(
            expected, "    return {\n        {{#parent}}...{{{.}}}ToJSONTyped(value, true),{{/parent}}\n",
            "    {{#vendorExtensions.x-pennilogic-strict-provider}}const result ={{/vendorExtensions.x-pennilogic-strict-provider}}"
            "{{^vendorExtensions.x-pennilogic-strict-provider}}return{{/vendorExtensions.x-pennilogic-strict-provider}} {\n"
            "        {{#parent}}...{{{.}}}ToJSONTyped(value, true),{{/parent}}\n",
        )
        ending = "        {{/isReadOnly}}\n        {{/vars}}\n    };\n"
        expected = self.replace_once(expected, ending, ending +
                                     "    {{#vendorExtensions.x-pennilogic-strict-provider}}\n"
                                     "    const wire = Object.fromEntries(Object.entries(result).filter(([, member]) => member !== undefined));\n"
                                     '    providerWire(wire, "{{name}}");\n    return wire;\n'
                                     "    {{/vendorExtensions.x-pennilogic-strict-provider}}\n")
        expected = self.core_adapter(expected, "typescript/modelGeneric.mustache")
        expected = self.replace_once(expected, "        {{#vars}}\n        {{#isPrimitiveType}}\n",
            "        {{#vars}}\n"
            "        {{#vendorExtensions.x-pennilogic-nullable-enum-items}}\n"
            "        '{{name}}': {{^required}}json['{{baseName}}'] === undefined ? undefined : {{/required}}"
            "{{#isNullable}}json['{{baseName}}'] === null ? null : {{/isNullable}}"
            "{{#uniqueItems}}new Set({{/uniqueItems}}(json['{{baseName}}'] as Array<any>).map("
            "{{#items}}{{>provider_enum_read}}{{/items}}){{#uniqueItems}}){{/uniqueItems}},\n"
            "        {{/vendorExtensions.x-pennilogic-nullable-enum-items}}\n"
            "        {{^vendorExtensions.x-pennilogic-nullable-enum-items}}\n        {{#isPrimitiveType}}\n")
        expected = self.replace_once(expected, "        {{/isPrimitiveType}}\n        {{/vars}}\n",
            "        {{/isPrimitiveType}}\n"
            "        {{/vendorExtensions.x-pennilogic-nullable-enum-items}}\n        {{/vars}}\n")
        expected = self.replace_once(expected, "        {{^isReadOnly}}\n        {{#isPrimitiveType}}\n",
            "        {{^isReadOnly}}\n"
            "        {{#vendorExtensions.x-pennilogic-nullable-enum-items}}\n"
            "        '{{baseName}}': {{^required}}value['{{name}}'] === undefined ? undefined : {{/required}}"
            "{{#isNullable}}value['{{name}}'] === null ? null : {{/isNullable}}"
            "{{#uniqueItems}}Array.from(value['{{name}}'] as Set<any>){{/uniqueItems}}"
            "{{^uniqueItems}}(value['{{name}}'] as Array<any>){{/uniqueItems}}"
            ".map({{#items}}{{>provider_enum_write}}{{/items}}),\n"
            "        {{/vendorExtensions.x-pennilogic-nullable-enum-items}}\n"
            "        {{^vendorExtensions.x-pennilogic-nullable-enum-items}}\n        {{#isPrimitiveType}}\n")
        expected = self.replace_once(expected, "        {{/isPrimitiveType}}\n        {{/isReadOnly}}\n",
            "        {{/isPrimitiveType}}\n"
            "        {{/vendorExtensions.x-pennilogic-nullable-enum-items}}\n        {{/isReadOnly}}\n")
        self.assertEqual(override, expected)
        self.assertIn("{{>providerField}}", field_block)
        partial = (ROOT / "generator" / "templates" / "typescript" / "providerField.mustache").read_text(encoding="utf-8")
        self.assertIn("{{#isInteger}}kind: 'integer'", partial)
        self.assertIn("{{#isBoolean}}kind: 'boolean'", partial)
        self.assertIn("{{#isModel}}kind: 'object'", partial)
        self.assertIn('providerPattern("{{{pattern}}}")', partial)
        self.assertIn("{{#uniqueItems}}uniqueItems: true", partial)
        self.assertIn("{{#items}}\nitems: {\n    name: '', required: true,\n    {{>providerField}}", partial)

    def test_python_enum_override_is_stock_plus_strict_wire_seam(self) -> None:
        stock = self.stock("python/model_enum.mustache")
        override = (ROOT / "generator" / "templates" / "python" / "model_enum.mustache").read_text(encoding="utf-8")
        expected = self.replace_once(stock, "from typing_extensions import Self\n",
                                     "from typing import Any\nfrom pydantic import GetCoreSchemaHandler\n"
                                     "from pydantic_core import CoreSchema, core_schema\nfrom typing_extensions import Self\n")
        anchor = "    @classmethod\n    def from_json(cls, json_str: str) -> Self:\n"
        seam = (
            "    @classmethod\n    def from_wire(cls, value: object) -> Self:\n"
            "        if isinstance(value, cls):\n            return value\n"
            "        if type(value) is {{vendorExtensions.x-py-enum-type}} and any(member.value == value for member in cls):\n            return cls(value)\n"
            '        raise ValueError("enum value rejected")\n\n'
            "    @classmethod\n    def __get_pydantic_core_schema__(cls, _source: Any, _handler: GetCoreSchemaHandler) -> CoreSchema:\n"
            "        # Strict generated models accept the exact wire spelling, never coercion or input-echoing enum errors.\n"
            "        return core_schema.no_info_plain_validator_function(\n            cls.from_wire,\n"
            "            json_schema_input_schema=core_schema.literal_schema([member.value for member in cls]),\n"
            "            serialization=core_schema.plain_serializer_function_ser_schema(lambda member: member.value),\n        )\n\n"
        )
        expected = self.replace_once(expected, anchor, seam + anchor)
        expected = self.replace_once(expected, "        return cls(json.loads(json_str))\n",
                                     "        return cls.from_wire(json.loads(json_str))\n")
        self.assertEqual(override, expected.rstrip() + "\n")

    def test_typescript_enum_override_replaces_only_converter_functions(self) -> None:
        stock = self.stock("typescript-fetch/modelEnum.mustache")
        override = (ROOT / "generator" / "templates" / "typescript" / "modelEnum.mustache").read_text(encoding="utf-8")
        self.assertEqual(stock.split("\n\n", 1)[0], "{{>modelEnumInterfaces}}")
        expected = (
            "{{>modelEnumInterfaces}}\n\n"
            "export function instanceOf{{classname}}(value: unknown): value is {{classname}} {\n"
            "    return Object.values({{classname}}).some((member) => member === value);\n}\n\n"
            "export function {{classname}}FromJSON(json: unknown): {{classname}} {\n"
            "    return {{classname}}FromJSONTyped(json, false);\n}\n\n"
            "export function {{classname}}FromJSONTyped(json: unknown, ignoreDiscriminator: boolean): {{classname}} {\n"
            '    if (!instanceOf{{classname}}(json)) throw new TypeError("enum value rejected");\n    return json;\n}\n\n'
            "export function {{classname}}ToJSON(value?: {{classname}} | null): {{classname}} | null | undefined {\n"
            "    if (value == null) return value;\n    return {{classname}}FromJSON(value);\n}\n\n"
            "export function {{classname}}ToJSONTyped(value?: {{classname}} | null, ignoreDiscriminator: boolean = false): {{classname}} | null | undefined {\n"
            "    return {{classname}}ToJSON(value);\n}\n"
        )
        self.assertEqual(override, expected)
        stock_functions = [line.split("(")[0] for line in stock.splitlines() if line.startswith("export function ")]
        self.assertEqual(stock_functions, [line.split("(")[0] for line in override.splitlines() if line.startswith("export function ")])

class DeterminismTest(unittest.TestCase):
    def test_verify_passes_on_the_committed_golden(self) -> None:
        completed = run_script("generate_clients.py", "--verify")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("byte-identical double generation", completed.stdout)


class PublishedApiCompatibilityTest(unittest.TestCase):
    def test_all_accepted_kotlin_primary_parameters_defaults_and_order_are_preserved(self) -> None:
        fixture = json.loads((ROOT / "scripts" / "tests" / "accepted-kotlin-constructors.json").read_bytes())
        self.assertEqual(fixture["generator_version"], pl_contracts.versions()["openapi_generator"]["version"])
        self.assertEqual(len(fixture["constructors"]), 22)
        models = ROOT / "build" / "generated" / "kotlin" / "src" / "main" / "kotlin" / "com" / "pennilogic" / "contracts" / "models"
        for name, expected in fixture["constructors"].items():
            with self.subTest(model=name):
                source = (models / f"{name}.kt").read_text(encoding="utf-8")
                match = re.search(r"data class " + re.escape(name) + r"\s*\((.*?)\n\)", source, re.S)
                self.assertIsNotNone(match)
                body = re.sub(r"/\*.*?\*/", "", match[1], flags=re.S)
                actual = [field.rstrip(",").strip() for field in re.findall(r"\bval ([^\n]+)", body)]
                self.assertEqual(actual, expected)

    def test_old_service_policies_and_total_key_typing_with_authentication_excluded(self) -> None:
        accepted = json.loads(subprocess.run(
            ["git", "show", "5b41d4580c85be3cc1617074c0f3052b1f7b02cd:spec/error-catalogue.v1.json"],
            cwd=ROOT, capture_output=True, check=True,
        ).stdout)
        current = json.loads((ROOT / "spec" / "error-catalogue.v1.json").read_bytes())
        current_rows = {entry["code"]: entry for entry in current["codes"]}
        self.assertEqual(len(accepted["codes"]), 14)
        for entry in accepted["codes"]:
            self.assertEqual(current_rows[entry["code"]], entry)
        spec = SpecDir()
        self.addCleanup(spec.cleanup)
        source = spec.path / "consumer.ts"
        config = spec.path / "tsconfig.json"
        (spec.path / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
        config.write_text(json.dumps({
            "compilerOptions": {
                "target": "ES2022", "module": "NodeNext", "moduleResolution": "NodeNext",
                "strict": True, "noEmit": True, "skipLibCheck": False,
                "paths": {"@contract/*": [str(ROOT / "build" / "generated" / "typescript" / "src" / "*")]},
            },
            "files": [str(source)],
        }), encoding="utf-8")
        imports = ('import { ERROR_POLICIES, type ErrorPolicy } from "@contract/errorCatalogue.js";\n'
                   'import { ProblemCode } from "@contract/models/ProblemCode.js";\n')
        source.write_text(imports +
            "const policy: ErrorPolicy = ERROR_POLICIES[ProblemCode.RequestFailed];\n"
            "const status: number = ERROR_POLICIES[ProblemCode.RequestFailed].status;\n"
            "export { policy, status };\n" +
            "".join(f'const old{index}: ErrorPolicy = ERROR_POLICIES[ProblemCode.'
                    f'{"".join(word.capitalize() for word in entry["code"].split("_"))}];\n'
                    for index, entry in enumerate(accepted["codes"])), encoding="utf-8")
        command = [pl_contracts.node_executable(), str(ROOT / "node_modules" / "typescript" / "bin" / "tsc"),
                   "--project", str(config)]
        positive = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(positive.returncode, 0, positive.stdout + positive.stderr)
        for entry in current["authentication_codes"]:
            name = "".join(word.capitalize() for word in entry["code"].split("_"))
            source.write_text(imports + f"const policy: ErrorPolicy = ERROR_POLICIES[ProblemCode.{name}];\n",
                              encoding="utf-8")
            negative = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
            self.assertEqual(negative.returncode, 1, negative.stdout + negative.stderr)
            self.assertIn("TS2322", negative.stdout)


if __name__ == "__main__":
    unittest.main()
