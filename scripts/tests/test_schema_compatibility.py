"""Actual pinned-diff pairs and adversarial source/record controls for the bounded proof."""

from __future__ import annotations

import copy
import json
import subprocess
import unittest

from support import ROOT, SPEC, SpecDir, spec_text, with_probe_paths
from pl_contracts import PipelineError, node_executable
from schema_compatibility import MAX_DEPTH, SourceProof, Unproved
from test_error_provider import error_examples, schema_results
import check_breaking_changes as cbc
import toolchain


def guard(tag: str, status: int) -> dict:
    return {"if": {"properties": {"kind": {"const": tag}}, "required": ["kind"]},
            "then": {"properties": {"status": {"const": status}}}}


def tagged_pair(document: dict) -> tuple[dict, dict]:
    before = copy.deepcopy(document)
    schemas = before["components"]["schemas"]
    schemas["CompatibilityKind"] = {"type": "string", "enum": ["stable", "other"], "description": "Synthetic tags."}
    schemas["CompatibilityEnvelope"] = {
        "type": "object", "additionalProperties": False, "required": ["kind", "status"],
        "properties": {
            "kind": {"$ref": "#/components/schemas/CompatibilityKind"},
            "status": {"type": "integer", "minimum": 100, "maximum": 599},
            "note": {"type": "string", "maxLength": 20},
            "flag": {"type": "boolean"}, "placeholder": {"type": "null"},
        },
        "allOf": [guard("stable", 200), guard("other", 202)],
    }
    schemas["CompatibilityRows"] = {
        "type": "object", "additionalProperties": False, "required": ["rows"],
        "properties": {"rows": {"type": "array", "maxItems": 10,
                               "items": {"$ref": "#/components/schemas/CompatibilityEnvelope"}}},
    }
    schemas["CompatibilityMetadata"] = {"type": "string", "enum": ["fixed"]}
    before["components"]["headers"]["CompatibilityMarker"] = {
        "description": "Unchanged synthetic response metadata.",
        "schema": {"$ref": "#/components/schemas/CompatibilityMetadata"},
    }
    before["components"]["responses"]["CompatibilityResponse"] = {
        "description": "Unchanged synthetic response envelope.",
        "headers": {"Probe-Marker": {"$ref": "#/components/headers/CompatibilityMarker"}},
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/CompatibilityEnvelope"}}},
    }
    before["paths"]["/probe"]["get"]["responses"]["200"] = {"$ref": "#/components/responses/CompatibilityResponse"}
    after = copy.deepcopy(before)
    after["components"]["schemas"]["CompatibilityKind"]["enum"].append("next")
    after["components"]["schemas"]["CompatibilityEnvelope"]["allOf"].append(guard("next", 409))
    return before, after


SUPPORTED_PAIRS = (
    "const guard", "enum guard", "multiple new tags", "two disjoint guards", "inline bound",
    "inherited required object", "inherited discriminator", "local ref chain", "escaped property pointer",
    "inert description", "positive subset", "explicit dialect", "fixed scalar consequence", "empty prefix",
)
REFUSED_PAIRS = (
    "old tag", "mixed old and new guard", "unregistered new tag", "optional guard discriminator",
    "wrong guard discriminator", "extra guard property", "guard sibling", "else", "branch sibling",
    "then required", "then nested object", "then pattern", "changed old guard", "removed prefix",
    "reordered prefix", "duplicate branch", "overlapping new guards", "non-object", "optional tag",
    "unbounded tag", "nullable tag", "removed enum value", "changed const", "changed pattern",
    "changed format", "narrowed scalar", "new required property", "removed required property",
    "changed reference identity", "narrowed nested items", "changed closedness",
    "response description", "response header", "response media", "response metadata reference",
)


def variant(document: dict, name: str) -> tuple[dict, dict]:
    before, after = tagged_pair(document)
    old_schemas, schemas = before["components"]["schemas"], after["components"]["schemas"]
    old, new = old_schemas["CompatibilityEnvelope"], schemas["CompatibilityEnvelope"]
    branch = new["allOf"][-1]
    response = after["components"]["responses"]["CompatibilityResponse"]
    match name:
        case "const guard":
            pass
        case "enum guard":
            branch["if"]["properties"]["kind"] = {"enum": ["next"]}
        case "multiple new tags":
            schemas["CompatibilityKind"]["enum"].append("later")
            branch["if"]["properties"]["kind"] = {"enum": ["next", "later"]}
        case "two disjoint guards":
            schemas["CompatibilityKind"]["enum"].append("later")
            new["allOf"].append(guard("later", 410))
        case "inline bound":
            old["properties"]["kind"] = copy.deepcopy(old_schemas["CompatibilityKind"])
            new["properties"]["kind"] = copy.deepcopy(schemas["CompatibilityKind"])
        case "inherited required object" | "inherited discriminator":
            for components, envelope in ((old_schemas, old), (schemas, new)):
                inherited = {"type": envelope.pop("type"), "required": envelope.pop("required")}
                if name == "inherited discriminator":
                    inherited["properties"] = {"kind": envelope["properties"]["kind"]}
                    envelope["properties"]["kind"] = {}
                components["CompatibilityInherited"] = inherited
                envelope["allOf"].insert(0, {"$ref": "#/components/schemas/CompatibilityInherited"})
        case "local ref chain":
            for components, envelope in ((old_schemas, old), (schemas, new)):
                components["CompatibilityAlias"] = {"$ref": "#/components/schemas/CompatibilityKind"}
                envelope["properties"]["kind"] = {"$ref": "#/components/schemas/CompatibilityAlias"}
        case "escaped property pointer":
            for components, envelope in ((old_schemas, old), (schemas, new)):
                components["CompatibilityNames"] = {
                    "type": "object", "properties": {"tag~/name": {"$ref": "#/components/schemas/CompatibilityKind"}},
                }
                envelope["properties"]["kind"] = {"$ref": "#/components/schemas/CompatibilityNames/properties/tag~0~1name"}
        case "inert description":
            schemas["CompatibilityKind"]["description"] = "A changed inert explanation."
        case "positive subset":
            schemas["CompatibilityKind"]["enum"].append("excluded")
            new["properties"]["kind"].update(type="string", enum=["stable", "other", "next"])
        case "explicit dialect":
            before["jsonSchemaDialect"] = after["jsonSchemaDialect"] = "https://json-schema.org/draft/2020-12/schema"
        case "fixed scalar consequence":
            branch["then"]["properties"].update(flag={"const": True}, placeholder={"const": None}, note={"enum": ["safe"]})
        case "empty prefix":
            del old["allOf"]
            new["allOf"] = [branch]
        case "old tag":
            branch["if"]["properties"]["kind"] = {"const": "stable"}
        case "mixed old and new guard":
            branch["if"]["properties"]["kind"] = {"enum": ["next", "stable"]}
        case "unregistered new tag":
            branch["if"]["properties"]["kind"] = {"const": "missing"}
        case "optional guard discriminator":
            del branch["if"]["required"]
        case "wrong guard discriminator":
            branch["if"]["required"] = ["status"]
        case "extra guard property":
            branch["if"]["properties"]["status"] = {"const": 409}
        case "guard sibling":
            branch["if"]["description"] = "Extra guard keyword."
        case "else":
            branch["else"] = {"properties": {"status": {"const": 409}}}
        case "branch sibling":
            branch["required"] = ["note"]
        case "then required":
            branch["then"]["required"] = ["note"]
        case "then nested object":
            branch["then"]["properties"]["note"] = {"properties": {"nested": {"const": "fixed"}}}
        case "then pattern":
            branch["then"]["properties"]["note"] = {"pattern": "^safe$"}
        case "changed old guard":
            new["allOf"][0]["then"]["properties"]["status"]["const"] = 201
        case "removed prefix":
            del new["allOf"][0]
        case "reordered prefix":
            new["allOf"][0], new["allOf"][1] = new["allOf"][1], new["allOf"][0]
        case "duplicate branch":
            new["allOf"].append(copy.deepcopy(branch))
        case "overlapping new guards":
            new["allOf"].append(guard("next", 410))
        case "non-object":
            del old["type"]
            del new["type"]
        case "optional tag":
            old["required"].remove("kind")
            new["required"].remove("kind")
        case "unbounded tag":
            del old_schemas["CompatibilityKind"]["enum"]
            del schemas["CompatibilityKind"]["enum"]
        case "nullable tag":
            old_schemas["CompatibilityKind"]["type"] = schemas["CompatibilityKind"]["type"] = ["string", "null"]
        case "removed enum value":
            schemas["CompatibilityKind"]["enum"].remove("other")
        case "changed const":
            old["properties"]["kind"]["const"] = "stable"
            new["properties"]["kind"]["const"] = "other"
        case "changed pattern":
            new["properties"]["kind"]["pattern"] = "^next$"
        case "changed format":
            new["properties"]["kind"]["format"] = "uuid"
        case "narrowed scalar":
            new["properties"]["note"]["maxLength"] = 10
        case "new required property":
            new["required"].append("note")
        case "removed required property":
            new["required"].remove("status")
        case "changed reference identity":
            schemas["CompatibilityReplacement"] = copy.deepcopy(schemas["CompatibilityKind"])
            new["properties"]["kind"] = {"$ref": "#/components/schemas/CompatibilityReplacement"}
        case "narrowed nested items":
            schemas["CompatibilityRows"]["properties"]["rows"]["maxItems"] = 5
        case "changed closedness":
            new["additionalProperties"] = True
        case "response description":
            response["description"] = "Changed response metadata."
        case "response header":
            del response["headers"]
        case "response media":
            response["content"]["application/problem+json"] = copy.deepcopy(response["content"]["application/json"])
        case "response metadata reference":
            schemas["CompatibilityMetadata"]["enum"].append("changed")
        case _:
            raise AssertionError(f"unknown test variant: {name}")
    return before, after


class SourceCompatibilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.specs = SpecDir()
        cls.tools = toolchain.ensure_installed()
        source = cls.specs.write(with_probe_paths(spec_text()), "with-probe.yaml")
        cls.document, _ = cbc.source_documents(source, source)
        cls.base, cls.revision = tagged_pair(cls.document)
        cls.base_path = cls.specs.write(json.dumps(cls.base), "bound-base.json")
        cls.revision_path = cls.specs.write(json.dumps(cls.revision), "bound-revision.json")
        cls.diff = cbc.oasdiff_json(cls.tools["oasdiff"], "diff", cls.base_path, cls.revision_path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.specs.cleanup()

    def test_real_pinned_pair_matrix_uses_complete_documents_and_the_shipped_checker(self) -> None:
        self.assertEqual(len(SUPPORTED_PAIRS) + len(REFUSED_PAIRS), 49)
        for name in SUPPORTED_PAIRS + REFUSED_PAIRS:
            with self.subTest(pair=name):
                before, after = variant(self.document, name)
                self.assertEqual(before["security"], self.document["security"])
                self.assertEqual(before["info"], self.document["info"])
                self.assertLessEqual(set(self.document["paths"]), set(before["paths"]))
                base = self.specs.write(json.dumps(before), "pair-base.json")
                revision = self.specs.write(json.dumps(after), "pair-revision.json")
                findings, _ = cbc.compare(base, revision, self.tools["oasdiff"])
                self.assertEqual(bool(findings), name in REFUSED_PAIRS, [finding.as_dict() for finding in findings])

    def test_partial_malformed_or_unbound_records_never_prove_a_source_addition(self) -> None:
        self.assertEqual(cbc.component_findings(self.diff, self.base, self.revision), [])
        for defect in range(16):
            with self.subTest(defect=defect):
                diff = copy.deepcopy(self.diff)
                modified = diff["components"]["schemas"]["modified"]
                change = modified["CompatibilityEnvelope"]
                added = change["allOf"]["added"]
                match defect:
                    case 0:
                        added[0]["index"] = True
                    case 1:
                        added[0]["index"] = float(added[0]["index"])
                    case 2:
                        added[0]["index"] = str(added[0]["index"])
                    case 3:
                        added[0]["index"] = -1
                    case 4:
                        added[0]["index"] += 1
                    case 5:
                        added[0]["component"] = "Unbound"
                    case 6:
                        added[0]["metadata"] = {}
                    case 7:
                        added.append(copy.deepcopy(added[0]))
                    case 8:
                        change["allOf"]["added"] = []
                    case 9:
                        del change["allOf"]
                    case 10:
                        change["allOf"]["added"] = None
                    case 11:
                        del change["properties"]
                    case 12:
                        change["properties"]["modified"]["kind"]["enum"]["added"].append("next")
                    case 13:
                        del modified["CompatibilityKind"]
                    case 14:
                        del modified["CompatibilityEnvelope"]
                    case 15:
                        del diff["components"]["responses"]["modified"]["CompatibilityResponse"]
                self.assertTrue(cbc.component_findings(diff, self.base, self.revision))

    def test_unsupported_source_semantics_fail_without_resolving_external_input(self) -> None:
        defects = (
            ("unsupported OpenAPI", "openapi", "3.0.3"),
            ("unsupported dialect", "jsonSchemaDialect", "https://invalid.example/dialect"),
            ("malformed dialect", "jsonSchemaDialect", {}),
            ("dynamic root", "$id", "https://invalid.example/root"),
            ("unknown keyword", "schema-key", ("unknownConstraint", True)),
            ("dynamic schema", "schema-key", ("$dynamicRef", "#anchor")),
            ("evaluation-sensitive object", "schema-key", ("unevaluatedProperties", False)),
            ("evaluation-sensitive array", "schema-key", ("unevaluatedItems", False)),
            ("dependent required", "schema-key", ("dependentRequired", {"kind": ["note"]})),
            ("anchor", "schema-key", ("$anchor", "local")),
            ("external reference", "reference", "https://invalid.example/no-request-is-made"),
            ("cyclic reference", "reference", "#/components/schemas/CompatibilityEnvelope"),
            ("unresolved reference", "reference", "#/components/schemas/Missing"),
            ("ambiguous pointer", "reference", "#/components/schemas/Compatibility%4bind"),
            ("duplicate required", "duplicate-required", None),
            ("duplicate enum", "duplicate-enum", None),
        )
        for name, kind, value in defects:
            with self.subTest(source=name):
                before, after = copy.deepcopy(self.base), copy.deepcopy(self.revision)
                for document in (before, after):
                    envelope = document["components"]["schemas"]["CompatibilityEnvelope"]
                    if kind == "schema-key":
                        envelope[value[0]] = value[1]
                    elif kind == "reference":
                        envelope["properties"]["kind"] = {"$ref": value}
                    elif kind == "duplicate-required":
                        envelope["required"].append("kind")
                    elif kind == "duplicate-enum":
                        document["components"]["schemas"]["CompatibilityKind"]["enum"].append("stable")
                    else:
                        document[kind] = value
                self.assertTrue(cbc.component_findings(self.diff, before, after))

    def test_unknown_component_envelopes_and_nested_record_types_are_explicit_refusals(self) -> None:
        for defect in (None, [], {"components": []}, {"components": {"unknown": {"modified": {}}}},
                       {"components": {"schemas": {"modified": []}}}):
            self.assertTrue(cbc.component_findings(defect, self.base, self.revision))
        for modified in (None, [], ["kind"], {"kind": None}, {"kind": []}):
            diff = copy.deepcopy(self.diff)
            diff["components"]["schemas"]["modified"]["CompatibilityEnvelope"]["properties"]["modified"] = modified
            self.assertTrue(cbc.component_findings(diff, self.base, self.revision))

    def test_changed_reference_closures_in_old_negative_positions_do_not_inherit_enum_permission(self) -> None:
        for position in ("if", "not", "oneOf", "anyOf"):
            with self.subTest(position=position):
                before, after = copy.deepcopy(self.base), copy.deepcopy(self.revision)
                for document in (before, after):
                    envelope = document["components"]["schemas"]["CompatibilityEnvelope"]
                    reference = {"properties": {"kind": {"$ref": "#/components/schemas/CompatibilityKind"}},
                                 "required": ["kind"]}
                    if position == "if":
                        member = {"if": reference, "then": {"properties": {"status": {"const": 200}}}}
                    elif position in ("oneOf", "anyOf"):
                        member = {position: [reference, {"required": ["note"]}]}
                    else:
                        member = {position: reference}
                    envelope["allOf"].insert(0, member)
                base = self.specs.write(json.dumps(before), "negative-base.json")
                revision = self.specs.write(json.dumps(after), "negative-revision.json")
                findings, _ = cbc.compare(base, revision, self.tools["oasdiff"])
                self.assertTrue(findings, position)

    def test_depth_work_bounds_and_cached_reference_cycles_remain_refusals(self) -> None:
        before, after = copy.deepcopy(self.base), copy.deepcopy(self.revision)
        for document in (before, after):
            schemas = document["components"]["schemas"]
            for index in range(MAX_DEPTH + 1):
                schemas[f"Depth{index}"] = {"$ref": f"#/components/schemas/Depth{index + 1}"}
            schemas[f"Depth{MAX_DEPTH + 1}"] = {"$ref": "#/components/schemas/CompatibilityKind"}
            schemas["CompatibilityEnvelope"]["properties"]["kind"] = {"$ref": "#/components/schemas/Depth0"}
        self.assertTrue(cbc.component_findings(self.diff, before, after))
        proof = SourceProof(self.base, self.revision)
        target = self.base["components"]["schemas"]["CompatibilityEnvelope"]
        proof.closure(target, self.base)
        with self.assertRaises(Unproved):
            proof.closure(target, self.base, depth=MAX_DEPTH)
        cyclic = {"components": {"schemas": {"Cycle": {"$ref": "#/components/schemas/Cycle"}}}}
        with self.assertRaises(Unproved):
            SourceProof(cyclic, cyclic).closure(cyclic["components"]["schemas"]["Cycle"], cyclic)
        before, after = copy.deepcopy(self.base), copy.deepcopy(self.revision)
        for document in (before, after):
            document["components"]["schemas"]["CompatibilityEnvelope"]["properties"]["wide"] = {
                "type": "object", "properties": {
                    f"group{group}": {"type": "object", "properties": {
                        f"value{index}": {"type": "string"} for index in range(256)
                    }} for group in range(70)
                },
            }
        self.assertTrue(cbc.component_findings(self.diff, before, after))

    def test_duplicate_source_mapping_names_are_rejected_before_any_proof(self) -> None:
        text = json.dumps(self.base)
        declaration = '"minimum": 100, "maximum": 599'
        self.assertEqual(text.count(declaration), 1)
        malformed = text.replace(declaration, '"minimum": 100, "minimum": 101, "maximum": 599')
        source = self.specs.write(malformed, "duplicate-source.json")
        with self.assertRaisesRegex(PipelineError, "complete OpenAPI source parsing failed"):
            cbc.source_documents(source, self.revision_path)

    def test_source_numeric_precision_is_not_lost_before_constraint_comparison(self) -> None:
        before, after = tagged_pair(self.document)
        for document in (before, after):
            document["components"]["schemas"]["CompatibilityEnvelope"]["properties"]["note"] = {
                "type": "integer", "exclusiveMaximum": 1, "description": "1.0000000000000001",
            }
        revision = self.specs.write(json.dumps(after), "numeric-revision.json")

        def source_text(token: str, syntax: str) -> str:
            source = copy.deepcopy(before)
            marker = "__NUMERIC_SOURCE_LEXEME__"
            source["components"]["schemas"]["CompatibilityEnvelope"]["properties"]["note"]["exclusiveMaximum"] = marker
            if syntax == "yaml":
                text = "---\n" + "\n".join(f"{json.dumps(key)}: {json.dumps(value)}" for key, value in source.items())
            else:
                text = json.dumps(source)
            self.assertEqual(text.count(json.dumps(marker)), 1)
            # Inject the named field's raw numeral without first rounding it through a Python float.
            return text.replace(json.dumps(marker), token)

        for syntax in ("json", "yaml"):
            for token in ("1.0000000000000001", "9007199254740993", "1e-400", "1e400"):
                with self.subTest(syntax=syntax, non_roundtrippable_number=token):
                    base = self.specs.write(source_text(token, syntax), "numeric-base." + syntax)
                    with self.assertRaisesRegex(PipelineError, "source numeric"):
                        cbc.compare(base, revision, self.tools["oasdiff"])
            for token, expected in (("0.1", 0.1), ("1.5", 1.5), ("1.0", 1), ("1e0", 1), ("1e-6", 0.000001), ("-0", 0), ("9007199254740992", 9007199254740992)):
                with self.subTest(syntax=syntax, lossless_number=token):
                    base = self.specs.write(source_text(token, syntax), "numeric-control." + syntax)
                    parsed, _ = cbc.source_documents(base, revision)
                    note = parsed["components"]["schemas"]["CompatibilityEnvelope"]["properties"]["note"]
                    self.assertEqual(note["exclusiveMaximum"], expected)
                    self.assertEqual(note["description"], "1.0000000000000001")
        for token in (".inf", ".nan", "0x10", "012"):
            with self.subTest(unsupported_yaml_number=token):
                base = self.specs.write(source_text(token, "yaml"), "numeric-unsupported.yaml")
                with self.assertRaisesRegex(PipelineError, "source numeric"):
                    cbc.source_documents(base, revision)
        narrowed = self.specs.write(source_text("1.5", "json"), "numeric-narrowed.json")
        findings, _ = cbc.compare(narrowed, revision, self.tools["oasdiff"])
        self.assertTrue(findings, "a representable changed exclusive bound remains breaking too")

    def test_actual_ajv_old_domain_new_branch_and_positive_intersection_witnesses(self) -> None:
        for name in ("const guard", "inherited required object", "inherited discriminator", "positive subset"):
            with self.subTest(pair=name):
                before, after = variant(self.document, name)
                cases = [
                    {"name": "old stable", "schema": "CompatibilityEnvelope", "wire": {"kind": "stable", "status": 200}},
                    {"name": "old other", "schema": "CompatibilityEnvelope", "wire": {"kind": "other", "status": 202}},
                    {"name": "old negative", "schema": "CompatibilityEnvelope", "wire": {"kind": "stable", "status": 409}},
                    {"name": "new branch", "schema": "CompatibilityEnvelope", "wire": {"kind": "next", "status": 409}},
                    {"name": "new wrong constant", "schema": "CompatibilityEnvelope", "wire": {"kind": "next", "status": 200}},
                    {"name": "excluded global tag", "schema": "CompatibilityEnvelope", "wire": {"kind": "excluded", "status": 200}},
                ]
                for label, document, expected in (("base", before, [True, True, False, False, False, False]),
                                                  ("revision", after, [True, True, False, True, False, False])):
                    path = self.specs.write(json.dumps(document), f"witness-{label}.json")
                    completed = subprocess.run(
                        [node_executable(), str(ROOT / "scripts" / "tests" / "provider_schema.cjs"), str(path)],
                        cwd=ROOT, input=json.dumps(cases), capture_output=True, text=True, encoding="utf-8", check=False,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual([result["valid"] for result in json.loads(completed.stdout)], expected)

    def test_actual_accepted_fourteen_problem_shapes_and_import_two_code_intersection_are_preserved(self) -> None:
        accepted = subprocess.run(
            ["git", "show", "5b41d4580c85be3cc1617074c0f3052b1f7b02cd:spec/openapi.yaml"],
            cwd=ROOT, capture_output=True, check=True,
        ).stdout.decode("utf-8")
        base = self.specs.write(accepted, "actual-accepted.yaml")
        examples = error_examples()
        self.assertEqual(len(examples), 14)
        ordinary = [{"name": code, "schema": "ServiceProblemDetail", "wire": wire} for code, wire in examples.items()]
        imported = [{"name": code, "schema": "ImportRowError", "wire": {"problem": wire, "source_row": 1}}
                    for code, wire in examples.items()]
        expected = [True] * 14 + [code in ("validation_rejected", "import_mapping_required") for code in examples]
        for source in (base, SPEC):
            results = schema_results(ordinary + imported, spec=source)
            self.assertEqual([result["valid"] for result in results], expected)


if __name__ == "__main__":
    unittest.main()
