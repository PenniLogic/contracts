"""Real process isolation and adverse completeness controls for the whole-source runner."""

from __future__ import annotations

import ast
from collections import Counter
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

from support import ROOT
import run_source_tests as source_tests
import toolchain


def module(body: str) -> str:
    return ("import unittest\nclass Fixture(unittest.TestCase):\n"
            "    def test_control(self):\n" +
            "".join("        " + line + "\n" for line in body.splitlines()))


def mocked_linux_owner(pid: int) -> source_tests.OwnedProcess:
    process = mock.Mock(pid=pid, args=["synthetic owned process"])
    process.wait.return_value = 0
    owner = source_tests.OwnedProcess(process)
    owner.windows = False
    owner.job = None
    owner.exit = 0
    owner.acquired = True
    owner.record["kind"] = "mocked unreaped POSIX process group"
    owner.wait_leader = mock.Mock(return_value=0)
    owner.active = mock.Mock(return_value=0)
    return owner


class SourceRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        (ROOT / "build").mkdir(exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="pl-source-runner-", dir=ROOT / "build"))
        self.addCleanup(shutil.rmtree, self.root)
        (self.root / "scripts" / "tests").mkdir(parents=True)
        for name in ("run_source_tests.py", "pl_contracts.py"):
            shutil.copyfile(ROOT / "scripts" / name, self.root / "scripts" / name)

    def fixture(self, files: dict[str, str]) -> None:
        for name, text in files.items():
            (self.root / "scripts" / "tests" / name).write_text(text, encoding="utf-8", newline="\n")

    def cached_overlap_tools(self) -> None:
        shutil.copyfile(ROOT / "scripts" / "toolchain.py", self.root / "scripts" / "toolchain.py")
        jar = b"synthetic pinned generator bytes; never executed\n"
        binary = b"synthetic pinned diff bytes; never executed\n"
        asset_digest = "1" * 64
        pins = {
            "openapi_generator": {"version": "fixture", "sha256": hashlib.sha256(jar).hexdigest()},
            "oasdiff": {"version": "fixture", "assets": {
                toolchain.platform_key(): {"sha256": asset_digest},
            }},
        }
        profile = self.root / "toolchain" / "versions.json"
        profile.parent.mkdir()
        profile.write_text(json.dumps(pins), encoding="utf-8")
        cache = self.root / ".toolchain"
        cache.mkdir()
        toolchain.generator_jar_path(pins, cache).write_bytes(jar)
        target = toolchain.oasdiff_path(pins, cache)
        target.write_bytes(binary)
        toolchain._marker(target).write_text(json.dumps({
            "tarball_sha256": asset_digest, "binary_sha256": hashlib.sha256(binary).hexdigest(),
        }), encoding="utf-8")

    def execute(self, driver: Path | None = None, guarded: bool = False) -> subprocess.CompletedProcess:
        command = [sys.executable, str(driver or self.root / "scripts" / "run_source_tests.py")]
        environment = {name: value for name, value in os.environ.items()
                       if name not in {"PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX"}}
        if guarded:
            build = self.root / "build"
            build.mkdir(exist_ok=True)
            guard = Path(tempfile.mkdtemp(prefix="guarded-control-", dir=build))
            with (guard / "stdout.txt").open("wb") as stdout, \
                    (guard / "stderr.txt").open("wb") as stderr:
                flags = {"start_new_session": True} if os.name != "nt" else {
                    "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | source_tests.WINDOWS_CREATE_SUSPENDED,
                }
                child = subprocess.Popen(command, cwd=self.root, env=environment,
                                         stdout=stdout, stderr=stderr, **flags)
                owner = None
                completed = False
                failure = None
                cleanup_errors = []
                pending_record = {"cleanup_confirmed": False, "leader_exit": None}
                try:
                    owner = source_tests.OwnedProcess(child)
                    owner.acquire()
                    code = owner.wait_leader(timeout=45)
                    completed = True
                except (OSError, ValueError, source_tests.PipelineError, subprocess.SubprocessError,
                        KeyboardInterrupt) as error:
                    failure = error
                finally:
                    if owner is None:
                        try:
                            source_tests.cleanup_pending_process(child, pending_record)
                        except (OSError, ValueError, source_tests.PipelineError, subprocess.SubprocessError,
                                KeyboardInterrupt) as error:
                            cleanup_errors.append(f"{type(error).__name__}: {error}")
                        self.guard_ownership_record = dict(pending_record)
                    else:
                        lingering, cleanup_errors = source_tests.cleanup_owner(owner, forced=not completed)
                        if lingering:
                            cleanup_errors.append("guarded fixture descendants outlived their leader")
                        self.guard_ownership_record = dict(owner.record)
                    if failure is not None or cleanup_errors:
                        self.guard_failure_record = {
                            "error": str(failure) if failure is not None else "guard cleanup failed",
                            "cleanup": self.guard_ownership_record, "cleanup_errors": cleanup_errors,
                        }
                if failure is not None or cleanup_errors:
                    try:
                        controls = ROOT / "build" / "source-runner-controls"
                        controls.mkdir(exist_ok=True)
                        evidence = Path(tempfile.mkdtemp(prefix="guard-error-", dir=controls))
                        for channel in ("stdout", "stderr"):
                            shutil.copyfile(guard / (channel + ".txt"), evidence / (channel + ".txt"))
                        (evidence / "guard-error.json").write_text(json.dumps({
                            "test": self.id(), "command": command, "pid": child.pid,
                            "primary_error_type": type(failure).__name__ if failure is not None else None,
                            **self.guard_failure_record,
                        }, indent=2) + "\n", encoding="utf-8")
                    except (OSError, ValueError, KeyboardInterrupt) as error:
                        cleanup_errors.append(f"guard evidence {type(error).__name__}: {error}")
                if failure is not None:
                    if cleanup_errors:
                        failure.add_note("Guard cleanup uncertainty: " + "; ".join(cleanup_errors))
                    raise failure
                if cleanup_errors or not self.guard_ownership_record["cleanup_confirmed"]:
                    raise source_tests.PipelineError("guard cleanup is unconfirmed: " + "; ".join(cleanup_errors))
            result = subprocess.CompletedProcess(
                command, code,
                (guard / "stdout.txt").read_text(encoding="utf-8"),
                (guard / "stderr.txt").read_text(encoding="utf-8"),
            )
        else:
            result = subprocess.run(
                command, cwd=self.root, capture_output=True, text=True, encoding="utf-8",
                check=False, timeout=45, env=environment,
            )
        controls = ROOT / "build" / "source-runner-controls"
        controls.mkdir(exist_ok=True)
        evidence = Path(tempfile.mkdtemp(prefix="control-", dir=controls))
        (evidence / "stdout.txt").write_text(result.stdout, encoding="utf-8")
        (evidence / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        receipts = []
        for path in sorted((self.root / "build" / "source-tests").rglob("*")):
            if not path.is_file() or "state" in path.parts:
                continue
            target = evidence / "receipts" / path.relative_to(self.root / "build" / "source-tests")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            data = target.read_bytes()
            receipts.append({"path": target.relative_to(evidence).as_posix(),
                             "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        (evidence / "control.json").write_text(json.dumps({
            "test": self.id(), "exit": result.returncode, "fixture_root": str(self.root),
            "files": receipts,
        }, indent=2) + "\n", encoding="utf-8")
        return result

    def receipt(self) -> tuple[Path, dict]:
        files = list((self.root / "build" / "source-tests").glob("run-*/complete.receipt.json"))
        self.assertEqual(len(files), 1)
        return files[0], json.loads(files[0].read_text(encoding="utf-8"))

    def retain_linux_mock(self, label: str, value: dict) -> None:
        controls = ROOT / "build" / "source-runner-controls"
        controls.mkdir(exist_ok=True)
        evidence = Path(tempfile.mkdtemp(prefix="linux-mock-", dir=controls))
        (evidence / "mock-control.json").write_text(json.dumps({
            "test": self.id(), "case": label,
            "classification": "Mocked Linux lifecycle on the selected host; no Linux or PID operation.",
            "runner_sha256": hashlib.sha256((ROOT / "scripts" / "run_source_tests.py").read_bytes()).hexdigest(),
            **value,
        }, indent=2) + "\n", encoding="utf-8")

    def timing_subject(self) -> tuple:
        plan_home = self.root / "build" / "timing-protocol"
        plan_home.mkdir(parents=True)
        tests = {}
        for name in ("test_generate", "test_allocation_refusal", "test_category_seed", "test_lint"):
            fixture = type("Fixture", (unittest.TestCase,), {
                "__module__": name, "test_control": lambda test: test.assertTrue(True),
            })
            tests[name] = fixture("test_control")
        assignments = {
            "serial": [tests["test_generate"].id()],
            "cli-1": [tests[name].id() for name in ("test_allocation_refusal", "test_category_seed")],
            "cli-2": [tests["test_lint"].id()],
        }
        ids = sorted(test.id() for test in tests.values())
        plan = {"version": 1, "root": str(self.root), "ids": ids, "assignments": assignments,
                "inputs": source_tests.input_snapshot(self.root)}
        plan_path = plan_home / "plan.json"
        source_tests.write_json(plan_path, plan)
        plan_sha = source_tests.sha256_file(plan_path)
        finals, prefixes = {}, {}
        for lane in source_tests.LANES:
            environment = source_tests.worker_environment(plan_home / "state" / lane, lane != "serial")
            isolation = {name: environment.get(name) for name in (
                "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "GRADLE_USER_HOME",
            )}
            if lane != "serial":
                isolation.update({"user.home": environment["HOME"], "java.io.tmpdir": environment["TEMP"]})
            selected = [test for test in tests.values() if test.id() in assignments[lane]]
            origin = time.perf_counter()

            def checkpoint(result, lane=lane):
                early = source_tests.independent_ids(plan, lane)
                if early:
                    path = plan_home / (lane + ".partial.receipt.json")
                    source_tests.write_json(path, source_tests.worker_receipt(
                        plan, lane, early, result, isolation, plan_sha, origin,
                    ))
                    prefixes[lane] = source_tests.validate_receipt(
                        path, plan, lane, plan_sha, source_tests.ChildExit(os.getpid(), 0), partial=True,
                    )

            early_ids = set(source_tests.independent_ids(plan, lane))
            suite = source_tests.TrackingSuite(selected) if lane == "serial" else source_tests.GatedSuite(
                [test for test in selected if test.id() in early_ids],
                [test for test in selected if test.id() not in early_ids], checkpoint, lambda: None,
            )
            with mock.patch.dict(sys.modules, {name: types.ModuleType(name) for name in tests}):
                result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=source_tests.TrackingResult).run(suite)
            self.assertTrue(result.wasSuccessful())
            path = plan_home / (lane + ".receipt.json")
            source_tests.write_json(path, source_tests.worker_receipt(
                plan, lane, assignments[lane], result, isolation, plan_sha, origin,
            ))
            finals[lane] = source_tests.validate_receipt(
                path, plan, lane, plan_sha, source_tests.ChildExit(os.getpid(), 0),
            )
        total = max(value["seconds"] for value in finals.values()) + 3
        phases = [
            {"phase": "serial", "lanes": ["serial"], "seconds": total - 2,
             "independent_cli_started_with_serial": True},
            {"phase": "parallel-cli", "lanes": ["cli-1", "cli-2"], "seconds": total - 1,
             "independent_work_started_with_serial": True},
        ]
        return plan, plan_sha, finals, prefixes, phases, total

    def projected_timing(self, output: str) -> dict:
        self.assertEqual(output.count(source_tests.TIMING_PROJECTION_BEGIN), 1)
        self.assertEqual(output.count(source_tests.TIMING_PROJECTION_END), 1)
        body = output.split(source_tests.TIMING_PROJECTION_BEGIN + "\n", 1)[1].split(
            "\n" + source_tests.TIMING_PROJECTION_END, 1)[0]
        return json.loads(body)

    def test_final_timing_projection_exact_validated_coverage_bindings_and_private_field_exclusion(self) -> None:
        subject = self.timing_subject()
        plan, plan_sha, finals, prefixes, phases, total = subject
        for receipt in finals.values():
            receipt["isolation"]["HOME"] = "PRIVATE_TIMING_CANARY"
            receipt["errors"] = [["private synthetic diagnostic", "PRIVATE_TIMING_CANARY"]]
        phases[0]["children"] = {"private": "PRIVATE_TIMING_CANARY"}
        phases[0]["cleanup_errors"] = ["PRIVATE_TIMING_CANARY"]
        encoded = source_tests.final_timing_projection(*subject, overlap=True)
        projection = self.projected_timing(encoded)
        self.assertNotIn("PRIVATE_TIMING_CANARY", encoded)
        self.assertNotIn(str(self.root), encoded)
        self.assertEqual(set(projection), {
            "timing_diagnostic_only", "hosted_600_qualification", "plan_sha256", "source_inputs_sha256",
            "complete_source_seconds", "final_receipts", "validated_independent_prefixes_not_final_receipts",
            "parent_phases", "receipt_seconds_origin", "complete_seconds_origin",
            "durations_are_not_additive_or_measured_waiting",
        })
        self.assertTrue(projection["timing_diagnostic_only"])
        self.assertFalse(projection["hosted_600_qualification"])
        self.assertEqual(projection["plan_sha256"], plan_sha)
        self.assertEqual(projection["source_inputs_sha256"], hashlib.sha256(json.dumps(
            plan["inputs"], sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest())
        self.assertEqual(projection["complete_source_seconds"], total)
        self.assertEqual(Counter(row["test"] for lane in projection["final_receipts"] for row in lane["methods"]),
                         Counter(plan["ids"]))
        for row in projection["final_receipts"]:
            self.assertEqual(row, {key: finals[row["lane"]][key]
                                  for key in ("lane", "seconds", "methods", "class_phases")})
        for row in projection["validated_independent_prefixes_not_final_receipts"]:
            self.assertEqual(row, {key: prefixes[row["lane"]][key]
                                  for key in ("lane", "seconds", "methods", "class_phases")})
        self.assertEqual([row["seconds"] for row in projection["parent_phases"]],
                         [row["seconds"] for row in phases])
        self.assertTrue(all(row["origin"] == "shared_overlap_phase_start"
                            for row in projection["parent_phases"]))
        self.assertTrue(projection["durations_are_not_additive_or_measured_waiting"])
        plan, plan_sha, finals, prefixes, phases, _ = copy.deepcopy(subject)
        for receipt in [*finals.values(), *prefixes.values()]:
            receipt["seconds"] = 0
            for row in receipt["methods"] + receipt["class_phases"]:
                row["seconds"] = 0
        for phase in phases:
            phase["seconds"] = 0
        zero = self.projected_timing(source_tests.final_timing_projection(
            plan, plan_sha, finals, prefixes, phases, 0, overlap=True,
        ))
        self.assertEqual(zero["complete_source_seconds"], 0)
        self.assertTrue(all(row["seconds"] == 0 for lane in zero["final_receipts"]
                            for row in lane["methods"] + lane["class_phases"]))

    def test_final_timing_projection_refuses_missing_malformed_and_private_identity_fields(self) -> None:
        subject = self.timing_subject()
        for defect in ("final", "prefix", "method-missing", "method-duplicate", "class-missing",
                       "final-private", "method-private", "class-private", "parent-private", "parent-missing",
                       "parent-origin", "plan-private-ID", "plan-oversized-ID", "source-digest",
                       "plan-hash", "prefix-agreement"):
            with self.subTest(defect=defect):
                plan, plan_sha, finals, prefixes, phases, total = copy.deepcopy(subject)
                if defect == "final":
                    finals.pop("serial")
                elif defect == "prefix":
                    prefixes.pop("cli-1")
                elif defect == "method-missing":
                    finals["serial"]["methods"] = []
                elif defect == "method-duplicate":
                    finals["serial"]["methods"] *= 2
                elif defect == "class-missing":
                    finals["serial"]["class_phases"] = []
                elif defect == "final-private":
                    finals["serial"]["stdout"] = "PRIVATE_TIMING_CANARY"
                elif defect == "method-private":
                    finals["serial"]["methods"][0]["private"] = "PRIVATE_TIMING_CANARY"
                elif defect == "class-private":
                    finals["serial"]["class_phases"][0]["class"] = "C:\\PRIVATE_TIMING_CANARY"
                elif defect == "parent-private":
                    phases[0]["private"] = "PRIVATE_TIMING_CANARY"
                elif defect == "parent-missing":
                    phases.pop()
                elif defect == "parent-origin":
                    phases[0]["independent_cli_started_with_serial"] = "PRIVATE_TIMING_CANARY"
                elif defect == "plan-private-ID":
                    plan["ids"][0] = "C:\\PRIVATE_TIMING_CANARY"
                elif defect == "plan-oversized-ID":
                    plan["ids"][0] = "test_source.Fixture." + "x" * 512
                elif defect == "source-digest":
                    plan["inputs"]["private"] = "PRIVATE_TIMING_CANARY"
                elif defect == "plan-hash":
                    plan_sha = "0" * 64
                elif defect == "prefix-agreement":
                    prefixes["cli-1"]["methods"][0]["seconds"] += 0.000001
                with self.assertRaises(source_tests.PipelineError) as caught:
                    source_tests.final_timing_projection(
                        plan, plan_sha, finals, prefixes, phases, total, overlap=True,
                    )
                self.assertNotIn("PRIVATE_TIMING_CANARY", str(caught.exception))

    def test_final_timing_projection_refuses_nonfinite_negative_bool_and_inconsistent_intervals(self) -> None:
        subject = self.timing_subject()
        for scope in ("method", "class", "final", "prefix", "parent", "complete"):
            for invalid in (-1, True, None, "private duration", float("nan"), float("inf"), float("-inf"), 1 << 10000):
                with self.subTest(scope=scope, type=type(invalid).__name__):
                    plan, plan_sha, finals, prefixes, phases, total = copy.deepcopy(subject)
                    if scope == "method":
                        finals["serial"]["methods"][0]["seconds"] = invalid
                    elif scope == "class":
                        finals["serial"]["class_phases"][0]["seconds"] = invalid
                    elif scope == "final":
                        finals["serial"]["seconds"] = invalid
                    elif scope == "prefix":
                        prefixes["cli-1"]["seconds"] = invalid
                    elif scope == "parent":
                        phases[0]["seconds"] = invalid
                    else:
                        total = invalid
                    with self.assertRaises(source_tests.PipelineError):
                        source_tests.final_timing_projection(
                            plan, plan_sha, finals, prefixes, phases, total, overlap=True,
                        )
        for scope in ("method", "class", "final", "prefix", "parent"):
            with self.subTest(inconsistent=scope):
                plan, plan_sha, finals, prefixes, phases, total = copy.deepcopy(subject)
                if scope == "method":
                    finals["serial"]["methods"][0]["seconds"] = total + 1
                elif scope == "class":
                    finals["serial"]["class_phases"][0]["seconds"] = total + 1
                elif scope == "final":
                    finals["serial"]["seconds"] = total + 1
                elif scope == "prefix":
                    prefixes["cli-1"]["seconds"] = total - 0.5
                else:
                    phases[0]["seconds"] = total + 1
                with self.assertRaises(source_tests.PipelineError):
                    source_tests.final_timing_projection(
                        plan, plan_sha, finals, prefixes, phases, total, overlap=True,
                    )

    def test_final_timing_projection_enforces_exact_encoded_byte_bound_without_truncation(self) -> None:
        subject = self.timing_subject()
        encoded = source_tests.final_timing_projection(*subject, overlap=True)
        size = len((encoded + "\n").encode("utf-8"))
        with mock.patch.object(source_tests, "TIMING_PROJECTION_MAX_BYTES", size):
            self.assertEqual(source_tests.final_timing_projection(*subject, overlap=True), encoded)
        with mock.patch.object(source_tests, "TIMING_PROJECTION_MAX_BYTES", size - 1), \
                self.assertRaisesRegex(source_tests.PipelineError, "exceeds.*nothing truncated"):
            source_tests.final_timing_projection(*subject, overlap=True)
        plan, plan_sha, finals, prefixes, phases, total = copy.deepcopy(subject)
        finals["serial"]["class_phases"] *= 16384
        with self.assertRaisesRegex(source_tests.PipelineError, "exceeds.*nothing truncated"):
            source_tests.final_timing_projection(plan, plan_sha, finals, prefixes, phases, total, overlap=True)

    def test_final_timing_projection_overlap_output_joins_validated_prefix_and_final_intervals(self) -> None:
        self.cached_overlap_tools()
        self.fixture({name + ".py": module("self.assertTrue(True)") for name in (
            "test_generate", "test_allocation_refusal", "test_category_seed", "test_lint",
        )})
        result = self.execute(guarded=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        _, complete = self.receipt()
        projection = self.projected_timing(result.stdout)
        self.assertTrue(complete["complete_graph"])
        self.assertTrue(complete["overlap_resource_profile"]["enabled"])
        self.assertEqual(Counter(row["test"] for lane in projection["final_receipts"] for row in lane["methods"]),
                         Counter(complete["discovered_ids"]))
        for key, receipts in (
            ("final_receipts", complete["receipts"]),
            ("validated_independent_prefixes_not_final_receipts",
             complete["independent_checkpoints_not_full_lane_results"]),
        ):
            for row in projection[key]:
                self.assertEqual(row, {field: receipts[row["lane"]][field]
                                       for field in ("lane", "seconds", "methods", "class_phases")})
        self.assertEqual(projection["complete_source_seconds"], complete["seconds"])
        self.assertEqual([row["seconds"] for row in projection["parent_phases"]],
                         [row["seconds"] for row in complete["phases"]])
        self.assertNotIn(str(self.root), json.dumps(projection))
        self.assertFalse(projection["hosted_600_qualification"])

    def test_final_timing_projection_reports_validated_failed_methods_without_upgrading_the_graph(self) -> None:
        self.fixture({"test_unknown_timing.py": module("self.fail('PRIVATE_TIMING_FAILURE_DETAIL')")})
        result = self.execute(guarded=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        _, complete = self.receipt()
        projection = self.projected_timing(result.stdout)
        self.assertFalse(complete["complete_graph"])
        self.assertIn("PRIVATE_TIMING_FAILURE_DETAIL", result.stderr)
        self.assertNotIn("PRIVATE_TIMING_FAILURE_DETAIL", json.dumps(projection))
        self.assertNotIn("Complete source-test graph PASS", result.stdout)
        self.assertEqual(projection["validated_independent_prefixes_not_final_receipts"], [])
        self.assertEqual(projection["final_receipts"][0]["methods"], complete["receipts"]["serial"]["methods"])
        self.assertEqual(projection["parent_phases"][0]["origin"], "parent_phase_start")
        self.assertEqual(set(complete["receipts"]["serial"]), source_tests.RECEIPT_KEYS)

    def test_final_timing_projection_omission_invalid_bound_and_stdout_errors_fail_closed(self) -> None:
        self.fixture({"test_unknown_timing.py": module("self.assertTrue(True)")})
        for defect in ("missing", "invalid", "bound", "stdout"):
            with self.subTest(defect=defect):
                driver = self.root / "scripts" / ("timing_" + defect + ".py")
                driver.write_text(
                    "import builtins,copy\nimport run_source_tests as runner\n"
                    "original=runner.final_timing_projection\n"
                    f"defect={defect!r}\n"
                    "def project(plan,sha,finals,prefixes,phases,seconds,**options):\n"
                    "    finals=copy.deepcopy(finals)\n"
                    "    if defect=='missing': finals.clear()\n"
                    "    elif defect=='invalid': finals['serial']['methods'][0]['seconds']=float('nan')\n"
                    "    elif defect=='bound': runner.TIMING_PROJECTION_MAX_BYTES=1\n"
                    "    return original(plan,sha,finals,prefixes,phases,seconds,**options)\n"
                    "runner.final_timing_projection=project\n"
                    "def output(*args,**kwargs):\n"
                    "    if defect=='stdout' and args[0].startswith(runner.TIMING_PROJECTION_BEGIN):\n"
                    "        raise OSError('synthetic final timing output denied')\n"
                    "    return builtins.print(*args,**kwargs)\n"
                    "runner.print=output\nraise SystemExit(runner.main())\n",
                    encoding="utf-8",
                )
                result = self.execute(driver, guarded=True)
                _, complete = self.receipt()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertFalse(complete["complete_graph"])
                self.assertTrue(complete["receipts"]["serial"]["successful"])
                self.assertNotIn(source_tests.TIMING_PROJECTION_BEGIN, result.stdout)
                self.assertNotIn("Complete source-test graph PASS", result.stdout)
                self.assertIn("final timing projection", result.stderr)
                self.assertTrue(any(row["stage"] == "final timing projection"
                                    for row in complete["orchestration_errors"]))
                source_tests.remove_tree(self.root / "build" / "source-tests")

    def test_provisional_progress_keeps_bound_incremental_timings_without_receipt_acceptance(self) -> None:
        events = []
        journal = self.root / "cli-1.progress.jsonl"
        final = self.root / "cli-1.receipt.json"

        class Fixture(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                events.append("setup")
                cls.addClassCleanup(events.append, "cleanup")

            @classmethod
            def tearDownClass(cls):
                events.append("teardown")

            def test_a_success(self):
                rows = [json.loads(line) for line in journal.read_text().splitlines()]
                self.assertEqual(rows[-1]["event"], "test_started")
                self.assertEqual(rows[-1]["test"], self.id())
                self.assertFalse(final.exists())

            def test_b_failure(self):
                rows = [json.loads(line) for line in journal.read_text().splitlines()]
                self.assertEqual(sum(row["event"] == "test_stopped" for row in rows), 1)
                with self.subTest(private="PRIVATE_SYNTHETIC_PROGRESS_CANARY"):
                    self.fail("PRIVATE_SYNTHETIC_PROGRESS_CANARY")

            def test_c_skip(self):
                self.skipTest("PRIVATE_SYNTHETIC_PROGRESS_CANARY")

        Fixture.__module__, Fixture.__qualname__ = "synthetic_progress", "Fixture"
        tests = [Fixture(name) for name in ("test_a_success", "test_b_failure", "test_c_skip")]
        ids = [test.id() for test in tests]
        plan = {"version": 1, "root": str(self.root), "ids": ids,
                "assignments": {"serial": [], "cli-1": ids, "cli-2": []}, "inputs": {}}
        path = self.root / "plan.json"
        source_tests.write_json(path, plan)
        discovery = source_tests.Discovery(tests, ids, plan["assignments"])
        environment = source_tests.worker_environment(self.root / "state" / "cli-1", parallel=True)
        mocked_isolation = {name: environment[name] for name in (
            "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
            "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "GRADLE_USER_HOME",
        )}
        mocked_isolation.update({"user.home": environment["HOME"], "java.io.tmpdir": environment["TEMP"]})
        with mock.patch.object(source_tests, "ROOT", self.root), \
                mock.patch.object(source_tests, "discover", return_value=discovery) as discover, \
                mock.patch.object(source_tests, "input_snapshot", return_value={}), \
                mock.patch.object(source_tests, "verify_isolation", return_value=mocked_isolation) as isolation, \
                mock.patch.dict(sys.modules, {"synthetic_progress": types.ModuleType("synthetic_progress")}), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            code = source_tests.worker(path, "cli-1")
        self.assertEqual(code, 1)
        discover.assert_called_once_with(self.root)
        isolation.assert_called_once_with("cli-1")
        self.assertEqual(events, ["setup", "teardown", "cleanup"])
        receipt = json.loads(final.read_bytes())
        self.assertEqual(set(receipt), source_tests.RECEIPT_KEYS)
        self.assertEqual(receipt["executed_ids"], ids)
        self.assertEqual(receipt["tests_run"], 3)
        self.assertFalse(receipt["successful"])
        self.assertEqual(len(receipt["failures"]), 1)
        self.assertEqual(len(receipt["skipped"]), 1)
        self.assertEqual(receipt["errors"], [])
        rows = [json.loads(line) for line in journal.read_text().splitlines()]
        self.assertEqual([row["sequence"] for row in rows], list(range(len(rows))))
        self.assertEqual({row["plan_sha256"] for row in rows}, {source_tests.sha256_file(path)})
        self.assertEqual({row["pid"] for row in rows}, {os.getpid()})
        self.assertEqual({row["lane"] for row in rows}, {"cli-1"})
        self.assertTrue(all(row["provisional"] is True and row["full_graph_qualified"] is False for row in rows))
        self.assertEqual([row["elapsed_seconds"] for row in rows], sorted(row["elapsed_seconds"] for row in rows))
        starts = [row for row in rows if row["event"] == "test_started"]
        stops = [row for row in rows if row["event"] == "test_stopped"]
        self.assertEqual([row["test"] for row in starts], ids)
        self.assertEqual([row["index"] for row in starts], list(range(3)))
        self.assertEqual([{"test": row["test"], "seconds": row["seconds"]} for row in stops], receipt["methods"])
        self.assertEqual([row["outcome_kinds"] for row in stops], [["success"], ["failure"], ["skip"]])
        phases = [row for row in rows if row["event"] == "class_phase_stopped"]
        self.assertEqual([{key: row[key] for key in ("class", "phase", "seconds")} for row in phases],
                         receipt["class_phases"])
        self.assertNotIn("PRIVATE_SYNTHETIC_PROGRESS_CANARY", journal.read_text())
        self.assertEqual(rows[-1]["event"], "tests_finished")
        progress_only = self.root / "provisional-only.json"
        source_tests.write_json(progress_only, rows[-1])
        with self.assertRaisesRegex(source_tests.PipelineError, "malformed source-test child receipt"):
            source_tests.validate_receipt(progress_only, plan, "cli-1", source_tests.sha256_file(path),
                                          source_tests.ChildExit(os.getpid(), 0))
        with self.assertRaises(source_tests.ChildResultError):
            source_tests.validate_receipt(final, plan, "cli-1", source_tests.sha256_file(path),
                                          source_tests.ChildExit(os.getpid(), 1))
        controls = ROOT / "build" / "source-runner-controls"
        controls.mkdir(exist_ok=True)
        retained = Path(tempfile.mkdtemp(prefix="progress-pure-", dir=controls))
        for artifact in (path, journal, final, progress_only):
            shutil.copyfile(artifact, retained / artifact.name)
        (retained / "stderr.txt").write_text(stderr.getvalue(), encoding="utf-8")
        (retained / "control.json").write_text(json.dumps({
            "test": self.id(), "classification": "In-process mock-discovery worker; no real lane/child/JVM workload.",
            "expected_worker_return": code, "fixture_events": events, "provisional_not_accepted": True,
        }) + "\n", encoding="utf-8")

    def test_provisional_progress_retains_interrupted_start_without_inventing_a_receipt(self) -> None:
        class Fixture(unittest.TestCase):
            def test_a_finished(self):
                self.assertTrue(True)

            def test_b_interrupted(self):
                raise KeyboardInterrupt("controlled in-process interruption")

        Fixture.__module__, Fixture.__qualname__ = "synthetic_progress_interrupt", "Fixture"
        tests = [Fixture("test_a_finished"), Fixture("test_b_interrupted")]
        ids = [test.id() for test in tests]
        plan = {"version": 1, "root": str(self.root), "ids": ids,
                "assignments": {"serial": ids, "cli-1": [], "cli-2": []}, "inputs": {}}
        path = self.root / "plan.json"
        source_tests.write_json(path, plan)
        with mock.patch.object(source_tests, "ROOT", self.root), \
                mock.patch.object(source_tests, "discover", return_value=source_tests.Discovery(tests, ids, plan["assignments"])), \
                mock.patch.object(source_tests, "input_snapshot", return_value={}), \
                mock.patch.object(source_tests, "verify_isolation", return_value={}), \
                mock.patch.dict(sys.modules, {"synthetic_progress_interrupt": types.ModuleType("synthetic_progress_interrupt")}), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as stderr, \
                self.assertRaisesRegex(KeyboardInterrupt, "controlled in-process interruption"):
            source_tests.worker(path, "serial")
        journal = self.root / "serial.progress.jsonl"
        rows = [json.loads(line) for line in journal.read_text().splitlines()]
        self.assertEqual([row["test"] for row in rows if row["event"] == "test_started"], ids)
        # TestCase stops the interrupted method in its finally; no terminal outcome or receipt is inferred.
        interrupted = [row for row in rows if row["event"] == "test_stopped" and row["test"] == ids[1]]
        self.assertEqual(len(interrupted), 1)
        self.assertEqual(interrupted[0]["outcome_kinds"], [])
        self.assertFalse(any(row["event"] == "tests_finished" for row in rows))
        self.assertFalse((self.root / "serial.receipt.json").exists())
        self.assertFalse((self.root / "serial.partial.receipt.json").exists())
        self.assertFalse((self.root / "complete.receipt.json").exists())
        self.assertTrue(all(row["full_graph_qualified"] is False for row in rows))
        controls = ROOT / "build" / "source-runner-controls"
        controls.mkdir(exist_ok=True)
        retained = Path(tempfile.mkdtemp(prefix="progress-interrupt-", dir=controls))
        for artifact in (path, journal):
            shutil.copyfile(artifact, retained / artifact.name)
        (retained / "stderr.txt").write_text(stderr.getvalue(), encoding="utf-8")
        (retained / "control.json").write_text(json.dumps({
            "test": self.id(), "classification": "In-process mocked interruption, not a killed child or cleanup qualification.",
            "original_exception": "KeyboardInterrupt", "invented_receipts": False,
        }) + "\n", encoding="utf-8")

    def test_provisional_progress_io_errors_keep_failed_results_and_once_only_cleanup(self) -> None:
        original_dump, original_open = json.dump, Path.open
        controls = ROOT / "build" / "source-runner-controls"
        controls.mkdir(exist_ok=True)
        retained = Path(tempfile.mkdtemp(prefix="progress-errors-", dir=controls))
        for defect in ("write", "close"):
            with self.subTest(defect=defect):
                events = []

                class Fixture(unittest.TestCase):
                    @classmethod
                    def setUpClass(cls):
                        events.append("setup")
                        cls.addClassCleanup(events.append, "cleanup")

                    @classmethod
                    def tearDownClass(cls):
                        events.append("teardown")

                    def test_failure(self):
                        events.append("assertion")
                        self.fail("controlled original assertion failure")

                Fixture.__module__, Fixture.__qualname__ = "synthetic_progress_error", "Fixture"
                test = Fixture("test_failure")
                ids = [test.id()]
                home = self.root / defect
                home.mkdir()
                plan = {"version": 1, "root": str(self.root), "ids": ids,
                        "assignments": {"serial": ids, "cli-1": [], "cli-2": []}, "inputs": {}}
                path = home / "plan.json"
                source_tests.write_json(path, plan)

                def dump(value, stream, **options):
                    if defect == "write" and value.get("provisional") and value["event"] == "test_started":
                        raise OSError("controlled progress write failure")
                    return original_dump(value, stream, **options)

                class CloseFailure:
                    def __init__(self, stream):
                        self.stream = stream

                    def __enter__(self):
                        return self.stream.__enter__()

                    def __exit__(self, *error):
                        self.stream.__exit__(*error)
                        raise OSError("controlled progress close failure")

                def opened(path, *args, **kwargs):
                    stream = original_open(path, *args, **kwargs)
                    return CloseFailure(stream) if defect == "close" and path.name.endswith(".progress.jsonl") else stream

                with mock.patch.object(source_tests, "ROOT", self.root), \
                        mock.patch.object(source_tests, "discover", return_value=source_tests.Discovery([test], ids, plan["assignments"])), \
                        mock.patch.object(source_tests, "input_snapshot", return_value={}), \
                        mock.patch.object(source_tests, "verify_isolation", return_value={}), \
                        mock.patch.dict(sys.modules, {"synthetic_progress_error": types.ModuleType("synthetic_progress_error")}), \
                        mock.patch.object(source_tests.json, "dump", side_effect=dump), \
                        mock.patch.object(Path, "open", opened), \
                        mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
                    code = source_tests.worker(path, "serial")
                self.assertEqual(code, 1)
                self.assertEqual(events, ["setup", "assertion", "teardown", "cleanup"])
                receipt = json.loads((home / "serial.receipt.json").read_bytes())
                self.assertFalse(receipt["successful"])
                self.assertEqual(receipt["executed_ids"], ids)
                self.assertEqual(len(receipt["failures"]), 1)
                self.assertIn("controlled original assertion failure", receipt["failures"][0][1])
                self.assertEqual(len(receipt["errors"]), 1)
                self.assertIn("controlled progress " + defect + " failure", receipt["errors"][0][1])
                self.assertEqual(len(receipt["methods"]), 1)
                self.assertEqual([row["phase"] for row in receipt["class_phases"]], ["setUpClass", "tearDownClass"])
                destination = retained / defect
                shutil.copytree(home, destination)
                (destination / "stderr.txt").write_text(stderr.getvalue(), encoding="utf-8")
        (retained / "control.json").write_text(json.dumps({
            "test": self.id(), "classification": "Pure/mock progress I/O failures; original failure and fixture cleanup retained.",
            "expected_worker_returns": [1, 1], "no_success_fallback": True,
        }) + "\n", encoding="utf-8")

    def test_provisional_progress_cannot_replace_an_existing_journal_or_start_a_test_after_open_failure(self) -> None:
        test = unittest.FunctionTestCase(lambda: self.fail("test must not begin after journal open failure"))
        ids = [test.id()]
        plan = {"version": 1, "root": str(self.root), "ids": ids,
                "assignments": {"serial": ids, "cli-1": [], "cli-2": []}, "inputs": {}}
        path = self.root / "plan.json"
        source_tests.write_json(path, plan)
        journal = self.root / "serial.progress.jsonl"
        before = b"previous provisional bytes; never overwritten\n"
        journal.write_bytes(before)
        with mock.patch.object(source_tests, "ROOT", self.root), \
                mock.patch.object(source_tests, "discover", return_value=source_tests.Discovery([test], ids, plan["assignments"])), \
                mock.patch.object(source_tests, "input_snapshot", return_value={}), \
                mock.patch.object(source_tests, "verify_isolation", return_value={}), \
                self.assertRaises(FileExistsError):
            source_tests.worker(path, "serial")
        self.assertEqual(journal.read_bytes(), before)
        self.assertFalse((self.root / "serial.receipt.json").exists())

    def test_actual_complete_default_discovery_serializes_unknown_generation_before_two_isolated_lanes(self) -> None:
        serial = module(
            "from pathlib import Path\nimport os\n"
            "root=Path(__file__).resolve().parents[2]\n"
            "generated=root/'build'/'generated'\ngenerated.mkdir(parents=True)\n"
            "(generated/'control.txt').write_text('fresh serialized generation')"
        )
        parallel = module(
            "from pathlib import Path\nimport os,json,time\n"
            "root=Path(__file__).resolve().parents[2]\n"
            "self.assertEqual((root/'build'/'generated'/'control.txt').read_text(), 'fresh serialized generation')\n"
            "observations=root/'build'/'observations'\nobservations.mkdir(exist_ok=True)\n"
            "name=Path(__file__).stem\n"
            "home=os.environ['HOME']; temporary=os.environ['TEMP']\n"
            "(observations/(name+'.ready')).write_text('ready')\n"
            "deadline=time.monotonic()+10\n"
            "while not all((observations/(item+'.ready')).exists() for item in ('test_provider_constraints','test_schema_usage')):\n"
            "    self.assertLess(time.monotonic(), deadline, 'separate processes must overlap')\n"
            "    time.sleep(0.01)\n"
            "(observations/(name+'.json')).write_text(json.dumps({'pid':os.getpid(),'home':home,'temp':temporary}))"
        )
        self.fixture({"test_unknown_generation.py": serial, "test_provider_constraints.py": parallel,
                      "test_schema_usage.py": parallel})
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        path, receipt = self.receipt()
        self.assertTrue(receipt["complete_graph"])
        self.assertEqual(len(receipt["discovered_ids"]), 3)
        self.assertEqual(set(receipt["discovered_ids"]), set(receipt["validated_executed_ids"]))
        self.assertEqual(receipt["assignments"]["serial"],
                         ["test_unknown_generation.Fixture.test_control"])
        self.assertEqual([phase["phase"] for phase in receipt["phases"]], ["serial", "parallel-cli"])
        children = receipt["phases"][1]["children"]
        self.assertEqual(len({child["pid"] for child in children.values()}), 2)
        for lane in ("cli-1", "cli-2"):
            child = receipt["receipts"][lane]
            self.assertEqual(Path(child["isolation"]["user.home"]),
                             path.parent / "state" / lane / "home")
            self.assertEqual(Path(child["isolation"]["java.io.tmpdir"]),
                             path.parent / "state" / lane / "temp")
            self.assertFalse(Path(child["isolation"]["TEMP"]).exists())
        values = [json.loads(file.read_text()) for file in (self.root / "build" / "observations").glob("*.json")]
        self.assertEqual(len({value["pid"] for value in values}), 2)
        self.assertEqual(len({value["home"] for value in values}), 2)
        self.assertEqual(len({value["temp"] for value in values}), 2)
        self.assertTrue(receipt["state_cleaned_after_child_join"])
        self.assertFalse(receipt["hosted_600_qualification"])
        self.assertEqual(len(receipt["shared_input_fences"]), 1)
        fence = receipt["shared_input_fences"][0]
        self.assertTrue(fence["unchanged"])
        self.assertEqual(fence["before"], fence["after"])
        self.assertIn("build/generated/control.txt", fence["before"])

    def test_every_unproved_module_defaults_to_serial_without_method_whitelists(self) -> None:
        safe = {name for lane in source_tests.PARALLEL_MODULES for name in lane}
        self.assertEqual(safe, {"test_lint", "test_provider_constraints", "test_core_cli_guards",
                               "test_schema_usage", "test_provider_transport", "test_custom_destinations",
                               "test_breaking", "test_schema_compatibility", "test_typescript_wire",
                               "test_category_seed", "test_import_provider", "test_allocation_refusal",
                               "test_error_provider", "test_authentication_provider", "test_egress_errors",
                               "test_generation_safety"})
        self.fixture({"test_new_unproved_module.py": module("self.assertTrue(True)")})
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        _, receipt = self.receipt()
        self.assertEqual(receipt["assignments"]["serial"], receipt["discovered_ids"])
        self.assertEqual(receipt["assignments"]["cli-1"], [])
        self.assertEqual(receipt["assignments"]["cli-2"], [])
        self.assertTrue(any((self.root / "scripts" / "__pycache__").glob("pl_contracts.*.pyc")),
                        "the fixture's ordinary caller must not depend on suppressed bytecode")

    def test_new_pure_cli_modules_run_every_discovered_method_in_their_owned_lanes(self) -> None:
        names = ("test_import_provider", "test_allocation_refusal", "test_error_provider",
                 "test_authentication_provider", "test_egress_errors")
        self.fixture({name + ".py": module(
            "from pathlib import Path\nimport os,tempfile\n"
            "with tempfile.TemporaryDirectory() as owned:\n"
            "    self.assertEqual(Path(owned).parent, Path(os.environ['TEMP']))\n"
            "    (Path(owned)/'synthetic-fixture.txt').write_text('owned only')"
        ) for name in names})
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        _, receipt = self.receipt()
        self.assertEqual(len(receipt["discovered_ids"]), 5)
        self.assertEqual(set(receipt["discovered_ids"]), set(receipt["validated_executed_ids"]))
        self.assertEqual(receipt["assignments"]["serial"], [])
        self.assertEqual({item.split(".")[0] for item in receipt["assignments"]["cli-1"]},
                         {"test_import_provider", "test_allocation_refusal"})
        self.assertEqual({item.split(".")[0] for item in receipt["assignments"]["cli-2"]},
                         {"test_error_provider", "test_authentication_provider", "test_egress_errors"})
        self.assertEqual(len({receipt["receipts"][lane]["pid"] for lane in ("cli-1", "cli-2")}), 2)
        self.assertTrue(receipt["state_cleaned_after_child_join"])

    def test_generation_safety_child_heap_options_preserve_all_four_owned_environments(self) -> None:
        import test_generation_safety as safety
        from support import SpecDir

        class CapturedChild(Exception):
            pass

        environment = source_tests.worker_environment(self.root / "owned lane with spaces", parallel=True)
        environment["JAVA_TOOL_OPTIONS"] = "-Xmx768m -Dfixture=kept " + environment["JAVA_TOOL_OPTIONS"]
        sites = (
            (safety.ConstructiveExampleTest, "test_aggregate_unconstructible_patterns_are_refused_before_every_target_output", 90),
            (safety.ConstructiveExampleTest, "test_huge_upper_counts_and_bounded_nested_alternatives_generate_all_three_real_targets", 180),
            (safety.GenerationTransactionTest, "test_actual_downstream_generator_failure_preserves_existing_tree_and_cleans_stage", 90),
            (safety.GenerationTransactionTest, "test_actual_cli_late_failure_reports_no_partial_client_set", 180),
        )
        observed = []

        def capture(command, **options):
            if command[0] == safety.node_executable():
                return subprocess.CompletedProcess(command, 0, '{"generation_examples":[]}', "")
            self.assertEqual(command[0], sys.executable)
            self.assertIn("--output-dir" if "-c" not in command else "-c", command)
            observed.append((command, options))
            raise CapturedChild

        for test_class, method, timeout in sites:
            with self.subTest(method=method):
                case = test_class(method)
                case.good, case.bad, case.output = self.root / "good.yaml", self.root / "bad.yaml", self.root / "output"
                with mock.patch.dict(os.environ, environment, clear=True):
                    case.spec = SpecDir()
                    try:
                        with mock.patch.object(safety.subprocess, "run", side_effect=capture), \
                                self.assertRaises(CapturedChild):
                            getattr(case, method)()
                    finally:
                        case.spec.cleanup()
                child = observed[-1][1]
                self.assertEqual(child["timeout"], timeout)
                self.assertEqual(child["env"]["JAVA_TOOL_OPTIONS"], environment["JAVA_TOOL_OPTIONS"] + " -Xmx256m")
                self.assertEqual(child["env"]["PYTHONDONTWRITEBYTECODE"], "1")
                for name in ("HOME", "TEMP", "TMP", "TMPDIR", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS"):
                    self.assertEqual(child["env"].get(name), environment.get(name))
        self.assertEqual(len(observed), 4)

    def test_generation_safety_classification_keeps_canonical_and_nullable_work_gated(self) -> None:
        self.assertEqual(source_tests.classify_module("test_generation_safety"), ("cli-1", True))
        for name in ("test_generate", "test_nullable_status_generation", "test_new_unproved_module"):
            self.assertEqual(source_tests.classify_module(name), ("serial", False))
        for name in source_tests.SDK_READERS:
            lane, independent = source_tests.classify_module(name)
            self.assertIn(lane, ("cli-1", "cli-2"))
            self.assertFalse(independent)
        self.assertNotIn("test_generation_safety", source_tests.REVIEWED_SERIAL_MODULES)
        ids = ["test_generation_safety.First.test_one", "test_generation_safety.Second.test_two",
               "test_category_seed.Fixture.test_reader", "test_generate.Fixture.test_writer",
               "test_nullable_status_generation.Fixture.test_native"]
        plan = {"assignments": {"cli-1": ids, "serial": [], "cli-2": []}}
        with mock.patch.object(source_tests, "classify_module", wraps=source_tests.classify_module) as classify:
            self.assertEqual(source_tests.independent_ids(plan, "cli-1"), ids[:2])
        self.assertEqual([call.args[0] for call in classify.call_args_list],
                         [identity.split(".", 1)[0] for identity in ids])

    def test_generation_safety_complete_module_overlaps_with_private_verification_and_once_only_fixtures(self) -> None:
        self.cached_overlap_tools()
        tree = ast.parse((ROOT / "scripts" / "tests" / "test_generation_safety.py").read_bytes())
        classes = {
            item.name: sorted(method.name for method in item.body
                              if isinstance(method, ast.FunctionDef) and method.name.startswith("test_"))
            for item in tree.body if isinstance(item, ast.ClassDef)
        }
        classes = {name: methods for name, methods in classes.items() if methods}
        expected = sorted("test_generation_safety." + name + "." + method
                          for name, methods in classes.items() for method in methods)
        for name in ("generate_clients.py", "render_success.py", "render_categories.py"):
            shutil.copyfile(ROOT / "scripts" / name, self.root / "scripts" / name)
        shutil.copyfile(ROOT / "scripts" / "tests" / "support.py", self.root / "scripts" / "tests" / "support.py")
        shutil.copyfile(ROOT / "scripts" / "tests" / "test_generation_safety.py",
                        self.root / "scripts" / "safety_fixture_source.py")
        golden = self.root / "generator" / "golden.json"
        golden.parent.mkdir()
        golden.write_text("{}\n", encoding="utf-8")
        common = (
            "from pathlib import Path\nimport os,time\nimport generate_clients as gc\n"
            "root=Path(__file__).resolve().parents[2]\n"
        )
        serial = common + (
            "deadline=time.monotonic()+10\n"
            "while not (root/'build'/'private-verify-patched').exists():\n"
            "    self.assertLess(time.monotonic(),deadline,'scratch verify must overlap canonical work')\n"
            "    time.sleep(0.01)\n"
            "self.assertEqual(gc.BUILD,root/'build')\n"
            "self.assertEqual(gc.GOLDEN,root/'generator'/'golden.json')\n"
            "self.assertNotEqual(os.environ.get('JAVA_TOOL_OPTIONS'),'synthetic lane-only options')\n"
            "self.assertEqual(gc.GOLDEN.read_text(),'{}\\n')\n"
            "canonical=gc.BUILD/'generated'/'canonical.txt';canonical.parent.mkdir()\n"
            "draft=canonical.with_suffix('.tmp');draft.write_text('exclusive canonical writer')\n"
            "draft.replace(canonical)\n"
            "(root/'build'/'canonical-scope-checked').write_text(str(os.getpid()))"
        )
        nullable = common + (
            "self.assertEqual((gc.BUILD/'generated'/'canonical.txt').read_text(),'exclusive canonical writer')\n"
            "(root/'build'/'nullable-finished').write_text(str(os.getpid()))"
        )
        reader = common + (
            "self.assertTrue((root/'build'/'nullable-finished').is_file())\n"
            "self.assertEqual((gc.BUILD/'generated'/'canonical.txt').read_text(),'exclusive canonical writer')"
        )
        control = """import json,os,shutil,tempfile,time,unittest
from pathlib import Path
from unittest import mock
import generate_clients as gc
import safety_fixture_source as safety
import toolchain
root=Path(__file__).resolve().parents[2]
events=root/'build'/'generation-safety-events.jsonl'
def record(phase,name):
    with events.open('a',encoding='utf-8') as stream:
        stream.write(json.dumps({'phase':phase,'name':name,'pid':os.getpid()})+'\\n')
def module_cleanup():
    module_home.rmdir()
    record('module_cleanup','test_generation_safety')
def setUpModule():
    global module_home,tools
    module_home=Path(tempfile.mkdtemp(prefix='module-fixture-'))
    (module_home/'live').write_text('non-idempotent teardown')
    unittest.addModuleCleanup(module_cleanup)
    with mock.patch.object(toolchain,'download',side_effect=AssertionError('no acquisition')):
        tools=toolchain.ensure_installed()
    record('module_setup','test_generation_safety')
def tearDownModule():
    (module_home/'live').unlink()
    record('module_teardown','test_generation_safety')
class _ControlCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home=Path(tempfile.mkdtemp(prefix='class-fixture-'))
        cls.baseline=cls.home/'baseline'
        (cls.home/'live').write_text('non-idempotent class teardown')
        for language in gc.LANGUAGES:
            target=cls.baseline/language;target.mkdir(parents=True)
            (target/'small.txt').write_text('synthetic staged body')
        cls.addClassCleanup(cls.cleanup)
        record('class_setup',cls.__name__)
    @classmethod
    def tearDownClass(cls):
        (cls.home/'live').unlink()
        record('class_teardown',cls.__name__)
    @classmethod
    def cleanup(cls):
        shutil.rmtree(cls.home)
        record('class_cleanup',cls.__name__)
    def exercise(self):
        record('method',self.id())
        output=self.home/self._testMethodName;output.mkdir()
        self.assertTrue(output.is_relative_to(Path(os.environ['TEMP'])))
        if self._testMethodName!='test_verify_failure_preserves_client_set_and_golden':
            (output/'independent.txt').write_text(self.id())
            return
        case=safety.GenerationTransactionTest(self._testMethodName)
        case.baseline,case.output,case.tools=self.baseline,output,tools
        original_build,original_golden=gc.BUILD,gc.GOLDEN
        calls=[]
        def generate(language,target,tools,spec=gc.SPEC):
            self.assertEqual(gc.BUILD,output/'build')
            self.assertEqual(gc.GOLDEN,output/'golden.json')
            self.assertTrue(target.is_relative_to(gc.BUILD))
            calls.append(language)
            with mock.patch.dict(os.environ,{'JAVA_TOOL_OPTIONS':'synthetic lane-only options'}), \
                    mock.patch.object(Path,'replace',side_effect=PermissionError('worker-local replacement denial')):
                (root/'build'/'private-verify-patched').write_text(str(os.getpid()))
                deadline=time.monotonic()+10
                while not (root/'build'/'canonical-scope-checked').exists():
                    self.assertLess(time.monotonic(),deadline,'serial globals/environment/Path must remain independent')
                    time.sleep(0.01)
            target.mkdir(parents=True)
            (target/'small.txt').write_text('stubbed native output')
            return {}
        with mock.patch.object(gc,'_provider_declarations',return_value={}), \
                mock.patch.object(gc,'generate',side_effect=generate), \
                mock.patch.object(gc,'run',side_effect=AssertionError('no SDK/compiler invocation')):
            case.test_verify_failure_preserves_client_set_and_golden()
        self.assertEqual(calls,['python'])
        self.assertEqual((gc.BUILD,gc.GOLDEN),(original_build,original_golden))
"""
        for name, methods in classes.items():
            control += "\nclass " + name + "(_ControlCase):\n"
            for method in methods:
                control += "    def " + method + "(self): self.exercise()\n"
        self.fixture({
            "test_generation_safety.py": control,
            "test_generate.py": module(serial),
            "test_nullable_status_generation.py": module(nullable),
            "test_category_seed.py": module(reader),
            "test_typescript_wire.py": module(reader),
            "test_provider_transport.py": module(reader),
            "test_authentication_provider.py": module("self.assertTrue(True)"),
        })
        result = self.execute(guarded=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        _, complete = self.receipt()
        self.assertTrue(complete["complete_graph"])
        self.assertTrue(complete["overlap_resource_profile"]["enabled"])
        prefix = complete["independent_checkpoints_not_full_lane_results"]["cli-1"]
        final = complete["receipts"]["cli-1"]
        self.assertEqual(prefix["assigned_ids"], expected)
        self.assertEqual(prefix["executed_ids"], expected)
        self.assertEqual(Counter(row["id"] for row in prefix["test_results"]), Counter(expected))
        self.assertTrue(all(row["started"] and row["stopped"] and row["outcomes"][0]["kind"] == "success"
                            for row in prefix["test_results"]))
        self.assertEqual(Counter(complete["validated_executed_ids"]), Counter(complete["discovered_ids"]))
        self.assertEqual(len(complete["discovered_ids"]), len(expected) + 6)
        self.assertEqual(complete["assignments"]["serial"],
                         ["test_generate.Fixture.test_control", "test_nullable_status_generation.Fixture.test_control"])
        for lane in ("cli-1", "cli-2"):
            self.assertTrue(all(row["id"].split(".", 1)[0] not in source_tests.SDK_READERS
                                for row in complete["independent_checkpoints_not_full_lane_results"][lane]["test_results"]))
        records = [json.loads(line) for line in
                   (self.root / "build" / "generation-safety-events.jsonl").read_text().splitlines()]
        self.assertEqual({row["pid"] for row in records}, {final["pid"]})
        self.assertEqual(Counter(row["name"] for row in records if row["phase"] == "method"), Counter(expected))
        for phase in ("module_setup", "module_teardown", "module_cleanup"):
            self.assertEqual(sum(row["phase"] == phase for row in records), 1)
        for phase in ("class_setup", "class_teardown", "class_cleanup"):
            self.assertEqual(Counter(row["name"] for row in records if row["phase"] == phase), Counter(classes.keys()))
        for name in classes:
            phases = [row["phase"] for row in prefix["class_phases"]
                      if row["class"] == "test_generation_safety." + name]
            self.assertEqual(phases, ["setUpClass", "tearDownClass"])
        children = {**complete["phases"][0]["children"], **complete["phases"][1]["children"]}
        self.assertEqual(set(children), set(source_tests.LANES))
        self.assertEqual(len({child["pid"] for child in children.values()}), 3)
        self.assertTrue(all(child["ownership"]["cleanup_confirmed"] for child in children.values()))
        self.assertTrue(complete["state_cleaned_after_child_join"])
        self.assertTrue(complete["shared_input_fences"][0]["unchanged"])
        self.assertEqual(golden.read_text(), "{}\n")
        self.assertFalse(complete["hosted_600_qualification"])

    def test_overlap_source_workers_exist_during_serial_but_sdk_readers_wait_for_join(self) -> None:
        self.cached_overlap_tools()
        common = "from pathlib import Path\nimport time,os\nroot=Path(__file__).resolve().parents[2]\n"
        serial = common + (
            "(root/'build'/'serial-running').write_text('running')\n"
            "deadline=time.monotonic()+10\n"
            "while not all((root/'build'/name).exists() for name in ('cli-one-early','cli-two-early')):\n"
            "    self.assertLess(time.monotonic(),deadline,'independent workers must overlap serial')\n"
            "    time.sleep(0.01)\n"
            "generated=root/'build'/'generated';generated.mkdir()\n"
            "(generated/'control.txt').write_text('fresh serial output')"
        )
        early = common + (
            "deadline=time.monotonic()+10\n"
            "while not (root/'build'/'serial-running').exists():\n"
            "    self.assertLess(time.monotonic(),deadline)\n"
            "    time.sleep(0.01)\n"
            "self.assertFalse((root/'build'/'generated').exists())\n"
            "(root/'build'/'MARKER').write_text(str(os.getpid()))"
        )
        late = common + (
            "self.assertEqual((root/'build'/'generated'/'control.txt').read_text(),'fresh serial output')\n"
            "(root/'build'/'sdk-reader').write_text(str(os.getpid()))"
        )
        self.fixture({
            "test_generate.py": module(serial),
            "test_allocation_refusal.py": module(early.replace("MARKER", "cli-one-early")),
            "test_authentication_provider.py": module(early.replace("MARKER", "cli-two-early")),
            "test_category_seed.py": module(late),
        })
        result = self.execute(guarded=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        path, receipt = self.receipt()
        self.assertTrue(receipt["complete_graph"])
        self.assertTrue(receipt["overlap_resource_profile"]["enabled"])
        self.assertEqual(len(receipt["discovered_ids"]), 4)
        self.assertEqual(set(receipt["discovered_ids"]), set(receipt["validated_executed_ids"]))
        self.assertEqual([phase["phase"] for phase in receipt["phases"]], ["serial", "parallel-cli"])
        children = {**receipt["phases"][0]["children"], **receipt["phases"][1]["children"]}
        self.assertEqual(set(children), set(source_tests.LANES))
        self.assertEqual(len({child["pid"] for child in children.values()}), 3)
        self.assertTrue(all(child["ownership"]["cleanup_confirmed"] for child in children.values()))
        early_receipts = receipt["independent_checkpoints_not_full_lane_results"]
        self.assertEqual(set(early_receipts), {"cli-1", "cli-2"})
        self.assertEqual(early_receipts["cli-1"]["assigned_ids"],
                         ["test_allocation_refusal.Fixture.test_control"])
        self.assertEqual(receipt["receipts"]["cli-1"]["tests_run"], 2)
        self.assertEqual(len(receipt["shared_input_fences"]), 1)
        self.assertTrue(receipt["shared_input_fences"][0]["unchanged"])
        self.assertTrue(receipt["state_cleaned_after_child_join"])
        self.assertFalse(receipt["hosted_600_qualification"])
        self.assertTrue(path.parent.joinpath("cli-1.partial.receipt.json").is_file())

    def test_provider_lint_class_readiness_preserves_lane_and_all_method_identities(self) -> None:
        self.assertEqual(source_tests.SOURCE_ONLY_CLASSES,
                         frozenset({"test_provider_transport.ProviderWiringNegativeTest"}))
        self.assertEqual(source_tests.classify_module("test_provider_transport"), ("cli-2", False))
        self.assertEqual(source_tests.classify_module("test_provider_transport",
                         test_class="ProviderWiringNegativeTest"), ("cli-2", True))
        for name in ("ProviderTransportSchemaTest", "UnknownReader", "ProviderWiringNegativeTestAlias"):
            self.assertEqual(source_tests.classify_module("test_provider_transport", test_class=name), ("cli-2", False))
        self.assertEqual(source_tests.classify_module("test_unknown", test_class="ProviderWiringNegativeTest"),
                         ("serial", False))
        tree = ast.parse((ROOT / "scripts" / "tests" / "test_provider_transport.py").read_bytes())
        cls = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == "ProviderWiringNegativeTest")
        identities = ["test_provider_transport.ProviderWiringNegativeTest." + item.name
                      for item in cls.body if isinstance(item, ast.FunctionDef) and item.name.startswith("test_")]
        future = "test_provider_transport.ProviderWiringNegativeTest.test_new_discovered_method"
        readers = ["test_provider_transport.ProviderTransportSchemaTest.test_generated_models",
                   "test_category_seed.Fixture.test_control", "test_typescript_wire.Fixture.test_control"]
        plan = {"assignments": {"cli-2": [*identities, future, *readers], "cli-1": [], "serial": []}}
        self.assertEqual(source_tests.independent_ids(plan, "cli-2"), [*identities, future])
        self.assertEqual(len(identities), 4)
        self.cached_overlap_tools()
        serial = ["test_generate.DeterminismTest.test_verify_passes_on_the_committed_golden"]
        canonical = ["test_provider_transport.ProviderTransportSchemaTest.test_generated_models_wire_guards_on_every_closed_provider"]
        unknown = ["test_provider_transport.UnknownReader.test_control"]
        with mock.patch.object(source_tests, "ROOT", self.root):
            for cli_ids, enabled in ((identities, True), (canonical, False), (unknown, False)):
                with self.subTest(cli_ids=cli_ids):
                    discovered = source_tests.Discovery([], [*serial, *cli_ids], {
                        "serial": serial, "cli-1": [], "cli-2": cli_ids,
                    })
                    profile = source_tests.overlap_profile(discovered)
                    self.assertIs(profile["enabled"], enabled)
                    self.assertEqual(len(profile["tools"]), 3 if enabled else 0)
                    if not enabled:
                        self.assertEqual(profile["reason"], "all CLI methods require the canonical SDK barrier")

    def test_provider_lint_complete_class_runs_once_early_while_canonical_reader_waits(self) -> None:
        self.cached_overlap_tools()
        tree = ast.parse((ROOT / "scripts" / "tests" / "test_provider_transport.py").read_bytes())
        cls = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == "ProviderWiringNegativeTest")
        names = sorted(item.name for item in cls.body if isinstance(item, ast.FunctionDef) and item.name.startswith("test_"))
        expected = ["test_provider_transport.ProviderWiringNegativeTest." + name for name in names]
        control = """import json,os,shutil,tempfile,time,unittest
from pathlib import Path
root=Path(__file__).resolve().parents[2]
def record(phase,name):
    directory=next((root/'build'/'source-tests').glob('run-*'))
    with (directory/'provider-class-events.jsonl').open('a',encoding='utf-8') as stream:
        stream.write(json.dumps({'phase':phase,'name':name,'pid':os.getpid()})+'\\n')
class ProviderWiringNegativeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home=Path(tempfile.mkdtemp(prefix='independent-lint-class-'))
        (cls.home/'live').write_text('non-idempotent fixture')
        cls.addClassCleanup(cls.cleanup)
        record('setup',cls.__name__)
    @classmethod
    def tearDownClass(cls):
        (cls.home/'live').unlink()
        record('teardown',cls.__name__)
    @classmethod
    def cleanup(cls):
        shutil.rmtree(cls.home)
        record('cleanup',cls.__name__)
        (root/'build'/'negative-class-cleaned').write_text(str(os.getpid()))
    def exercise(self):
        deadline=time.monotonic()+10
        while not (root/'build'/'serial-live').exists():
            self.assertLess(time.monotonic(),deadline)
            time.sleep(0.01)
        self.assertFalse((root/'build'/'generated').exists(),'independent class must precede SDK readiness')
        (self.home/self._testMethodName).write_text('separate controlled output')
        record('method',self.id())
"""
        for name in names:
            control += "    def " + name + "(self): self.exercise()\n"
        control += """
class ProviderTransportSchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (root/'build'/'negative-class-cleaned').is_file():
            raise AssertionError('independent class cleanup must finish before SDK reader setup')
        if not (root/'build'/'generated'/'canonical.txt').is_file():
            raise AssertionError('canonical SDK reader must remain gated')
        record('sdk_setup',cls.__name__)
    def test_generated_models_wire_guards_on_every_closed_provider(self):
        self.assertEqual((root/'build'/'generated'/'canonical.txt').read_text(),'exclusive serial source')
        record('sdk_method',self.id())
"""
        serial = (
            "from pathlib import Path\nimport time\nroot=Path(__file__).resolve().parents[2]\n"
            "(root/'build'/'serial-live').write_text('running')\n"
            "deadline=time.monotonic()+10\n"
            "while not (root/'build'/'negative-class-cleaned').is_file():\n"
            "    self.assertLess(time.monotonic(),deadline,'only the independent class should overlap')\n"
            "    time.sleep(0.01)\n"
            "output=root/'build'/'generated';output.mkdir()\n"
            "(output/'canonical.txt').write_text('exclusive serial source')"
        )
        self.fixture({"test_generate.py": module(serial), "test_provider_transport.py": control,
                      "test_allocation_refusal.py": module("self.assertTrue(True)")})
        result = self.execute(guarded=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        path, complete = self.receipt()
        self.assertTrue(complete["complete_graph"])
        self.assertTrue(complete["overlap_resource_profile"]["enabled"])
        prefix = complete["independent_checkpoints_not_full_lane_results"]["cli-2"]
        final = complete["receipts"]["cli-2"]
        reader = "test_provider_transport.ProviderTransportSchemaTest.test_generated_models_wire_guards_on_every_closed_provider"
        self.assertEqual(complete["assignments"]["cli-2"], [reader, *expected])
        self.assertEqual(prefix["assigned_ids"], expected)
        self.assertEqual(prefix["executed_ids"], expected)
        self.assertEqual(final["executed_ids"], [*expected, reader])
        self.assertEqual(prefix["pid"], final["pid"])
        self.assertEqual(Counter(complete["validated_executed_ids"]), Counter(complete["discovered_ids"]))
        self.assertEqual(len(complete["discovered_ids"]), len(expected) + 3)
        self.assertTrue(all(row["started"] and row["stopped"] and
                            [outcome["kind"] for outcome in row["outcomes"]] == ["success"]
                            for row in final["test_results"]))
        records = [json.loads(line) for line in (path.parent / "provider-class-events.jsonl").read_text().splitlines()]
        self.assertEqual({row["pid"] for row in records}, {final["pid"]})
        self.assertEqual(Counter(row["name"] for row in records if row["phase"] == "method"), Counter(expected))
        for phase in ("setup", "teardown", "cleanup", "sdk_setup", "sdk_method"):
            self.assertEqual(sum(row["phase"] == phase for row in records), 1)
        self.assertLess(next(index for index, row in enumerate(records) if row["phase"] == "cleanup"),
                        next(index for index, row in enumerate(records) if row["phase"] == "sdk_setup"))
        for classname in ("ProviderWiringNegativeTest", "ProviderTransportSchemaTest"):
            phases = [row["phase"] for row in final["class_phases"]
                      if row["class"] == "test_provider_transport." + classname]
            self.assertEqual(phases, ["setUpClass", "tearDownClass"])
        self.assertEqual(complete["assignments"]["serial"], ["test_generate.Fixture.test_control"])
        self.assertEqual(complete["assignments"]["cli-1"], ["test_allocation_refusal.Fixture.test_control"])
        self.assertTrue(complete["owned_process_cleanup_confirmed"])
        self.assertTrue(complete["state_cleaned_after_child_join"])
        self.assertTrue(complete["shared_input_fences"][0]["unchanged"])
        self.assertFalse(complete["hosted_600_qualification"])

    def test_private_verify_checkpoints_are_bounded_durable_and_do_not_replace_failure(self) -> None:
        import test_generation_safety as safety

        golden = self.root / "generator" / "golden.json"
        golden.parent.mkdir()
        golden.write_bytes(b"{}\n")
        case = safety.GenerationTransactionTest("test_verify_failure_preserves_client_set_and_golden")
        case.baseline, case.output, case.tools = self.root / "baseline", self.root / "clients", {}
        case.output.mkdir()
        for language in safety.gc.LANGUAGES:
            path = case.baseline / language
            path.mkdir(parents=True)
            (path / "old.txt").write_bytes(b"controlled previous target\n")
        phases = ["before-verify", "first-generate-entry", "first-generate-return", "second-generate-refusal"]
        sync = os.fsync

        def generated(language, output, tools, spec):
            self.assertEqual(language, "python")
            self.assertEqual(tools, case.tools)
            self.assertEqual(safety.gc.BUILD, case.output / "build")
            self.assertEqual(safety.gc.GOLDEN, case.output / "golden.json")
            evidence = next((self.root / "build" / "generation-transaction-controls").glob("verify-phases-*"))
            self.assertEqual({path.stem for path in evidence.iterdir()}, set(phases[:2]))
            self.assertEqual(fsynced.call_count, 2, "entry checkpoints must be fsynced before the real delegate")
            output.mkdir(parents=True)
            (output / "new.txt").write_bytes(b"stubbed native target\n")
            return {}

        with mock.patch.object(safety, "ROOT", self.root), \
                mock.patch.object(safety.gc, "generate", side_effect=generated) as native, \
                mock.patch.object(safety.gc, "_provider_declarations", return_value={}) as declarations, \
                mock.patch.object(safety.gc, "run", side_effect=AssertionError("no native or source compiler")), \
                mock.patch.object(safety.os, "fsync", wraps=sync) as fsynced:
            case.test_verify_failure_preserves_client_set_and_golden()
        native.assert_called_once()
        self.assertEqual(declarations.call_count, 2, "the two original declaration scopes remain fresh")
        self.assertEqual(fsynced.call_count, 4)
        evidence = next((self.root / "build" / "generation-transaction-controls").glob("verify-phases-*"))
        records = [json.loads((evidence / (phase + ".json")).read_bytes()) for phase in phases]
        self.assertEqual([record["phase"] for record in records], phases)
        self.assertTrue(all(set(record) == {"phase", "seconds"} and
                            isinstance(record["seconds"], float) and record["seconds"] >= 0 for record in records))
        self.assertEqual([record["seconds"] for record in records], sorted(record["seconds"] for record in records))
        self.assertTrue(all(path.stat().st_size < 256 for path in evidence.iterdir()))
        controls = ROOT / "build" / "generation-transaction-controls"
        controls.mkdir(exist_ok=True)
        retained = Path(tempfile.mkdtemp(prefix="verify-phase-unit-", dir=controls))
        shutil.copytree(evidence, retained / "phases")
        case.output = self.root / "io-failure"
        case.output.mkdir()
        with mock.patch.object(safety, "ROOT", self.root), \
                mock.patch.object(safety.gc, "verify") as verify, \
                mock.patch.object(safety.os, "fsync", side_effect=OSError("controlled checkpoint fsync failure")), \
                self.assertRaisesRegex(OSError, "controlled checkpoint fsync failure"):
            case.test_verify_failure_preserves_client_set_and_golden()
        verify.assert_not_called()
        failed = next(path for path in (self.root / "build" / "generation-transaction-controls").glob("verify-phases-*")
                      if path != evidence)
        shutil.copytree(failed, retained / "fsync-failure-not-durable")
        (retained / "control.json").write_text(json.dumps({
            "test": self.id(), "classification": "Pure/stubbed private verify; no real generator or declaration compiler.",
            "phases": phases, "real_calls": 0, "fsync_failure_is_explicit": True,
            "negative_record_is_not_a_durability_claim": True,
        }) + "\n", encoding="utf-8")

    def test_overlap_retains_actual_early_failure_and_executes_every_sdk_dependent_method(self) -> None:
        self.cached_overlap_tools()
        self.fixture({
            "test_generate.py": module(
                "from pathlib import Path\nroot=Path(__file__).resolve().parents[2]\n"
                "generated=root/'build'/'generated';generated.mkdir()\n"
                "(generated/'control.txt').write_text('fresh serial output')"),
            "test_allocation_refusal.py": module("self.fail('synthetic independent indexed failure')"),
            "test_category_seed.py": module(
                "from pathlib import Path\nroot=Path(__file__).resolve().parents[2]\n"
                "self.assertEqual((root/'build'/'generated'/'control.txt').read_text(),'fresh serial output')"),
        })
        result = self.execute(guarded=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("synthetic independent indexed failure", result.stderr)
        _, receipt = self.receipt()
        self.assertFalse(receipt["complete_graph"])
        self.assertEqual(set(receipt["discovered_ids"]), set(receipt["validated_executed_ids"]))
        full = receipt["receipts"]["cli-1"]
        partial = receipt["independent_checkpoints_not_full_lane_results"]["cli-1"]
        self.assertFalse(partial["successful"])
        self.assertEqual(partial["failures"], full["failures"])
        self.assertEqual(full["tests_run"], 2)
        self.assertTrue(receipt["owned_process_cleanup_confirmed"])

    def test_overlap_plan_loss_aborts_sdk_readers_and_retains_the_indexed_prefix(self) -> None:
        self.cached_overlap_tools()
        self.fixture({
            "test_generate.py": module(
                "from pathlib import Path\nimport time\nroot=Path(__file__).resolve().parents[2]\n"
                "deadline=time.monotonic()+10\n"
                "while not list((root/'build'/'source-tests').glob('run-*/cli-1.partial.receipt.json')):\n"
                "    self.assertLess(time.monotonic(),deadline)\n"
                "    time.sleep(0.01)\n"
                "next((root/'build'/'source-tests').glob('run-*/plan.json')).unlink()"),
            "test_allocation_refusal.py": module("self.fail('synthetic checkpoint failure before abort')"),
            "test_category_seed.py": module(
                "from pathlib import Path\nroot=Path(__file__).resolve().parents[2]\n"
                "(root/'build'/'forbidden-sdk-reader').write_text('must not execute')"),
        })
        result = self.execute(guarded=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse((self.root / "build" / "forbidden-sdk-reader").exists())
        _, receipt = self.receipt()
        self.assertFalse(receipt["complete_graph"])
        prefix = receipt["independent_checkpoints_not_full_lane_results"]["cli-1"]
        self.assertIn("synthetic checkpoint failure before abort", prefix["failures"][0][1])
        self.assertTrue(receipt["owned_process_cleanup_confirmed"])
        self.assertTrue(receipt["state_cleaned_after_child_join"])

    def test_overlap_partial_third_launch_drains_all_started_owners_and_keeps_checkpoint_failure(self) -> None:
        self.cached_overlap_tools()
        self.fixture({
            "test_generate.py": module("import time\ntime.sleep(10)\nself.assertTrue(True)"),
            "test_allocation_refusal.py": module("self.fail('synthetic prefix before third launch failure')"),
            "test_category_seed.py": module(
                "from pathlib import Path\nroot=Path(__file__).resolve().parents[2]\n"
                "(root/'build'/'forbidden-sdk-reader').write_text('must not execute')"),
            "test_authentication_provider.py": module("self.assertTrue(True)"),
        })
        driver = self.root / "scripts" / "overlap_partial_driver.py"
        driver.write_text(
            "import subprocess,time\nfrom pathlib import Path\nimport run_source_tests as runner\n"
            "original=subprocess.Popen\ncount=0\n"
            "def launch(command,*args,**kwargs):\n"
            "    global count\n"
            "    if '--worker-plan' in command:\n"
            "        count+=1\n"
            "        if count==3:\n"
            "            deadline=time.monotonic()+10\n"
            "            while not list((Path.cwd()/'build'/'source-tests').glob('run-*/cli-1.partial.receipt.json')):\n"
            "                if time.monotonic()>=deadline: raise TimeoutError('synthetic prefix did not settle')\n"
            "                time.sleep(0.01)\n"
            "            raise OSError('synthetic third overlap launch failure')\n"
            "    return original(command,*args,**kwargs)\n"
            "runner.subprocess.Popen=launch\nraise SystemExit(runner.main())\n",
            encoding="utf-8",
        )
        result = self.execute(driver, guarded=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("synthetic third overlap launch failure", result.stderr)
        self.assertFalse((self.root / "build" / "forbidden-sdk-reader").exists())
        _, receipt = self.receipt()
        self.assertFalse(receipt["complete_graph"])
        prefix = receipt["independent_checkpoints_not_full_lane_results"]["cli-1"]
        self.assertIn("synthetic prefix before third launch failure", prefix["failures"][0][1])
        children = {**receipt["phases"][0]["children"], **receipt["phases"][1]["children"]}
        self.assertEqual(set(children), {"serial", "cli-1"})
        self.assertTrue(all(child["ownership"]["cleanup_confirmed"] for child in children.values()))
        self.assertTrue(receipt["state_cleaned_after_child_join"])

    def test_overlap_owner_construction_failure_closes_pending_pipe_and_joins_its_process(self) -> None:
        self.cached_overlap_tools()
        self.fixture({
            "test_generate.py": module("import time\ntime.sleep(10)\nself.assertTrue(True)"),
            "test_allocation_refusal.py": module("self.assertTrue(True)"),
        })
        driver = self.root / "scripts" / "overlap_owner_failure.py"
        driver.write_text(
            "import json\nfrom pathlib import Path\nimport run_source_tests as runner\n"
            "original=runner.OwnedProcess\npending=None\n"
            "def owner(process):\n"
            "    global pending\n"
            "    if '--overlap-cli' in process.args:\n"
            "        pending=process\n"
            "        raise OSError('synthetic overlap owner construction failure')\n"
            "    return original(process)\n"
            "runner.OwnedProcess=owner\ncode=runner.main()\n"
            "(Path.cwd()/'build'/'pending-control.json').write_text(json.dumps({\n"
            "    'pipe_closed':pending.stdin.closed,'exit':pending.returncode}))\n"
            "raise SystemExit(code)\n",
            encoding="utf-8",
        )
        result = self.execute(driver, guarded=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("synthetic overlap owner construction failure", result.stderr)
        pending = json.loads((self.root / "build" / "pending-control.json").read_bytes())
        self.assertTrue(pending["pipe_closed"])
        self.assertIsInstance(pending["exit"], int)
        _, receipt = self.receipt()
        self.assertFalse(receipt["complete_graph"])
        self.assertTrue(receipt["owned_process_cleanup_confirmed"])
        self.assertTrue(receipt["state_cleaned_after_child_join"])

    def test_pending_posix_cleanup_uses_retained_primitives_without_owner_reconstruction(self) -> None:
        process = mock.Mock(pid=2901, args=["synthetic pending process"])
        record = {"cleanup_confirmed": False, "leader_exit": None}
        events = []
        scans = iter((1, 1, 0))

        def active(pid):
            self.assertEqual(pid, process.pid)
            events.append("scan")
            return next(scans)

        def join(timeout):
            events.append("join")
            self.assertTrue(record["group_fenced"])
            self.assertTrue(record["group_drain_confirmed"])
            self.assertTrue(record["reap_started"])
            self.assertAlmostEqual(timeout, 4.6)
            return -9

        process.wait.side_effect = join
        with mock.patch.object(source_tests.os, "name", "posix"), \
                mock.patch.object(source_tests, "OwnedProcess",
                                  side_effect=OSError("synthetic owner construction refused")) as factory, \
                mock.patch.object(source_tests, "_wait_posix_leader",
                                  side_effect=lambda *args, **kwargs: events.append("leader") or -9) as leader, \
                mock.patch.object(source_tests, "_posix_group_active", side_effect=active) as scan, \
                mock.patch.object(source_tests.signal, "SIGKILL", 9, create=True), \
                mock.patch.object(source_tests.os, "killpg", create=True,
                                  side_effect=lambda *args: events.append("fence")) as signal_group, \
                mock.patch.object(source_tests, "perf_counter",
                                  side_effect=(100, 100.2, 100.3, 100.4)) as clock, \
                mock.patch.object(source_tests, "sleep") as sleep:
            source_tests.cleanup_pending_process(process, record)
        self.retain_linux_mock("pending constructor-independent primitives", {
            "events": events, "ownership": record,
            "shared_deadline": 105, "leader_timeout": leader.call_args.args[1],
            "join_timeout": process.wait.call_args.kwargs["timeout"],
        })
        factory.assert_not_called()
        self.assertEqual(events, ["fence", "leader", "scan", "fence", "scan", "scan", "join"])
        self.assertEqual(signal_group.call_args_list, [mock.call(process.pid, 9)] * 2)
        self.assertEqual(scan.call_count, 3)
        self.assertEqual(clock.call_count, 4)
        sleep.assert_called_once_with(0.01)
        self.assertAlmostEqual(leader.call_args.args[1], 4.8)
        self.assertEqual(leader.call_args.kwargs, {"deadline": 105})
        self.assertEqual(record["leader_exit"], -9)
        self.assertEqual(record["remaining_after_leader"], 1)
        self.assertEqual(record["remaining_after_cleanup"], 0)
        self.assertTrue(record["leader_join_confirmed"])
        self.assertTrue(record["cleanup_confirmed"])
        self.assertFalse(record["attached_before_execution"])

    def test_pending_posix_fence_scan_drain_and_join_errors_remain_unconfirmed(self) -> None:
        for label in ("fence", "scan", "scan-containment", "post-fence-scan", "drain", "join"):
            with self.subTest(boundary=label):
                process = mock.Mock(pid=2902, args=["synthetic pending process"])
                record = {"cleanup_confirmed": False, "leader_exit": None}
                error = OSError("synthetic pending " + label + " refused")
                signal_effect = error if label == "fence" else (
                    [None, OSError("synthetic pending containment denied")] if label == "scan-containment"
                    else None)
                scan_effect = error if label in {"scan", "scan-containment"} else (
                    [0, error] if label == "post-fence-scan" else None)
                process.wait.side_effect = error if label == "join" else None
                with mock.patch.object(source_tests.os, "name", "posix"), \
                        mock.patch.object(source_tests, "OwnedProcess",
                                          side_effect=AssertionError("must not rebuild pending owner")) as factory, \
                        mock.patch.object(source_tests, "_wait_posix_leader", return_value=-9) as leader, \
                        mock.patch.object(source_tests, "_posix_group_active",
                                          return_value=1 if label == "drain" else 0,
                                          side_effect=scan_effect) as scan, \
                        mock.patch.object(source_tests.signal, "SIGKILL", 9, create=True), \
                        mock.patch.object(source_tests.os, "killpg", create=True,
                                          side_effect=signal_effect) as signal_group, \
                        mock.patch.object(source_tests, "perf_counter",
                                          side_effect=(100, 100, 105) if label == "drain" else None,
                                          return_value=100), \
                        self.assertRaises(source_tests.PipelineError if label == "drain" else OSError) as caught:
                    source_tests.cleanup_pending_process(process, record)
                factory.assert_not_called()
                self.assertFalse(record["cleanup_confirmed"])
                self.assertFalse(record["leader_join_confirmed"])
                self.assertEqual(record["reap_started"], label == "join")
                self.assertEqual(record["group_drain_confirmed"], label == "join")
                self.assertEqual(record["group_fenced"], label != "fence")
                if label == "drain":
                    self.assertIn("did not drain", str(caught.exception))
                else:
                    self.assertIs(caught.exception, error)
                if label == "scan-containment":
                    self.assertIn("containment denied", str(caught.exception.__cause__))
                    self.assertTrue(any("containment denied" in note for note in caught.exception.__notes__))
                if label != "join":
                    process.wait.assert_not_called()
                if label == "fence":
                    leader.assert_not_called()
                    scan.assert_not_called()
                self.retain_linux_mock("pending refusal " + label, {
                    "ownership": record, "error": str(caught.exception),
                    "group_signal_count": signal_group.call_count,
                    "join_count": process.wait.call_count,
                })

    def test_pending_owner_failure_keeps_closed_pipe_primary_error_and_cleanup_uncertainty(self) -> None:
        plan = self.root / "pending-owner-plan.json"
        plan.write_text("{}", encoding="utf-8")
        serial, pending = mock.Mock(pid=2903), mock.Mock(pid=2904, stdin=io.BytesIO())
        owner = mock.Mock(process=serial, exit=-9)
        owner.record = source_tests._ownership_record(os.name == "nt")

        def drained(forced):
            owner.record.update(cleanup_confirmed=True, leader_exit=-9,
                                leader_join_confirmed=True, remaining_after_cleanup=0)
            return False

        owner.finish.side_effect = drained
        primary = OSError("synthetic primary pending owner construction refused")
        secondary = OSError("synthetic pending cleanup fence refused")
        observation = source_tests.PhaseObservation()
        readiness = mock.Mock()
        with mock.patch.object(source_tests.subprocess, "Popen", side_effect=(serial, pending)), \
                mock.patch.object(source_tests, "OwnedProcess", side_effect=(owner, primary)), \
                mock.patch.object(source_tests, "cleanup_pending_process", side_effect=secondary) as cleanup, \
                self.assertRaises(OSError) as caught:
            source_tests.run_phase(plan, ["serial", "cli-1"], self.root / "pending-state",
                                   observation, serial_ready=readiness)
        self.assertIs(caught.exception, primary)
        self.assertTrue(pending.stdin.closed)
        cleanup.assert_called_once_with(pending, observation.children["cli-1"]["ownership"])
        owner.finish.assert_called_once_with(forced=True)
        readiness.assert_not_called()
        self.assertTrue(observation.children["serial"]["ownership"]["cleanup_confirmed"])
        self.assertIsNone(observation.children["cli-1"]["exit"])
        self.assertFalse(observation.children["cli-1"]["ownership"]["cleanup_confirmed"])
        self.assertFalse(observation.cleanup_confirmed)
        self.assertTrue(any("pending cleanup fence refused" in error for error in observation.cleanup_errors))

    def test_overlap_exited_serial_leader_with_live_descendant_cannot_release_sdk_readers(self) -> None:
        self.cached_overlap_tools()
        self.fixture({
            "test_generate.py": module(
                "from pathlib import Path\nimport subprocess,sys,time\n"
                "root=Path(__file__).resolve().parents[2]\n"
                "marker=root/'build'/'serial-owned-descendant'\n"
                "program='from pathlib import Path;import os,time;Path('+repr(str(marker))+"
                "').write_text(str(os.getpid()));time.sleep(30)'\n"
                "subprocess.Popen([sys.executable,'-c',program],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
                "deadline=time.monotonic()+10\n"
                "while not marker.exists() or not list((root/'build'/'source-tests').glob('run-*/cli-1.partial.receipt.json')):\n"
                "    self.assertLess(time.monotonic(),deadline)\n"
                "    time.sleep(0.01)\n"
                "self.assertTrue(marker.is_file())"),
            "test_allocation_refusal.py": module("self.assertTrue(True)"),
            "test_category_seed.py": module(
                "from pathlib import Path\nroot=Path(__file__).resolve().parents[2]\n"
                "(root/'build'/'forbidden-sdk-reader').write_text('must not execute')"),
        })
        result = self.execute(guarded=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("owned descendants survived worker leader exit", result.stderr)
        self.assertFalse((self.root / "build" / "forbidden-sdk-reader").exists())
        _, receipt = self.receipt()
        self.assertFalse(receipt["complete_graph"])
        self.assertTrue(receipt["owned_process_cleanup_confirmed"])
        self.assertTrue(receipt["state_cleaned_after_child_join"])
        self.assertIn("cli-1", receipt["independent_checkpoints_not_full_lane_results"])

    def test_overlap_unverified_shared_tool_fails_before_any_worker_or_acquisition(self) -> None:
        self.cached_overlap_tools()
        jar = next((self.root / ".toolchain").glob("*.jar"))
        jar.write_bytes(jar.read_bytes() + b"tampered")
        self.fixture({
            "test_generate.py": module("self.fail('serial must not start with a tampered shared tool')"),
            "test_allocation_refusal.py": module("self.fail('CLI must not start with a tampered shared tool')"),
        })
        result = self.execute(guarded=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("pinned SHA-256", result.stderr)
        _, receipt = self.receipt()
        self.assertFalse(receipt["complete_graph"])
        self.assertEqual(receipt["phases"], [])
        self.assertEqual(receipt["validated_executed_ids"], [])
        self.assertNotIn("downloading", result.stdout + result.stderr)
        self.assertTrue(receipt["state_cleaned_after_child_join"])

    def test_overlap_sdk_readiness_requires_the_exact_plan_and_never_accepts_failure(self) -> None:
        digest = "2" * 64
        valid = {"version": 1, "plan_sha256": digest, "ready": True}
        for index, value in enumerate((valid, {**valid, "ready": False}, {**valid, "plan_sha256": "3" * 64},
                                       {**valid, "version": True}, {**valid, "extra": True})):
            with self.subTest(value=value):
                raw = (json.dumps(value) + "\n").encode()
                channel = mock.Mock(buffer=io.BytesIO(raw))
                with mock.patch.object(source_tests.sys, "stdin", channel):
                    if index == 0:
                        source_tests.receive_serial_release(digest)
                    else:
                        with self.assertRaises(source_tests.PipelineError):
                            source_tests.receive_serial_release(digest)
        for raw in (b"", b"x" * 4097):
            with mock.patch.object(source_tests.sys, "stdin", mock.Mock(buffer=io.BytesIO(raw))):
                with self.assertRaises((ValueError, source_tests.PipelineError)):
                    source_tests.receive_serial_release(digest)

    def test_overlap_gate_cancellation_keeps_the_actual_prefix_and_marks_sdk_unstarted(self) -> None:
        events = []

        class Fixture(unittest.TestCase):
            def test_early(self):
                self.fail("synthetic prefix before gate cancellation")

            def test_sdk(self):
                events.append("must not execute")

        def checkpoint(result):
            events.append(copy.deepcopy(result.outcomes))

        def cancelled():
            raise KeyboardInterrupt("synthetic SDK gate cancellation")

        result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=source_tests.TrackingResult).run(
            source_tests.GatedSuite([Fixture("test_early")], [Fixture("test_sdk")], checkpoint, cancelled),
        )
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("synthetic SDK gate cancellation", result.errors[0][1])
        self.assertNotIn("must not execute", events)
        self.assertEqual(len(events), 1)

    def test_scheduler_correction_class_and_module_cleanup_finish_once_before_readiness(self) -> None:
        for delayed in (False, True):
            with self.subTest(delayed=delayed):
                events = []
                resources = {"class": object()}
                early_name, late_name = "synthetic_early_fixture", "synthetic_late_fixture"
                early_module, late_module = types.ModuleType(early_name), types.ModuleType(late_name)
                marker = self.root / ("module-ready-" + str(delayed))

                def setup_module():
                    marker.write_text("owned module fixture", encoding="utf-8")
                    events.append("module setup")
                    unittest.addModuleCleanup(events.append, "module cleanup")

                def teardown_module():
                    events.append("module teardown")
                    marker.unlink()

                early_module.setUpModule = setup_module
                early_module.tearDownModule = teardown_module

                class Early(unittest.TestCase):
                    @classmethod
                    def setUpClass(cls):
                        events.append("class setup")
                        cls.addClassCleanup(events.append, "class cleanup")

                    @classmethod
                    def tearDownClass(cls):
                        events.append("class teardown")
                        resources.pop("class")

                    def test_control(self):
                        events.append("early method")

                class Late(unittest.TestCase):
                    def test_control(self):
                        events.append("delayed method")

                Early.__module__, Late.__module__ = early_name, late_name

                def checkpoint(result):
                    self.assertEqual(events.count("class teardown"), 1)
                    self.assertEqual(events.count("module teardown"), 1)
                    self.assertEqual(events.count("class cleanup"), 1)
                    self.assertEqual(events.count("module cleanup"), 1)
                    self.assertFalse(marker.exists())
                    self.assertNotIn("class", resources)
                    events.append("checkpoint")

                with mock.patch.dict(sys.modules, {early_name: early_module, late_name: late_module}):
                    result = unittest.TextTestRunner(
                        stream=io.StringIO(), resultclass=source_tests.TrackingResult,
                    ).run(source_tests.GatedSuite(
                        [Early("test_control")], [Late("test_control")] if delayed else [],
                        checkpoint, lambda: events.append("readiness"),
                    ))
                self.assertTrue(result.wasSuccessful(), result.errors)
                self.assertEqual(result.testsRun, 2 if delayed else 1)
                for event in ("class teardown", "module teardown", "class cleanup", "module cleanup"):
                    self.assertEqual(events.count(event), 1, events)
                self.assertLess(events.index("module cleanup"), events.index("readiness"))
                if delayed:
                    self.assertLess(events.index("readiness"), events.index("delayed method"))

    def test_scheduler_correction_standard_fixture_errors_survive_without_second_teardown(self) -> None:
        counts = {"class": 0, "module": 0}
        module_name = "synthetic_failing_early_fixture"
        early_module = types.ModuleType(module_name)

        def teardown_module():
            counts["module"] += 1
            raise RuntimeError("synthetic module teardown error")

        early_module.tearDownModule = teardown_module

        class Early(unittest.TestCase):
            @classmethod
            def tearDownClass(cls):
                counts["class"] += 1
                raise RuntimeError("synthetic class teardown error")

            def test_control(self):
                self.assertTrue(True)

        class Late(unittest.TestCase):
            def test_control(self):
                self.assertTrue(True)

        Early.__module__ = module_name
        captured = []
        with mock.patch.dict(sys.modules, {module_name: early_module}):
            result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=source_tests.TrackingResult).run(
                source_tests.GatedSuite(
                    [Early("test_control")], [Late("test_control")],
                    lambda result: captured.append(copy.deepcopy(result.outcomes)), lambda: None,
                ),
            )
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(result.testsRun, 2)
        self.assertEqual(counts, {"class": 1, "module": 1})
        self.assertEqual(len(result.errors), 2)
        self.assertEqual(sum(len(events) for events in captured[0].values()), 3)
        self.assertIn("synthetic class teardown error", result.errors[0][1])
        self.assertIn("synthetic module teardown error", result.errors[1][1])

    def test_scheduler_correction_failed_module_setup_keeps_error_and_cleanup_before_delayed_work(self) -> None:
        events = []
        name = "synthetic_failed_module_setup"
        fixture = types.ModuleType(name)

        def setup_module():
            unittest.addModuleCleanup(events.append, "failed module cleanup")
            raise RuntimeError("synthetic module setup failure")

        fixture.setUpModule = setup_module

        class Early(unittest.TestCase):
            def test_control(self):
                events.append("must not start")

        class Late(unittest.TestCase):
            def test_control(self):
                events.append("delayed diagnostic")

        Early.__module__ = name
        with mock.patch.dict(sys.modules, {name: fixture}):
            result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=source_tests.TrackingResult).run(
                source_tests.GatedSuite(
                    [Early("test_control")], [Late("test_control")],
                    lambda result: events.append("checkpoint"), lambda: events.append("readiness"),
                ),
            )
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("synthetic module setup failure", result.errors[0][1])
        self.assertEqual(events, ["failed module cleanup", "checkpoint", "readiness", "delayed diagnostic"])

    def test_scheduler_correction_coherent_prefix_conflicts_refuse_a_zero_exit_final_pass(self) -> None:
        self.cached_overlap_tools()
        self.fixture({
            "test_generate.py": module("self.assertTrue(True)"),
            "test_allocation_refusal.py": module("self.assertTrue(True)"),
            "test_category_seed.py": module("self.assertTrue(True)"),
        })
        for kind in ("failure", "skip", "expected_failure", "method-time", "fixture-error"):
            with self.subTest(kind=kind):
                driver = self.root / "scripts" / "prefix_conflict.py"
                driver.write_text(
                    "import json\nimport run_source_tests as runner\n"
                    "original=runner.run_phase\n"
                    "def phase(plan,*args,**kwargs):\n"
                    "    result=original(plan,*args,**kwargs)\n"
                    "    path=plan.parent/'cli-1.partial.receipt.json'\n"
                    "    value=json.loads(path.read_bytes())\n"
                    f"    kind={kind!r}\n"
                    "    if kind=='method-time': value['methods'][0]['seconds']+=1\n"
                    "    elif kind=='fixture-error':\n"
                    "        identity='tearDownClass (synthetic_fixture)'\n"
                    "        value['fixture_results']=[{'kind':'error','id':identity,'detail':'synthetic fixture witness'}]\n"
                    "        value['errors']=[[identity,'synthetic fixture witness']]\n"
                    "        value['successful']=False\n"
                    "    else:\n"
                    "        identity=value['assigned_ids'][0]\n"
                    "        value['test_results'][0]['outcomes']=[{'kind':kind,'id':identity,'detail':'synthetic prefix witness'}]\n"
                    "        field={'failure':'failures','skip':'skipped','expected_failure':'expected_failures'}[kind]\n"
                    "        value[field]=[[identity,'synthetic prefix witness']]\n"
                    "        value['successful']=kind!='failure'\n"
                    "    path.write_text(json.dumps(value),encoding='utf-8')\n"
                    "    return result\n"
                    "runner.run_phase=phase\nraise SystemExit(runner.main())\n",
                    encoding="utf-8",
                )
                result = self.execute(driver, guarded=True)
                _, complete = self.receipt()
                source_tests.remove_tree(self.root / "build" / "source-tests")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertFalse(complete["complete_graph"])
                final = complete["receipts"]["cli-1"]
                prefix = complete["independent_checkpoints_not_full_lane_results"]["cli-1"]
                self.assertEqual(prefix["pid"], final["pid"])
                self.assertEqual(prefix["plan_sha256"], final["plan_sha256"])
                self.assertTrue(final["successful"])
                self.assertEqual(complete["phases"][1]["children"]["cli-1"]["exit"], 0)
                self.assertTrue(any("checkpoint" in problem for problem in complete["problems"]))
                self.assertEqual(set(complete["validated_executed_ids"]), set(complete["discovered_ids"]))
                self.assertTrue(complete["owned_process_cleanup_confirmed"])

    def test_scheduler_correction_bad_serial_stop_cardinality_cannot_release_sdk_readers(self) -> None:
        self.cached_overlap_tools()
        self.fixture({
            "test_generate.py": module("self.assertTrue(True)"),
            "test_allocation_refusal.py": module("self.assertTrue(True)"),
            "test_category_seed.py": module(
                "from pathlib import Path\nroot=Path(__file__).resolve().parents[2]\n"
                "(root/'build'/'forbidden-sdk-reader').write_text('must not execute')"),
        })
        for kind in ("duplicate", "missing", "nonzero-success"):
            with self.subTest(kind=kind):
                driver = self.root / "scripts" / "serial_cardinality.py"
                worker = self.root / "scripts" / "serial_exit.py"
                worker.write_text(
                    "import run_source_tests as runner\ncode=runner.main()\nraise SystemExit(2 if code==0 else code)\n",
                    encoding="utf-8",
                )
                driver.write_text(
                    "import json\nimport run_source_tests as runner\noriginal=runner.run_phase\n"
                    f"kind={kind!r}\n"
                    "if kind=='nonzero-success':\n"
                    "    launch=runner.subprocess.Popen\n"
                    "    def child(command,*args,**kwargs):\n"
                    "        if '--lane' in command and command[command.index('--lane')+1]=='serial':\n"
                    f"            command=[*command[:2],{str(worker)!r},*command[3:]]\n"
                    "        return launch(command,*args,**kwargs)\n"
                    "    runner.subprocess.Popen=child\n"
                    "def phase(plan,*args,**kwargs):\n"
                    "    release=kwargs['serial_ready']\n"
                    "    def corrupt(child):\n"
                    "        path=plan.parent/'serial.receipt.json'\n"
                    "        value=json.loads(path.read_bytes())\n"
                    "        if kind=='duplicate': value['methods'].append(dict(value['methods'][0]))\n"
                    "        elif kind=='missing':\n"
                    "            value['methods']=[]\n"
                    "            for row in value['test_results']: row['stopped']=False\n"
                    "        path.write_text(json.dumps(value),encoding='utf-8')\n"
                    "        return release(child)\n"
                    "    kwargs['serial_ready']=corrupt\n"
                    "    return original(plan,*args,**kwargs)\n"
                    "runner.run_phase=phase\nraise SystemExit(runner.main())\n",
                    encoding="utf-8",
                )
                result = self.execute(driver, guarded=True)
                _, complete = self.receipt()
                source_tests.remove_tree(self.root / "build" / "source-tests")
                sdk_started = (self.root / "build" / "forbidden-sdk-reader").exists()
                if sdk_started:
                    (self.root / "build" / "forbidden-sdk-reader").unlink()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertFalse(sdk_started)
                self.assertFalse(complete["complete_graph"])
                self.assertIn("serial", complete["receipts"])
                expected = "child failed" if kind == "nonzero-success" else "stop receipts"
                self.assertTrue(any(expected in problem for problem in complete["problems"]))
                self.assertTrue(complete["owned_process_cleanup_confirmed"])

    def test_scheduler_correction_gated_prefix_is_required_but_conservative_route_needs_none(self) -> None:
        self.cached_overlap_tools()
        for route in ("present", "missing", "conservative"):
            with self.subTest(route=route):
                serial_name = "test_unknown_writer.py" if route == "conservative" else "test_generate.py"
                for name in ("test_unknown_writer.py", "test_generate.py"):
                    path = self.root / "scripts" / "tests" / name
                    if path.exists():
                        path.unlink()
                self.fixture({
                    serial_name: module("self.assertTrue(True)"),
                    "test_allocation_refusal.py": module("self.assertTrue(True)"),
                    "test_category_seed.py": module("self.assertTrue(True)"),
                })
                driver = self.root / "scripts" / "missing_checkpoint.py"
                driver.write_text(
                    "import run_source_tests as runner\noriginal=runner.run_phase\n"
                    f"missing={route == 'missing'}\n"
                    "def phase(plan,*args,**kwargs):\n"
                    "    result=original(plan,*args,**kwargs)\n"
                    "    if missing: (plan.parent/'cli-1.partial.receipt.json').unlink()\n"
                    "    return result\n"
                    "runner.run_phase=phase\nraise SystemExit(runner.main())\n",
                    encoding="utf-8",
                )
                result = self.execute(driver, guarded=True)
                _, complete = self.receipt()
                source_tests.remove_tree(self.root / "build" / "source-tests")
                self.assertEqual(result.returncode, 1 if route == "missing" else 0,
                                 result.stdout + result.stderr)
                self.assertEqual(complete["complete_graph"], route != "missing")
                self.assertEqual(complete["overlap_resource_profile"]["enabled"], route != "conservative")
                if route == "missing":
                    self.assertTrue(any("checkpoint" in problem and "missing" in problem
                                        for problem in complete["problems"]))
                elif route == "present":
                    self.assertIn("cli-1", complete["independent_checkpoints_not_full_lane_results"])
                else:
                    self.assertEqual(complete["independent_checkpoints_not_full_lane_results"], {})
                self.assertEqual(set(complete["validated_executed_ids"]), set(complete["discovered_ids"]))
                self.assertTrue(complete["owned_process_cleanup_confirmed"])

    def test_scheduler_correction_accounted_serial_failure_keeps_delayed_diagnostics(self) -> None:
        self.cached_overlap_tools()
        self.fixture({
            "test_generate.py": module("self.fail('synthetic accounted serial assertion')"),
            "test_allocation_refusal.py": module("self.assertTrue(True)"),
            "test_category_seed.py": module("self.fail('synthetic delayed diagnostic assertion')"),
        })
        result = self.execute(guarded=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("synthetic accounted serial assertion", result.stderr)
        self.assertIn("synthetic delayed diagnostic assertion", result.stderr)
        _, complete = self.receipt()
        self.assertFalse(complete["complete_graph"])
        self.assertEqual(set(complete["validated_executed_ids"]), set(complete["discovered_ids"]))
        self.assertEqual(len(complete["receipts"]["serial"]["failures"]), 1)
        self.assertEqual(len(complete["receipts"]["cli-1"]["failures"]), 1)
        self.assertTrue(complete["owned_process_cleanup_confirmed"])

    def test_scheduler_correction_matching_prefix_outcomes_and_fixture_diagnostics_reconcile(self) -> None:
        self.cached_overlap_tools()
        for kind in ("expected-skip", "fixture-errors"):
            with self.subTest(kind=kind):
                early = (
                    "import unittest\nclass Fixture(unittest.TestCase):\n"
                    "    @unittest.expectedFailure\n"
                    "    def test_expected(self): self.fail('synthetic expected outcome')\n"
                    "    @unittest.skip('synthetic recorded skip')\n"
                    "    def test_skipped(self): pass\n"
                ) if kind == "expected-skip" else (
                    "import unittest\n"
                    "def tearDownModule(): raise RuntimeError('synthetic early module diagnostic')\n"
                    "class Fixture(unittest.TestCase):\n"
                    "    @classmethod\n"
                    "    def tearDownClass(cls): raise RuntimeError('synthetic early class diagnostic')\n"
                    "    def test_control(self):\n"
                    "        self.addCleanup(lambda:self.fail('synthetic early cleanup diagnostic'))\n"
                )
                self.fixture({
                    "test_generate.py": module("self.assertTrue(True)"),
                    "test_allocation_refusal.py": early,
                    "test_category_seed.py": module("self.assertTrue(True)"),
                })
                result = self.execute(guarded=True)
                _, complete = self.receipt()
                source_tests.remove_tree(self.root / "build" / "source-tests")
                self.assertEqual(result.returncode, 0 if kind == "expected-skip" else 1,
                                 result.stdout + result.stderr)
                prefix = complete["independent_checkpoints_not_full_lane_results"]["cli-1"]
                final = complete["receipts"]["cli-1"]
                self.assertEqual(prefix["successful"], kind == "expected-skip")
                self.assertEqual(prefix["fixture_results"], final["fixture_results"])
                self.assertEqual(final["methods"][:len(prefix["methods"])], prefix["methods"])
                self.assertEqual(set(complete["validated_executed_ids"]), set(complete["discovered_ids"]))
                self.assertFalse(any("disagree" in problem for problem in complete["problems"]))
                self.assertTrue(complete["owned_process_cleanup_confirmed"])
                if kind == "expected-skip":
                    self.assertTrue(complete["complete_graph"])
                    self.assertEqual(len(prefix["skipped"]), 1)
                    self.assertEqual(len(prefix["expected_failures"]), 1)
                else:
                    self.assertFalse(complete["complete_graph"])
                    self.assertEqual(len(prefix["errors"]), 2)
                    self.assertEqual(len(prefix["failures"]), 1)

    def test_scheduler_correction_non_object_diff_markers_fail_with_planned_aggregate(self) -> None:
        self.cached_overlap_tools()
        marker = next((self.root / ".toolchain").glob("*.verified.json"))
        self.fixture({
            "test_generate.py": module("self.fail('unverified serial must not execute')"),
            "test_allocation_refusal.py": module("self.fail('unverified CLI must not execute')"),
        })
        for value in ([], None, "not an object", 0, True):
            with self.subTest(value=value):
                marker.write_text(json.dumps(value), encoding="utf-8")
                result = self.execute(guarded=True)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                _, complete = self.receipt()
                self.assertFalse(complete["complete_graph"])
                self.assertEqual(complete["phases"], [])
                self.assertEqual(complete["validated_executed_ids"], [])
                self.assertTrue(complete["orchestration_errors"])
                self.assertNotIn("AttributeError", result.stderr)
                self.assertNotIn("downloading", result.stdout + result.stderr)
                self.assertTrue(complete["state_cleaned_after_child_join"])
                source_tests.remove_tree(self.root / "build" / "source-tests")

    def test_scheduler_correction_non_object_marker_at_later_fences_preserves_lane_results(self) -> None:
        self.cached_overlap_tools()
        marker = next((self.root / ".toolchain").glob("*.verified.json"))
        valid_marker = marker.read_bytes()
        for fence in ("readiness", "final"):
            with self.subTest(fence=fence):
                marker.write_bytes(valid_marker)
                serial = "self.assertTrue(True)"
                delayed = "self.assertTrue(True)"
                corrupt = (
                    "from pathlib import Path\nroot=Path(__file__).resolve().parents[2]\n"
                    "next((root/'.toolchain').glob('*.verified.json')).write_text('[]')"
                )
                if fence == "readiness":
                    serial = (
                        "from pathlib import Path\nimport time\nroot=Path(__file__).resolve().parents[2]\n"
                        "deadline=time.monotonic()+10\n"
                        "while not list((root/'build'/'source-tests').glob('run-*/cli-1.partial.receipt.json')):\n"
                        "    self.assertLess(time.monotonic(),deadline)\n"
                        "    time.sleep(0.01)\n" + corrupt
                    )
                else:
                    delayed = corrupt
                self.fixture({
                    "test_generate.py": module(serial),
                    "test_allocation_refusal.py": module("self.assertTrue(True)"),
                    "test_category_seed.py": module(delayed + "\n" +
                        "from pathlib import Path\nroot=Path(__file__).resolve().parents[2]\n"
                        "(root/'build'/'sdk-executed').write_text('actual delayed method')"),
                })
                result = self.execute(guarded=True)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                _, complete = self.receipt()
                self.assertFalse(complete["complete_graph"])
                self.assertTrue(complete["receipts"]["serial"]["successful"])
                self.assertTrue(complete["independent_checkpoints_not_full_lane_results"]["cli-1"]["successful"])
                self.assertTrue(complete["owned_process_cleanup_confirmed"])
                self.assertTrue(complete["state_cleaned_after_child_join"])
                if fence == "readiness":
                    self.assertFalse((self.root / "build" / "sdk-executed").exists())
                else:
                    self.assertTrue(complete["receipts"]["cli-1"]["successful"])
                    self.assertEqual(set(complete["validated_executed_ids"]), set(complete["discovered_ids"]))
                    (self.root / "build" / "sdk-executed").unlink()
                self.assertNotIn("AttributeError", result.stderr)
                source_tests.remove_tree(self.root / "build" / "source-tests")

    def test_actual_import_zero_test_and_duplicate_discovery_fail_explicitly(self) -> None:
        cases = {
            "import": ("raise RuntimeError('synthetic import failure')\n", "source-test discovery failed"),
            "zero": ("import unittest\n", "zero-test discovery"),
            "duplicate": (module("self.assertTrue(True)") + "\nAlias=Fixture\n", "duplicate identities"),
        }
        for label, (text, diagnostic) in cases.items():
            with self.subTest(case=label):
                path = self.root / "scripts" / "tests" / "test_bad.py"
                path.write_text(text, encoding="utf-8")
                result = self.execute()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(diagnostic, result.stderr)
                self.assertNotIn("Complete source-test graph PASS", result.stdout)
                self.assertFalse((self.root / "build" / "source-tests").exists())
                path.unlink()

    def test_load_tests_cannot_hide_a_planted_method_from_fixed_complete_discovery(self) -> None:
        self.fixture({"test_reduced.py":
            "import unittest\nclass Fixture(unittest.TestCase):\n"
            "    def test_control(self): self.assertTrue(True)\n"
            "    def test_planted_defect_must_fail(self): self.fail('synthetic hidden defect')\n"
            "def load_tests(loader, suite, pattern):\n"
            "    return unittest.TestSuite([Fixture('test_control')])\n"
        })
        result = self.execute()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("load_tests", result.stderr)
        self.assertNotIn("Complete source-test graph PASS", result.stdout)

    def test_indexed_actual_testresult_preserves_planted_subtest_failure_and_complete_method_ids(self) -> None:
        self.fixture({"test_witness.py":
            "import unittest\nclass Fixture(unittest.TestCase):\n"
            "    def test_control(self): self.assertTrue(True)\n"
            "    def test_planted_defect_must_fail(self):\n"
            "        print('SYNTHETIC actual indexed witness')\n"
            "        with self.subTest(case='synthetic plant'):\n"
            "            self.fail('synthetic planted assertion')\n"
        })
        result = self.execute()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("SYNTHETIC actual indexed witness", result.stdout)
        self.assertIn("test_planted_defect_must_fail", result.stderr)
        _, receipt = self.receipt()
        serial = receipt["receipts"]["serial"]
        self.assertEqual(serial["executed_ids"], serial["assigned_ids"])
        witness = serial["test_results"][1]
        self.assertEqual(witness["index"], receipt["discovered_ids"].index(witness["id"]))
        self.assertEqual(witness["id"], "test_witness.Fixture.test_planted_defect_must_fail")
        self.assertTrue(witness["started"])
        self.assertTrue(witness["stopped"])
        self.assertEqual(witness["outcomes"], [{
            "kind": "failure",
            "id": serial["failures"][0][0],
            "detail": serial["failures"][0][1],
        }])
        self.assertIn("synthetic planted assertion", witness["outcomes"][0]["detail"])
        self.assertFalse(receipt["complete_graph"])
        self.assertFalse(receipt["hosted_600_qualification"])

    def test_unstarted_class_methods_preserve_actual_testresult_error_and_indexed_receipt(self) -> None:
        self.fixture({"test_unstarted.py":
            "import unittest\nclass Fixture(unittest.TestCase):\n"
            "    @classmethod\n"
            "    def setUpClass(cls): raise RuntimeError('synthetic fixture failure')\n"
            "    def test_first(self): self.assertTrue(True)\n"
            "    def test_second(self): self.assertTrue(True)\n"
        })
        result = self.execute()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("synthetic fixture failure", result.stderr)
        self.assertIn("missing, duplicate or substituted source-test execution", result.stderr)
        _, receipt = self.receipt()
        serial = receipt["receipts"]["serial"]
        self.assertEqual(serial["tests_run"], 0)
        self.assertEqual(len(serial["errors"]), 1)
        self.assertIn("setUpClass", serial["errors"][0][0])
        self.assertIn("synthetic fixture failure", serial["errors"][0][1])
        self.assertEqual([row["id"] for row in serial["test_results"]], serial["assigned_ids"])
        self.assertEqual([row["index"] for row in serial["test_results"]], [0, 1])
        self.assertTrue(all(not row["started"] and not row["stopped"]
                            for row in serial["test_results"]))
        self.assertEqual(receipt["validated_executed_ids"], [])
        self.assertTrue(receipt["state_cleaned_after_child_join"])
        self.assertFalse(receipt["complete_graph"])

    def test_skip_expected_failure_and_unexpected_success_keep_their_actual_indexed_outcomes(self) -> None:
        self.fixture({"test_outcomes.py":
            "import unittest\nclass Fixture(unittest.TestCase):\n"
            "    @unittest.expectedFailure\n"
            "    def test_expected(self): self.fail('synthetic expected failure')\n"
            "    @unittest.skip('synthetic visible skip')\n"
            "    def test_skipped(self): pass\n"
            "    @unittest.expectedFailure\n"
            "    def test_unexpected(self): self.assertTrue(True)\n"
        })
        result = self.execute()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("unexpected success", result.stderr)
        _, receipt = self.receipt()
        serial = receipt["receipts"]["serial"]
        self.assertEqual([row["index"] for row in serial["test_results"]], [0, 1, 2])
        self.assertEqual([row["outcomes"][0]["kind"] for row in serial["test_results"]],
                         ["expected_failure", "skip", "unexpected_success"])
        self.assertEqual(len(serial["expected_failures"]), 1)
        self.assertEqual(len(serial["skipped"]), 1)
        self.assertEqual(len(serial["unexpected_successes"]), 1)
        self.assertFalse(serial["successful"])
        self.assertFalse(receipt["complete_graph"])

    def test_duplicate_and_contradictory_parent_terminal_events_refuse_passing_receipts(self) -> None:
        self.fixture({"test_terminal.py": module("self.assertTrue(True)")})
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        path, complete = self.receipt()
        valid = complete["receipts"]["serial"]
        plan = json.loads(path.parent.joinpath("plan.json").read_text())
        child = source_tests.ChildExit(valid["pid"], 0)
        candidate = path.parent / "terminal-mutant.json"
        for kind in ("duplicate-success", "success-expected-failure", "success-skip"):
            with self.subTest(case=kind):
                changed = copy.deepcopy(valid)
                event = copy.deepcopy(changed["test_results"][0]["outcomes"][0])
                if kind != "duplicate-success":
                    event.update(kind="expected_failure" if kind == "success-expected-failure" else "skip",
                                 detail="synthetic contradictory terminal result")
                    changed["expected_failures" if kind == "success-expected-failure" else "skipped"].append(
                        [event["id"], event["detail"]])
                changed["test_results"][0]["outcomes"].append(event)
                candidate.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaisesRegex(source_tests.PipelineError, "terminal"):
                    source_tests.validate_receipt(candidate, plan, "serial", valid["plan_sha256"], child)
        self.assertEqual(source_tests.validate_receipt(
            path.parent / "serial.receipt.json", plan, "serial", valid["plan_sha256"], child,
        ), valid)

    def test_multiple_subtest_cleanup_diagnostics_and_skipped_subtests_keep_legal_outcomes(self) -> None:
        self.fixture({"test_legal_outcomes.py":
            "import unittest\nclass Fixture(unittest.TestCase):\n"
            "    def test_diagnostics(self):\n"
            "        self.addCleanup(lambda:self.fail('synthetic cleanup assertion'))\n"
            "        def cleanup_error(): raise RuntimeError('synthetic cleanup error')\n"
            "        self.addCleanup(cleanup_error)\n"
            "        for index in range(2):\n"
            "            with self.subTest(index=index): self.fail('synthetic subtest failure')\n"
            "    @unittest.expectedFailure\n"
            "    def test_expected(self):\n"
            "        self.fail('synthetic expected failure')\n"
            "    def test_success_after_skip(self):\n"
            "        with self.subTest(case='skip'): self.skipTest('synthetic subtest skip')\n"
            "        self.assertTrue(True)\n"
        })
        result = self.execute()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        _, complete = self.receipt()
        serial = complete["receipts"]["serial"]
        self.assertEqual(len(serial["failures"]), 3)
        self.assertEqual(len(serial["errors"]), 1)
        self.assertEqual(len(serial["skipped"]), 1)
        self.assertEqual(len(serial["expected_failures"]), 1)
        self.assertEqual(serial["executed_ids"], serial["assigned_ids"])
        self.assertIn("synthetic cleanup assertion", result.stderr)
        self.assertIn("synthetic cleanup error", result.stderr)

    def test_failed_first_phase_and_later_partial_launch_keep_failed_aggregate(self) -> None:
        self.fixture({
            "test_serial_failure.py": module("self.fail('synthetic first-phase failure')"),
            "test_provider_constraints.py": module("import time\ntime.sleep(0.2)\nself.assertTrue(True)"),
            "test_schema_usage.py": module("self.assertTrue(True)"),
        })
        driver = self.root / "scripts" / "partial_launch_driver.py"
        driver.write_text(
            "import subprocess\nimport run_source_tests as runner\n"
            "original=subprocess.Popen\ncount=0\n"
            "def launch(command,*args,**kwargs):\n"
            "    global count\n"
            "    if '--worker-plan' in command:\n"
            "        count+=1\n"
            "        if count==3: raise OSError('synthetic later partial launch failure')\n"
            "    return original(command,*args,**kwargs)\n"
            "runner.subprocess.Popen=launch\nraise SystemExit(runner.main())\n",
            encoding="utf-8",
        )
        result = self.execute(driver)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("synthetic first-phase failure", result.stderr)
        self.assertIn("synthetic later partial launch failure", result.stderr)
        path, complete = self.receipt()
        self.assertFalse(complete["complete_graph"])
        serial = complete["receipts"]["serial"]
        self.assertIn("synthetic first-phase failure", serial["failures"][0][1])
        partial = complete["phases"][1]["children"]["cli-1"]
        self.assertIsInstance(partial["pid"], int)
        self.assertIsInstance(partial["exit"], int)
        self.assertTrue(complete["state_cleaned_after_child_join"])
        self.assertFalse(path.parent.joinpath("state").exists())

    def test_snapshot_and_log_read_errors_finalize_existing_actual_failures(self) -> None:
        for defect in ("snapshot", "log-read"):
            with self.subTest(case=defect):
                self.fixture({"test_serial_io_failure.py": module("self.fail('synthetic retained failure')")})
                driver = self.root / "scripts" / (defect + "_driver.py")
                mutation = (
                    "def snapshot(root): raise OSError('synthetic snapshot read failure')\n"
                    "runner.shared_snapshot=snapshot\n"
                    if defect == "snapshot" else
                    "original=runner.Path.read_text\n"
                    "def read(path,*args,**kwargs):\n"
                    "    if path.name=='serial.stderr.txt': raise OSError('synthetic log read failure')\n"
                    "    return original(path,*args,**kwargs)\n"
                    "runner.Path.read_text=read\n"
                )
                if defect == "snapshot":
                    self.fixture({"test_lint.py": module("self.assertTrue(True)")})
                driver.write_text("import run_source_tests as runner\n" + mutation +
                                  "raise SystemExit(runner.main())\n", encoding="utf-8")
                result = self.execute(driver)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("synthetic " + ("snapshot read" if defect == "snapshot" else "log read") +
                              " failure", result.stderr)
                _, complete = self.receipt()
                self.assertIn("synthetic retained failure", complete["receipts"]["serial"]["failures"][0][1])
                self.assertFalse(complete["complete_graph"])
                self.assertTrue(complete["state_cleaned_after_child_join"])
                source_tests.remove_tree(self.root / "build" / "source-tests")

    def test_exited_leader_live_descendant_is_drained_and_cannot_overlap_the_next_phase(self) -> None:
        self.fixture({
            "test_serial_descendant.py": module(
                "from pathlib import Path\nimport subprocess,sys,time\n"
                "root=Path(__file__).resolve().parents[2]\n"
                "marker=root/'build'/'descendant-ready.txt'\n"
                "program='from pathlib import Path;import os,time;Path('+repr(str(marker))+"
                "').write_text(str(os.getpid()));time.sleep(30)'\n"
                "subprocess.Popen([sys.executable,'-c',program],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
                "deadline=time.monotonic()+5\n"
                "while not marker.exists():\n"
                "    self.assertLess(time.monotonic(),deadline)\n"
                "    time.sleep(0.01)\n"
                "self.assertTrue(marker.is_file())"
            ),
            "test_lint.py": module(
                "from pathlib import Path\n"
                "root=Path(__file__).resolve().parents[2]\n"
                "(root/'build'/'forbidden-overlap.txt').write_text('parallel phase started')"
            ),
        })
        result = self.execute(guarded=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("owned descendants survived worker leader exit", result.stderr)
        self.assertFalse((self.root / "build" / "forbidden-overlap.txt").exists())
        _, complete = self.receipt()
        self.assertEqual([row["phase"] for row in complete["phases"]], ["serial"])
        child = complete["phases"][0]["children"]["serial"]
        self.assertEqual(child["exit"], 0)
        self.assertGreater(child["ownership"]["remaining_after_leader"], 0)
        self.assertEqual(child["ownership"]["remaining_after_cleanup"], 0)
        self.assertTrue(child["ownership"]["cleanup_confirmed"])
        self.assertTrue(complete["receipts"]["serial"]["successful"])
        self.assertTrue(complete["owned_process_cleanup_confirmed"])
        self.assertTrue(complete["state_cleaned_after_child_join"])
        self.assertFalse(complete["complete_graph"])

    def test_each_launched_lane_is_drained_even_when_an_earlier_cleanup_reports_failure(self) -> None:
        self.fixture({
            "test_provider_constraints.py": module("self.assertTrue(True)"),
            "test_schema_usage.py": module("self.assertTrue(True)"),
        })
        driver = self.root / "scripts" / "cleanup_driver.py"
        driver.write_text(
            "import json\nfrom pathlib import Path\nimport run_source_tests as runner\n"
            "original=runner.OwnedProcess.finish\ncalls=[]\n"
            "def finish(owner,forced):\n"
            "    result=original(owner,forced)\n"
            "    calls.append(dict(owner.record,pid=owner.process.pid))\n"
            "    (runner.ROOT/'build'/'drained-lanes.json').write_text(json.dumps(calls))\n"
            "    if len(calls)==1: raise runner.PipelineError('synthetic first cleanup failure after drain')\n"
            "    return result\n"
            "runner.OwnedProcess.finish=finish\nraise SystemExit(runner.main())\n",
            encoding="utf-8",
        )
        result = self.execute(driver)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("synthetic first cleanup failure after drain", result.stderr)
        drained = json.loads((self.root / "build" / "drained-lanes.json").read_text())
        self.assertEqual(len(drained), 2)
        self.assertEqual(len({row["pid"] for row in drained}), 2)
        self.assertTrue(all(row["cleanup_confirmed"] and row["remaining_after_cleanup"] == 0 for row in drained))
        path, complete = self.receipt()
        self.assertFalse(complete["owned_process_cleanup_confirmed"])
        self.assertFalse(complete["state_cleaned_after_child_join"])
        self.assertTrue(path.parent.joinpath("state").is_dir())
        self.assertFalse(complete["complete_graph"])
        self.assertEqual(len(complete["receipts"]), 2)

    def test_aggregate_write_failure_is_explicit_and_preserves_actual_raw_lane_result(self) -> None:
        self.fixture({"test_serial_write_failure.py": module("self.fail('synthetic retained write failure')")})
        driver = self.root / "scripts" / "write_failure_driver.py"
        driver.write_text(
            "import run_source_tests as runner\noriginal=runner.write_json\n"
            "def write(path,value):\n"
            "    if path.name=='complete.receipt.json': raise OSError('synthetic aggregate write denied')\n"
            "    return original(path,value)\n"
            "runner.write_json=write\nraise SystemExit(runner.main())\n",
            encoding="utf-8",
        )
        result = self.execute(driver)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Failed source-test aggregate could not be written", result.stderr)
        self.assertIn("synthetic aggregate write denied", result.stderr)
        self.assertIn("synthetic retained write failure", result.stderr)
        paths = list((self.root / "build" / "source-tests").glob("run-*/serial.receipt.json"))
        self.assertEqual(len(paths), 1)
        value = json.loads(paths[0].read_text())
        self.assertIn("synthetic retained write failure", value["failures"][0][1])
        self.assertFalse(paths[0].parent.joinpath("complete.receipt.json").exists())
        self.assertFalse(paths[0].parent.joinpath("state").exists())

    def test_guard_acquisition_failure_cleans_creation_handle_before_any_native_control(self) -> None:
        child = mock.Mock()
        child.pid = 123
        child.wait.return_value = -1
        failure = OSError("synthetic pre-attachment Job acquisition failure")
        def recover(process, record):
            process.kill()
            record.update(leader_exit=process.wait(timeout=5), cleanup_confirmed=True)

        with mock.patch.object(source_tests.subprocess, "Popen", return_value=child), \
                mock.patch.object(source_tests, "OwnedProcess", side_effect=failure):
            if os.name == "nt":
                with self.assertRaisesRegex(OSError, "pre-attachment Job acquisition failure"):
                    self.execute(guarded=True)
            else:
                with mock.patch.object(source_tests, "cleanup_pending_process", side_effect=recover), \
                        self.assertRaisesRegex(OSError, "pre-attachment Job acquisition failure"):
                    self.execute(guarded=True)
        child.kill.assert_called_once_with()
        child.wait.assert_called_once_with(timeout=5)
        self.assertTrue(self.guard_failure_record["cleanup"]["cleanup_confirmed"])
        self.assertEqual(self.guard_failure_record["error"], str(failure))

    def test_expected_failure_subtest_subjects_and_duplicate_events_cannot_validate(self) -> None:
        self.fixture({"test_expected_parent.py":
            "import unittest\nclass Fixture(unittest.TestCase):\n"
            "    @unittest.expectedFailure\n"
            "    def test_expected(self):\n"
            "        with self.subTest(case='synthetic expected subtest'):\n"
            "            self.fail('synthetic expected assertion')\n"
        })
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        path, complete = self.receipt()
        valid = complete["receipts"]["serial"]
        identity = valid["assigned_ids"][0]
        self.assertEqual(valid["test_results"][0]["outcomes"][0]["id"], identity)
        self.assertEqual(valid["expected_failures"][0][0], identity)
        plan = json.loads(path.parent.joinpath("plan.json").read_text())
        child = source_tests.ChildExit(valid["pid"], 0)
        candidate = path.parent / "expected-mutant.json"
        for count in (1, 2):
            for suffix in (" (case='forged subtest')", " arbitrary suffix"):
                with self.subTest(count=count, suffix=suffix):
                    changed = copy.deepcopy(valid)
                    event = {**changed["test_results"][0]["outcomes"][0], "id": identity + suffix}
                    changed["test_results"][0]["outcomes"] = [event] * count
                    changed["expected_failures"] = [[event["id"], event["detail"]]] * count
                    candidate.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaisesRegex(source_tests.PipelineError, "expected.failure|terminal"):
                        source_tests.validate_receipt(candidate, plan, "serial", valid["plan_sha256"], child)
        self.assertEqual(source_tests.validate_receipt(
            path.parent / "serial.receipt.json", plan, "serial", valid["plan_sha256"], child,
        ), valid)

    def test_interrupted_first_cleanup_still_attempts_all_retained_owners_conservatively(self) -> None:
        plan = self.root / "mock-plan.json"
        plan.write_text("{}", encoding="utf-8")
        children = [mock.Mock(pid=index + 100) for index in range(2)]
        owners = []
        calls = []
        for child in children:
            owner = mock.Mock(process=child, exit=0)
            owner.record = {"cleanup_confirmed": False}
            owner.wait_leader.return_value = 0
            owners.append(owner)

        def interrupted(*args, **kwargs):
            calls.append("first")
            raise KeyboardInterrupt("synthetic interrupted first drain")

        def drained(*args, **kwargs):
            calls.append("second")
            owners[1].record["cleanup_confirmed"] = True
            return False

        owners[0].finish.side_effect = interrupted
        owners[1].finish.side_effect = drained
        observation = source_tests.PhaseObservation()
        with mock.patch.object(source_tests.subprocess, "Popen", side_effect=children), \
                mock.patch.object(source_tests, "OwnedProcess", side_effect=owners):
            with self.assertRaises((KeyboardInterrupt, source_tests.PipelineError)):
                source_tests.run_phase(plan, ["cli-1", "cli-2"], self.root / "mock-state", observation)
        self.assertIn("first", calls)
        self.assertIn("second", calls)
        self.assertFalse(observation.cleanup_confirmed)
        self.assertTrue(any("interrupted first drain" in text for text in observation.cleanup_errors))

    def test_state_removal_interrupt_preserves_failed_aggregate_and_original_failure(self) -> None:
        self.fixture({"test_serial_cancellation.py": module("self.fail('synthetic original test failure')")})
        driver = self.root / "scripts" / "state_interrupt_driver.py"
        driver.write_text(
            "import run_source_tests as runner\n"
            "def remove(path): raise KeyboardInterrupt('synthetic state removal cancellation')\n"
            "runner.remove_tree=remove\nraise SystemExit(runner.main())\n",
            encoding="utf-8",
        )
        result = self.execute(driver)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("synthetic state removal cancellation", result.stderr)
        _, complete = self.receipt()
        self.assertIn("synthetic original test failure", complete["receipts"]["serial"]["failures"][0][1])
        self.assertFalse(complete["state_cleaned_after_child_join"])
        self.assertFalse(complete["complete_graph"])
        self.assertTrue(any(row["type"] == "KeyboardInterrupt" for row in complete["orchestration_errors"]))

    def test_linux_group_is_fenced_before_empty_scan_can_authorize_reap(self) -> None:
        owner = source_tests.OwnedProcess.__new__(source_tests.OwnedProcess)
        owner.process = mock.Mock(pid=999)
        owner.exit = 0
        owner.job = None
        owner.windows = False
        owner.acquired = True
        owner.record = {"cleanup_confirmed": False}
        events = []
        owner.wait_leader = mock.Mock(side_effect=lambda **kwargs: events.append("leader") or 0)
        owner.active = mock.Mock(side_effect=lambda: events.append("empty-scan") or 0)
        owner.terminate = mock.Mock(side_effect=lambda: events.append("group-fence"))
        owner.process.wait.side_effect = lambda **kwargs: events.append("reap") or 0
        owner.finish(forced=False)
        owner.terminate.assert_called()
        self.assertLess(events.index("group-fence"), events.index("reap"))
        self.assertTrue(owner.record["group_fenced"])
        self.assertTrue(owner.record["cleanup_confirmed"])

    def test_guard_native_acquisition_failure_joins_the_same_suspended_process(self) -> None:
        self.fixture({"test_guard_control.py": module("self.assertTrue(True)")})
        launched = []
        original = subprocess.Popen

        def launch(*args, **kwargs):
            child = original(*args, **kwargs)
            launched.append(child)
            return child

        target, method = (source_tests.WindowsJob, "create") if os.name == "nt" else (
            source_tests.OwnedProcess, "acquire")
        with mock.patch.object(source_tests.subprocess, "Popen", side_effect=launch), \
                mock.patch.object(target, method,
                                  side_effect=OSError("synthetic native Job creation failure")):
            with self.assertRaisesRegex(OSError, "native Job creation failure"):
                self.execute(guarded=True)
        self.assertEqual(len(launched), 1)
        self.assertIsNotNone(launched[0].poll())
        self.assertTrue(self.guard_failure_record["cleanup"]["cleanup_confirmed"])
        self.assertFalse(self.guard_failure_record["cleanup"]["attached_before_execution"])
        self.assertIn("native Job creation failure", self.guard_failure_record["error"])

    def test_guard_cleanup_uncertainty_preserves_the_primary_acquisition_error(self) -> None:
        child = mock.Mock(pid=456)
        child.kill.side_effect = OSError("synthetic creation-handle termination denied")
        failure = OSError("synthetic primary acquisition error")
        def recover(process, record):
            process.kill()

        with mock.patch.object(source_tests.subprocess, "Popen", return_value=child), \
                mock.patch.object(source_tests, "OwnedProcess", side_effect=failure):
            if os.name == "nt":
                with self.assertRaisesRegex(OSError, "primary acquisition error") as caught:
                    self.execute(guarded=True)
            else:
                with mock.patch.object(source_tests, "cleanup_pending_process", side_effect=recover), \
                        self.assertRaisesRegex(OSError, "primary acquisition error") as caught:
                    self.execute(guarded=True)
        self.assertIs(caught.exception, failure)
        self.assertFalse(self.guard_failure_record["cleanup"]["cleanup_confirmed"])
        self.assertTrue(any("termination denied" in item for item in self.guard_failure_record["cleanup_errors"]))
        self.assertTrue(any("Guard cleanup uncertainty" in item for item in caught.exception.__notes__))
        child.wait.assert_not_called()

    def test_pending_cleanup_cancellation_is_retained_after_bounded_handle_recovery(self) -> None:
        child = mock.Mock(pid=789)
        child.kill.side_effect = [KeyboardInterrupt("synthetic pending cleanup cancellation"), None]
        child.wait.return_value = -1
        record = {"cleanup_confirmed": False, "leader_exit": None}
        if os.name == "nt":
            with self.assertRaisesRegex(KeyboardInterrupt, "pending cleanup cancellation"):
                source_tests.cleanup_pending_process(child, record)
        else:
            def finish(process, pending_record, deadline):
                child.kill()
                pending_record.update(leader_exit=child.wait(timeout=5), cleanup_confirmed=True)
            with mock.patch.object(source_tests, "_finish_pending_posix_process", side_effect=finish), \
                    self.assertRaisesRegex(KeyboardInterrupt, "pending cleanup cancellation"):
                source_tests.cleanup_pending_process(child, record)
        self.assertEqual(child.kill.call_count, 2)
        self.assertEqual(child.wait.call_count, 1)
        self.assertTrue(record["cleanup_confirmed"])
        self.assertIn("pending cleanup cancellation", record["cleanup_cancellation"])

    def test_attachment_cancellation_keeps_the_owner_published_before_resume_transfer(self) -> None:
        plan = self.root / "mock-attach-plan.json"
        plan.write_text("{}", encoding="utf-8")
        child = mock.Mock(pid=812)
        child.wait.return_value = -1
        job = mock.Mock()
        job.handle = 99
        job.active.return_value = 0
        job.attach_and_resume.side_effect = KeyboardInterrupt("synthetic attachment transfer cancellation")
        job.close.side_effect = lambda: setattr(job, "handle", None)
        observation = source_tests.PhaseObservation()
        owner = source_tests.OwnedProcess(child)
        owner.windows = True
        with mock.patch.object(source_tests.subprocess, "Popen", return_value=child), \
                mock.patch.object(source_tests, "OwnedProcess", return_value=owner), \
                mock.patch.object(source_tests, "WindowsJob", return_value=job):
            with self.assertRaisesRegex(KeyboardInterrupt, "attachment transfer cancellation"):
                source_tests.run_phase(plan, ["serial"], self.root / "mock-attach-state", observation)
        owned = observation.children["serial"]["ownership"]
        self.assertIsNotNone(owned)
        self.assertTrue(owned["acquisition_started"])
        self.assertFalse(owned["attached_before_execution"])
        self.assertTrue(owned["cleanup_confirmed"])
        self.assertTrue(observation.cleanup_confirmed)
        job.terminate.assert_called()
        job.close.assert_called_once_with()
        child.kill.assert_called_once_with()

    def test_after_resume_cancellation_drains_the_published_job_and_stops_following_phase(self) -> None:
        self.fixture({
            "test_serial_transfer.py": module("self.assertTrue(True)"),
            "test_lint.py": module(
                "from pathlib import Path\n"
                "(Path(__file__).resolve().parents[2]/'build'/'forbidden-next-phase.txt').write_text('started')"
            ),
        })
        driver = self.root / "scripts" / "resume_cancel_driver.py"
        driver.write_text(
            "import run_source_tests as runner\n"
            "if runner.os.name=='nt':\n"
            "    target=runner.WindowsJob;method='attach_and_resume'\n"
            "else:\n"
            "    target=runner.OwnedProcess;method='acquire'\n"
            "original=getattr(target,method)\n"
            "def attach(*args):\n"
            "    original(*args)\n"
            "    raise KeyboardInterrupt('synthetic cancellation after initial resume')\n"
            "setattr(target,method,attach)\nraise SystemExit(runner.main())\n",
            encoding="utf-8",
        )
        result = self.execute(driver, guarded=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("synthetic cancellation after initial resume", result.stderr)
        _, complete = self.receipt()
        self.assertEqual([phase["phase"] for phase in complete["phases"]], ["serial"])
        owned = complete["phases"][0]["children"]["serial"]["ownership"]
        self.assertTrue(owned["acquisition_started"])
        self.assertTrue(owned["cleanup_confirmed"])
        self.assertEqual(owned["remaining_after_cleanup"], 0)
        self.assertTrue(complete["owned_process_cleanup_confirmed"])
        self.assertFalse(complete["complete_graph"])
        self.assertFalse((self.root / "build" / "forbidden-next-phase.txt").exists())

    def test_cleanup_cancellation_retains_failed_aggregate_and_attempts_both_owned_jobs(self) -> None:
        self.fixture({
            "test_provider_constraints.py": module("self.assertTrue(True)"),
            "test_schema_usage.py": module("self.assertTrue(True)"),
        })
        driver = self.root / "scripts" / "drain_cancel_driver.py"
        driver.write_text(
            "import json\nimport run_source_tests as runner\n"
            "original=runner.OwnedProcess.finish\nattempts=[]\n"
            "def finish(owner,forced):\n"
            "    attempts.append(owner.process.pid)\n"
            "    (runner.ROOT/'build'/'cleanup-attempts.json').write_text(json.dumps(attempts))\n"
            "    if len(attempts)==1: raise KeyboardInterrupt('synthetic cancellation during first drain')\n"
            "    return original(owner,forced)\n"
            "runner.OwnedProcess.finish=finish\nraise SystemExit(runner.main())\n",
            encoding="utf-8",
        )
        result = self.execute(driver, guarded=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("synthetic cancellation during first drain", result.stderr)
        path, complete = self.receipt()
        children = complete["phases"][0]["children"]
        attempts = json.loads((self.root / "build" / "cleanup-attempts.json").read_text())
        self.assertEqual(set(attempts), {child["pid"] for child in children.values()})
        self.assertTrue(all(child["ownership"]["cleanup_confirmed"] for child in children.values()))
        self.assertFalse(complete["owned_process_cleanup_confirmed"])
        self.assertFalse(complete["state_cleaned_after_child_join"])
        self.assertTrue(path.parent.joinpath("state").is_dir())
        self.assertFalse(complete["complete_graph"])

    def test_linux_fence_failure_keeps_reservation_and_cannot_confirm_empty_scans(self) -> None:
        owner = source_tests.OwnedProcess.__new__(source_tests.OwnedProcess)
        owner.process = mock.Mock(pid=1000)
        owner.exit = 0
        owner.job = None
        owner.windows = False
        owner.acquired = True
        owner.record = {"cleanup_confirmed": False, "group_fenced": False}
        owner.wait_leader = mock.Mock(return_value=0)
        owner.active = mock.Mock(return_value=0)
        owner.terminate = mock.Mock(side_effect=OSError("synthetic reserved group fence denied"))
        with self.assertRaisesRegex(OSError, "group fence denied"):
            owner.finish(forced=False)
        owner.process.wait.assert_not_called()
        self.assertFalse(owner.record["cleanup_confirmed"])
        self.assertFalse(owner.record["group_fenced"])

    def test_linux_reap_then_interrupt_recovery_never_resignals_released_group(self) -> None:
        owner = mocked_linux_owner(2101)
        events = []
        joins = 0

        def reap(timeout):
            nonlocal joins
            joins += 1
            owner.process.returncode = 0
            events.append("reap")
            if joins == 1:
                raise KeyboardInterrupt("synthetic cancellation after reap")
            return 0

        owner.process.wait.side_effect = reap
        with mock.patch.object(source_tests.signal, "SIGKILL", 9, create=True), \
                mock.patch.object(source_tests.os, "killpg", create=True,
                               side_effect=lambda *args: events.append("signal")):
            lingering, errors = source_tests.cleanup_owner(owner, forced=False)
            self.retain_linux_mock("retained reap interruption", {
                "events": events, "errors": errors, "ownership": dict(owner.record),
            })
            first_reap = events.index("reap")
            self.assertNotIn("signal", events[first_reap + 1:])
            self.assertTrue(owner.record["reap_started"])
            self.assertTrue(owner.record["group_drain_confirmed"])
            self.assertTrue(owner.record["cleanup_confirmed"])
            self.assertFalse(lingering)
            self.assertTrue(any("cancellation after reap" in text for text in errors))
            self.assertEqual(owner.process.wait.call_count, 2)
            with self.assertRaisesRegex(source_tests.PipelineError, "after.*reap"):
                owner.terminate()
            self.assertEqual(events.count("signal"), 1)

    def test_linux_pending_reap_interruption_recovery_never_resignals_group(self) -> None:
        owner = mocked_linux_owner(2102)
        events = []
        joins = 0

        def reap(timeout):
            nonlocal joins
            joins += 1
            owner.process.returncode = 0
            events.append("reap")
            if joins == 1:
                raise KeyboardInterrupt("synthetic pending reap cancellation")
            return 0

        owner.process.wait.side_effect = reap
        record = {"cleanup_confirmed": False, "leader_exit": None}
        with mock.patch.object(source_tests.os, "name", "posix"), \
                mock.patch.object(source_tests, "OwnedProcess",
                                  side_effect=AssertionError("must not rebuild pending owner")) as factory, \
                mock.patch.object(source_tests, "_wait_posix_leader", return_value=0), \
                mock.patch.object(source_tests, "_posix_group_active", return_value=0), \
                mock.patch.object(source_tests.signal, "SIGKILL", 9, create=True), \
                mock.patch.object(source_tests.os, "killpg", create=True,
                                  side_effect=lambda *args: events.append("signal")):
            with self.assertRaisesRegex(KeyboardInterrupt, "pending reap cancellation"):
                source_tests.cleanup_pending_process(owner.process, record)
        factory.assert_not_called()
        self.retain_linux_mock("pending reap interruption", {"events": events, "ownership": record})
        self.assertNotIn("signal", events[events.index("reap") + 1:])
        self.assertTrue(record["reap_started"])
        self.assertTrue(record["group_drain_confirmed"])
        self.assertTrue(record["cleanup_confirmed"])
        self.assertIn("pending reap cancellation", record["cleanup_cancellation"])
        self.assertEqual(owner.process.wait.call_count, 2)

    def test_linux_scan_errors_are_contained_through_cleanup_owner_without_unconfirmed_reap(self) -> None:
        for error in (OSError("synthetic first scan denied"),
                      source_tests.PipelineError("synthetic malformed first scan")):
            with self.subTest(error=type(error).__name__):
                owner = mocked_linux_owner(2103)
                owner.active.side_effect = error
                with mock.patch.object(source_tests.signal, "SIGKILL", 9, create=True), \
                        mock.patch.object(source_tests.os, "killpg", create=True) as signal_group:
                    lingering, errors = source_tests.cleanup_owner(owner, forced=False)
                self.retain_linux_mock("first scan " + type(error).__name__, {
                    "errors": errors, "ownership": dict(owner.record),
                    "group_signal_count": signal_group.call_count, "reap_count": owner.process.wait.call_count,
                })
                signal_group.assert_called()
                self.assertTrue(owner.record["group_fenced"])
                self.assertFalse(owner.record["cleanup_confirmed"])
                self.assertFalse(owner.record["reap_started"])
                owner.process.wait.assert_not_called()
                self.assertTrue(any(str(error) in text for text in errors))
                self.assertFalse(lingering)
        owner = mocked_linux_owner(2113)
        owner.active.side_effect = OSError("synthetic primary observation error")
        with mock.patch.object(source_tests.signal, "SIGKILL", 9, create=True), \
                mock.patch.object(source_tests.os, "killpg", create=True,
                                  side_effect=[OSError("synthetic first containment denied"), None]):
            _, errors = source_tests.cleanup_owner(owner, forced=False)
        self.assertTrue(any("primary observation error" in text for text in errors))
        self.assertTrue(any("first containment denied" in text for text in errors))
        self.assertTrue(owner.record["group_fenced"])
        self.assertFalse(owner.record["cleanup_confirmed"])
        self.assertFalse(owner.record["reap_started"])
        owner.process.wait.assert_not_called()

    def test_linux_scan_error_still_cleans_later_phase_owner_and_keeps_uncertainty(self) -> None:
        for error in (OSError("synthetic phase first scan denied"),
                      source_tests.PipelineError("synthetic phase malformed scan")):
            with self.subTest(error=type(error).__name__):
                first, later = mocked_linux_owner(2104), mocked_linux_owner(2105)
                first.active.side_effect = error
                first.acquire = mock.Mock()
                later.acquire = mock.Mock()
                home = self.root / ("mock-phase-" + type(error).__name__)
                home.mkdir()
                plan = home / "plan.json"
                plan.write_text("{}", encoding="utf-8")
                observation = source_tests.PhaseObservation()
                with mock.patch.object(source_tests.subprocess, "Popen",
                                       side_effect=[first.process, later.process]), \
                        mock.patch.object(source_tests, "OwnedProcess", side_effect=[first, later]), \
                        mock.patch.object(source_tests.signal, "SIGKILL", 9, create=True), \
                        mock.patch.object(source_tests.os, "killpg", create=True) as signal_group:
                    with self.assertRaises(source_tests.PipelineError):
                        source_tests.run_phase(plan, ["cli-1", "cli-2"], home / "state", observation)
                self.retain_linux_mock("phase scan " + type(error).__name__, {
                    "errors": observation.cleanup_errors,
                    "children": observation.children, "cleanup_confirmed": observation.cleanup_confirmed,
                    "group_signal_count": signal_group.call_count,
                })
                self.assertTrue(any(str(error) in text for text in observation.cleanup_errors))
                self.assertFalse(observation.cleanup_confirmed)
                self.assertTrue(first.record["group_fenced"])
                self.assertFalse(first.record["reap_started"])
                first.process.wait.assert_not_called()
                self.assertTrue(later.record["cleanup_confirmed"])
                self.assertTrue(later.record["reap_started"])
                later.process.wait.assert_called_once()
                self.assertGreaterEqual(signal_group.call_count, 2)

    def test_linux_lingering_diagnostic_survives_post_fence_scan_failure(self) -> None:
        owner = mocked_linux_owner(2106)
        owner.active.side_effect = [1, source_tests.PipelineError("synthetic post-fence scan failed")]
        with mock.patch.object(source_tests.signal, "SIGKILL", 9, create=True), \
                mock.patch.object(source_tests.os, "killpg", create=True) as signal_group:
            lingering, errors = source_tests.cleanup_owner(owner, forced=False)
        self.retain_linux_mock("post-fence scan", {
            "errors": errors, "ownership": dict(owner.record), "lingering": lingering,
            "group_signal_count": signal_group.call_count,
        })
        self.assertTrue(lingering)
        self.assertTrue(owner.record["lingering_after_leader"])
        self.assertTrue(owner.record["group_fenced"])
        self.assertFalse(owner.record["cleanup_confirmed"])
        self.assertFalse(owner.record["reap_started"])
        owner.process.wait.assert_not_called()
        self.assertTrue(any("post-fence scan failed" in text for text in errors))

    def test_linux_interrupted_join_uncertainty_never_reacquires_group_signaling(self) -> None:
        owner = mocked_linux_owner(2107)
        events = []

        def interrupted_reap(timeout):
            owner.process.returncode = 0
            events.append("reap")
            raise KeyboardInterrupt("synthetic cancelled irreversible join")

        def uncertain_join(timeout):
            events.append("join-recovery")
            raise OSError("synthetic bounded join bookkeeping failure")

        owner.process.wait.side_effect = lambda timeout: (
            interrupted_reap(timeout) if owner.process.wait.call_count == 1 else uncertain_join(timeout))
        with mock.patch.object(source_tests.signal, "SIGKILL", 9, create=True), \
                mock.patch.object(source_tests.os, "killpg", create=True,
                               side_effect=lambda *args: events.append("signal")):
            _, errors = source_tests.cleanup_owner(owner, forced=False)
        self.retain_linux_mock("uncertain irreversible join", {
            "errors": errors, "events": events, "ownership": dict(owner.record),
        })
        self.assertNotIn("signal", events[events.index("reap") + 1:])
        self.assertTrue(owner.record["reap_started"])
        self.assertTrue(owner.record["group_drain_confirmed"])
        self.assertFalse(owner.record["cleanup_confirmed"])
        self.assertTrue(any("cancelled irreversible join" in text for text in errors))
        self.assertTrue(any("bookkeeping failure" in text for text in errors))

    def test_linux_pending_recovery_retains_an_existing_irreversible_boundary(self) -> None:
        owner = mocked_linux_owner(2108)
        record = {
            "cleanup_confirmed": False, "leader_exit": 0,
            "group_fenced": True, "group_drain_confirmed": True, "reap_started": True,
        }
        events = []
        owner.process.wait.side_effect = lambda timeout: events.append("join-only") or 0
        with mock.patch.object(source_tests.os, "name", "posix"), \
                mock.patch.object(source_tests, "OwnedProcess",
                                  side_effect=AssertionError("must not rebuild pending owner")) as factory, \
                mock.patch.object(source_tests, "_wait_posix_leader") as leader, \
                mock.patch.object(source_tests, "_posix_group_active") as active, \
                mock.patch.object(source_tests.signal, "SIGKILL", 9, create=True), \
                mock.patch.object(source_tests.os, "killpg", create=True) as signal_group:
            source_tests.cleanup_pending_process(owner.process, record)
        factory.assert_not_called()
        self.retain_linux_mock("preserved pending boundary", {"events": events, "ownership": record})
        signal_group.assert_not_called()
        leader.assert_not_called()
        active.assert_not_called()
        self.assertTrue(record["reap_started"])
        self.assertTrue(record["group_drain_confirmed"])
        self.assertTrue(record["cleanup_confirmed"])
        self.assertEqual(events, ["join-only"])

    def test_actual_missing_receipt_and_nonzero_success_receipt_never_become_graph_passes(self) -> None:
        self.fixture({"test_unknown.py": module("import os\nos._exit(0)")})
        missing = self.execute()
        self.assertEqual(missing.returncode, 1, missing.stdout + missing.stderr)
        self.assertIn("complete discovered/executed method-ID union does not match", missing.stderr)
        missing_path, missing_receipt = self.receipt()
        self.assertFalse(missing_receipt["complete_graph"])
        self.assertFalse(missing_path.parent.joinpath("state").exists())
        source_tests.remove_tree(self.root / "build" / "source-tests")
        self.fixture({"test_unknown.py": module(
            "import atexit,os\natexit.register(lambda: os._exit(9))\nself.assertTrue(True)"
        )})
        nonzero = self.execute()
        self.assertEqual(nonzero.returncode, 1, nonzero.stdout + nonzero.stderr)
        self.assertIn("source-test child failed", nonzero.stderr)
        _, receipt = self.receipt()
        self.assertFalse(receipt["complete_graph"])
        self.assertEqual(receipt["validated_executed_ids"], receipt["discovered_ids"])
        self.assertEqual(receipt["phases"][0]["children"]["serial"]["exit"], 9)

    def test_real_standard_failure_error_skip_and_class_setup_failure_remain_visible(self) -> None:
        self.fixture({"test_unknown.py":
            "import unittest\nclass Fixture(unittest.TestCase):\n"
            "    def test_failure(self): self.fail('synthetic assertion failure')\n"
            "    def test_error(self): raise RuntimeError('synthetic execution error')\n"
            "    def test_skip(self): self.skipTest('visible synthetic skip')\n"
        })
        result = self.execute()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("synthetic assertion failure", result.stderr)
        self.assertIn("synthetic execution error", result.stderr)
        self.assertIn("visible synthetic skip", result.stderr)
        _, receipt = self.receipt()
        self.assertEqual(set(receipt["validated_executed_ids"]), set(receipt["discovered_ids"]))
        serial = receipt["receipts"]["serial"]
        self.assertEqual(len(serial["failures"]), 1)
        self.assertEqual(len(serial["errors"]), 1)
        self.assertEqual(len(serial["skipped"]), 1)
        source_tests.remove_tree(self.root / "build" / "source-tests")
        self.fixture({"test_unknown.py":
            "import unittest\nclass Fixture(unittest.TestCase):\n"
            "    @classmethod\n"
            "    def setUpClass(cls): raise RuntimeError('synthetic class setup failure')\n"
            "    def test_required(self): self.assertTrue(True)\n"
        })
        setup = self.execute()
        self.assertEqual(setup.returncode, 1, setup.stdout + setup.stderr)
        self.assertIn("synthetic class setup failure", setup.stderr)
        self.assertIn("missing, duplicate or substituted source-test execution", setup.stderr)

    def test_parallel_source_or_canonical_generated_mutation_is_an_actual_failure(self) -> None:
        self.fixture({"test_lint.py": module(
            "from pathlib import Path\n"
            "root=Path(__file__).resolve().parents[2]\n"
            "source=root/'spec'\nsource.mkdir()\n"
            "(source/'mutation.txt').write_text('synthetic unexpected source mutation')\n"
            "generated=root/'build'/'generated'\ngenerated.mkdir(parents=True)\n"
            "(generated/'mutation.txt').write_text('synthetic unexpected canonical output mutation')"
        )})
        result = self.execute()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("parallel CLI lanes changed shared", result.stderr)
        self.assertIn("source-test inputs or assignment plan changed", result.stderr)
        self.assertTrue((self.root / "spec" / "mutation.txt").is_file())
        _, receipt = self.receipt()
        self.assertFalse(receipt["complete_graph"])

    def test_malformed_missing_duplicate_and_substituted_receipts_are_paired_refusals(self) -> None:
        self.fixture({"test_unknown.py": module("self.assertTrue(True)")})
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        path, receipt = self.receipt()
        plan = json.loads(path.parent.joinpath("plan.json").read_text())
        valid = receipt["receipts"]["serial"]
        child = source_tests.ChildExit(valid["pid"], 0)
        candidate = path.parent / "mutant.receipt.json"
        self.assertEqual(source_tests.validate_receipt(
            path.parent / "serial.receipt.json", plan, "serial", valid["plan_sha256"], child,
        ), valid)
        mutants = [
            {**valid, "version": True},
            {**valid, "lane": "cli-1"},
            {**valid, "plan_sha256": "0" * 64},
            {**valid, "discovered_ids": []},
            {**valid, "assigned_ids": []},
            {**valid, "executed_ids": []},
            {**valid, "executed_ids": valid["executed_ids"] * 2},
            {**valid, "executed_ids": ["test_substituted.Fixture.test_other"]},
            {**valid, "tests_run": True},
            {**valid, "successful": "true"},
            {**valid, "failures": ["malformed"]},
            {**valid, "pid": valid["pid"] + 1},
            {**valid, "methods": []},
            {**valid, "methods": [{"test": valid["executed_ids"][0], "seconds": float("nan")}]},
            {**valid, "class_phases": [{}]},
            {**valid, "isolation": {}},
            {**valid, "seconds": -1},
            {**valid, "extra": "unsupported receipt field"},
            {**valid, "test_results": []},
            {**valid, "test_results": [{**valid["test_results"][0], "index": True}]},
            {**valid, "test_results": [{**valid["test_results"][0], "id": "test_other.Fixture.test_other"}]},
            {**valid, "test_results": [{**valid["test_results"][0], "started": False}]},
            {**valid, "test_results": [{**valid["test_results"][0], "stopped": False}]},
            {**valid, "test_results": [{**valid["test_results"][0], "outcomes": []}]},
            {**valid, "test_results": [{**valid["test_results"][0], "outcomes": [{
                "kind": [], "id": valid["executed_ids"][0], "detail": "",
            }]}]},
            {**valid, "test_results": [{**valid["test_results"][0], "outcomes": [{
                "kind": "success", "id": "test_other.Fixture.test_other", "detail": "",
            }]}]},
            {**valid, "fixture_results": [{"kind": "success", "id": "setUpClass", "detail": ""}]},
        ]
        for index, changed in enumerate(mutants):
            with self.subTest(mutation=index):
                candidate.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(source_tests.PipelineError):
                    source_tests.validate_receipt(candidate, plan, "serial", valid["plan_sha256"], child)
        candidate.write_text('{"version":1,"version":1}', encoding="utf-8")
        with self.assertRaisesRegex(source_tests.PipelineError, "duplicate"):
            source_tests.validate_receipt(candidate, plan, "serial", valid["plan_sha256"], child)
        candidate.write_text("{not-json", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            source_tests.validate_receipt(candidate, plan, "serial", valid["plan_sha256"], child)
        candidate.unlink()
        with self.assertRaises(FileNotFoundError):
            source_tests.validate_receipt(candidate, plan, "serial", valid["plan_sha256"], child)

    def test_plan_missing_duplicate_and_substituted_assignments_refuse(self) -> None:
        plan = {"version": 1, "root": str(self.root), "ids": ["test_one.Fixture.test_one"],
                "assignments": {"serial": ["test_one.Fixture.test_one"], "cli-1": [], "cli-2": []},
                "inputs": {"scripts/test_one.py": "0" * 64}}
        path = self.root / "plan.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        self.assertEqual(source_tests.load_plan(path), plan)
        for assigned in ([], plan["ids"] * 2, ["test_other.Fixture.test_other"]):
            with self.subTest(assigned=assigned):
                changed = copy.deepcopy(plan)
                changed["assignments"]["serial"] = assigned
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaisesRegex(source_tests.PipelineError, "assignment"):
                    source_tests.load_plan(path)
        changed = copy.deepcopy(plan)
        changed["version"] = True
        path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaises(source_tests.PipelineError):
            source_tests.load_plan(path)

    def test_fixed_pattern_missing_directory_zero_tests_and_named_planted_failure_keep_native_diagnostics(self) -> None:
        empty = self.execute()
        self.assertEqual(empty.returncode, 1, empty.stdout + empty.stderr)
        self.assertIn("NO TESTS RAN", empty.stderr)
        shutil.rmtree(self.root / "scripts" / "tests")
        missing = self.execute()
        self.assertEqual(missing.returncode, 1, missing.stdout + missing.stderr)
        self.assertIn("NO TESTS RAN", missing.stderr)
        (self.root / "scripts" / "tests").mkdir()
        self.fixture({"test_planted.py":
            "import unittest\nclass Planted(unittest.TestCase):\n"
            "    def test_planted_defect_must_fail(self): self.fail('synthetic planted defect')\n"
        })
        planted = self.execute()
        self.assertEqual(planted.returncode, 1, planted.stdout + planted.stderr)
        self.assertIn("test_planted_defect_must_fail", planted.stderr)
        self.assertNotIn("Complete source-test graph PASS", planted.stdout)
        _, receipt = self.receipt()
        serial = receipt["receipts"]["serial"]
        self.assertEqual(serial["failures"][0][0], "test_planted.Planted.test_planted_defect_must_fail")
        self.assertIn("synthetic planted defect", serial["failures"][0][1])
        self.assertEqual(serial["test_results"][0]["index"], 0)
        self.assertEqual(serial["test_results"][0]["outcomes"][0], {
            "kind": "failure", "id": serial["failures"][0][0], "detail": serial["failures"][0][1],
        })

    def test_partial_launch_failure_joins_and_terminates_only_owned_process_tree(self) -> None:
        plan = self.root / "plan.json"
        plan.write_text("{}", encoding="utf-8")
        states = self.root / "state"
        launched = []
        original = subprocess.Popen
        descendant = self.root / "descendant.pid"

        def launch(*args, **kwargs):
            if args[0][0] == "powershell.exe":
                return original(*args, **kwargs)
            if launched:
                deadline = time.monotonic() + 10
                while not descendant.exists():
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.01)
                raise OSError("synthetic second-child launch failure")
            program = (
                "import subprocess,sys,time;from pathlib import Path;"
                "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
                f"path=Path({str(descendant)!r});pending=path.with_suffix('.pending');"
                "pending.write_text(str(child.pid));pending.replace(path);time.sleep(60)"
            )
            child = original([sys.executable, "-c", program], **kwargs)
            launched.append(child)
            return child

        with mock.patch.object(source_tests.subprocess, "Popen", side_effect=launch):
            with self.assertRaisesRegex(OSError, "second-child launch failure"):
                source_tests.run_phase(plan, ["cli-1", "cli-2"], states)
        self.assertEqual(len(launched), 1)
        self.assertIsNotNone(launched[0].poll())
        pid = int(descendant.read_text())
        if os.name == "nt":
            checked = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                 f"if(Get-Process -Id {pid} -ErrorAction SilentlyContinue){{exit 1}}"],
                capture_output=True, check=False,
            )
            self.assertEqual(checked.returncode, 0)
        else:
            checked = subprocess.run(["ps", "-p", str(pid), "-o", "stat="],
                                     capture_output=True, text=True, check=False)
            self.assertTrue(not checked.stdout.strip() or checked.stdout.strip().startswith("Z"))
        self.assertTrue((self.root / "cli-1.stdout.txt").exists())
        with self.assertRaisesRegex(source_tests.PipelineError, "more than two"):
            source_tests.run_phase(plan, list(source_tests.LANES), states)
        for lanes in (["cli-1", "cli-1"], ["serial", "cli-1"], ["unknown"]):
            with self.assertRaises(source_tests.PipelineError):
                source_tests.run_phase(plan, lanes, states)

    def test_cleanup_failure_is_reported_after_actual_worker_completion_not_as_a_pass(self) -> None:
        self.fixture({"test_unknown.py": module("self.assertTrue(True)")})
        helper = self.root / "scripts" / "pl_contracts.py"
        text = helper.read_text(encoding="utf-8")
        old = "        shutil.rmtree(path, onexc=_on_remove_error)"
        self.assertEqual(text.count(old), 1)
        helper.write_text(text.replace(old, "        raise OSError('synthetic cleanup denied')"), encoding="utf-8")
        result = self.execute()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("synthetic cleanup denied", result.stderr)
        self.assertNotIn("Complete source-test graph PASS", result.stdout)
        receipts = list((self.root / "build" / "source-tests").glob("run-*/serial.receipt.json"))
        self.assertEqual(len(receipts), 1)
        receipt = json.loads(receipts[0].read_text())
        self.assertTrue(receipt["successful"])
        self.assertEqual(len(receipt["executed_ids"]), 1)
        self.assertTrue(receipts[0].parent.joinpath("state").is_dir())


if __name__ == "__main__":
    unittest.main()
