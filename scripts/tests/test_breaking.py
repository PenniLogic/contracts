"""Breaking-change detection: a planted removal fails the check unless explicitly acknowledged."""

from __future__ import annotations

import json
import subprocess
import unittest

from support import ROOT, SPEC, SpecDir, replace_once, run_script, spec_text, with_probe_paths

import check_breaking_changes as cbc


def published_tags_exist() -> bool:
    completed = subprocess.run(["git", "tag", "--list", "v*"], cwd=str(ROOT), capture_output=True, text=True, check=False)
    return bool(completed.stdout.strip())


class BaselineTest(unittest.TestCase):
    def test_identity_has_no_breaking_change(self) -> None:
        completed = run_script("check_breaking_changes.py", "--base", str(SPEC), "--revision", str(SPEC))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("no breaking change", completed.stdout)

    def test_default_run_uses_the_highest_tag_or_reports_the_missing_baseline(self) -> None:
        completed = run_script("check_breaking_changes.py")
        if published_tags_exist():
            self.assertIn("against v", completed.stdout + completed.stderr)
        else:
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertIn("no published v* tag exists yet", completed.stdout)
            completed = run_script("check_breaking_changes.py", "--require-baseline")
            self.assertEqual(completed.returncode, 1)

    def test_tag_ordering_is_semantic(self) -> None:
        tags = sorted(["v1.10.0", "v1.2.0", "v0.9.9"], key=lambda t: tuple(int(x) for x in t[1:].split(".")))
        self.assertEqual(tags, ["v0.9.9", "v1.2.0", "v1.10.0"])


class PlantedRemovalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = SpecDir()
        self.addCleanup(self.spec.cleanup)
        self.base = self.spec.write(spec_text(), "base.yaml")

    def check(self, revision_text: str, *args: str) -> subprocess.CompletedProcess:
        revision = self.spec.write(revision_text, "revision.yaml")
        return run_script("check_breaking_changes.py", "--base", str(self.base), "--revision", str(revision), "--acknowledgement", str(self.spec.path / "ack.json"), *args)

    def test_removed_component_property_fails_with_actionable_message(self) -> None:
        removed = replace_once(spec_text(), "        correlation_id:\n          type: string\n          pattern: '^[A-Za-z0-9._-]{1,128}$'\n", "        correlation_id_renamed:\n          type: string\n          pattern: '^[A-Za-z0-9._-]{1,128}$'\n")
        completed = self.check(removed)
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertIn("[component-schema-property-removed] components/schemas/ProblemDetail/properties/correlation_id", completed.stdout)
        self.assertIn("expand-and-contract", completed.stderr)
        self.assertIn("naming every finding (id + location + reason)", completed.stderr)

    def test_removed_schema_parameter_header_and_narrowed_constraints_are_detected(self) -> None:
        text = spec_text()
        text = replace_once(text, "    TimeZone:\n      type: string\n      pattern: '^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*$'\n      maxLength: 64\n      examples: [Asia/Kolkata]\n", "")
        text = replace_once(text, "          maxLength: 21\n", "          maxLength: 20\n")
        text = replace_once(text, "      required: [amount, currency]\n", "      required: [amount, currency, scale]\n")
        text = replace_once(text, "  headers:\n    IdempotentReplayed:", "  headers:\n    IdempotentReplayedRenamed:")
        completed = self.check(text, "--format", "json")
        report = json.loads(completed.stdout[completed.stdout.index("{"):])
        ids = {(f["id"], f["location"]) for f in report["findings"]}
        self.assertIn(("component-schemas-removed", "components/schemas/TimeZone"), ids)
        self.assertIn(("component-schema-maxlength-decreased", "components/schemas/Money/properties/amount"), ids)
        self.assertIn(("component-schema-required-added", "components/schemas/Money/required/scale"), ids)
        self.assertIn(("component-headers-removed", "components/headers/IdempotentReplayed"), ids)
        self.assertEqual(completed.returncode, 1)

    def test_additive_change_passes(self) -> None:
        added = replace_once(spec_text(), "      examples:\n        - type: about:blank\n", "        hint:\n          type: string\n          description: Additive optional member.\n      examples:\n        - type: about:blank\n")
        completed = self.check(added)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_operation_removal_is_detected_by_oasdiff(self) -> None:
        base = self.spec.write(with_probe_paths(spec_text()), "base.yaml")
        revision = replace_once(with_probe_paths(spec_text()), "    post:\n      operationId: postProbe\n      description: Probe write.\n      tags: [probe]\n      parameters:\n        - $ref: '#/components/parameters/IdempotencyKey'\n      requestBody:\n        content:\n          application/json:\n            schema:\n              type: object\n              properties:\n                total:\n                  $ref: '#/components/schemas/Money'\n      responses:\n        '204':\n          description: ok\n", "")
        completed = run_script("check_breaking_changes.py", "--base", str(base), "--revision", str(self.spec.write(revision, "revision.yaml")), "--acknowledgement", str(self.spec.path / "ack.json"))
        self.assertEqual(completed.returncode, 1)
        self.assertIn("[api-removed-without-deprecation] POST /probe", completed.stdout)
        self.assertNotIn("component-parameters-name-changed", completed.stdout, "header names are case-insensitive")

    def test_acknowledgement_lets_a_named_break_pass(self) -> None:
        removed = replace_once(spec_text(), "        correlation_id:\n          type: string\n          pattern: '^[A-Za-z0-9._-]{1,128}$'\n", "        correlation_id_renamed:\n          type: string\n          pattern: '^[A-Za-z0-9._-]{1,128}$'\n")
        (self.spec.path / "ack.json").write_text(json.dumps({
            "baseline": str(self.base),
            "acknowledged": [{"id": "component-schema-property-removed", "location": "components/schemas/ProblemDetail/properties/correlation_id",
                              "reason": "expand-and-contract: correlation_id_renamed was added in the previous tag and every consumer migrated"}],
        }), encoding="utf-8")
        completed = self.check(removed)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("every finding is acknowledged", completed.stdout)

    def test_partial_stale_and_blanket_acknowledgements_fail(self) -> None:
        removed = replace_once(spec_text(), "        correlation_id:\n          type: string\n          pattern: '^[A-Za-z0-9._-]{1,128}$'\n          description: Opaque request correlation identifier for support and logs; not a secret and never an idempotency key.\n", "")
        removed = replace_once(removed, "          maxLength: 21\n", "          maxLength: 20\n")
        ack = {"baseline": str(self.base), "acknowledged": [{"id": "component-schema-property-removed", "location": "components/schemas/ProblemDetail/properties/correlation_id", "reason": "documented"}]}
        (self.spec.path / "ack.json").write_text(json.dumps(ack), encoding="utf-8")
        completed = self.check(removed)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("unacknowledged: [component-schema-maxlength-decreased]", completed.stderr)
        ack["baseline"] = "v0.0.1"
        (self.spec.path / "ack.json").write_text(json.dumps(ack), encoding="utf-8")
        completed = self.check(removed)
        self.assertIn("stale acknowledgement never carries over", completed.stderr)
        ack["baseline"] = str(self.base)
        ack["acknowledged"].append({"id": "component-schema-maxlength-decreased", "location": "components/schemas/Money/properties/amount", "reason": "ok"})
        ack["acknowledged"].append({"id": "component-schemas-removed", "location": "components/schemas/Nothing", "reason": "blanket"})
        (self.spec.path / "ack.json").write_text(json.dumps(ack), encoding="utf-8")
        completed = self.check(removed)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("blanket acknowledgements are refused", completed.stderr)

    def test_acknowledgement_without_a_break_is_refused(self) -> None:
        (self.spec.path / "ack.json").write_text(json.dumps({"baseline": str(self.base), "acknowledged": []}), encoding="utf-8")
        completed = self.check(spec_text())
        self.assertEqual(completed.returncode, 1)
        self.assertIn("no breaking change was detected", completed.stderr)


class VersionBumpRuleTest(unittest.TestCase):
    def test_pre_1_needs_minor_and_post_1_needs_major(self) -> None:
        self.assertTrue(cbc.version_bump_ok("v0.1.0", "0.2.0")[0])
        self.assertFalse(cbc.version_bump_ok("v0.1.0", "0.1.1")[0])
        self.assertTrue(cbc.version_bump_ok("v0.1.0", "1.0.0")[0])
        self.assertTrue(cbc.version_bump_ok("v1.4.2", "2.0.0")[0])
        self.assertFalse(cbc.version_bump_ok("v1.4.2", "1.5.0")[0])

    def test_evaluate_requires_the_bump_for_a_tag_baseline(self) -> None:
        finding = cbc.Finding("component-schemas-removed", "components/schemas/TimeZone", "removed", "component-guard")
        ack = {"baseline": "v1.0.0", "target_version": "1.1.0", "acknowledged": [{"id": finding.id, "location": finding.location, "reason": "documented"}]}
        problems = cbc.evaluate([finding], ack, "v1.0.0", "1.1.0", ROOT / "spec" / "breaking-change-acknowledgement.json")
        self.assertTrue(any("MAJOR bump" in p for p in problems))
        self.assertEqual(cbc.evaluate([finding], {**ack, "target_version": "2.0.0"}, "v1.0.0", "2.0.0", ROOT / "spec" / "x.json"), [])


if __name__ == "__main__":
    unittest.main()
