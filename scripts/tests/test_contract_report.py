"""Compatibility report (contracts#3, T-QA-02): closed schema, verdict and acceptance-eligibility rules.

Every rule test assesses its unmutated control report (in setUp), applies exactly one mutation and asserts
the exact rule IDs and subjects. Reports are in-memory. This repository's own runs are synthetic-fixture
reports with synthetic-<language> rows (synthetic_report_parts). The published-mode control with live rows
(fixture_report_parts) exists only to give the acceptance-eligibility rule a passing control; its rows carry
fixture names, not real repositories, and it is never written or published. Also covers the strict JSON
reader and the closed-world schema subset (scripts/contract_schema.py). Nothing is written inside this
repository.
"""

from __future__ import annotations

import copy
import shutil
import unittest
from pathlib import Path
from unittest import mock

from verification_support import (
    SYNTHETIC_INSTANT, fixture_report_parts, synthetic_digest, synthetic_report_parts, temporary_root,
)

import contract_gate  # noqa: E402 - verification_support puts scripts/ on sys.path
import contract_report  # noqa: E402
import contract_schema  # noqa: E402
import pl_contracts  # noqa: E402
from contract_report import assess_report, assess_report_file, build_report, not_run_compatibility  # noqa: E402
from contract_schema import DRAFT_07, SchemaUnsupported, StrictJsonError, check_schema, first_violation, parse_strict_json  # noqa: E402

PYTHON_ROW = "python/fixture-python-consumer"
PROVIDER_ROW = "provider/fixture-provider"
FINDING = {"id": "component-schema-property-removed", "location": "components/schemas/SyntheticProbe/properties/note",
           "detector": "component-guard"}
PROBLEM = "1 breaking change(s) against v1.2.2 and no breaking-change-acknowledgement.json"
WARNING_GATE = {"mode": "warn", "reason": "gate.warn_active", "warn_until": "2026-02-01T00:00:00.000Z",
                "state_sha256": synthetic_digest("warning gate state")}


def notes(items) -> list[tuple[str, str]]:
    return [(note.rule, note.subject) for note in items]


def rebuild(report: dict) -> dict:
    """What the builder declares for the (possibly mutated) parts of `report`."""
    return build_report(mode=report["mode"], contract=report["contract"], compatibility=report["compatibility"],
                        gate=report["gate"], provider=report["provider"], consumers=report["consumers"],
                        generated_at=contract_gate.parse_instant(report["generated_at"]))


def breaking(result: str) -> dict:
    return {"baseline": "v1.2.2", "revision_version": "1.2.3", "change_class": "breaking", "findings": [dict(FINDING)],
            "problems": [PROBLEM], "result": result}


class ReportTestCase(unittest.TestCase):
    """setUp builds the control from its parts and checks it is valid with the expected verdict and eligibility."""

    @staticmethod
    def parts() -> dict:
        return fixture_report_parts()

    EXPECTED = ("pass", True)

    def setUp(self) -> None:
        self.control = build_report(**self.parts())
        assessment = assess_report(self.control)
        self.assertEqual((notes(assessment.problems), notes(assessment.reasons), assessment.verdict, assessment.acceptance_eligible),
                         ([], [], *self.EXPECTED))

    def mutated(self, mutate) -> dict:
        report = copy.deepcopy(self.control)
        mutate(report)
        return report

    def assertProblems(self, expected: list[tuple[str, str]], mutate) -> None:
        """The hand-edited report is invalid with exactly these (rule, subject) problems and no verdict."""
        assessment = assess_report(self.mutated(mutate))
        self.assertEqual(notes(assessment.problems), expected)
        self.assertIsNone(assessment.verdict)
        self.assertIsNone(assessment.acceptance_eligible)

    def assertDerived(self, mutate, verdict: str, reasons: list[tuple[str, str]], eligible: bool, missing: tuple[str, ...] = ()) -> None:
        """The builder's report for the mutated parts is valid and declares exactly this verdict and eligibility."""
        report = rebuild(self.mutated(mutate))
        assessment = assess_report(report)
        self.assertEqual(notes(assessment.problems), [])
        self.assertEqual((assessment.verdict, notes(assessment.reasons), assessment.acceptance_eligible, assessment.missing_languages),
                         (verdict, reasons, eligible, missing))
        self.assertEqual((report["verdict"], report["acceptance_eligible"], report["missing_languages"]), (verdict, eligible, list(missing)))


class ReportVerdictTest(ReportTestCase):
    """Verdict and acceptance eligibility, on the published-mode control with live rows."""

    def test_valid_published_report_passes_and_is_acceptance_eligible(self) -> None:
        self.assertEqual(list(self.control), [
            "schema", "generated_at", "mode", "contract", "required_languages", "compatibility", "gate", "provider",
            "consumers", "missing_languages", "verdict", "acceptance_eligible"])
        self.assertEqual({key: self.control[key] for key in ("schema", "generated_at", "required_languages", "missing_languages",
                                                             "verdict", "acceptance_eligible")},
                         {"schema": "pennilogic-compat-report@1", "generated_at": SYNTHETIC_INSTANT,
                          "required_languages": ["kotlin", "typescript", "python"], "missing_languages": [],
                          "verdict": "pass", "acceptance_eligible": True})
        self.assertEqual(parse_strict_json(contract_schema.dumps(self.control).encode("ascii")), self.control)

    def test_synthetic_run_never_acceptance_eligible(self) -> None:
        with self.subTest("builder: synthetic-fixture mode, rows unchanged"):
            self.assertDerived(lambda report: report.update(mode="synthetic-fixture"), "pass", [], False)
        with self.subTest("validator: a synthetic-fixture report that claims eligibility"):
            self.assertProblems([("report.acceptance_eligible_inconsistent", "acceptance_eligible")],
                                lambda report: report.update(mode="synthetic-fixture"))
        with self.subTest("this repository's own run: synthetic-fixture mode and synthetic rows"):
            assessment = assess_report(build_report(**synthetic_report_parts()))
            self.assertEqual((notes(assessment.problems), assessment.verdict, assessment.acceptance_eligible), ([], "pass", False))

    def test_acceptance_needs_live_provider_and_every_live_consumer(self) -> None:
        mutations = {
            "synthetic provider": lambda report: report["provider"].update(name="synthetic-provider", mode="synthetic-fixture"),
            "one synthetic consumer": lambda report: report["consumers"][1].update(name="synthetic-typescript", mode="synthetic-fixture"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                self.assertDerived(mutate, "pass", [], False)

    def test_report_missing_language_row_fails(self) -> None:
        for index, language in enumerate(pl_contracts.LANGUAGES):
            with self.subTest(language):
                self.assertDerived(lambda report, index=index: report["consumers"].pop(index), "fail",
                                   [("report.required_language_missing", language)], False, (language,))

    def test_report_missing_python_row_fails(self) -> None:
        """Criterion 7: the Python row is required by language; an omission is a failure, never a pass."""
        with self.subTest("the only Python consumer row is filed under another language"):
            self.assertDerived(lambda report: report["consumers"][2].update(language="typescript"), "fail",
                               [("report.required_language_missing", "python")], False, ("python",))
        with self.subTest("validator: the omission declared as a pass"):
            self.assertProblems([("report.missing_languages_inconsistent", "missing_languages"), ("report.verdict_inconsistent", "verdict"),
                                 ("report.acceptance_eligible_inconsistent", "acceptance_eligible")],
                                lambda report: report["consumers"].pop(2))

    def test_not_run_consumer_row_fails(self) -> None:
        def not_run(report: dict) -> None:
            report["consumers"][2].update(mode="not_run", result="not_run", version=None, verified_contract_version=None)

        self.assertDerived(not_run, "fail", [("report.row_not_run", PYTHON_ROW)], False)

    def test_not_run_provider_or_compatibility_fails(self) -> None:
        with self.subTest("provider not run"):
            self.assertDerived(lambda report: report["provider"].update(mode="not_run", result="not_run", version=None,
                                                                        verified_contract_version=None),
                               "fail", [("report.row_not_run", PROVIDER_ROW)], False)
        with self.subTest("compatibility not run (eligibility is about liveness, the verdict still fails)"):
            self.assertDerived(lambda report: report.update(compatibility=not_run_compatibility("v1.2.2")), "fail",
                               [("report.compat_not_run", "compatibility")], True)

    def test_failed_rows_and_compatibility_fail_and_a_warning_warns(self) -> None:
        mutations = {
            "consumer failed": (lambda report: report["consumers"][0].update(result="fail"), "fail",
                                [("report.row_failed", "kotlin/fixture-kotlin-consumer")]),
            "provider failed": (lambda report: report["provider"].update(result="fail"), "fail", [("report.row_failed", PROVIDER_ROW)]),
            "unacknowledged break under the blocking gate": (lambda report: report.update(compatibility=breaking("fail")), "fail",
                                                             [("report.compat_failed", "compatibility")]),
            "unacknowledged break under an unexpired warning gate": (
                lambda report: report.update(compatibility=breaking("warn"), gate=dict(WARNING_GATE)), "warn",
                [("report.compat_warned", "compatibility")]),
        }
        for label, (mutate, verdict, reasons) in mutations.items():
            with self.subTest(label):
                self.assertDerived(mutate, verdict, reasons, True)

    def test_report_claims_unverified_consumer_version_refused(self) -> None:
        mutations = {
            PYTHON_ROW: lambda report: report["consumers"][2].update(verified_contract_version="1.2.2"),
            PROVIDER_ROW: lambda report: report["provider"].update(verified_contract_version="1.3.0"),
        }
        for subject, mutate in mutations.items():
            with self.subTest(subject):
                self.assertProblems([("report.contract_version_mismatch", subject)], mutate)

    def test_declared_values_must_equal_the_recomputed_ones(self) -> None:
        mutations = {
            ("report.verdict_inconsistent", "verdict"): lambda report: report.update(verdict="warn"),
            ("report.missing_languages_inconsistent", "missing_languages"): lambda report: report.update(missing_languages=["python"]),
            ("report.acceptance_eligible_inconsistent", "acceptance_eligible"): lambda report: report.update(acceptance_eligible=False),
        }
        for expected, mutate in mutations.items():
            with self.subTest(expected[0]):
                self.assertProblems([expected], mutate)


class ReportRuleTest(ReportTestCase):
    """Closed schema, bounds and consistency rules, on the published-mode control."""

    def test_schema_violations_refused(self) -> None:
        mutations = {
            "unknown key": (lambda report: report.update(signed_by="synthetic"), "/ fails additionalProperties"),
            "missing key": (lambda report: report.pop("gate"), "/gate fails required"),
            "other schema": (lambda report: report.update(schema="pennilogic-compat-report@2"), "/schema fails const"),
            "fewer required languages": (lambda report: report.update(required_languages=["kotlin", "typescript"]),
                                         "/required_languages fails const"),
            "unknown language": (lambda report: report["consumers"][2].update(language="java"), "/consumers/2/language fails enum"),
            "leading-zero tag": (lambda report: report["contract"].update(tag="v01.2.3"), "/contract/tag fails pattern"),
            "version with a trailing newline": (lambda report: report["contract"].update(version="1.2.3\n"),
                                                "/contract/version fails pattern"),
            "boolean spelled as text": (lambda report: report.update(acceptance_eligible="true"), "/acceptance_eligible fails type"),
            "control character in a problem": (lambda report: report["compatibility"]["problems"].append("bell\x07"),
                                               "/compatibility/problems/0 fails pattern"),
            "unknown row mode": (lambda report: report["provider"].update(mode="recorded-fixture"), "/provider/mode fails enum"),
            "impossible generation date": (lambda report: report.update(generated_at="2026-02-30T00:00:00.000Z"),
                                           "/generated_at fails instant"),
        }
        for label, (mutate, subject) in mutations.items():
            with self.subTest(label):
                self.assertProblems([("report.schema", subject)], mutate)

    def test_bounds_refused(self) -> None:
        for label, (bound, value, pointer) in {
            "consumer rows": ("MAX_CONSUMERS", 2, "/consumers"),
            "row name length": ("MAX_NAME", len("fixture-provider") - 1, "/provider/name"),
            "row version length": ("MAX_IDENTIFIER", len("0.0.0-fixture") - 1, "/provider/version"),
        }.items():
            with self.subTest(label), mock.patch.object(contract_report, bound, value):
                self.assertProblems([("report.bounds", pointer)], lambda report: None)

    def test_text_bounds_refused(self) -> None:
        report = rebuild(self.mutated(lambda report: report.update(compatibility=breaking("fail"))))
        self.assertEqual((notes(assess_report(report).problems), assess_report(report).verdict), ([], "fail"))
        self.assertGreater(len(PROBLEM), len(FINDING["location"]))
        for label, (bound, value, pointer) in {
            "finding location": ("MAX_TEXT", len(FINDING["location"]) - 1, "/compatibility/findings/0/location"),
            "problem text": ("MAX_TEXT", len(PROBLEM) - 1, "/compatibility/problems/0"),
            "finding count": ("MAX_FINDINGS", 0, "/compatibility/findings"),
            "problem count": ("MAX_PROBLEMS", 0, "/compatibility/problems"),
        }.items():
            with self.subTest(label), mock.patch.object(contract_report, bound, value):
                self.assertEqual(notes(assess_report(report).problems), [("report.bounds", pointer)])

    def test_contract_identity_refused(self) -> None:
        mutations = {
            "published without a tag": lambda report: report["contract"].update(tag=None),
            "tag of another version": lambda report: report["contract"].update(tag="v1.2.4"),
            "published without the tag commit": lambda report: report["contract"].update(commit=None),
            "published without the release manifest digest": lambda report: report["contract"].update(release_manifest_sha256=None),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                self.assertProblems([("report.contract_identity", "contract")], mutate)

    def test_duplicate_consumer_refused(self) -> None:
        self.assertProblems([("report.duplicate_consumer", "consumers/3")],
                            lambda report: report["consumers"].append(dict(report["consumers"][2])))

    def test_live_row_without_version_refused(self) -> None:
        self.assertProblems([("report.live_row_unidentified", PYTHON_ROW)], lambda report: report["consumers"][2].update(version=None))

    def test_live_row_with_a_synthetic_name_refused(self) -> None:
        self.assertProblems([("report.synthetic_row_name", "kotlin/synthetic-kotlin")],
                            lambda report: report["consumers"][0].update(name="synthetic-kotlin"))

    def test_compatibility_section_inconsistent_refused(self) -> None:
        mutations = {
            "breaking without findings": lambda report: report["compatibility"].update(change_class="breaking"),
            "findings without a breaking class": lambda report: report["compatibility"].update(findings=[dict(FINDING)]),
            "pass with problems": lambda report: report["compatibility"].update(problems=[PROBLEM]),
            "fail without problems": lambda report: report["compatibility"].update(result="fail"),
            "warn under the blocking gate": lambda report: report.update(compatibility=breaking("warn")),
            "fail under a warning gate": lambda report: report.update(compatibility=breaking("fail"), gate=dict(WARNING_GATE)),
            "class none against another version": lambda report: report["compatibility"].update(change_class="none"),
            "revision of another version": lambda report: report["compatibility"].update(revision_version="1.2.4"),
            "a run without a baseline": lambda report: report["compatibility"].update(baseline=None),
            "not run with findings": lambda report: report.update(compatibility=dict(not_run_compatibility(), findings=[dict(FINDING)])),
            "not run with a class": lambda report: report.update(compatibility=dict(not_run_compatibility(), change_class="additive")),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                self.assertProblems([("report.compat_inconsistent", "compatibility")], mutate)

    def test_gate_section_inconsistent_refused(self) -> None:
        later = "2026-01-02T03:04:05.001Z"
        with self.subTest("boundary control: a warning that expires 1 ms after generation is consistent"):
            self.assertDerived(lambda report: report.update(gate=dict(WARNING_GATE, warn_until=later)), "pass", [], True)
        mutations = {
            "warning reason under the blocking mode": lambda report: report["gate"].update(reason="gate.warn_active"),
            "warning mode with a blocking reason": lambda report: report["gate"].update(mode="warn", warn_until=later),
            "warning that expired at generation": lambda report: report.update(gate=dict(WARNING_GATE, warn_until=SYNTHETIC_INSTANT)),
            "expired reason before the expiry": lambda report: report["gate"].update(reason="gate.warn_expired", warn_until=later),
            "blocking gate naming an expiry": lambda report: report["gate"].update(warn_until=later),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                self.assertProblems([("report.gate_inconsistent", "gate")], mutate)

    def test_report_file_refusals(self) -> None:
        root = temporary_root()
        self.addCleanup(shutil.rmtree, root, True)
        path = root / "report.json"
        path.write_text(contract_schema.dumps(self.control), encoding="ascii", newline="\n")
        self.assertEqual((notes(assess_report_file(path).problems), assess_report_file(path).verdict), ([], "pass"))
        text = path.read_text(encoding="ascii")
        mutations = {
            "not JSON": (text[: len(text) // 2], "report.not_json"),
            "duplicate key": (text.replace("{\n", '{\n  "verdict": "pass",\n', 1), "report.not_json"),
            "byte order mark": ("\ufeff" + text, "report.not_json"),
        }
        for label, (content, rule) in mutations.items():
            with self.subTest(label):
                path.write_text(content, encoding="utf-8", newline="\n")
                self.assertEqual(notes(assess_report_file(path).problems), [(rule, "/")])
        with self.subTest("too large"):
            path.write_text(text, encoding="ascii", newline="\n")
            with mock.patch.object(contract_report, "MAX_REPORT_BYTES", len(text) - 1):
                self.assertEqual(notes(assess_report_file(path).problems), [("report.bounds", "/")])
        with self.subTest("missing or not a file"):
            with self.assertRaises(contract_schema.InputMissing):
                assess_report_file(root / "absent.json")
            with self.assertRaises(contract_schema.InputUnavailable):
                assess_report_file(root)


class SyntheticReportRuleTest(ReportTestCase):
    """Row naming and status rules, on the synthetic-fixture control this repository's runs produce."""

    @staticmethod
    def parts() -> dict:
        return synthetic_report_parts()

    EXPECTED = ("pass", False)

    def test_synthetic_row_must_carry_its_synthetic_name(self) -> None:
        mutations = {
            "kotlin/fixture-kotlin-consumer": lambda report: report["consumers"][0].update(name="fixture-kotlin-consumer"),
            "kotlin/synthetic-python": lambda report: report["consumers"][0].update(name="synthetic-python"),
            PROVIDER_ROW: lambda report: report["provider"].update(name="fixture-provider"),
        }
        for subject, mutate in mutations.items():
            with self.subTest(subject):
                self.assertProblems([("report.synthetic_row_name", subject)], mutate)

    def test_row_status_inconsistent_refused(self) -> None:
        mutations = {
            "not_run mode with a result": lambda report: report["consumers"][2].update(mode="not_run"),
            "a mode without a result": lambda report: report["consumers"][2].update(result="not_run"),
            "a not_run row naming a verified version": lambda report: report["consumers"][2].update(mode="not_run", result="not_run"),
            "a not_run row naming its version": lambda report: report["consumers"][2].update(
                mode="not_run", result="not_run", verified_contract_version=None, version="0.0.0-fixture"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                self.assertProblems([("report.row_status_inconsistent", "python/synthetic-python")], mutate)


class SchemaSubsetTest(unittest.TestCase):
    """The strict JSON reader and the closed-world draft-07 subset behind every verification file."""

    @staticmethod
    def nodes(node: dict):
        yield node
        for key in ("properties", "definitions"):
            for child in node.get(key, {}).values():
                yield from SchemaSubsetTest.nodes(child)
        if "items" in node:
            yield from SchemaSubsetTest.nodes(node["items"])

    def test_verification_files_are_closed_canonical_and_inside_the_subset(self) -> None:
        paths = sorted(contract_gate.VERIFICATION.glob("*.json"))
        self.assertEqual([path.name for path in paths], ["compat-gate.json", "compat-gate.schema.v1.json", "compat-report.schema.v1.json"])
        for path in paths:
            data = path.read_bytes()
            self.assertEqual(data.decode("ascii"), contract_schema.dumps(parse_strict_json(data)), f"{path.name} is not canonical")
        for path in (contract_gate.GATE_SCHEMA, contract_report.REPORT_SCHEMA):
            schema = contract_schema.load_schema(path)
            for node in self.nodes(schema):
                if node.get("type") == "object":
                    self.assertIs(node.get("additionalProperties"), False, path.name)
                    self.assertEqual(node.get("required"), list(node.get("properties", {})), path.name)
                pattern = node.get("pattern")
                if pattern is not None:
                    self.assertTrue(pattern.startswith("^") and pattern.endswith("(?![\\s\\S])"), pattern)
                    self.assertNotRegex(pattern.replace("[\\s\\S]", ""), r"\\[dwsDWS]|\$", pattern)
        report = contract_schema.load_schema(contract_report.REPORT_SCHEMA)
        self.assertEqual(report["properties"]["required_languages"]["const"], list(contract_report.REQUIRED_LANGUAGES))
        self.assertEqual(contract_report.REQUIRED_LANGUAGES, pl_contracts.LANGUAGES)
        self.assertEqual(report["definitions"]["gate"]["properties"]["reason"]["enum"], list(contract_gate.DECISIONS))
        self.assertEqual(list(report["definitions"]["gate"]["properties"]),
                         list(contract_gate.GateDecision("block", "gate.block", None, None).as_dict()))

    def test_schema_outside_the_subset_refused(self) -> None:
        def valid() -> dict:
            return {"$schema": DRAFT_07, "type": "object", "additionalProperties": False, "required": ["a"],
                    "properties": {"a": {"$ref": "#/definitions/text"}},
                    "definitions": {"text": {"type": "string", "pattern": "^a(?![\\s\\S])"}}}

        check_schema(valid())
        mutations = {
            "format keyword": lambda schema: schema["definitions"]["text"].update(format="date-time"),
            "additionalProperties true": lambda schema: schema.update(additionalProperties=True),
            "$ref with a sibling": lambda schema: schema["properties"]["a"].update(type="string"),
            "$ref to a missing definition": lambda schema: schema["properties"]["a"].update({"$ref": "#/definitions/other"}),
            "unknown type": lambda schema: schema["definitions"]["text"].update(type="text"),
            "another draft": lambda schema: schema.update({"$schema": "https://json-schema.org/draft/2020-12/schema"}),
            "invalid pattern": lambda schema: schema["definitions"]["text"].update(pattern="("),
            "definitions below the root": lambda schema: schema["definitions"]["text"].update(definitions={}),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                schema = valid()
                mutate(schema)
                with self.assertRaises(SchemaUnsupported):
                    check_schema(schema)

    def test_strict_json_refusals(self) -> None:
        self.assertEqual(parse_strict_json(b'{"a": [1, 1.5, "x", null, true]}'), {"a": [1, 1.5, "x", None, True]})
        for label, data in {
            "byte order mark": b"\xef\xbb\xbf{}",
            "duplicate key": b'{"a": 1, "a": 2}',
            "NaN": b'{"a": NaN}',
            "Infinity": b"[Infinity]",
            "float overflowing to infinity": b"[1e400]",
            "not UTF-8": b'["\xff"]',
            "truncated": b'{"a": ',
        }.items():
            with self.subTest(label):
                with self.assertRaises(StrictJsonError):
                    parse_strict_json(data)

    def test_json_equality_and_types_follow_json(self) -> None:
        self.assertIsNone(first_violation(1.0, {"const": 1}))
        self.assertIsNone(first_violation([1, True], {"type": "array", "uniqueItems": True}))
        for instance, schema in ((True, {"enum": [1]}), (1, {"const": True}), (1, {"type": "boolean"}), (True, {"type": "integer"}),
                                 ([1, 1.0], {"type": "array", "uniqueItems": True}), ({"a": 1}, {"const": {"a": True}})):
            with self.subTest(instance=instance, schema=schema):
                self.assertIsNotNone(first_violation(instance, schema))


class RuleCoverageTest(unittest.TestCase):
    def test_every_report_rule_is_asserted(self) -> None:
        source = Path(__file__).read_text(encoding="utf-8")
        self.assertEqual([rule for rule in contract_report.RULES if f'"{rule}"' not in source], [])


if __name__ == "__main__":
    unittest.main()
