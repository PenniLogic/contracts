"""Closed-provider serializer coverage and schema agreement, not producer adoption."""

from __future__ import annotations

import copy
import hashlib
import json
import unittest

from support import ROOT, SpecDir, replace_once, run_script, spec_text
from test_error_provider import schema_results
from test_import_provider import schema_document


def fixture(name: str) -> dict:
    return json.loads((ROOT / "spec" / "fixtures" / name).read_text(encoding="utf-8"))


def samples(*, arrays: bool = False, legacy: bool = False) -> list[tuple[str, dict]]:
    result = []
    inventory = fixture("provider-transport.v1.json")
    entries = inventory["models"][:29] if legacy else inventory["models"]
    controls = inventory["array_controls"][:2] if legacy else inventory["array_controls"]
    for entry in entries + (controls if arrays else []):
        value = fixture(entry["fixture"])
        for key in entry["path"]:
            value = value[key]
        result.append((entry["schema"], {**value, **entry.get("set", {})}))
    return result


def quoted_primitives(value):
    if isinstance(value, dict):
        for key, child in value.items():
            for replacement in quoted_primitives(child):
                yield {**value, key: replacement}
    elif isinstance(value, list):
        for index, child in enumerate(value):
            for replacement in quoted_primitives(child):
                result = copy.deepcopy(value)
                result[index] = replacement
                yield result
    elif type(value) in (int, bool):
        yield json.dumps(value)

def null_array_entries(value):
    if isinstance(value, dict):
        for key, child in value.items():
            for replacement in null_array_entries(child):
                yield {**value, key: replacement}
    elif isinstance(value, list):
        yield [*value, None]
        for index, child in enumerate(value):
            for replacement in null_array_entries(child):
                result = copy.deepcopy(value)
                result[index] = replacement
                yield result


class ProviderTransportSchemaTest(unittest.TestCase):
    def test_original_29_model_11_array_15_negative_corpus_is_preserved_exactly(self) -> None:
        inventory = fixture("provider-transport.v1.json")
        original = [inventory["models"][:29], inventory["array_controls"][:2], inventory["refusal_negatives"]]
        binding = hashlib.sha256(json.dumps(original, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(binding, "7f3ceb6b3b93a7666c78de21bdfef69e01584f2da5313fe5d0da23959964d942")
        controls = [{"name": row["schema"], "schema": row["schema"], "wire": {}}
                    for row in inventory["models"] if row.get("empty_allowed")]
        self.assertTrue(all(row["valid"] for row in schema_results(controls)))

    def test_inventory_covers_every_new_closed_object_and_keeps_legacy_bindings(self) -> None:
        schemas = schema_document()["components"]["schemas"]
        marked = {name for name, schema in schemas.items() if schema.get("x-pennilogic-strict-provider") is True}
        self.assertEqual(marked, {name for name, _ in samples()})
        self.assertEqual(len(samples(legacy=True)), 29)
        self.assertEqual(len(marked), 112)
        self.assertNotIn("Money", marked)
        self.assertNotIn("ProblemDetail", marked)
        for name in marked:
            self.assertEqual(schemas[name]["type"], "object")
            self.assertFalse(schemas[name]["additionalProperties"])

    def test_all_controls_extras_required_members_and_quoted_primitive_leaves_match_schema(self) -> None:
        schemas = schema_document()["components"]["schemas"]
        cases, expected = [], []
        primitives = 0
        for name, wire in samples():
            cases.append({"name": name, "schema": name, "wire": wire})
            expected.append(True)
            cases.append({"name": name + " null root", "schema": name, "wire": None})
            expected.append(False)
            for invalid in ({**wire, "provider_detail": "PRIVATE_SYNTHETIC_CANARY"},
                            {**wire, "PRIVATE_SYNTHETIC_CANARY": "synthetic"}):
                cases.append({"name": name + " unknown member", "schema": name, "wire": invalid})
                expected.append(False)
            for required in schemas[name].get("required", []):
                cases.append({"name": name + " missing " + required, "schema": name,
                              "wire": {key: value for key, value in wire.items() if key != required}})
                expected.append(False)
            for invalid in quoted_primitives(wire):
                cases.append({"name": name + " quoted primitive", "schema": name, "wire": invalid})
                expected.append(False)
                primitives += 1
        results = schema_results(cases)
        self.assertEqual([result["valid"] for result in results], expected)
        self.assertGreaterEqual(primitives, 50)

    def test_successful_refusal_negatives_match_schema(self) -> None:
        valid = fixture("error-provider.v1.json")["refusal"]
        cases = []
        for negative in fixture("provider-transport.v1.json")["refusal_negatives"]:
            invalid = {**valid, **negative.get("set", {})}
            for key in negative.get("remove", []):
                invalid.pop(key)
            cases.append({"name": negative["name"], "schema": "AiRefusal", "wire": invalid})
        self.assertFalse(any(result["valid"] for result in schema_results(cases)))

    def test_every_nested_provider_array_rejects_null_items_in_the_committed_schema(self) -> None:
        schemas = schema_document()["components"]["schemas"]
        declared = {
            (name, key) for name, schema in schemas.items() if schema.get("x-pennilogic-strict-provider")
            for key, field in schema["properties"].items() if field.get("type") == "array"
        }
        represented = {
            (name, key) for name, wire in samples(arrays=True) for key, value in wire.items()
            if isinstance(value, list)
        }
        self.assertEqual(represented, declared)
        legacy_arrays = {(name, key) for name, wire in samples(arrays=True, legacy=True)
                         for key, value in wire.items() if isinstance(value, list)}
        self.assertEqual(len(legacy_arrays), 11)
        self.assertTrue(legacy_arrays.issubset(declared))
        self.assertEqual(len(declared), 34)
        cases = [
            {"name": name + " null array entry", "schema": name, "wire": invalid}
            for name, wire in samples(arrays=True) for invalid in null_array_entries(wire)
        ]
        legacy_cases = [invalid for _, wire in samples(arrays=True, legacy=True)
                        for invalid in null_array_entries(wire)]
        self.assertEqual(len(legacy_cases), 15)
        self.assertEqual(len(cases), 43)
        self.assertFalse(any(result["valid"] for result in schema_results(cases)))

    def test_generated_models_wire_guards_on_every_closed_provider(self) -> None:
        for name, _ in samples():
            python = ROOT / "build" / "generated" / "python" / "pennilogic_contracts" / "models"
            module = "".join(("_" + letter.lower()) if letter.isupper() and index else letter.lower()
                             for index, letter in enumerate(name))
            py_text = (python / (module + ".py")).read_text(encoding="utf-8")
            self.assertIn("ProviderModel", py_text, name)
            self.assertIn(f'_provider_schema_name: ClassVar[str] = "{name}"', py_text, name)
            self.assertNotIn("additional_properties:", py_text, name)
            ts_text = (ROOT / "build" / "generated" / "typescript" / "src" / "models" / (name + ".ts")).read_text(encoding="utf-8")
            self.assertIn("providerObject", ts_text, name)
            self.assertIn("providerWire", ts_text, name)
            kt_text = (ROOT / "build" / "generated" / "kotlin" / "src" / "main" / "kotlin" / "com" / "pennilogic" / "contracts" / "models" / (name + ".kt")).read_text(encoding="utf-8")
            self.assertIn("KeepGeneratedSerializer", kt_text, name)
            self.assertIn("StrictProviderSerializer", kt_text, name)
            self.assertIn(f'generatedSerializer(), "{name}"', kt_text, name)


class ProviderWiringNegativeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = SpecDir()
        self.addCleanup(self.spec.cleanup)

    def test_a_missing_strict_binding_fails_for_each_closed_provider(self) -> None:
        for name, _ in samples(legacy=True):
            with self.subTest(schema=name):
                anchor = f"    {name}:\n      type: object\n      x-pennilogic-strict-provider: true\n"
                text = replace_once(spec_text(), anchor, anchor.replace("      x-pennilogic-strict-provider: true\n", ""))
                result = run_script("lint_spec.py", "--spec", str(self.spec.write(text)))
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                expected = ("must select x-pennilogic-strict-provider" if name.startswith("CustomDestination")
                            else "strict generated conversion and serialization")
                self.assertIn(expected, result.stderr)

    def test_all_added_core_strict_bindings_are_enforced_by_the_real_cli(self) -> None:
        text = spec_text()
        added = samples()[29:]
        self.assertEqual(len(added), 83)
        for name, _ in added:
            anchor = f"    {name}:\n      type: object\n      x-pennilogic-strict-provider: true\n"
            text = replace_once(text, anchor, anchor.replace("      x-pennilogic-strict-provider: true\n", ""))
        result = run_script("lint_spec.py", "--spec", str(self.spec.write(text)))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertGreaterEqual(result.stderr.count("strict generated conversion and serialization"), len(added))

    def test_a_local_copy_without_runtime_metadata_still_fails_inline_duplicate_guard(self) -> None:
        shape = schema_document()["components"]["schemas"]["DedupOutcome"]
        shape.pop("x-pennilogic-strict-provider")
        shape.pop("x-pennilogic-provider-validator")
        text = replace_once(spec_text(), "    ImportGroupVersion:\n", "    LocalCopy: " + json.dumps(shape) + "\n    ImportGroupVersion:\n")
        result = run_script("lint_spec.py", "--spec", str(self.spec.write(text)))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("equivalent inline/local provider", result.stderr)

    def test_missing_semantic_serializer_callback_is_not_a_wire_only_success(self) -> None:
        text = replace_once(spec_text(), "      x-pennilogic-provider-validator: com.pennilogic.contracts.imports.ImportContract.verifyPreview\n", "")
        result = run_script("lint_spec.py", "--spec", str(self.spec.write(text)))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("retain their published semantic verification", result.stderr)


if __name__ == "__main__":
    unittest.main()
