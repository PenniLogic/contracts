"""Warn/block gate state and compatibility orchestration (contracts#3, T-QA-02).

Gate: the committed state, the expiry boundary (warn at warn_until - 1 ms, block at warn_until), a warning
without expiry, and every unusable state resolving to block. Compatibility: the existing
check_breaking_changes detectors and acknowledgement protocol, imported and composed over already-parsed
synthetic inputs (complete OpenAPI 3.1.0 documents and oasdiff-shaped output, built in memory), with a drift
guard that runs check_breaking_changes.compare itself over the same inputs. Every negative runs its passing
control first, applies exactly one mutation and asserts the exact rule ID. No network, no Node and no
oasdiff; nothing is written inside this repository.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import verification_support as support
from verification_support import PROBE_SCHEMA, probe_path, synthetic_digest, synthetic_document, temporary_root, version_diff

import check_breaking_changes as cbc  # noqa: E402 - verification_support puts scripts/ on sys.path
import contract_compat  # noqa: E402
import contract_gate  # noqa: E402
import contract_report  # noqa: E402
import contract_schema  # noqa: E402
from contract_compat import CompatRefused, DetectorInputs, check_compatibility, classify, read_acknowledgement  # noqa: E402
from contract_gate import GateDecision, GateRefused, decide, parse_gate_state, parse_instant, resolve_gate  # noqa: E402

BASELINE = "v1.2.3"
UNTIL = "2026-11-01T00:00:00.000Z"
REMOVED = f"components/schemas/{PROBE_SCHEMA}/properties/note"
BLOCK = GateDecision("block", "gate.block", None, synthetic_digest("blocking gate state"))
WARN = GateDecision("warn", "gate.warn_active", UNTIL, synthetic_digest("warning gate state"))
WARN_STATE = {"schema": "pennilogic-compat-gate@1", "mode": "warn", "warn_until": UNTIL}
REASON = "Synthetic expand-and-contract fixture: every consumer migrated first (verification tests only)."


def state_bytes(value: object) -> bytes:
    return contract_schema.dumps(value).encode("ascii")


def at(instant: str, milliseconds: int = 0) -> datetime:
    return parse_instant(instant) + timedelta(milliseconds=milliseconds)


def inputs(revision: object, *, breaking: object = None, diff: object = None, base: object = None,
           baseline: str = BASELINE) -> DetectorInputs:
    return DetectorInputs(baseline, synthetic_document(baseline[1:]) if base is None else base, revision,
                          [] if breaking is None else breaking, {} if diff is None else diff)


def identical_inputs() -> DetectorInputs:
    return inputs(synthetic_document("1.2.3"))


def additive_inputs() -> DetectorInputs:
    """An optional response field and a new endpoint, MINOR bump."""
    revision = synthetic_document("1.3.0", properties=("amount", "currency", "note", "memo"), paths={"/probe": probe_path()})
    diff = {**version_diff("1.2.3", "1.3.0"), "paths": {"added": ["/probe"]},
            "components": {"schemas": {"modified": {PROBE_SCHEMA: {"properties": {"added": ["memo"]}}}}}}
    return inputs(revision, diff=diff)


def removal_inputs(version: str = "2.0.0", baseline: str = BASELINE) -> DetectorInputs:
    """The planted breaking change: the response field `note` is removed (oasdiff and the component guard both see it)."""
    base = synthetic_document(baseline[1:], paths={"/probe": probe_path()})
    revision = synthetic_document(version, properties=("amount", "currency"), paths={"/probe": probe_path()})
    breaking = [{"id": "response-property-removed", "level": 3, "operation": "GET", "path": "/probe", "source": "synthetic",
                 "text": "removed the optional property 'note' from the response with the '200' status"}]
    diff = {**version_diff(baseline[1:], version),
            "components": {"schemas": {"modified": {PROBE_SCHEMA: {"properties": {"deleted": ["note"]}}}}}}
    return inputs(revision, breaking=breaking, diff=diff, base=base, baseline=baseline)


def warning_inputs(level: int = 2) -> DetectorInputs:
    """An oasdiff change at WARN level (2) only; level 3 makes the same change breaking."""
    base = synthetic_document("1.2.3", paths={"/probe": probe_path()})
    revision = synthetic_document("1.3.0", paths={"/probe": probe_path()})
    breaking = [{"id": "response-optional-property-removed", "level": level, "operation": "GET", "path": "/probe",
                 "text": "removed the optional property 'memo' from the response with the '200' status"}]
    return inputs(revision, breaking=breaking, diff=version_diff("1.2.3", "1.3.0"), base=base)


def duplicate_key_inputs() -> DetectorInputs:
    """oasdiff reports one removal twice (for example once per media type): same id and location, other text."""
    data = removal_inputs()
    change = data.oasdiff_breaking[0]
    return dataclasses.replace(data, oasdiff_breaking=[change, dict(change, text=change["text"].replace("'200'", "'206'"))])


def acknowledgement_for(findings: list, baseline: str = BASELINE, target: str = "2.0.0") -> dict:
    return {"baseline": baseline, "target_version": target,
            "acknowledged": [{"id": finding.id, "location": finding.location, "reason": REASON} for finding in findings]}


def report_for(result: contract_compat.CompatResult) -> dict:
    """A synthetic-fixture report (contract_report.build_report) whose compatibility section and gate are `result`'s."""
    parts = support.synthetic_report_parts()
    version = result.revision_version
    parts["contract"] = dict(parts["contract"], version=version, tag=f"v{version}")
    for row in [parts["provider"], *parts["consumers"]]:
        row["verified_contract_version"] = version
    parts.update(compatibility=result.section(), gate=result.gate.as_dict())
    return contract_report.build_report(**parts)


class GateStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = temporary_root()
        self.addCleanup(shutil.rmtree, self.root, True)

    def write(self, data: bytes, name: str = "compat-gate.json") -> Path:
        path = self.root / name
        path.write_bytes(data)
        return path

    def test_committed_gate_state_blocks(self) -> None:
        state = contract_gate.load_gate_state(contract_gate.GATE_STATE)
        self.assertEqual((state.mode, state.warn_until, state.sha256),
                         ("block", None, support.sha256_bytes(contract_gate.GATE_STATE.read_bytes())))
        decision = resolve_gate(contract_gate.GATE_STATE, at(UNTIL))
        self.assertEqual(decision.as_dict(), {"mode": "block", "reason": "gate.block", "warn_until": None, "state_sha256": state.sha256})

    def test_warn_gate_past_expiry_blocks(self) -> None:
        path = self.write(state_bytes(WARN_STATE))
        control = resolve_gate(path, at(UNTIL, -1))
        self.assertEqual((control.mode, control.reason, control.warn_until), ("warn", "gate.warn_active", UNTIL))
        for label, now in (("at warn_until", at(UNTIL)), ("a day later", at(UNTIL, 86_400_000))):
            with self.subTest(label):
                decision = resolve_gate(path, now)
                self.assertEqual((decision.mode, decision.reason, decision.warn_until), ("block", "gate.warn_expired", UNTIL))
        with self.subTest("an unacknowledged break warns 1 ms before the expiry and fails at it"):
            self.assertEqual(check_compatibility(removal_inputs(), control).result, "warn")
            self.assertEqual(check_compatibility(removal_inputs(), resolve_gate(path, at(UNTIL))).result, "fail")

    def test_warn_gate_without_expiry_refused(self) -> None:
        self.assertEqual(parse_gate_state(state_bytes(WARN_STATE)).mode, "warn")
        mutations = {
            "warn_until null": dict(WARN_STATE, warn_until=None),
            "warn_until absent": {key: value for key, value in WARN_STATE.items() if key != "warn_until"},
        }
        for label, state in mutations.items():
            with self.subTest(label):
                with self.assertRaises(GateRefused) as caught:
                    parse_gate_state(state_bytes(state))
                self.assertEqual(caught.exception.rule, "gate.warn_until_missing")
                decision = resolve_gate(self.write(state_bytes(state), label.replace(" ", "-") + ".json"), at(UNTIL, -1))
                self.assertEqual((decision.mode, decision.reason, decision.warn_until), ("block", "gate.warn_until_missing", None))

    def test_gate_never_defaults_to_warn(self) -> None:
        self.assertEqual(resolve_gate(self.write(state_bytes(WARN_STATE)), at(UNTIL, -1)).mode, "warn")
        duplicate = state_bytes(WARN_STATE).replace(b"{\n", b'{\n  "mode": "block",\n', 1)
        cases = {
            "no path": (None, "gate.missing"),
            "absent file": (self.root / "absent.json", "gate.missing"),
            "a directory": (self.root, "gate.unreadable"),
            "not JSON": (self.write(b'{"mode": "warn"', "truncated.json"), "gate.malformed"),
            "duplicate key": (self.write(duplicate, "duplicate.json"), "gate.malformed"),
            "unknown mode": (self.write(state_bytes(dict(WARN_STATE, mode="Warn")), "mode.json"), "gate.malformed"),
            "unknown key": (self.write(state_bytes(dict(WARN_STATE, approved_by="synthetic")), "key.json"), "gate.malformed"),
            "other schema": (self.write(state_bytes(dict(WARN_STATE, schema="pennilogic-compat-gate@2")), "schema.json"), "gate.malformed"),
            "not an object": (self.write(b'["warn"]\n', "list.json"), "gate.malformed"),
            "blocking gate naming an expiry": (self.write(state_bytes(dict(WARN_STATE, mode="block")), "block.json"),
                                               "gate.warn_until_unexpected"),
        }
        for label, (path, rule) in cases.items():
            with self.subTest(label):
                decision = resolve_gate(path, at(UNTIL, -1))
                self.assertEqual((decision.mode, decision.reason, decision.warn_until), ("block", rule, None))
        with self.subTest("larger than its bound"):
            path = self.write(state_bytes(WARN_STATE), "large.json")
            with mock.patch.object(contract_gate, "MAX_STATE_BYTES", 8):
                self.assertEqual(resolve_gate(path, at(UNTIL, -1)).reason, "gate.unreadable")

    def test_warn_until_must_be_a_real_utc_instant(self) -> None:
        self.assertEqual(parse_gate_state(state_bytes(WARN_STATE)).warn_until, datetime(2026, 11, 1, tzinfo=timezone.utc))
        for value in ("2026-11-01", "2026-11-01T00:00:00Z", "2026-11-01T00:00:00.000+00:00", "2026-11-01T00:00:00.000z",
                      "2026-02-30T00:00:00.000Z", "2026-11-01T24:00:00.000Z", "2026-11-01T00:00:60.000Z",
                      " 2026-11-01T00:00:00.000Z", "2026-11-01T00:00:00.000Z\n", 20261101, True):
            with self.subTest(value=value):
                with self.assertRaises(GateRefused) as caught:
                    parse_gate_state(state_bytes(dict(WARN_STATE, warn_until=value)))
                self.assertEqual(caught.exception.rule, "gate.warn_until_invalid")

    def test_clock_must_be_timezone_aware_utc(self) -> None:
        state = parse_gate_state(state_bytes(WARN_STATE))
        self.assertEqual(decide(state, at(UNTIL, -1)).mode, "warn")
        for label, now in (("naive", datetime(2026, 10, 1)),
                           ("an offset other than UTC", datetime(2026, 10, 1, tzinfo=timezone(timedelta(hours=5, minutes=30)))),
                           ("not a datetime", "2026-10-01T00:00:00.000Z")):
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    decide(state, now)  # type: ignore[arg-type]
                with self.assertRaises(ValueError):
                    resolve_gate(contract_gate.GATE_STATE, now)  # type: ignore[arg-type]


class CompatibilityTest(unittest.TestCase):
    def test_identical_documents_are_class_none_and_pass(self) -> None:
        result = check_compatibility(identical_inputs(), BLOCK)
        self.assertEqual((result.baseline, result.revision_version, result.change_class, result.findings, result.problems, result.result),
                         (BASELINE, "1.2.3", "none", (), (), "pass"))

    def test_additive_optional_field_and_new_endpoint_classified_additive_and_pass(self) -> None:
        result = check_compatibility(additive_inputs(), BLOCK)
        self.assertEqual((result.change_class, result.findings, result.problems, result.result), ("additive", (), (), "pass"))

    def test_removed_response_field_without_acknowledgement_fails(self) -> None:
        """The planted breaking change fails unless the expand-and-contract protocol was followed."""
        self.assertEqual(check_compatibility(identical_inputs(), BLOCK).result, "pass")
        result = check_compatibility(removal_inputs(), BLOCK)
        keys = {finding.key() for finding in result.findings}
        self.assertIn(("component-schema-property-removed", REMOVED), keys)
        self.assertIn(("response-property-removed", "GET /probe"), keys)
        self.assertEqual((result.change_class, result.result), ("breaking", "fail"))
        self.assertEqual(result.problems, (f"{len(result.findings)} breaking change(s) against {BASELINE} and no "
                                           "breaking-change-acknowledgement.json",))

    def test_removed_field_with_ack_and_version_bump_passes(self) -> None:
        findings, _ = classify(removal_inputs())
        self.assertEqual(check_compatibility(removal_inputs(), BLOCK).result, "fail")
        result = check_compatibility(removal_inputs(), BLOCK, acknowledgement_for(findings))
        self.assertEqual((result.change_class, result.problems, result.result), ("breaking", (), "pass"))

    def test_acknowledged_break_without_the_required_bump_fails(self) -> None:
        """Boundary pairs of version_bump_ok: from 1.x a MAJOR bump, from 0.y a MINOR bump."""
        for baseline, enough, short, message in (
                ("v1.2.3", "2.0.0", "1.3.0", "baseline v1.2.3: a breaking change needs a MAJOR bump; info.version is 1.3.0"),
                ("v0.4.1", "0.5.0", "0.4.2",
                 "pre-1.0 baseline v0.4.1: a breaking change needs a MINOR (or MAJOR) bump; info.version is 0.4.2")):
            with self.subTest(baseline):
                findings, _ = classify(removal_inputs(enough, baseline))
                control = check_compatibility(removal_inputs(enough, baseline), BLOCK, acknowledgement_for(findings, baseline, enough))
                self.assertEqual((control.problems, control.result), ((), "pass"))
                findings, _ = classify(removal_inputs(short, baseline))
                result = check_compatibility(removal_inputs(short, baseline), BLOCK, acknowledgement_for(findings, baseline, short))
                self.assertEqual((result.problems, result.result), ((message,), "fail"))

    def test_stale_acknowledgement_baseline_fails(self) -> None:
        findings, _ = classify(removal_inputs())
        self.assertEqual(check_compatibility(removal_inputs(), BLOCK, acknowledgement_for(findings)).result, "pass")
        result = check_compatibility(removal_inputs(), BLOCK, acknowledgement_for(findings, baseline="v1.2.2"))
        self.assertEqual((result.problems, result.result), ((
            "breaking-change-acknowledgement.json acknowledges baseline 'v1.2.2' but the comparison baseline is 'v1.2.3'; "
            "a stale acknowledgement never carries over",), "fail"))

    def test_partial_or_blanket_acknowledgement_fails(self) -> None:
        findings, _ = classify(removal_inputs())
        self.assertEqual(check_compatibility(removal_inputs(), BLOCK, acknowledgement_for(findings)).result, "pass")
        component = next(finding for finding in findings if finding.key() == ("component-schema-property-removed", REMOVED))
        partial = acknowledgement_for([finding for finding in findings if finding is not component])
        blanket = acknowledgement_for(findings)
        blanket["acknowledged"].append({"id": "component-schemas-removed", "location": "components/schemas/Other", "reason": REASON})
        for label, acknowledgement, problem in (
                ("partial", partial, f"unacknowledged: [component-schema-property-removed] {REMOVED} - {component.text}"),
                ("blanket", blanket, "acknowledgement names a change that was not detected: [component-schemas-removed] "
                                     "components/schemas/Other; remove it (blanket acknowledgements are refused)")):
            with self.subTest(label):
                result = check_compatibility(removal_inputs(), BLOCK, acknowledgement)
                self.assertEqual((result.problems, result.result), ((problem,), "fail"))

    def test_acknowledgement_without_break_fails(self) -> None:
        self.assertEqual(check_compatibility(identical_inputs(), BLOCK).result, "pass")
        result = check_compatibility(identical_inputs(), BLOCK, {"baseline": BASELINE, "acknowledged": []})
        self.assertEqual((result.problems, result.result), ((
            f"breaking-change-acknowledgement.json is present but no breaking change was detected against {BASELINE}; "
            "remove the file so it cannot pre-approve a future break",), "fail"))

    def test_warning_gate_downgrades_problems_to_warn(self) -> None:
        blocked = check_compatibility(removal_inputs(), BLOCK)
        self.assertEqual(blocked.result, "fail")
        warned = check_compatibility(removal_inputs(), WARN)
        self.assertEqual((warned.result, warned.change_class, warned.problems, warned.gate), ("warn", "breaking", blocked.problems, WARN))

    def test_oasdiff_warning_level_is_not_breaking(self) -> None:
        result = check_compatibility(warning_inputs(2), BLOCK)
        self.assertEqual((result.change_class, result.findings, result.result), ("additive", (), "pass"))
        self.assertEqual(result.warnings, ("warning [response-optional-property-removed] GET /probe: removed the optional "
                                           "property 'memo' from the response with the '200' status",))
        breaking = check_compatibility(warning_inputs(3), BLOCK)
        self.assertEqual(([finding.key() for finding in breaking.findings], breaking.result),
                         ([("response-optional-property-removed", "GET /probe")], "fail"))

    def test_classification_matches_check_breaking_changes_compare(self) -> None:
        """Drift guard: compare() itself, fed the same parsed inputs, finds exactly what classify() finds."""
        for label, data in (("identical", identical_inputs()), ("additive", additive_inputs()), ("removal", removal_inputs()),
                            ("oasdiff warning", warning_inputs()), ("one key reported twice", duplicate_key_inputs())):
            with self.subTest(label):
                def documents(base: Path, revision: Path, data: DetectorInputs = data) -> tuple[object, object]:
                    return copy.deepcopy(data.base_document), copy.deepcopy(data.revision_document)

                def oasdiff(tool: str, subcommand: str, base: Path, revision: Path, data: DetectorInputs = data) -> object:
                    return copy.deepcopy(data.oasdiff_breaking if subcommand == "breaking" else data.oasdiff_diff)

                with mock.patch.object(cbc, "source_documents", documents), mock.patch.object(cbc, "oasdiff_json", oasdiff):
                    expected, expected_warnings = cbc.compare(Path("base.yaml"), Path("revision.yaml"), "oasdiff")
                findings, warnings = classify(data)
                self.assertEqual([finding.as_dict() for finding in findings], [finding.as_dict() for finding in expected])
                self.assertEqual(warnings, expected_warnings)
                self.assertEqual(len({finding.key() for finding in findings}), len(findings))
                if label in ("removal", "one key reported twice"):
                    self.assertEqual(len(findings), len(classify(removal_inputs())[0]))
                    self.assertGreaterEqual(len(findings), 2)

    def test_malformed_detector_output_refused(self) -> None:
        self.assertEqual(check_compatibility(removal_inputs(), BLOCK).result, "fail")
        change = removal_inputs().oasdiff_breaking[0]
        nested: dict = {}
        node = nested
        for _ in range(5000):
            node["properties"] = {"modified": {"x": {}}}
            node = node["properties"]["modified"]["x"]
        mutations = {
            "breaking output not a list": {"oasdiff_breaking": {"changes": [change]}},
            "change without a level": {"oasdiff_breaking": [{key: value for key, value in change.items() if key != "level"}]},
            "level as text": {"oasdiff_breaking": [dict(change, level="3")]},
            "level as a boolean": {"oasdiff_breaking": [dict(change, level=True)]},
            "level out of range": {"oasdiff_breaking": [dict(change, level=4)]},
            "change without an id": {"oasdiff_breaking": [{key: value for key, value in change.items() if key != "id"}]},
            "location that is not text": {"oasdiff_breaking": [dict(change, operation=1)]},
            "diff not an object": {"oasdiff_diff": []},
            "diff the classifier cannot compare": {"oasdiff_diff": {"components": {"schemas": {"modified": {
                PROBE_SCHEMA: {"maxLength": {"from": "a", "to": 1}}}}}}},
            "diff nested beyond the recursion limit": {"oasdiff_diff": {"components": {"schemas": {"modified": {PROBE_SCHEMA: nested}}}}},
        }
        for label, change_fields in mutations.items():
            with self.subTest(label):
                with self.assertRaises(CompatRefused) as caught:
                    check_compatibility(dataclasses.replace(removal_inputs(), **change_fields), BLOCK)
                self.assertEqual(caught.exception.rule, "compat.detector_output_invalid")

    def test_identical_documents_with_detector_changes_refused(self) -> None:
        self.assertEqual(check_compatibility(identical_inputs(), BLOCK).result, "pass")
        with self.assertRaises(CompatRefused) as caught:
            check_compatibility(dataclasses.replace(identical_inputs(), oasdiff_diff=version_diff("1.2.2", "1.2.3")), BLOCK)
        self.assertEqual(caught.exception.rule, "compat.detector_output_invalid")

    def test_baseline_and_document_versions_refused(self) -> None:
        self.assertEqual(check_compatibility(identical_inputs(), BLOCK).result, "pass")
        without_info = synthetic_document("1.2.3")
        del without_info["info"]
        mutations = {
            "leading-zero baseline": ({"baseline": "v01.2.3"}, "compat.baseline_invalid"),
            "branch name as baseline": ({"baseline": "main"}, "compat.baseline_invalid"),
            "baseline with a trailing newline": ({"baseline": "v1.2.3\n"}, "compat.baseline_invalid"),
            "base document without info": ({"base_document": without_info}, "compat.document_invalid"),
            "revision document not an object": ({"revision_document": []}, "compat.document_invalid"),
            "revision version not MAJOR.MINOR.PATCH": ({"revision_document": synthetic_document("1.3")}, "compat.version_invalid"),
            "revision version with a leading zero": ({"revision_document": synthetic_document("1.03.0")}, "compat.version_invalid"),
            "base document of another version": ({"base_document": synthetic_document("1.2.2")}, "compat.baseline_version_mismatch"),
        }
        for label, (fields, rule) in mutations.items():
            with self.subTest(label):
                with self.assertRaises(CompatRefused) as caught:
                    check_compatibility(dataclasses.replace(identical_inputs(), **fields), BLOCK)
                self.assertEqual(caught.exception.rule, rule)

    def test_acknowledgement_file_refusals(self) -> None:
        root = temporary_root()
        self.addCleanup(shutil.rmtree, root, True)
        findings, _ = classify(removal_inputs())
        path = root / "breaking-change-acknowledgement.json"
        text = json.dumps(acknowledgement_for(findings))
        path.write_text(text, encoding="utf-8", newline="\n")
        acknowledgement = read_acknowledgement(path)
        self.assertEqual(check_compatibility(removal_inputs(), BLOCK, acknowledgement, path.name).result, "pass")
        stale_first = text.replace("{", '{"baseline": "v1.2.2", ', 1)
        # check_breaking_changes alone (json.loads) keeps the last of the duplicate keys:
        self.assertEqual(cbc.load_acknowledgement(self.write(root, stale_first))["baseline"], BASELINE)
        too_deep = json.dumps(dict(acknowledgement_for(findings), acknowledged=None)).replace("null", "[" * 100_000 + "]" * 100_000)
        mutations = {
            "absent": lambda: root / "absent.json",
            "a directory": lambda: root,
            "not JSON": lambda: self.write(root, "{"),
            "no acknowledged list": lambda: self.write(root, json.dumps({"baseline": BASELINE})),
            "an entry without a reason": lambda: self.write(root, json.dumps(
                {"baseline": BASELINE, "acknowledged": [{"id": "component-schema-property-removed", "location": REMOVED}]})),
            "a duplicate key (a reviewer may read the first value)": lambda: self.write(root, stale_first),
            "nesting beyond the parser's limit": lambda: self.write(root, too_deep),
        }
        for label, make in mutations.items():
            with self.subTest(label):
                with self.assertRaises(CompatRefused) as caught:
                    read_acknowledgement(make())
                self.assertEqual(caught.exception.rule, "compat.acknowledgement_invalid")
        with self.subTest("larger than its bound"), mock.patch.object(contract_compat, "MAX_ACKNOWLEDGEMENT_BYTES", 8):
            with self.assertRaises(CompatRefused) as caught:
                read_acknowledgement(path)
            self.assertEqual(caught.exception.rule, "compat.acknowledgement_invalid")
        protocol_reading = cbc.load_acknowledgement
        with self.subTest("the protocol's reading differs from the strict one (the file changed in between)"), \
                mock.patch.object(cbc, "load_acknowledgement", lambda name: dict(protocol_reading(name), baseline="v1.2.2")):
            with self.assertRaises(CompatRefused) as caught:
                read_acknowledgement(path)
            self.assertEqual(caught.exception.rule, "compat.acknowledgement_invalid")

    @staticmethod
    def write(root: Path, text: str) -> Path:
        path = root / "acknowledgement.json"
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def test_result_section_fits_the_report(self) -> None:
        """A compatibility result becomes a valid report section, including hostile detector text."""
        # C0 escape, C1 control sequence introducer, right-to-left override, zero-width space, a non-BMP character:
        escape = dict(removal_inputs().oasdiff_breaking[0], operation="GET\x1b[31m\u009b31m\u202e\u200b\U0001f600")
        for label, data, gate in (("none", identical_inputs(), BLOCK), ("additive", additive_inputs(), BLOCK),
                                  ("breaking, blocked", removal_inputs(), BLOCK), ("breaking, warned", removal_inputs(), WARN),
                                  ("terminal and bidirectional controls in a detector location",
                                   dataclasses.replace(removal_inputs(), oasdiff_breaking=[escape]), BLOCK)):
            with self.subTest(label):
                result = check_compatibility(data, gate)
                report = report_for(result)
                assessment = contract_report.assess_report(report)
                self.assertEqual(assessment.problems, ())
                self.assertEqual(assessment.verdict, result.result)  # every synthetic row passes
        self.assertIn("GET\\u001b[31m\\u009b31m\\u202e\\u200b\\U0001f600 /probe",
                      [finding["location"] for finding in report["compatibility"]["findings"]])

    def test_deeply_nested_documents_compared_without_recursion(self) -> None:
        """The strict reader accepts nesting deeper than the recursion limit; comparing such documents must not crash."""
        def nested(leaf: object) -> list:
            value = [leaf]
            for _ in range(20_000):
                value = [value]
            return value

        self.assertEqual(check_compatibility(identical_inputs(), BLOCK).change_class, "none")
        for label, revision_leaf, change_class in (("identical deep extension", 1, "none"), ("different deep leaf", 2, "additive")):
            with self.subTest(label):
                base, revision = synthetic_document("1.2.3"), synthetic_document("1.2.3")
                base["x-synthetic-depth"], revision["x-synthetic-depth"] = nested(1), nested(revision_leaf)
                result = check_compatibility(inputs(revision, base=base), BLOCK)
                self.assertEqual((result.change_class, result.result), (change_class, "pass"))

    def test_change_id_longer_than_the_report_bound_refused(self) -> None:
        """Boundary pair: an oasdiff id of the report's identifier bound is classified and reported; one more is refused."""
        change = removal_inputs().oasdiff_breaking[0]
        longest = "r" * contract_report.MAX_IDENTIFIER
        result = check_compatibility(dataclasses.replace(removal_inputs(), oasdiff_breaking=[dict(change, id=longest)]), BLOCK)
        self.assertIn(longest, [finding["id"] for finding in report_for(result)["compatibility"]["findings"]])
        with self.assertRaises(CompatRefused) as caught:
            check_compatibility(dataclasses.replace(removal_inputs(), oasdiff_breaking=[dict(change, id=longest + "r")]), BLOCK)
        self.assertEqual(caught.exception.rule, "compat.detector_output_invalid")

    def test_result_vocabulary_matches_the_report_schema(self) -> None:
        schema = contract_schema.load_schema(contract_report.REPORT_SCHEMA)["definitions"]["compatibility"]["properties"]
        self.assertEqual((schema["change_class"]["enum"], schema["result"]["enum"]),
                         ([*contract_compat.CHANGE_CLASSES, "not_run"], [*contract_compat.RESULTS, "not_run"]))


class RuleCoverageTest(unittest.TestCase):
    def test_every_gate_and_compatibility_rule_is_asserted(self) -> None:
        source = Path(__file__).read_text(encoding="utf-8")
        rules = contract_gate.DECISIONS + contract_compat.RULES
        self.assertEqual([rule for rule in rules if f'"{rule}"' not in source], [])


if __name__ == "__main__":
    unittest.main()
