"""Command line of the contract verification runner (contracts#3, T-QA-02): subcommands and exit codes.

Exit codes: 0 pass (including a warning under an unexpired warning gate), 1 fail or refused, 2 usage
error or a missing, unreadable or malformed input. Every command runs in process through
verify_contract.main(argv) with an injected clock; there is no clock option on the command line. Inputs
are synthetic and live in a temporary directory; the artifact command verifies a synthetic release built by
the real release.build_release_set and published to a temporary bare origin (verification_support).
test_runner_writes_only_inside_temp records every file-system write and every process the commands start
through an audit hook. No network; nothing is written inside this repository.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import verification_support as support
from verification_support import (
    FIXTURE_NAMES, PROBE_SCHEMA, SyntheticContracts, copy_release, probe_path, synthetic_document, synthetic_report_parts,
    temporary_root, version_diff,
)

import contract_artifact  # noqa: E402 - verification_support puts scripts/ on sys.path
import contract_report  # noqa: E402
import contract_schema  # noqa: E402
import verify_contract  # noqa: E402
from contract_artifact import load_published_artifact  # noqa: E402
from contract_gate import parse_instant  # noqa: E402
from verify_contract import main  # noqa: E402

NOW = parse_instant("2026-10-12T00:00:00.000Z")
UNTIL = "2026-11-01T00:00:00.000Z"
REASON = "Synthetic expand-and-contract fixture: every consumer migrated first (verification tests only)."
READ_ONLY_GIT = ("rev-parse", "cat-file", "ls-remote", "ls-tree", "merge-base", ("remote", "get-url"))


class AuditRecorder:
    """Writes and processes seen by one process-wide audit hook while a recorder is active."""

    active: "AuditRecorder | None" = None
    installed = False
    WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    # Event -> (indexes of written paths, indexes of directory descriptors that make those paths relative).
    PATH_EVENTS = {
        "os.mkdir": ((0,), (2,)), "os.remove": ((0,), (1,)), "os.rmdir": ((0,), (1,)), "os.chmod": ((0,), (2,)),
        "os.utime": ((0,), (3,)), "os.chown": ((0,), (3,)), "os.truncate": ((0,), ()), "os.rename": ((0, 1), (2, 3)),
        "os.symlink": ((1,), (2,)), "os.link": ((1,), (2, 3)), "shutil.rmtree": ((0,), (1,)),
        "tempfile.mkdtemp": ((0,), ()), "tempfile.mkstemp": ((0,), ()),
    }

    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []
        self.processes: list[list[str]] = []
        self.errors: list[str] = []

    @classmethod
    def hook(cls, event: str, args: tuple) -> None:
        recorder = cls.active
        if recorder is None:
            return
        try:
            if event == "open":
                path, _, flags = args
                if isinstance(flags, int) and flags & cls.WRITE_FLAGS:
                    recorder.record(event, path)
            elif event in cls.PATH_EVENTS:
                paths, descriptors = cls.PATH_EVENTS[event]
                if any(isinstance(args[index], int) and args[index] >= 0 for index in descriptors if index < len(args)):
                    return  # relative to a directory descriptor: a step inside a recorded shutil.rmtree
                for index in paths:
                    recorder.record(event, args[index])
            elif event == "subprocess.Popen":
                recorder.processes.append([os.fsdecode(part) for part in args[1]])
        except Exception as error:  # never let the hook break the code under test; the test asserts no errors
            recorder.errors.append(f"{event}: {error!r}")

    def record(self, event: str, path: object) -> None:
        if isinstance(path, int):
            return  # wraps a descriptor that already exists (subprocess pipes); a path open of a file is recorded itself
        absolute = os.path.abspath(os.fsdecode(path))
        if "__pycache__" not in Path(absolute).parts:  # the interpreter's bytecode cache, not a write of the runner
            self.writes.append((event, absolute))

    def __enter__(self) -> "AuditRecorder":
        if not AuditRecorder.installed:
            sys.addaudithook(AuditRecorder.hook)
            AuditRecorder.installed = True
        AuditRecorder.active = self
        return self

    def __exit__(self, *exc: object) -> None:
        AuditRecorder.active = None


class CommandTestCase(unittest.TestCase):
    """One synthetic published release and one directory of synthetic inputs per class."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.enterClassContext(mock.patch.dict(os.environ, support.hermetic_environment(), clear=True))
        cls.root = temporary_root()
        cls.addClassCleanup(shutil.rmtree, cls.root, True)
        cls.inputs = cls.root / "inputs"
        cls.inputs.mkdir()
        base = synthetic_document("1.2.3", paths={"/probe": probe_path()})
        removal = synthetic_document("2.0.0", properties=("amount", "currency"), paths={"/probe": probe_path()})
        change = {"id": "response-property-removed", "level": 3, "operation": "GET", "path": "/probe",
                  "text": "removed the optional property 'note' from the response with the '200' status"}
        diff = {**version_diff("1.2.3", "2.0.0"),
                "components": {"schemas": {"modified": {PROBE_SCHEMA: {"properties": {"deleted": ["note"]}}}}}}
        acknowledged = [{"id": "response-property-removed", "location": "GET /probe", "reason": REASON},
                        {"id": "component-schema-property-removed", "location": f"components/schemas/{PROBE_SCHEMA}/properties/note",
                         "reason": REASON}]
        python_missing = synthetic_report_parts()
        python_missing["consumers"] = [row for row in python_missing["consumers"] if row["language"] != "python"]
        omission_as_pass = contract_report.build_report(**synthetic_report_parts())
        omission_as_pass["consumers"].pop()
        for name, value in {
            "base.json": base, "removal.json": removal, "no-changes.json": [], "no-diff.json": {},
            "removal-breaking.json": [change], "removal-diff.json": diff,
            "acknowledgement.json": {"baseline": "v1.2.3", "target_version": "2.0.0", "acknowledged": acknowledged},
            "acknowledgement-invalid.json": {"baseline": "v1.2.3"},
            "breaking-invalid.json": [dict(change, level="3")],
            "removal-breaking-hostile.json": [dict(change, operation="GET\u009b31m\u202e")],
            "gate-warn.json": {"schema": "pennilogic-compat-gate@1", "mode": "warn", "warn_until": UNTIL},
            "gate-warn-no-expiry.json": {"schema": "pennilogic-compat-gate@1", "mode": "warn", "warn_until": None},
            "report-synthetic.json": contract_report.build_report(**synthetic_report_parts()),
            "report-python-missing.json": contract_report.build_report(**python_missing),
            "report-omission-as-pass.json": omission_as_pass,
        }.items():
            (cls.inputs / name).write_text(contract_schema.dumps(value), encoding="ascii", newline="\n")
        (cls.inputs / "not-json.json").write_text("{\n", encoding="ascii", newline="\n")
        (cls.inputs / "acknowledgement-deep.json").write_text(
            '{"baseline": "v1.2.3", "acknowledged": ' + "[" * 100_000 + "]" * 100_000 + "}\n", encoding="ascii", newline="\n")
        cls.contracts = SyntheticContracts(cls.root / "contracts")
        cls.release_dir = cls.contracts.publish()
        # The synthetic bare origin stands in for the canonical PenniLogic/contracts repository.
        cls.enterClassContext(mock.patch.object(contract_artifact, "CANONICAL_REMOTE_URLS", (str(cls.contracts.origin),)))

    def path(self, name: str) -> str:
        return str(self.inputs / name)

    def run_command(self, *argv: str, now=NOW) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(argv), clock=lambda: now)
        return code, stdout.getvalue(), stderr.getvalue()

    def run_json(self, *argv: str, now=NOW) -> tuple[int, dict]:
        code, out, err = self.run_command(*argv, "--json", now=now)
        self.assertEqual(err, "")
        payload = contract_schema.parse_strict_json(out.encode("ascii"))
        self.assertEqual(out, contract_schema.dumps(payload), "--json prints one canonical ASCII JSON document")
        assert isinstance(payload, dict)
        return code, payload

    def artifact_argv(self, release_dir: Path | None = None, tag: str | None = None, repo: Path | None = None) -> list[str]:
        return ["artifact", "--tag", tag or self.contracts.tag, "--release-dir", str(release_dir or self.release_dir),
                "--repo", str(repo or self.contracts.repo)]

    def compat_argv(self, revision: str = "removal.json", breaking: str = "removal-breaking.json", diff: str = "removal-diff.json",
                    *extra: str, baseline: str = "v1.2.3") -> list[str]:
        return ["compat", "--baseline", baseline, "--base-document", self.path("base.json"), "--revision-document",
                self.path(revision), "--oasdiff-breaking", self.path(breaking), "--oasdiff-diff", self.path(diff), *extra]


class UsageTest(CommandTestCase):
    def test_help_and_usage_errors_exit_2(self) -> None:
        code, out, _ = self.run_command("--help")
        self.assertEqual(code, 0)
        self.assertIn("Exit codes: 0 pass", out)
        for label, argv in {
            "no subcommand": [],
            "a subcommand of the second pull request": ["provider"],
            "a missing required option": ["compat", "--baseline", "v1.2.3"],
            "a clock option (the clock is never a command-line input)": ["gate", "--now", UNTIL],
        }.items():
            with self.subTest(label):
                code, out, err = self.run_command(*argv)
                self.assertEqual((code, out), (2, ""))
                self.assertIn("usage: verify_contract.py", err)


class ArtifactCommandTest(CommandTestCase):
    def test_artifact_command_exit_codes(self) -> None:
        code, payload = self.run_json(*self.artifact_argv())
        self.assertEqual((code, payload["verified"], payload["artifact"]["tag"]), (0, True, self.contracts.tag))
        self.assertEqual(payload["artifact"]["checks"][-1], {"id": "github_release.presence", "status": "not_run"})
        code, out, _ = self.run_command(*self.artifact_argv())
        self.assertEqual(code, 0)
        self.assertIn(f"artifact: VERIFIED {self.contracts.tag}", out)
        self.assertIn(f"  published on origin ({self.contracts.origin})\n", out)
        self.assertIn("a verified artifact alone is not acceptance evidence", out)
        code, payload = self.run_json(*self.artifact_argv(), "--expect-tag-object", self.contracts.tag_object)
        self.assertEqual((code, {check["id"]: check["status"] for check in payload["artifact"]["checks"]}["tag.pinned_identity"]),
                         (0, "pass"))
        work = temporary_root()
        self.addCleanup(shutil.rmtree, work, True)
        tampered = copy_release(self.release_dir, work, "tampered")
        (tampered / FIXTURE_NAMES[0]).write_bytes((tampered / FIXTURE_NAMES[0]).read_bytes() + b" ")
        for label, argv, expected in (
                ("an asset changed after publication", self.artifact_argv(tampered), (1, "sums.digest_mismatch")),
                ("a branch name instead of a tag", self.artifact_argv(tag="main"), (1, "tag.grammar")),
                ("another pinned tag object", self.artifact_argv() + ["--expect-tag-object", "0" * 40], (1, "tag.pin_mismatch"))):
            with self.subTest(label):
                code, payload = self.run_json(*argv)
                self.assertEqual((code, payload["refusal"]["rule"], payload["verified"]), (*expected, False))
        for label, argv, rule in (
                ("missing release directory", self.artifact_argv(work / "absent"), "release_dir.missing"),
                ("a directory that is not a checkout", self.artifact_argv(repo=work), "repository.invalid"),
                ("unconfigured remote", self.artifact_argv() + ["--remote", "upstream"], "remote.invalid")):
            with self.subTest(label):
                code, out, err = self.run_command(*argv)
                self.assertEqual((code, out), (2, ""))
                self.assertIn(f"[{rule}]", err)
        rule = "git.unavailable"
        with self.subTest("git not on PATH"), mock.patch.dict(os.environ, {"PATH": str(work)}):
            code, out, err = self.run_command(*self.artifact_argv())
            self.assertEqual((code, out), (2, ""))
            self.assertIn(f"[{rule}]", err)

    def test_hostile_archive_refused_with_one_json_document(self) -> None:
        """Hostile archives are refusals with a static rule and exit 1, never a traceback with empty stdout."""
        code, payload = self.run_json(*self.artifact_argv())
        self.assertEqual((code, payload["verified"]), (0, True))  # control
        work = temporary_root()
        self.addCleanup(shutil.rmtree, work, True)
        prefix = f"pennilogic-contracts-python-{self.contracts.tag}"
        chained = b"".join(support.gnu_long_header(f"{prefix}/chain-{index}.py") for index in range(5000))
        chained += support.ustar_header(f"{prefix}/x.py", 2) + support.tar_padded(b"x\n") + support.END_OF_ARCHIVE
        archive = f"{prefix}.tar.gz"
        cases = {
            "5000 chained GNU long-name headers": lambda release: (release / archive).write_bytes(support.gzip_bytes(chained)),
            "a member name that is not UTF-8": lambda release: support.rewrite_archive(
                release, self.contracts.tag, "python", lambda members: members + [support.member(f"{prefix}/x\udcff.py")]),
        }
        for label, mutate in cases.items():
            with self.subTest(label):
                release = copy_release(self.release_dir, work, label.replace(" ", "-"))
                mutate(release)
                support.reseal(release)
                code, payload = self.run_json(*self.artifact_argv(release))
                self.assertEqual((code, payload["verified"], payload["refusal"]["rule"]), (1, False, "archive.ambiguous"))

    def test_verified_artifact_fills_the_report_contract_section(self) -> None:
        artifact = load_published_artifact(self.contracts.tag, self.release_dir, repo=self.contracts.repo)
        section = contract_report.contract_section(artifact)
        self.assertEqual(section, {
            "version": self.contracts.version, "tag": self.contracts.tag,
            "commit": support.git(self.contracts.repo, "rev-parse", f"{self.contracts.tag}^{{commit}}"),
            "spec_sha256": support.sha256_bytes((self.release_dir / artifact.spec_asset).read_bytes()),
            "release_manifest_sha256": support.sha256_bytes((self.release_dir / "release-manifest.json").read_bytes()),
        })
        report = contract_report.build_report(**dict(synthetic_report_parts(), contract=section))
        assessment = contract_report.assess_report(report)
        self.assertEqual((assessment.problems, assessment.verdict, assessment.acceptance_eligible), ((), "pass", False))


class GateCommandTest(CommandTestCase):
    def test_gate_command_exit_codes(self) -> None:
        code, out, _ = self.run_command("gate")
        self.assertEqual((code, out), (0, "gate: block (gate.block) at 2026-10-12T00:00:00.000Z\n"))
        warning = self.path("gate-warn.json")
        for label, now, expected in (("before the expiry", NOW, ("warn", "gate.warn_active")),
                                     ("at the expiry", parse_instant(UNTIL), ("block", "gate.warn_expired"))):
            with self.subTest(label):
                code, payload = self.run_json("gate", "--gate-state", warning, now=now)
                self.assertEqual((code, payload["valid"], payload["gate"]["mode"], payload["gate"]["reason"]), (0, True, *expected))
        with self.subTest("a warning without expiry is refused, and readers block"):
            code, payload = self.run_json("gate", "--gate-state", self.path("gate-warn-no-expiry.json"))
            self.assertEqual((code, payload["valid"], payload["refusal"]["rule"], payload["gate"]["mode"]),
                             (1, False, "gate.warn_until_missing", "block"))
        for label, path, rule in (("missing", self.path("absent.json"), "input.missing"),
                                  ("a directory", str(self.inputs), "input.unreadable")):
            with self.subTest(label):
                code, payload = self.run_json("gate", "--gate-state", path)
                self.assertEqual((code, payload["usage_error"]["rule"]), (2, rule))


class CompatCommandTest(CommandTestCase):
    def test_compat_command_exit_codes(self) -> None:
        cases = {
            "identical documents": (self.compat_argv("base.json", "no-changes.json", "no-diff.json"), NOW, (0, "pass", "none")),
            "unacknowledged removal": (self.compat_argv(), NOW, (1, "fail", "breaking")),
            "acknowledged removal with a MAJOR bump": (self.compat_argv("removal.json", "removal-breaking.json", "removal-diff.json",
                                                                        "--acknowledgement", self.path("acknowledgement.json")),
                                                       NOW, (0, "pass", "breaking")),
            "unacknowledged removal under a warning gate": (self.compat_argv(
                "removal.json", "removal-breaking.json", "removal-diff.json", "--gate-state", self.path("gate-warn.json")), NOW,
                (0, "warn", "breaking")),
            "the same warning at its expiry": (self.compat_argv(
                "removal.json", "removal-breaking.json", "removal-diff.json", "--gate-state", self.path("gate-warn.json")),
                parse_instant(UNTIL), (1, "fail", "breaking")),
            "a warning without expiry blocks": (self.compat_argv(
                "removal.json", "removal-breaking.json", "removal-diff.json", "--gate-state", self.path("gate-warn-no-expiry.json")), NOW,
                (1, "fail", "breaking")),
        }
        for label, (argv, now, expected) in cases.items():
            with self.subTest(label):
                code, payload = self.run_json(*argv, now=now)
                self.assertEqual((code, payload["result"], payload["compatibility"]["change_class"]), expected)
        code, out, _ = self.run_command(*self.compat_argv("removal.json", "removal-breaking.json", "removal-diff.json",
                                                          "--gate-state", self.path("gate-warn.json")))
        self.assertTrue(out.startswith("compat: WARN revision 2.0.0 against v1.2.3: change class breaking\n"), out)
        self.assertIn(f"until {UNTIL}; they block from then on", out)

    def test_compat_command_input_errors_exit_2(self) -> None:
        self.assertEqual(self.run_command(*self.compat_argv())[0], 1)
        cases = {
            "a missing document": (self.compat_argv("absent.json"), "input.missing"),
            "a document that is not JSON": (self.compat_argv("not-json.json"), "input.not_json"),
            "a baseline the base document does not declare": (self.compat_argv(baseline="v1.2.2"), "compat.baseline_version_mismatch"),
            "malformed detector output": (self.compat_argv("removal.json", "breaking-invalid.json"), "compat.detector_output_invalid"),
            "a missing acknowledgement": (self.compat_argv("removal.json", "removal-breaking.json", "removal-diff.json",
                                                           "--acknowledgement", self.path("absent.json")), "input.missing"),
            "a malformed acknowledgement": (self.compat_argv("removal.json", "removal-breaking.json", "removal-diff.json",
                                                             "--acknowledgement", self.path("acknowledgement-invalid.json")),
                                            "compat.acknowledgement_invalid"),
            "an acknowledgement nested beyond the parser's limit": (
                self.compat_argv("removal.json", "removal-breaking.json", "removal-diff.json",
                                 "--acknowledgement", self.path("acknowledgement-deep.json")), "compat.acknowledgement_invalid"),
        }
        for label, (argv, rule) in cases.items():
            with self.subTest(label):
                code, payload = self.run_json(*argv)
                self.assertEqual((code, payload["usage_error"]["rule"]), (2, rule))
                code, out, err = self.run_command(*argv)
                self.assertEqual((code, out), (2, ""))
                self.assertIn(f"[{rule}]", err)

    def test_compat_text_output_is_printable_ascii(self) -> None:
        """Detector text comes from the compared documents; it cannot restyle or reorder the terminal or a log viewer."""
        def printable(text: str) -> bool:
            return all(" " <= character <= "~" for line in text.splitlines() for character in line)

        code, out, _ = self.run_command(*self.compat_argv())
        self.assertEqual(code, 1)
        self.assertTrue(printable(out), out)
        code, out, err = self.run_command(*self.compat_argv("removal.json", "removal-breaking-hostile.json"))
        self.assertEqual((code, err), (1, ""))
        self.assertIn("  finding [response-property-removed] GET\\u009b31m\\u202e /probe (oasdiff)\n", out)
        self.assertTrue(printable(out), ascii(out))


class ReportCommandTest(CommandTestCase):
    def test_report_command_exit_codes(self) -> None:
        code, out, _ = self.run_command("report", "--report", self.path("report-synthetic.json"))
        self.assertEqual((code, out), (0, "report: VALID, verdict pass, acceptance_eligible false\n"))
        code, payload = self.run_json("report", "--report", self.path("report-python-missing.json"))
        self.assertEqual((code, payload["valid"], payload["verdict"], payload["missing_languages"], payload["reasons"]),
                         (1, True, "fail", ["python"], [{"rule": "report.required_language_missing", "subject": "python"}]))
        for label, name, rule in (
                ("an omission declared as a pass", "report-omission-as-pass.json", "report.missing_languages_inconsistent"),
                ("not JSON", "not-json.json", "report.not_json")):
            with self.subTest(label):
                code, payload = self.run_json("report", "--report", self.path(name))
                self.assertEqual((code, payload["valid"], payload["verdict"], payload["problems"][0]["rule"]), (1, False, None, rule))
        code, payload = self.run_json("report", "--report", self.path("absent.json"))
        self.assertEqual((code, payload["usage_error"]["rule"]), (2, "input.missing"))


class WriteBoundaryTest(CommandTestCase):
    def test_runner_writes_only_inside_temp(self) -> None:
        scratch, cwd = temporary_root("pl-verify-scratch-"), temporary_root("pl-verify-cwd-")
        self.addCleanup(shutil.rmtree, scratch, True)
        self.addCleanup(shutil.rmtree, cwd, True)
        watched = (self.contracts.repo, self.contracts.origin, self.release_dir, self.inputs, support.SCRIPTS / "verification")
        before = [support.snapshot(path) for path in watched]
        commands = (
            (self.artifact_argv(), 0), (self.artifact_argv(tag="v9.9.9"), 1), (["gate"], 0),
            (["gate", "--gate-state", self.path("gate-warn.json")], 0), (self.compat_argv(), 1),
            (self.compat_argv("removal.json", "removal-breaking.json", "removal-diff.json", "--acknowledgement",
                              self.path("acknowledgement.json")), 0),
            (["report", "--report", self.path("report-synthetic.json")], 0),
        )
        with mock.patch.object(tempfile, "tempdir", str(scratch)), contextlib.chdir(cwd), AuditRecorder() as recorder:
            codes = [self.run_command(*argv)[0] for argv, _ in commands]
        self.assertEqual(codes, [expected for _, expected in commands])
        self.assertEqual(recorder.errors, [])
        self.assertTrue(recorder.writes, "the artifact command unpacks archives, so writes must have been recorded")
        outside = [(event, path) for event, path in recorder.writes if not Path(path).is_relative_to(scratch)]
        self.assertEqual(outside, [])
        self.assertEqual(list(scratch.iterdir()), [], "the loader removes its unpack directory")
        self.assertEqual(list(cwd.iterdir()), [])
        self.assertEqual([support.snapshot(path) for path in watched], before)
        self.assertTrue(recorder.processes)
        for argv in recorder.processes:
            self.assertEqual((Path(argv[0]).name, argv[1:3]), ("git", ["-C", str(self.contracts.repo)]), argv)
            self.assertIn(tuple(argv[3:5]) if argv[3] == "remote" else argv[3], READ_ONLY_GIT, argv)


class TemporaryRootTest(unittest.TestCase):
    """Where the tests may write. On main, CI runs scripts/run_source_tests.py, which sets each lane's TMPDIR inside the
    checkout's gitignored build/ (build/source-tests/run-*/state/<lane>/temp). Checked against a fake checkout that
    lives in a temporary directory, so nothing is written inside this repository."""

    def test_temporary_root_allows_the_runner_temp_area_but_no_other_checkout_path(self) -> None:
        base = temporary_root("pl-verify-fake-")
        self.addCleanup(shutil.rmtree, base, True)
        checkout, outside = base / "checkout", base / "elsewhere"
        lane_temp = checkout / "build" / "source-tests" / "run-synthetic" / "state" / "serial" / "temp"
        tracked = checkout / "scripts" / "tests"
        for directory in (outside, lane_temp, tracked):
            directory.mkdir(parents=True)

        def make(tempdir: Path) -> Path:
            with mock.patch.object(support, "ROOT", checkout), mock.patch.object(tempfile, "tempdir", str(tempdir)):
                return support.temporary_root()

        self.assertEqual(make(outside).parent, outside.resolve())  # control: outside the checkout
        with self.subTest("the runner lane's TMPDIR inside the gitignored build/"):
            self.assertEqual(make(lane_temp).parent, lane_temp.resolve())
        for label, tempdir in (("a tracked directory of the checkout", tracked), ("the checkout itself", checkout)):
            with self.subTest(label):
                with self.assertRaises(AssertionError) as caught:
                    make(tempdir)
                self.assertEqual(str(caught.exception), "the temporary directory is inside the repository but outside its "
                                                         "gitignored build/; set TMPDIR elsewhere")
                self.assertEqual([path.name for path in tempdir.iterdir() if path.name.startswith("pl-verify-")], [])


class RuleCoverageTest(unittest.TestCase):
    def test_every_usage_rule_is_asserted(self) -> None:
        self.assertEqual(verify_contract.UsageProblem("input.missing", "synthetic").rule, "input.missing")
        with self.assertRaises(ValueError):
            verify_contract.UsageProblem("input.unknown", "synthetic")
        source = Path(__file__).read_text(encoding="utf-8")
        rules = verify_contract.RULES + tuple(sorted(verify_contract.ARTIFACT_USAGE_RULES))
        self.assertEqual([rule for rule in rules if f'"{rule}"' not in source], [])


if __name__ == "__main__":
    unittest.main()
