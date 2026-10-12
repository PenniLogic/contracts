"""Discover every test_*.py method and run the complete graph with two isolated CLI lanes.

  python scripts/run_source_tests.py

Unproved shared-mutable and canonical-generation modules share one serial worker. Reviewed
source-only CLI work can overlap it in the two existing isolated CLI lanes; readers of
canonical clients wait for serial ownership to drain. Logs and identity receipts remain
in build/source-tests/.
Canonical profile activation is separate from this manual entry point.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass, field
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import fnmatch
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
from time import perf_counter, sleep
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pl_contracts import PipelineError, ROOT, fail, java_executable, remove_tree, run, sha256_file  # noqa: E402

PATTERN = "test_*.py"
PARALLEL_MODULES = (
    frozenset({"test_custom_destinations", "test_provider_constraints", "test_core_cli_guards",
               "test_typescript_wire", "test_category_seed", "test_breaking",
               "test_import_provider", "test_allocation_refusal", "test_generation_safety"}),
    frozenset({"test_lint", "test_schema_usage", "test_provider_transport", "test_schema_compatibility",
               "test_error_provider", "test_authentication_provider", "test_egress_errors"}),
)
LANES = ("serial", "cli-1", "cli-2")
SDK_READERS = frozenset({"test_typescript_wire", "test_category_seed", "test_provider_transport"})
SOURCE_ONLY_MODULES = frozenset().union(*PARALLEL_MODULES) - SDK_READERS
SOURCE_ONLY_CLASSES = frozenset({"test_provider_transport.ProviderWiringNegativeTest"})
REVIEWED_SERIAL_MODULES = frozenset({
    "test_core_endpoints", "test_core_metadata", "test_fixtures", "test_generate",
    "test_nullable_status_generation", "test_provider_composition",
    "test_release", "test_source_runner", "test_toolchain",
})
WINDOWS_CREATE_SUSPENDED = 0x00000004
PLAN_KEYS = {"version", "root", "ids", "assignments", "inputs"}
RECEIPT_KEYS = {
    "version", "lane", "plan_sha256", "discovered_ids", "assigned_ids", "executed_ids",
    "tests_run", "successful", "failures", "errors", "skipped", "expected_failures",
    "unexpected_successes", "methods", "class_phases", "seconds", "pid", "isolation",
    "test_results", "fixture_results",
}


@dataclass
class Discovery:
    tests: list[unittest.TestCase]
    ids: list[str]
    assignments: dict[str, list[str]]


@dataclass
class ChildExit:
    pid: int
    returncode: int


@dataclass
class PhaseObservation:
    children: dict[str, dict] = field(default_factory=dict)
    cleanup_errors: list[str] = field(default_factory=list)
    cleanup_confirmed: bool = False


class ChildResultError(PipelineError):
    def __init__(self, message: str, receipt: dict, *, test_failure_only: bool = False):
        super().__init__(message)
        self.receipt = receipt
        self.test_failure_only = test_failure_only


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PipelineError("duplicate source-test receipt or plan field")
        result[key] = value
    return result


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


class CompleteLoader(unittest.TestLoader):
    def loadTestsFromModule(self, module, *, pattern=None):
        if getattr(module, "load_tests", None) is not None:
            raise PipelineError(f"custom load_tests discovery is unsupported: {module.__name__}")
        suite = super().loadTestsFromModule(module, pattern=pattern)
        if fnmatch.fnmatch(Path(module.__file__).name, PATTERN) and not suite.countTestCases():
            self.errors.append(f"NO TESTS RAN: zero-test discovery in {module.__name__}")
        return suite


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        elif isinstance(item, unittest.TestCase):
            yield item
        else:
            raise PipelineError("discovery returned a non-TestCase")


def classify_module(module: str, *, test_class: str = "") -> tuple[str, bool]:
    lane = next((LANES[index + 1] for index, modules in enumerate(PARALLEL_MODULES)
                 if module in modules), "serial")
    independent = module in SOURCE_ONLY_MODULES or module + "." + test_class in SOURCE_ONLY_CLASSES
    return lane, lane != "serial" and independent


def discover(root: Path) -> Discovery:
    if not (root / "scripts" / "tests").is_dir():
        raise PipelineError("NO TESTS RAN: scripts/tests is missing")
    loader = CompleteLoader()
    suite = loader.discover(str(root / "scripts" / "tests"), pattern=PATTERN)
    if loader.errors:
        raise PipelineError("source-test discovery failed:\n" + "\n".join(loader.errors))
    tests = list(cases(suite))
    ids = [test.id() for test in tests]
    if not ids:
        raise PipelineError("NO TESTS RAN: complete test_*.py discovery returned zero methods")
    if any(not isinstance(value, str) or not value for value in ids):
        raise PipelineError("source-test discovery returned malformed identities")
    if len(ids) != len(set(ids)):
        raise PipelineError("source-test discovery returned duplicate identities")
    assignments: dict[str, list[str]] = {lane: [] for lane in LANES}
    for test in tests:
        lane, _ = classify_module(test.__class__.__module__, test_class=test.__class__.__qualname__)
        assignments[lane].append(test.id())
    return Discovery(tests, ids, assignments)


def input_snapshot(root: Path) -> dict[str, str]:
    if (root / ".git").exists():
        inventory = subprocess.check_output(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root,
        ).decode("utf-8")
        paths = [root / name for name in inventory.split("\0") if name]
    else:
        ignored = {"build", "node_modules", ".git", ".gradle", "__pycache__", ".toolchain"}
        paths = [path for path in root.rglob("*") if path.is_file() and
                 not ignored.intersection(path.relative_to(root).parts)]
    result = {}
    for path in sorted(set(paths)):
        if path.is_symlink():
            raise PipelineError("source-test inputs must not be symlinks")
        result[path.relative_to(root).as_posix()] = sha256_file(path)
    return result


def shared_snapshot(root: Path) -> dict[str, str]:
    result = input_snapshot(root)
    for folder in (".toolchain", "build/generated"):
        for path in sorted((root / folder).rglob("*")):
            if path.is_file():
                if path.is_symlink():
                    raise PipelineError("shared test inputs must not be symlinks")
                result[path.relative_to(root).as_posix()] = sha256_file(path)
    return result


def overlap_profile(discovered: Discovery) -> dict:
    if not discovered.assignments["serial"] or not any(
            discovered.assignments[lane] for lane in ("cli-1", "cli-2")):
        return {"enabled": False, "reason": "no serial/CLI overlap to schedule", "tools": {}}
    if not any(independent_ids({"assignments": discovered.assignments}, lane)
               for lane in ("cli-1", "cli-2")):
        return {"enabled": False, "reason": "all CLI methods require the canonical SDK barrier", "tools": {}}
    serial_modules = {identity.split(".", 1)[0] for identity in discovered.assignments["serial"]}
    if not serial_modules <= REVIEWED_SERIAL_MODULES:
        return {"enabled": False, "reason": "unproved serial resource writer retains the SDK barrier", "tools": {}}
    if not (ROOT / "toolchain" / "versions.json").is_file():
        return {"enabled": False, "reason": "managed tool resource profile is absent; retaining the SDK barrier", "tools": {}}
    return {"enabled": True, "reason": "reviewed independent source-CLI resources", "tools": source_tool_snapshot()}


def source_tool_snapshot() -> dict[str, str]:
    import toolchain

    pins = read_json(ROOT / "toolchain" / "versions.json")
    cache = ROOT / ".toolchain"
    if cache.is_symlink() or cache.is_junction():
        raise PipelineError("source-CLI overlap tools must use the owned normal cache directory")
    try:
        binary = toolchain.oasdiff_path(pins, cache)
        paths = (toolchain.generator_jar_path(pins, cache), binary,
                 toolchain._marker(binary))
        if any(path.is_symlink() or not path.resolve().is_relative_to(cache.resolve()) for path in paths):
            raise PipelineError("source-CLI overlap tool paths must remain in the owned cache")
        if not isinstance(read_json(toolchain._marker(binary)), dict):
            raise PipelineError("source-CLI overlap requires an object-valued oasdiff verification marker")
        toolchain.verify(pins, cache)
    except (KeyError, TypeError, AttributeError) as error:
        raise PipelineError("source-CLI overlap requires a complete managed tool profile") from error
    return {path.relative_to(ROOT).as_posix(): sha256_file(path) for path in paths}


def write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def load_plan(path: Path) -> dict:
    value = read_json(path)
    if not isinstance(value, dict) or set(value) != PLAN_KEYS or type(value["version"]) is not int or value["version"] != 1:
        raise PipelineError("malformed source-test plan")
    if not isinstance(value["root"], str) or not isinstance(value["inputs"], dict):
        raise PipelineError("malformed source-test plan input binding")
    if any(not isinstance(name, str) or not isinstance(digest, str) or
           re.fullmatch("[0-9a-f]{64}", digest) is None for name, digest in value["inputs"].items()):
        raise PipelineError("malformed source-test plan input digest")
    ids, assignments = value["ids"], value["assignments"]
    if not isinstance(ids, list) or not ids or any(not isinstance(item, str) or not item for item in ids):
        raise PipelineError("malformed source-test plan identities")
    if len(ids) != len(set(ids)) or not isinstance(assignments, dict) or set(assignments) != set(LANES):
        raise PipelineError("malformed source-test plan assignment")
    flattened = []
    for assigned in assignments.values():
        if not isinstance(assigned, list) or any(not isinstance(item, str) for item in assigned):
            raise PipelineError("malformed source-test lane identities")
        flattened.extend(assigned)
    if Counter(flattened) != Counter(ids):
        raise PipelineError("missing, duplicate or substituted source-test assignments")
    return value


class TrackingResult(unittest.TextTestResult):
    def __init__(self, *args, progress: Callable[[dict], None] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.executed: list[str] = []
        self.methods: list[dict] = []
        self.class_phases: list[dict] = []
        self.started: dict[str, float] = {}
        self.outcomes: dict[str, list[dict]] = {}
        self.progress = progress

    def observe(self, event: str, **fields) -> None:
        if self.progress is None:
            return
        try:
            self.progress({"event": event, **fields})
        except (OSError, ValueError) as error:
            self.progress = None
            self.addError(unittest.suite._ErrorHolder("source-test provisional progress"),
                          (type(error), error, error.__traceback__))

    def startTest(self, test):
        self.executed.append(test.id())
        self.started[test.id()] = perf_counter()
        super().startTest(test)
        self.observe("test_started", test=test.id())

    def stopTest(self, test):
        method = {"test": test.id(), "seconds": perf_counter() - self.started.pop(test.id())}
        self.methods.append(method)
        super().stopTest(test)
        self.observe("test_stopped", **method,
                     outcome_kinds=[outcome["kind"] for outcome in self.outcomes.get(test.id(), [])])

    def outcome(self, test, kind: str, detail: str = "") -> None:
        parent = getattr(test, "test_case", test)
        self.outcomes.setdefault(parent.id(), []).append({
            "kind": kind, "id": test.id(), "detail": detail,
        })

    def addSuccess(self, test):
        super().addSuccess(test)
        self.outcome(test, "success")

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.outcome(test, "failure", self.failures[-1][1])

    def addError(self, test, error):
        super().addError(test, error)
        self.outcome(test, "error", self.errors[-1][1])

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.outcome(test, "skip", reason)

    def addExpectedFailure(self, test, error):
        super().addExpectedFailure(test, error)
        self.outcome(test, "expected_failure", self.expectedFailures[-1][1])

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.outcome(test, "unexpected_success")

    def addSubTest(self, test, subtest, error):
        super().addSubTest(test, subtest, error)
        if error is not None:
            failure = issubclass(error[0], test.failureException)
            records = self.failures if failure else self.errors
            self.outcome(subtest, "failure" if failure else "error", records[-1][1])


class TrackingSuite(unittest.TestSuite):
    def _handleClassSetUp(self, test, result):
        if test.__class__ == getattr(result, "_previousTestClass", None):
            return super()._handleClassSetUp(test, result)
        started = perf_counter()
        name = test.__class__.__module__ + "." + test.__class__.__qualname__
        result.observe("class_phase_started", **{"class": name, "phase": "setUpClass"})
        try:
            return super()._handleClassSetUp(test, result)
        finally:
            phase = {
                "class": name,
                "phase": "setUpClass", "seconds": perf_counter() - started,
            }
            result.class_phases.append(phase)
            result.observe("class_phase_stopped", **phase)

    def _tearDownPreviousClass(self, test, result):
        previous = getattr(result, "_previousTestClass", None)
        if previous is None or test is not None and test.__class__ == previous:
            return super()._tearDownPreviousClass(test, result)
        started = perf_counter()
        name = previous.__module__ + "." + previous.__qualname__
        result.observe("class_phase_started", **{"class": name, "phase": "tearDownClass"})
        try:
            return super()._tearDownPreviousClass(test, result)
        finally:
            phase = {
                "class": name,
                "phase": "tearDownClass", "seconds": perf_counter() - started,
            }
            result.class_phases.append(phase)
            result.observe("class_phase_stopped", **phase)


def independent_ids(plan: dict, lane: str) -> list[str]:
    return [identity for identity in plan["assignments"][lane]
            if classify_module(identity.split(".", 1)[0],
                               test_class=identity.rsplit(".", 1)[0].partition(".")[2]) == (lane, True)]


def receive_serial_release(plan_sha: str) -> None:
    raw = sys.stdin.buffer.read(4097)
    if len(raw) > 4096:
        raise PipelineError("source-test SDK readiness control exceeds its bound")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    if not isinstance(value, dict) or set(value) != {"version", "plan_sha256", "ready"} or \
            type(value["version"]) is not int or value["version"] != 1 or \
            value["plan_sha256"] != plan_sha or type(value["ready"]) is not bool:
        raise PipelineError("source-test SDK readiness has the wrong plan or shape")
    if not value["ready"]:
        raise PipelineError("serial prerequisites or owned cleanup were not confirmed")


class GatedSuite(TrackingSuite):
    def __init__(self, early, delayed, checkpoint: Callable, release: Callable):
        super().__init__()
        self.early, self.delayed = early, delayed
        self.checkpoint, self.release = checkpoint, release

    def countTestCases(self):
        return len(self.early) + len(self.delayed)

    def run(self, result, debug=False):
        if self.early:
            TrackingSuite(self.early).run(result, debug)
        # A completed top-level unittest suite leaves its last fixture class recorded.
        # Clear that lifecycle pointer, not the retained TestResult evidence.
        result._previousTestClass = None
        try:
            self.checkpoint(result)
            self.release()
        except (OSError, ValueError, PipelineError, subprocess.SubprocessError, KeyboardInterrupt) as error:
            result.addError(unittest.suite._ErrorHolder("source-test SDK readiness"),
                            (type(error), error, error.__traceback__))
            return result
        return TrackingSuite(self.delayed).run(result, debug)


def worker_environment(state: Path, parallel: bool) -> dict[str, str]:
    env = dict(os.environ)
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "COPILOT_TOKEN", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(name, None)
    directories = {
        "TEMP": "temp", "TMP": "temp", "TMPDIR": "temp",
        "NODE_COMPILE_CACHE": "node-cache", "PYTHONUSERBASE": "python-user",
    }
    if "NODE_COMPILE_CACHE" not in env:
        directories.pop("NODE_COMPILE_CACHE")
    if parallel:
        directories.update({
            "HOME": "home", "USERPROFILE": "home", "APPDATA": "appdata",
            "LOCALAPPDATA": "localappdata", "XDG_CONFIG_HOME": "config",
            "XDG_CACHE_HOME": "cache", "XDG_DATA_HOME": "data", "GRADLE_USER_HOME": "gradle",
        })
    for name, relative in directories.items():
        directory = state / relative
        directory.mkdir(parents=True, exist_ok=True)
        env[name] = str(directory)
    if parallel:
        options = f' -Duser.home="{state / "home"}" -Djava.io.tmpdir="{state / "temp"}"'
        for name in ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS"):
            if name == "JAVA_TOOL_OPTIONS" or name in env:
                env[name] = env.get(name, "") + options
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def verify_isolation(lane: str) -> dict:
    values = {name: os.environ.get(name) for name in (
        "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
        "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "GRADLE_USER_HOME",
    )}
    if lane != "serial":
        properties = run([java_executable(), "-XshowSettings:properties", "-version"], capture=True)
        for key, expected in (("user.home", values["HOME"]), ("java.io.tmpdir", values["TEMP"])):
            match = re.search(r"^\s*" + re.escape(key) + r" = (.+)$", properties.stderr, re.MULTILINE)
            if not isinstance(expected, str) or not match or Path(match.group(1).strip()).resolve() != Path(expected).resolve():
                raise PipelineError("effective JVM source-test isolation does not match the owned lane")
            values[key] = match.group(1).strip()
    return values


def worker_receipt(plan: dict, lane: str, assigned: list[str], result: TrackingResult,
                   isolation: dict, plan_sha: str, started: float) -> dict:
    indices = {identity: index for index, identity in enumerate(plan["ids"])}
    stopped = {method["test"] for method in result.methods}
    assigned_set = set(assigned)
    return {
        "version": 1, "lane": lane, "plan_sha256": plan_sha,
        "discovered_ids": plan["ids"], "assigned_ids": assigned, "executed_ids": list(result.executed),
        "tests_run": result.testsRun, "successful": result.wasSuccessful(),
        "failures": [[test.id(), text] for test, text in result.failures],
        "errors": [[test.id(), text] for test, text in result.errors],
        "skipped": [[test.id(), reason] for test, reason in result.skipped],
        "expected_failures": [[test.id(), text] for test, text in result.expectedFailures],
        "unexpected_successes": [test.id() for test in result.unexpectedSuccesses],
        "methods": list(result.methods), "class_phases": list(result.class_phases),
        "seconds": perf_counter() - started, "pid": os.getpid(), "isolation": isolation,
        "test_results": [{
            "index": indices[identity], "id": identity,
            "started": identity in result.executed, "stopped": identity in stopped,
            "outcomes": result.outcomes.get(identity, []),
        } for identity in assigned],
        "fixture_results": [outcome for identity, outcomes in result.outcomes.items()
                            if identity not in assigned_set for outcome in outcomes],
    }


def worker(plan_path: Path, lane: str, overlap_cli: bool = False) -> int:
    plan = load_plan(plan_path)
    root = Path(plan["root"])
    if root.resolve() != ROOT.resolve():
        raise PipelineError("source-test worker has the wrong source root")
    loaded = discover(root)
    if loaded.ids != plan["ids"] or loaded.assignments != plan["assignments"] or input_snapshot(root) != plan["inputs"]:
        raise PipelineError("worker discovery, assignment or source binding changed")
    assigned = plan["assignments"][lane]
    if not assigned:
        raise PipelineError("source-test worker received zero methods")
    isolation = verify_isolation(lane)
    assigned_set = set(assigned)
    selected = [test for test in loaded.tests if test.id() in assigned_set]
    started = perf_counter()
    plan_sha = sha256_file(plan_path)
    suite = TrackingSuite(selected)
    if overlap_cli:
        if lane == "serial" or not overlap_profile(loaded)["enabled"]:
            raise PipelineError("source-test CLI overlap has no reviewed resource profile")
        early_ids = independent_ids(plan, lane)
        early_set = set(early_ids)

        def checkpoint(result):
            if early_ids:
                write_json(plan_path.parent / (lane + ".partial.receipt.json"),
                           worker_receipt(plan, lane, early_ids, result, isolation, plan_sha, started))

        suite = GatedSuite([test for test in selected if test.id() in early_set],
                           [test for test in selected if test.id() not in early_set],
                           checkpoint, lambda: receive_serial_release(plan_sha))
    result = None
    indices = {identity: index for index, identity in enumerate(plan["ids"])}
    try:
        with (plan_path.parent / (lane + ".progress.jsonl")).open("x", encoding="utf-8", newline="\n") as journal:
            sequence = 0

            def progress(event):
                nonlocal sequence
                value = {
                    "version": 1, "provisional": True, "full_graph_qualified": False,
                    "plan_sha256": plan_sha, "lane": lane, "pid": os.getpid(),
                    "sequence": sequence, "elapsed_seconds": perf_counter() - started, **event,
                }
                if "test" in event:
                    value["index"] = indices[event["test"]]
                json.dump(value, journal, allow_nan=False, separators=(",", ":"))
                journal.write("\n")
                journal.flush()
                sequence += 1

            progress({"event": "worker_started", "worker_origin_seconds": started})
            result = unittest.TextTestRunner(
                stream=sys.stderr, verbosity=2,
                resultclass=lambda *args, **kwargs: TrackingResult(*args, progress=progress, **kwargs),
                failfast=False, buffer=False,
            ).run(suite)
            result.observe("tests_finished", tests_run=result.testsRun)
    except (OSError, ValueError) as error:
        if result is None:
            raise
        result.addError(unittest.suite._ErrorHolder("source-test provisional progress close"),
                        (type(error), error, error.__traceback__))
    write_json(plan_path.parent / (lane + ".receipt.json"),
               worker_receipt(plan, lane, assigned, result, isolation, plan_sha, started))
    return 0 if result.wasSuccessful() else 1


def validate_receipt(path: Path, plan: dict, lane: str, plan_sha: str, child: ChildExit,
                     *, partial: bool = False) -> dict:
    value = read_json(path)
    if not isinstance(value, dict) or set(value) != RECEIPT_KEYS:
        raise PipelineError("missing or malformed source-test child receipt")
    if type(value["version"]) is not int or value["version"] != 1 or value["lane"] != lane or value["plan_sha256"] != plan_sha:
        raise PipelineError("source-test child receipt has the wrong subject")
    assigned = independent_ids(plan, lane) if partial else plan["assignments"][lane]
    if partial and (lane == "serial" or not assigned):
        raise PipelineError("source-test checkpoint is not an independent CLI prefix")
    if value["discovered_ids"] != plan["ids"] or value["assigned_ids"] != assigned:
        raise PipelineError("source-test child substituted discovery or assignment")
    executed = value["executed_ids"]
    if not isinstance(executed, list) or any(not isinstance(item, str) for item in executed):
        raise PipelineError("malformed executed source-test identities")
    if type(value["tests_run"]) is not int or value["tests_run"] != len(executed) or type(value["successful"]) is not bool:
        raise PipelineError("source-test child has inconsistent counts or result")
    for key in ("failures", "errors", "skipped", "expected_failures"):
        if not isinstance(value[key], list) or any(
            not isinstance(item, list) or len(item) != 2 or any(not isinstance(part, str) for part in item)
            for item in value[key]
        ):
            raise PipelineError("malformed source-test child outcomes")
    if not isinstance(value["unexpected_successes"], list) or any(
        not isinstance(item, str) for item in value["unexpected_successes"]
    ):
        raise PipelineError("malformed unexpected source-test successes")
    if not isinstance(value["methods"], list) or any(
        not isinstance(item, dict) or set(item) != {"test", "seconds"} or
        not isinstance(item["test"], str) or type(item["seconds"]) not in (int, float) or
        not math.isfinite(item["seconds"]) or item["seconds"] < 0 for item in value["methods"]
    ):
        raise PipelineError("malformed source-test method timings")
    results = value["test_results"]
    if not isinstance(results, list) or len(results) != len(assigned):
        raise PipelineError("missing or malformed indexed source-test results")
    indices = {identity: index for index, identity in enumerate(plan["ids"])}
    stopped = {item["test"] for item in value["methods"]}
    outcomes = []
    kinds = {"success", "failure", "error", "skip", "expected_failure", "unexpected_success"}

    def checked_outcomes(records):
        if not isinstance(records, list) or any(
            not isinstance(item, dict) or set(item) != {"kind", "id", "detail"} or
            not isinstance(item["kind"], str) or item["kind"] not in kinds or
            not isinstance(item["id"], str) or not item["id"] or
            not isinstance(item["detail"], str) for item in records
        ):
            raise PipelineError("malformed indexed source-test outcomes")
        return records

    for identity, row in zip(assigned, results):
        if not isinstance(row, dict) or set(row) != {"index", "id", "started", "stopped", "outcomes"} or \
                type(row["index"]) is not int or row["index"] != indices[identity] or row["id"] != identity or \
                type(row["started"]) is not bool or row["started"] != (identity in executed) or \
                type(row["stopped"]) is not bool or row["stopped"] != (identity in stopped):
            raise PipelineError("source-test indexed identity/start/stop witness does not match")
        events = checked_outcomes(row["outcomes"])
        if not row["started"] and events:
            raise PipelineError("unstarted source-test method has a forged outcome")
        if any(item["id"] != identity and not item["id"].startswith(identity + " ") for item in events):
            raise PipelineError("source-test outcome belongs to a different method")
        if row["started"] and row["stopped"] and not events:
            raise PipelineError("completed source-test method has no actual TestResult outcome")
        if any(item["kind"] in {"success", "unexpected_success"} and
               (item["id"] != identity or item["detail"]) for item in events):
            raise PipelineError("malformed source-test success outcome")
        if any(item["kind"] == "expected_failure" and item["id"] != identity for item in events):
            raise PipelineError("expected-failure outcome must identify its parent method")
        terminal = [item for item in events if item["id"] == identity and
                    item["kind"] in {"success", "expected_failure", "unexpected_success"}]
        if len(terminal) > 1 or terminal and any(item is not terminal[0] for item in events):
            raise PipelineError("duplicate or contradictory parent terminal outcomes")
        outcomes.extend(events)
    fixtures = checked_outcomes(value["fixture_results"])
    if any(item["kind"] not in {"error", "skip"} or item["id"] in indices for item in fixtures):
        raise PipelineError("source-test fixture outcomes contain a method result")
    outcomes.extend(fixtures)
    for field, kind in (("failures", "failure"), ("errors", "error"), ("skipped", "skip"),
                        ("expected_failures", "expected_failure")):
        if Counter(tuple(item) for item in value[field]) != Counter(
            (item["id"], item["detail"]) for item in outcomes if item["kind"] == kind
        ):
            raise PipelineError("indexed source-test witness differs from actual TestResult")
    if Counter(value["unexpected_successes"]) != Counter(
        item["id"] for item in outcomes if item["kind"] == "unexpected_success"
    ):
        raise PipelineError("indexed unexpected-success witness differs from actual TestResult")
    if not isinstance(value["class_phases"], list) or any(
        not isinstance(item, dict) or set(item) != {"class", "phase", "seconds"} or
        not isinstance(item["class"], str) or item["phase"] not in ("setUpClass", "tearDownClass") or
        type(item["seconds"]) not in (int, float) or not math.isfinite(item["seconds"]) or
        item["seconds"] < 0 for item in value["class_phases"]
    ) or not isinstance(value["isolation"], dict):
        raise PipelineError("malformed source-test class or isolation receipt")
    if type(value["pid"]) is not int or value["pid"] <= 0 or value["pid"] != child.pid:
        raise PipelineError("source-test child process identity does not match")
    state = path.parent / "state" / lane
    expected = {"TEMP": state / "temp", "TMP": state / "temp", "TMPDIR": state / "temp"}
    isolation_keys = {"TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                      "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "GRADLE_USER_HOME"}
    if lane != "serial":
        expected.update({
            "HOME": state / "home", "USERPROFILE": state / "home",
            "APPDATA": state / "appdata", "LOCALAPPDATA": state / "localappdata",
            "XDG_CONFIG_HOME": state / "config", "XDG_CACHE_HOME": state / "cache",
            "GRADLE_USER_HOME": state / "gradle",
            "user.home": state / "home", "java.io.tmpdir": state / "temp",
        })
        isolation_keys.update(("user.home", "java.io.tmpdir"))
    if set(value["isolation"]) != isolation_keys or any(
        member is not None and not isinstance(member, str) for member in value["isolation"].values()
    ):
        raise PipelineError("malformed source-test environment isolation")
    for key, directory in expected.items():
        actual = value["isolation"][key]
        if not isinstance(actual, str) or Path(actual).resolve() != directory.resolve():
            raise PipelineError("source-test child did not use its isolated lane state")
    if type(value["seconds"]) not in (int, float) or not math.isfinite(value["seconds"]) or value["seconds"] < 0:
        raise PipelineError("malformed source-test elapsed time")
    successful = not (value["failures"] or value["errors"] or value["unexpected_successes"])
    if value["successful"] != successful:
        raise PipelineError("source-test child result contradicts its actual outcomes")
    if Counter(executed) != Counter(assigned):
        raise ChildResultError("missing, duplicate or substituted source-test execution", value)
    if Counter(item["test"] for item in value["methods"]) != Counter(executed):
        raise ChildResultError("source-test stop receipts do not match execution", value)
    if not partial and (child.returncode != 0 or not value["successful"]):
        raise ChildResultError(
            "source-test child failed; retained standard results are not a pass", value,
            test_failure_only=child.returncode == 1 and not value["successful"],
        )
    return value


def checkpoint_problems(prefix: dict, final: dict | None) -> list[str]:
    problems = []
    if not prefix["successful"]:
        problems.append("independent checkpoint contains failed actual TestResult evidence")
    if final is None:
        problems.append("independent checkpoint has no accepted final lane receipt")
        return problems
    for key in ("version", "lane", "plan_sha256", "discovered_ids", "pid", "isolation"):
        if prefix[key] != final[key]:
            problems.append(f"independent checkpoint and final lane disagree on {key}")
    final_rows = {row["id"]: row for row in final["test_results"]}
    if any(row != final_rows.get(row["id"]) for row in prefix["test_results"]):
        problems.append("independent checkpoint and final completed method/index/outcomes disagree")
    for key in ("executed_ids", "methods", "fixture_results", "class_phases",
                "failures", "errors", "skipped", "expected_failures", "unexpected_successes"):
        if final[key][:len(prefix[key])] != prefix[key]:
            problems.append(f"independent checkpoint and final completed {key} evidence disagree")
    if prefix["seconds"] > final["seconds"]:
        problems.append("independent checkpoint elapsed time exceeds the final lane")
    return problems


class WindowsJob:
    def __init__(self):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        declarations = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                          ctypes.c_void_p], wintypes.BOOL),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "GetProcessId": ([wintypes.HANDLE], wintypes.DWORD),
            "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            "Thread32First": ([wintypes.HANDLE, ctypes.c_void_p], wintypes.BOOL),
            "Thread32Next": ([wintypes.HANDLE, ctypes.c_void_p], wintypes.BOOL),
            "OpenThread": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "GetProcessIdOfThread": ([wintypes.HANDLE], wintypes.DWORD),
            "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
        }
        for name, (arguments, result) in declarations.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result
        self.handle = None

    def create(self):
        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t),
                ("maximum_working_set", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimits), ("io", ctypes.c_ulonglong * 6),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
            ]

        self.handle = self.api.CreateJobObjectW(None, None)
        self.check(self.handle, "CreateJobObject")
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; breakaway is not enabled.
        self.check(self.api.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)), "SetInformationJobObject")

    def check(self, success, operation):
        if not success:
            code = ctypes.get_last_error()
            raise OSError(code, f"owned Windows process {operation} failed ({code})")

    def attach_and_resume(self, process: subprocess.Popen):
        process_handle = int(process._handle)
        if self.api.GetProcessId(process_handle) != process.pid:
            raise PipelineError("spawned Windows process handle does not match its recorded identity")
        self.check(self.api.AssignProcessToJobObject(self.handle, process_handle), "AssignProcessToJobObject")

        class ThreadEntry(ctypes.Structure):
            _fields_ = [
                ("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("id", wintypes.DWORD),
                ("owner", wintypes.DWORD), ("priority", wintypes.LONG),
                ("delta", wintypes.LONG), ("flags", wintypes.DWORD),
            ]

        snapshot = self.api.CreateToolhelp32Snapshot(4, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            self.check(False, "CreateToolhelp32Snapshot")
        threads = []
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            found = self.api.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.owner == process.pid:
                    threads.append(entry.id)
                found = self.api.Thread32Next(snapshot, ctypes.byref(entry))
            if ctypes.get_last_error() != 18:
                self.check(False, "Thread32Next")
        finally:
            self.check(self.api.CloseHandle(snapshot), "CloseHandle(thread snapshot)")
        if len(threads) != 1:
            raise PipelineError("suspended owned Windows worker has an ambiguous initial thread")
        thread = self.api.OpenThread(0x0802, False, threads[0])
        self.check(thread, "OpenThread")
        try:
            if self.api.GetProcessIdOfThread(thread) != process.pid:
                raise PipelineError("owned initial thread no longer belongs to the spawned worker")
            suspended = self.api.ResumeThread(thread)
            if suspended == 0xFFFFFFFF:
                self.check(False, "ResumeThread")
            if suspended != 1:
                raise PipelineError("owned worker did not have exactly one initial suspension")
        finally:
            self.check(self.api.CloseHandle(thread), "CloseHandle(initial thread)")

    def active(self) -> int:
        class Accounting(ctypes.Structure):
            _fields_ = [
                ("times", ctypes.c_longlong * 4), ("page_faults", wintypes.DWORD),
                ("total", wintypes.DWORD), ("active", wintypes.DWORD), ("terminated", wintypes.DWORD),
            ]

        value = Accounting()
        self.check(self.api.QueryInformationJobObject(
            self.handle, 1, ctypes.byref(value), ctypes.sizeof(value), None), "QueryInformationJobObject")
        return value.active

    def terminate(self):
        self.check(self.api.TerminateJobObject(self.handle, 1), "TerminateJobObject")

    def close(self):
        if self.handle is not None:
            handle = self.handle
            self.check(self.api.CloseHandle(handle), "CloseHandle(job)")
            self.handle = None


def _ownership_record(windows: bool) -> dict:
    return {
        "kind": "Windows Job Object" if windows else "unreaped POSIX process group",
        "acquisition_started": False, "attached_before_execution": False, "leader_exit": None,
        "remaining_after_leader": None, "remaining_after_cleanup": None,
        "cleanup_confirmed": False, "group_fenced": False,
        "group_drain_confirmed": False, "reap_started": False,
        "leader_join_confirmed": False, "lingering_after_leader": False,
    }


def _wait_posix_leader(process: subprocess.Popen, timeout: float | None, *,
                       deadline: float | None = None) -> int:
    if deadline is None and timeout is not None:
        deadline = perf_counter() + timeout
    while True:
        result = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT |
                           (os.WNOHANG if deadline is not None else 0))
        if result is not None:
            return result.si_status if result.si_code == os.CLD_EXITED else -result.si_status
        if perf_counter() >= deadline:
            raise subprocess.TimeoutExpired(process.args, timeout)
        sleep(0.01)


def _posix_group_active(pid: int) -> int:
    count = 0
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit() or int(directory.name) == pid:
            continue
        try:
            text = (directory / "stat").read_bytes()
        except FileNotFoundError:
            continue  # A process that disappeared during observation is already gone.
        if b")" not in text:
            raise PipelineError("owned process-group observation returned a malformed stat record")
        fields = text[text.rindex(b")") + 2:].split()
        if len(fields) < 3:
            raise PipelineError("owned process-group stat record is incomplete")
        if int(fields[2]) == pid and fields[0] not in {b"Z", b"X"}:
            count += 1
    return count


def _fence_posix_group(process: subprocess.Popen, record: dict) -> None:
    if record.get("reap_started"):
        raise PipelineError("Linux process-group signaling refused after reaping started")
    os.killpg(process.pid, signal.SIGKILL)
    record["group_fenced"] = True


def _join_posix_after_drain(process: subprocess.Popen, record: dict, timeout: float) -> int:
    if not record.get("group_fenced") or not record.get("group_drain_confirmed"):
        raise PipelineError("Linux leader cannot be reaped before confirmed reserved-group drain")
    # Popen.wait can reap and then re-raise cancellation; authority must end before the call.
    record["reap_started"] = True
    code = process.wait(timeout=timeout)
    record.update(leader_exit=code, leader_join_confirmed=True)
    return code


class OwnedProcess:
    def __init__(self, process: subprocess.Popen):
        self.process = process
        self.exit: int | None = None
        self.windows = os.name == "nt"
        self.job = None
        self.acquired = False
        self.record = _ownership_record(self.windows)

    def acquire(self):
        self.record["acquisition_started"] = True
        if self.windows:
            self.job = WindowsJob()
            self.job.create()
            self.job.attach_and_resume(self.process)
        elif os.getpgid(self.process.pid) != self.process.pid:
            raise PipelineError("owned POSIX worker did not start in its own reserved process group")
        self.acquired = True
        self.record["attached_before_execution"] = True

    def wait_leader(self, timeout: float | None = None) -> int:
        if self.exit is None:
            if self.windows:
                self.exit = self.process.wait(timeout=timeout)
            else:
                self.exit = _wait_posix_leader(self.process, timeout)
            self.record["leader_exit"] = self.exit
        return self.exit

    def active(self) -> int:
        if self.job:
            return self.job.active()
        return _posix_group_active(self.process.pid)

    def terminate(self):
        if self.windows:
            if not self.acquired:
                self.process.kill()  # The original Popen creation handle covers pre-attachment failure.
            if self.job is not None and self.job.handle is not None:
                self.job.terminate()
        else:
            _fence_posix_group(self.process, self.record)

    def join_after_drain(self, timeout: float) -> None:
        if not self.windows:
            self.exit = _join_posix_after_drain(self.process, self.record, timeout)
        else:
            self.exit = self.process.wait(timeout=timeout)
        self.record["leader_exit"] = self.exit
        self.record["leader_join_confirmed"] = True

    def finish(self, forced: bool) -> bool:
        if self.record["cleanup_confirmed"]:
            return False
        deadline = perf_counter() + 5
        if not self.windows and self.record.get("reap_started"):
            self.join_after_drain(timeout=max(0, deadline - perf_counter()))
            self.record.update(remaining_after_cleanup=0, cleanup_confirmed=True)
            return self.record.get("lingering_after_leader", False)
        if forced:
            self.terminate()
        self.wait_leader(timeout=max(0, deadline - perf_counter()))
        if self.windows and (self.job is None or self.job.handle is None):
            if self.acquired:
                raise PipelineError("acquired Windows ownership has no retained Job handle")
            remaining = 0  # The unresumed or acquisition-failed leader was killed through its handle.
        else:
            try:
                remaining = self.active()
            except (OSError, ValueError, PipelineError, KeyboardInterrupt) as error:
                if not self.windows:
                    try:
                        self.terminate()
                    except (OSError, ValueError, PipelineError, KeyboardInterrupt) as containment_error:
                        error.add_note(
                            f"Reserved-group containment failed: {type(containment_error).__name__}: "
                            f"{containment_error}")
                        raise error from containment_error
                raise
        self.record["remaining_after_leader"] = remaining
        lingering = bool(remaining) and not forced
        if lingering:
            self.record["lingering_after_leader"] = True
        if not self.windows:
            self.terminate()  # Fence the reserved group even when a racing procfs scan returned zero.
            self.record["group_fenced"] = True
        elif remaining:
            self.terminate()
        while not self.windows or self.job is not None and self.job.handle is not None:
            if not self.active():
                break
            if perf_counter() >= deadline:
                raise PipelineError("owned process group/job did not drain within five seconds")
            sleep(0.01)
        if not self.windows:
            self.record["group_drain_confirmed"] = True
        self.join_after_drain(timeout=max(0, deadline - perf_counter()))
        if self.job is not None:
            self.job.close()
        self.record.update(remaining_after_cleanup=0, cleanup_confirmed=True)
        return lingering

    def release_unconfirmed(self):
        """Best-effort containment after failed drain; never upgrades its confirmation."""
        if not self.windows:
            if not self.record.get("reap_started") and not self.record.get("group_fenced"):
                self.terminate()
            return
        try:
            if self.windows and self.job is not None:
                self.job.close()  # Kill-on-close contains assigned descendants without a PID lookup.
        finally:
            if self.windows:
                self.process.kill()
                self.exit = self.process.wait(timeout=5)
                self.record["leader_exit"] = self.exit


def _finish_pending_posix_process(process: subprocess.Popen, record: dict, deadline: float) -> None:
    if not record.get("reap_started"):
        _fence_posix_group(process, record)
        record["leader_exit"] = _wait_posix_leader(
            process, max(0, deadline - perf_counter()), deadline=deadline)
        try:
            record["remaining_after_leader"] = _posix_group_active(process.pid)
        except (OSError, ValueError, PipelineError, KeyboardInterrupt) as error:
            try:
                _fence_posix_group(process, record)
            except (OSError, ValueError, PipelineError, KeyboardInterrupt) as containment_error:
                error.add_note(
                    f"Reserved-group containment failed: {type(containment_error).__name__}: "
                    f"{containment_error}")
                raise error from containment_error
            raise
        _fence_posix_group(process, record)
        while _posix_group_active(process.pid):
            if perf_counter() >= deadline:
                raise PipelineError("owned process group/job did not drain within five seconds")
            sleep(0.01)
        record["group_drain_confirmed"] = True
    _join_posix_after_drain(process, record, max(0, deadline - perf_counter()))
    record.update(remaining_after_cleanup=0, cleanup_confirmed=True)


def cleanup_pending_process(process: subprocess.Popen, record: dict) -> None:
    windows = os.name == "nt"
    if not windows:
        for key, value in _ownership_record(False).items():
            record.setdefault(key, value)
    record["cleanup_confirmed"] = False
    cancellation = None
    deadline = perf_counter() + 5
    for attempt in range(2):
        try:
            if windows:
                process.kill()
                record["leader_exit"] = process.wait(timeout=5 if attempt == 0 else
                                                    max(0, deadline - perf_counter()))
                record.update(remaining_after_cleanup=0, cleanup_confirmed=True)
            else:
                _finish_pending_posix_process(process, record, deadline)
            break
        except KeyboardInterrupt as error:
            record["cleanup_cancellation"] = str(error)
            cancellation = cancellation or error
        except (OSError, ValueError, PipelineError, subprocess.SubprocessError) as error:
            if cancellation is not None:
                cancellation.add_note(f"Pending cleanup recovery failed: {type(error).__name__}: {error}")
                raise cancellation from error
            raise
    if cancellation is not None:
        raise cancellation


def cleanup_owner(owner: OwnedProcess, forced: bool) -> tuple[bool, list[str]]:
    errors = []
    lingering = False
    try:
        lingering = owner.finish(forced=forced)
    except (OSError, ValueError, PipelineError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        errors.append(f"{type(error).__name__}: {error}")
        errors.extend(getattr(error, "__notes__", []))
        if isinstance(error, KeyboardInterrupt) and not owner.record["cleanup_confirmed"]:
            # One bounded containment attempt after cancellation; the cancellation remains a failure.
            try:
                owner.finish(forced=True)
            except (OSError, ValueError, PipelineError, subprocess.SubprocessError, KeyboardInterrupt) as recovery:
                errors.append(f"cancelled cleanup recovery {type(recovery).__name__}: {recovery}")
        if not owner.record["cleanup_confirmed"]:
            try:
                owner.release_unconfirmed()
            except (OSError, ValueError, PipelineError, subprocess.SubprocessError, KeyboardInterrupt) as recovery:
                errors.append(f"unconfirmed containment {type(recovery).__name__}: {recovery}")
    return lingering or owner.record.get("lingering_after_leader", False), errors


def run_phase(plan_path: Path, lanes: list[str], states: Path,
              observation: PhaseObservation | None = None, *,
              serial_ready: Callable[[ChildExit], None] | None = None) -> dict[str, ChildExit]:
    overlap = serial_ready is not None
    if len(lanes) > (3 if overlap else 2):
        raise PipelineError("more than two CLI lanes and one exclusive serial worker")
    if len(lanes) != len(set(lanes)) or any(lane not in LANES for lane in lanes) or \
            "serial" in lanes and len(lanes) != 1 and not overlap or \
            overlap and (len(lanes) < 2 or lanes[0] != "serial"):
        raise PipelineError("duplicate, unknown or overlapping serial/parallel lanes")
    if os.name != "nt" and (not hasattr(os, "waitid") or not hasattr(os, "WNOWAIT") or not Path("/proc").is_dir()):
        raise PipelineError("owned source-test groups require waitid/WNOWAIT and procfs on this platform")
    observation = observation if observation is not None else PhaseObservation()
    children: dict[str, OwnedProcess] = {}
    pending: subprocess.Popen | None = None
    failure = None
    cleanup_uncertain = False
    drained = set()
    released = set()
    gate_sha = sha256_file(plan_path) if overlap else None

    def release_cli(ready: bool):
        errors = []
        control = (json.dumps({"version": 1, "plan_sha256": gate_sha,
                               "ready": ready}) + "\n").encode("utf-8")
        for lane, owner in children.items():
            if lane == "serial" or lane in released:
                continue
            released.add(lane)
            try:
                owner.process.stdin.write(control)
                owner.process.stdin.flush()
            except (OSError, ValueError, KeyboardInterrupt) as error:
                errors.append(f"{lane}: SDK readiness delivery {type(error).__name__}: {error}")
            finally:
                try:
                    owner.process.stdin.close()
                except (OSError, ValueError, KeyboardInterrupt) as error:
                    errors.append(f"{lane}: SDK readiness close {type(error).__name__}: {error}")
        return errors

    try:
        with ExitStack() as stack:
            for lane in lanes:
                stdout = stack.enter_context((plan_path.parent / (lane + ".stdout.txt")).open("xb"))
                stderr = stack.enter_context((plan_path.parent / (lane + ".stderr.txt")).open("xb"))
                command = [sys.executable, "-B", str(Path(__file__).resolve()),
                           "--worker-plan", str(plan_path), "--lane", lane]
                if overlap and lane != "serial":
                    command.append("--overlap-cli")
                kwargs = {"start_new_session": True} if os.name != "nt" else {
                    "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | WINDOWS_CREATE_SUSPENDED,
                }
                pending = subprocess.Popen(
                    command, cwd=ROOT, env=worker_environment(states / lane, lane != "serial"),
                    stdin=subprocess.PIPE if overlap and lane != "serial" else subprocess.DEVNULL,
                    stdout=stdout, stderr=stderr, **kwargs,
                )
                observation.children[lane] = {"pid": pending.pid, "exit": None, "ownership": None}
                children[lane] = OwnedProcess(pending)
                observation.children[lane]["ownership"] = children[lane].record
                children[lane].acquire()
                pending = None
            for lane, child in children.items():
                observation.children[lane]["exit"] = child.wait_leader()
                if overlap and lane == "serial":
                    lingering, errors = cleanup_owner(child, forced=False)
                    if child.record["cleanup_confirmed"]:
                        drained.add(lane)
                    if lingering:
                        errors.append("owned descendants survived worker leader exit")
                    if errors or not child.record["cleanup_confirmed"]:
                        observation.cleanup_errors.extend(f"{lane}: owned cleanup failed: {error}" for error in errors)
                        raise PipelineError("serial ownership did not qualify SDK readiness")
                    serial_ready(ChildExit(child.process.pid, child.exit))
                    errors = release_cli(True)
                    if errors:
                        observation.cleanup_errors.extend(errors)
                        raise PipelineError("source-test SDK readiness delivery failed")
    except (OSError, ValueError, PipelineError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        failure = error
    finally:
        if overlap:
            try:
                observation.cleanup_errors.extend(release_cli(False))
            except (OSError, ValueError, KeyboardInterrupt) as error:
                observation.cleanup_errors.append(f"SDK readiness abort {type(error).__name__}: {error}")
            controls = [(lane, owner.process) for lane, owner in children.items() if lane != "serial"]
            if pending is not None and not any(owner.process is pending for owner in children.values()):
                controls.append((lane, pending))
            for lane, process in controls:
                if process.stdin is None or process.stdin.closed:
                    continue
                try:
                    process.stdin.close()
                except (OSError, ValueError, KeyboardInterrupt) as error:
                    observation.cleanup_errors.append(f"{lane}: SDK control cleanup {type(error).__name__}: {error}")
                if not process.stdin.closed:
                    observation.cleanup_errors.append(f"{lane}: SDK control handle close is unconfirmed")
                    cleanup_uncertain = True
        if pending is not None and not any(owner.process is pending for owner in children.values()):
            # A Windows worker is still suspended when ownership attachment failed.
            record = {"cleanup_confirmed": False, "leader_exit": None}
            observation.children.setdefault(lane, {"pid": pending.pid, "exit": None, "ownership": record})
            observation.children[lane]["ownership"] = record
            try:
                cleanup_pending_process(pending, record)
            except (OSError, ValueError, PipelineError, subprocess.SubprocessError, KeyboardInterrupt) as error:
                observation.cleanup_errors.append(f"{lane}: pending cleanup {type(error).__name__}: {error}")
                cleanup_uncertain = True
            observation.children[lane]["exit"] = record["leader_exit"]
        for lane, child in children.items():
            if lane in drained:
                continue
            try:
                lingering, errors = cleanup_owner(child, forced=failure is not None)
            except (OSError, ValueError, PipelineError, subprocess.SubprocessError, KeyboardInterrupt) as error:
                lingering = False
                errors = [f"cleanup dispatch {type(error).__name__}: {error}"]
                try:
                    _, recovery_errors = cleanup_owner(child, forced=True)
                    errors.extend(recovery_errors)
                except (OSError, ValueError, PipelineError, subprocess.SubprocessError, KeyboardInterrupt) as recovery:
                    errors.append(f"cleanup dispatch recovery {type(recovery).__name__}: {recovery}")
            if lingering:
                observation.cleanup_errors.append(f"{lane}: owned descendants survived worker leader exit")
            observation.cleanup_errors.extend(f"{lane}: owned cleanup failed: {error}" for error in errors)
            cleanup_uncertain = cleanup_uncertain or bool(errors)
            observation.children[lane]["exit"] = child.exit
        observation.cleanup_confirmed = (
            all(child["ownership"] is not None and child["ownership"].get("cleanup_confirmed") is True
                for child in observation.children.values())
            and not cleanup_uncertain
        )
    if failure is not None:
        raise failure
    if observation.cleanup_errors:
        raise PipelineError("\n".join(observation.cleanup_errors))
    if any(child.exit is None for child in children.values()):
        raise PipelineError("owned worker has no confirmed exit")
    return {lane: ChildExit(child.process.pid, child.exit) for lane, child in children.items()}


def complete() -> int:
    started = perf_counter()
    before = input_snapshot(ROOT)
    discovered = discover(ROOT)
    if input_snapshot(ROOT) != before:
        raise PipelineError("source-test inputs changed during discovery")
    artifact_root = ROOT / "build" / "source-tests"
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifacts = Path(tempfile.mkdtemp(prefix="run-", dir=artifact_root))
    states = artifacts / "state"
    states.mkdir()
    plan = {"version": 1, "root": str(ROOT), "ids": discovered.ids,
            "assignments": discovered.assignments, "inputs": before}
    plan_path = artifacts / "plan.json"
    receipts, problems, phases, shared_fences = {}, [], [], []
    orchestration_errors = []
    ownership_observations = []
    profile = {"enabled": False, "reason": "resource profile not evaluated", "tools": {}}
    checkpoints = {}

    def orchestration_error(stage, error):
        detail = f"{stage}: {type(error).__name__}: {error}"
        problems.append(detail)
        orchestration_errors.append({"stage": stage, "type": type(error).__name__, "error": str(error)})

    def collect_lane(lane: str, observation: PhaseObservation):
        child = observation.children.get(lane)
        if child is None or type(child["exit"]) is not int:
            problems.append(f"{lane}: worker was not launched or its exit is unconfirmed")
            return True
        failed = False
        try:
            receipts[lane] = validate_receipt(
                artifacts / (lane + ".receipt.json"), plan, lane, plan_sha,
                ChildExit(child["pid"], child["exit"]),
            )
        except ChildResultError as error:
            receipts[lane] = error.receipt
            problems.append(f"{lane}: {error}")
            failed = not error.test_failure_only
        except (OSError, ValueError, PipelineError, subprocess.SubprocessError) as error:
            orchestration_error(lane + " receipt", error)
            failed = True
        return failed

    def collect_logs(lane: str):
        failed = False
        for suffix, stream in (("stdout", sys.stdout), ("stderr", sys.stderr)):
            path = artifacts / (lane + "." + suffix + ".txt")
            try:
                stream.write(path.read_text(encoding="utf-8", errors="replace"))
                stream.flush()
            except OSError as error:
                orchestration_error(lane + " " + suffix + " log", error)
                failed = True
        return failed

    print(f"Complete discovery: {len(discovered.ids)} methods; one serial worker and at most two isolated CLI lanes.", flush=True)
    try:
        write_json(plan_path, plan)
        plan_sha = sha256_file(plan_path)
        profile = overlap_profile(discovered)
        if profile["enabled"]:
            print("Independent source-CLI work overlaps serial; canonical SDK readers retain the owned serial barrier.", flush=True)
            tick = perf_counter()
            observation = PhaseObservation()
            ownership_observations.append(observation)
            shared = None
            serial_recorded = False

            def ready(child):
                nonlocal shared, serial_recorded
                phases.append({
                    "phase": "serial", "seconds": perf_counter() - tick, "lanes": ["serial"],
                    "children": {"serial": observation.children["serial"]},
                    "cleanup_confirmed": True, "cleanup_errors": [],
                    "independent_cli_started_with_serial": True,
                })
                serial_recorded = True
                failed = collect_lane("serial", observation)
                failed = collect_logs("serial") or failed
                if input_snapshot(ROOT) != before or sha256_file(plan_path) != plan_sha:
                    raise PipelineError("source-test inputs or assignment plan changed before SDK readiness")
                if source_tool_snapshot() != profile["tools"]:
                    raise PipelineError("immutable source-CLI tools changed during serial work")
                if failed:
                    raise PipelineError("serial execution or result custody did not qualify SDK readiness")
                shared = shared_snapshot(ROOT)

            try:
                run_phase(plan_path, [lane for lane in LANES if plan["assignments"][lane]],
                          states, observation, serial_ready=ready)
            except (OSError, ValueError, PipelineError, subprocess.SubprocessError, KeyboardInterrupt) as error:
                orchestration_error("overlapped source execution", error)
            problems.extend(observation.cleanup_errors)
            if not serial_recorded:
                phases.append({
                    "phase": "serial", "seconds": perf_counter() - tick, "lanes": ["serial"],
                    "children": {key: value for key, value in observation.children.items() if key == "serial"},
                    "cleanup_confirmed": observation.cleanup_confirmed,
                    "cleanup_errors": observation.cleanup_errors,
                })
                collect_lane("serial", observation)
                collect_logs("serial")
            active_cli = [lane for lane in ("cli-1", "cli-2") if plan["assignments"][lane]]
            phases.append({
                "phase": "parallel-cli", "seconds": perf_counter() - tick, "lanes": active_cli,
                "children": {key: value for key, value in observation.children.items() if key != "serial"},
                "cleanup_confirmed": observation.cleanup_confirmed,
                "cleanup_errors": observation.cleanup_errors,
                "independent_work_started_with_serial": True,
            })
            for lane in active_cli:
                collect_lane(lane, observation)
                collect_logs(lane)
                checkpoint = artifacts / (lane + ".partial.receipt.json")
                child = observation.children.get(lane)
                if independent_ids(plan, lane) and not checkpoint.is_file():
                    problems.append(f"{lane}: required independent checkpoint is missing")
                if checkpoint.is_file() and child is not None and type(child["exit"]) is int:
                    try:
                        checkpoints[lane] = validate_receipt(
                            checkpoint, plan, lane, plan_sha, ChildExit(child["pid"], child["exit"]), partial=True,
                        )
                    except ChildResultError as error:
                        checkpoints[lane] = error.receipt
                        problems.append(f"{lane}: incomplete independent checkpoint: {error}")
                    except (OSError, ValueError, PipelineError, subprocess.SubprocessError) as error:
                        orchestration_error(lane + " independent checkpoint", error)
                if lane in checkpoints:
                    problems.extend(f"{lane}: {problem}" for problem in
                                    checkpoint_problems(checkpoints[lane], receipts.get(lane)))
            if input_snapshot(ROOT) != before or sha256_file(plan_path) != plan_sha:
                problems.append("source-test inputs or assignment plan changed")
            if source_tool_snapshot() != profile["tools"]:
                problems.append("immutable source-CLI tools changed")
            if shared is not None:
                after = shared_snapshot(ROOT)
                shared_fences.append({"phase": "parallel-cli", "before": shared, "after": after,
                                      "unchanged": after == shared})
                if after != shared:
                    problems.append("parallel CLI lanes changed shared source/toolchain/generated inputs")
        else:
            print("Keeping serial-first scheduling: " + profile["reason"], flush=True)
        for phase, lanes in (() if profile["enabled"] else (
            ("serial", ["serial"]),
            ("parallel-cli", ["cli-1", "cli-2"]),
        )):
            active = [lane for lane in lanes if plan["assignments"][lane]]
            if not active:
                continue
            shared = None
            if phase == "parallel-cli":
                try:
                    shared = shared_snapshot(ROOT)
                except (OSError, ValueError, PipelineError, subprocess.SubprocessError) as error:
                    orchestration_error(phase + " pre-snapshot", error)
                    break
            tick = perf_counter()
            observation = PhaseObservation()
            ownership_observations.append(observation)
            phase_failed = False
            try:
                run_phase(plan_path, active, states, observation)
            except (OSError, ValueError, PipelineError, subprocess.SubprocessError, KeyboardInterrupt) as error:
                orchestration_error(phase + " execution", error)
                phase_failed = True
            for error in observation.cleanup_errors:
                problems.append(error)
                phase_failed = True
            phases.append({
                "phase": phase, "seconds": perf_counter() - tick, "lanes": active,
                "children": observation.children,
                "cleanup_confirmed": observation.cleanup_confirmed,
                "cleanup_errors": observation.cleanup_errors,
            })
            for lane in active:
                child = observation.children.get(lane)
                if child is None or type(child["exit"]) is not int:
                    problems.append(f"{lane}: worker was not launched or its exit is unconfirmed")
                    phase_failed = True
                else:
                    try:
                        receipts[lane] = validate_receipt(
                            artifacts / (lane + ".receipt.json"), plan, lane, plan_sha,
                            ChildExit(child["pid"], child["exit"]),
                        )
                    except ChildResultError as error:
                        receipts[lane] = error.receipt
                        problems.append(f"{lane}: {error}")
                        if not error.test_failure_only:
                            phase_failed = True
                    except (OSError, ValueError, PipelineError, subprocess.SubprocessError) as error:
                        orchestration_error(lane + " receipt", error)
                        phase_failed = True
                for suffix, stream in (("stdout", sys.stdout), ("stderr", sys.stderr)):
                    path = artifacts / (lane + "." + suffix + ".txt")
                    try:
                        stream.write(path.read_text(encoding="utf-8", errors="replace"))
                        stream.flush()
                    except OSError as error:
                        orchestration_error(lane + " " + suffix + " log", error)
                        phase_failed = True
            try:
                if input_snapshot(ROOT) != before or sha256_file(plan_path) != plan_sha:
                    problems.append("source-test inputs or assignment plan changed")
                    phase_failed = True
            except (OSError, ValueError, PipelineError, subprocess.SubprocessError) as error:
                orchestration_error(phase + " input snapshot", error)
                phase_failed = True
            if shared is not None:
                try:
                    after = shared_snapshot(ROOT)
                    shared_fences.append({"phase": phase, "before": shared, "after": after,
                                          "unchanged": after == shared})
                    if after != shared:
                        problems.append("parallel CLI lanes changed shared source/toolchain/generated inputs")
                        phase_failed = True
                except (OSError, ValueError, PipelineError) as error:
                    orchestration_error(phase + " post-snapshot", error)
                    phase_failed = True
            if phase_failed:
                break
    except (OSError, ValueError, PipelineError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        orchestration_error("planned source-test orchestration", error)
    finally:
        cleanup_confirmed = all(observation.cleanup_confirmed for observation in ownership_observations)
        if cleanup_confirmed:
            try:
                remove_tree(states)
            except (OSError, ValueError, PipelineError, KeyboardInterrupt) as error:
                orchestration_error("owned source-test state cleanup failed", error)
        else:
            problems.append("owned process cleanup is unconfirmed; lane state retained")
    executed = [identity for receipt in receipts.values() for identity in receipt["executed_ids"]]
    if Counter(executed) != Counter(discovered.ids):
        problems.append("complete discovered/executed method-ID union does not match")
    summary = {
        "version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "complete_graph": not problems, "discovered_ids": discovered.ids,
        "assignments": discovered.assignments, "validated_executed_ids": executed,
        "receipts": receipts, "problems": problems, "phases": phases,
        "shared_input_fences": shared_fences,
        "orchestration_errors": orchestration_errors,
        "owned_process_cleanup_confirmed": cleanup_confirmed,
        "overlap_resource_profile": profile,
        "independent_checkpoints_not_full_lane_results": checkpoints,
        "seconds": perf_counter() - started, "state_cleaned_after_child_join": not states.exists(),
        "hosted_600_qualification": False,
    }
    try:
        write_json(artifacts / "complete.receipt.json", summary)
    except (OSError, ValueError, KeyboardInterrupt) as error:
        return fail(f"Failed source-test aggregate could not be written: {error}; raw lane evidence retained.")
    print(f"Source-test evidence: {artifacts.relative_to(ROOT)}")
    if problems:
        return fail("Complete source-test graph FAILED:\n" + "\n".join(problems))
    skipped = sum(len(receipt["skipped"]) for receipt in receipts.values())
    print(f"Complete source-test graph PASS: {len(executed)} exact methods, {skipped} skips, "
          f"{summary['seconds']:.3f}s source-run time; no hosted job/workflow qualification.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--worker-plan", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--lane", choices=LANES, help=argparse.SUPPRESS)
    parser.add_argument("--overlap-cli", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if bool(args.worker_plan) != bool(args.lane):
        parser.error("worker plan and lane must be supplied together")
    if args.overlap_cli and (not args.worker_plan or args.lane == "serial"):
        parser.error("overlap applies only to an owned CLI worker")
    try:
        return worker(args.worker_plan, args.lane, args.overlap_cli) if args.worker_plan else complete()
    except (OSError, ValueError, PipelineError, subprocess.SubprocessError) as error:
        return fail(f"source tests failed: {error}")


if __name__ == "__main__":
    sys.exit(main())
