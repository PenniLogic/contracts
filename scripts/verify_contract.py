"""Contract verification runner command line (PenniLogic/contracts#3, T-QA-02).

  python scripts/verify_contract.py artifact --tag vX.Y.Z --release-dir DIR [--repo DIR] [--remote NAME]
                                             [--expect-tag-object OBJECT_ID] [--json]
  python scripts/verify_contract.py gate [--gate-state FILE] [--json]
  python scripts/verify_contract.py compat --baseline vX.Y.Z --base-document FILE --revision-document FILE
                                           --oasdiff-breaking FILE --oasdiff-diff FILE
                                           [--acknowledgement FILE] [--gate-state FILE] [--json]
  python scripts/verify_contract.py report --report FILE [--json]

artifact  Verify downloaded Release assets against their annotated, published tag (contract_artifact.py).
gate      Validate the warn/block gate state and print the effective gate now (contract_gate.py).
compat    Classify a revision against its published baseline from already-parsed detector inputs, then
          apply the acknowledgement protocol and the gate (contract_compat.py).
report    Validate a compatibility report and recompute its verdict (contract_report.py).

Exit codes: 0 pass (including a warning under an unexpired warning gate), 1 fail or refused (a missing
release directory is a refused artifact), 2 usage error or a missing, unreadable or malformed input. The
--gate-state of compat is the exception: compat applies a missing or unusable gate state as block, names
the reason in its output and exits as the comparison does. Results go to stdout (with --json, one JSON
document, also for a usage error); without --json usage errors go to stderr. Nothing is written to disk
except the loader's temporary unpack directory, which is removed before the command returns. The only
network access is the loader's `git ls-remote` against the configured remote: git never fetches the
objects a partial clone lacks (repository.objects_missing, exit 2). No run of this command is
product verification unless its report says acceptance_eligible, which needs a pass verdict (a fail or
warn verdict is never eligible), a published artifact whose every check passed (the held GitHub Release
presence check included), the live provider and every live consumer.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contract_compat  # noqa: E402
from contract_artifact import ArtifactRefused, load_published_artifact  # noqa: E402
from contract_compat import (  # noqa: E402
    ACKNOWLEDGEMENT_NAME, CompatRefused, DetectorInputs, check_compatibility, read_acknowledgement,
)
from contract_gate import (  # noqa: E402
    GATE_STATE, GateRefused, decide, format_instant, load_gate_state, require_utc, resolve_gate, utc_now,
)
from contract_report import MAX_TEXT, assess_report_file  # noqa: E402
from contract_schema import (  # noqa: E402
    InputMissing, InputUnavailable, StrictJsonError, dumps, parse_strict_json, printable_text, read_bounded,
)
from pl_contracts import ROOT  # noqa: E402

EXIT_PASS, EXIT_FAIL, EXIT_USAGE = 0, 1, 2
RULES = ("input.missing", "input.unreadable", "input.not_json", "usage.arguments")
COMMANDS = ("artifact", "gate", "compat", "report")
# Loader refusals about the command's own arguments or environment rather than about the artifact. A missing
# release directory is not among them: it is a missing artifact, which is refused (exit 1).
ARTIFACT_USAGE_RULES = frozenset({"git.unavailable", "repository.invalid", "repository.objects_missing", "remote.invalid"})
USAGE_RULES = frozenset(RULES) | ARTIFACT_USAGE_RULES | frozenset(contract_compat.RULES)
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_LINE_TEXT = 400


class UsageProblem(Exception):
    """The command cannot evaluate: a missing, unreadable or malformed input (exit 2)."""

    def __init__(self, rule: str, detail: str) -> None:
        if rule not in USAGE_RULES:
            raise ValueError(f"unknown usage rule {rule!r}")
        super().__init__(f"{rule}: {detail}")
        self.rule = rule
        self.detail = detail


class ArgumentsRefused(Exception):
    """argparse refused the command line (exit 2); raised instead of argparse printing and exiting."""

    def __init__(self, prog: str, usage: str, message: str) -> None:
        super().__init__(message)
        self.prog, self.usage, self.message = prog, usage, message


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # type: ignore[override]
        raise ArgumentsRefused(self.prog, self.format_usage(), message)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="verify_contract.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                     allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True, metavar="{artifact,gate,compat,report}")
    artifact = commands.add_parser("artifact", help="verify downloaded Release assets against their published tag",
                                   allow_abbrev=False)
    artifact.add_argument("--tag", required=True, help="published version tag vMAJOR.MINOR.PATCH")
    artifact.add_argument("--release-dir", required=True, type=Path,
                          help="directory outside the checkout holding exactly the downloaded Release assets")
    artifact.add_argument("--repo", type=Path, default=ROOT, help="top level of a checkout that has the tag (default: this one)")
    artifact.add_argument("--remote", default="origin",
                          help="configured remote that fetches from the canonical PenniLogic/contracts repository (default: origin)")
    artifact.add_argument("--expect-tag-object", metavar="OBJECT_ID",
                          help="refuse unless the tag is this annotated tag object, the one verified before (trust on first use otherwise)")
    gate = commands.add_parser("gate", help="validate the warn/block gate state and print the effective gate now",
                               allow_abbrev=False)
    gate.add_argument("--gate-state", type=Path, default=GATE_STATE, help="gate state file (default: the committed one)")
    compat = commands.add_parser("compat", help="classify a revision against its published baseline", allow_abbrev=False)
    compat.add_argument("--baseline", required=True, help="published baseline tag vMAJOR.MINOR.PATCH")
    compat.add_argument("--base-document", required=True, type=Path, help="baseline OpenAPI document as parsed JSON")
    compat.add_argument("--revision-document", required=True, type=Path, help="revision OpenAPI document as parsed JSON")
    compat.add_argument("--oasdiff-breaking", required=True, type=Path, help="`oasdiff breaking --format json` output")
    compat.add_argument("--oasdiff-diff", required=True, type=Path, help="`oasdiff diff --format json` output")
    compat.add_argument("--acknowledgement", type=Path, help="breaking-change acknowledgement file, when the revision has one")
    compat.add_argument("--gate-state", type=Path, default=GATE_STATE, help="gate state file (default: the committed one)")
    report = commands.add_parser("report", help="validate a compatibility report and recompute its verdict", allow_abbrev=False)
    report.add_argument("--report", required=True, type=Path, help="report file (compat-report.schema.v1.json)")
    for command in (artifact, gate, compat, report):
        command.add_argument("--json", action="store_true", help="print one JSON document instead of text")
    return parser


def _emit(args: argparse.Namespace, payload: dict, lines: list[str]) -> None:
    if args.json:
        sys.stdout.write(dumps(payload))
    else:
        for line in lines:
            print(line)


def _read_json(path: Path, label: str) -> object:
    try:
        return parse_strict_json(read_bounded(path, MAX_INPUT_BYTES))
    except InputMissing:
        raise UsageProblem("input.missing", f"--{label} {ascii(str(path))} does not exist") from None
    except InputUnavailable as error:
        raise UsageProblem("input.unreadable", f"--{label} {ascii(str(path))} {error}") from None
    except StrictJsonError as error:
        raise UsageProblem("input.not_json", f"--{label} {ascii(str(path))} {error}") from None


def _artifact(args: argparse.Namespace, clock: Callable[[], datetime]) -> int:
    try:
        artifact = load_published_artifact(args.tag, args.release_dir, repo=args.repo, remote=args.remote,
                                           expect_tag_object=args.expect_tag_object)
    except ArtifactRefused as refusal:
        if refusal.rule in ARTIFACT_USAGE_RULES:
            raise UsageProblem(refusal.rule, refusal.detail) from None
        _emit(args, {"command": "artifact", "verified": False, "refusal": {"rule": refusal.rule, "detail": refusal.detail}},
              [f"artifact: REFUSED [{refusal.rule}] {refusal.detail}"])
        return EXIT_FAIL
    lines = [f"artifact: VERIFIED {artifact.tag} (tag object {artifact.tag_object}, commit {artifact.commit})",
             f"  published on {artifact.remote} ({artifact.remote_url})",
             f"  specification {artifact.spec_asset} sha256 {artifact.spec_sha256}",
             f"  release-manifest.json sha256 {artifact.manifest_sha256}"]
    lines += [f"  check {check}: {status}" for check, status in artifact.checks]
    lines.append("GitHub Release presence is held (not_run): a verified artifact alone is not acceptance evidence.")
    _emit(args, {"command": "artifact", "verified": True, "artifact": artifact.as_dict()}, lines)
    return EXIT_PASS


def _gate_line(decision_mode: str, reason: str, warn_until: str | None) -> str:
    until = "" if warn_until is None else f", warn_until {warn_until}"
    return f"gate: {decision_mode} ({reason}{until})"


def _gate(args: argparse.Namespace, clock: Callable[[], datetime]) -> int:
    now = require_utc(clock())
    try:
        state = load_gate_state(args.gate_state)
    except GateRefused as refusal:
        if refusal.rule in ("gate.missing", "gate.unreadable"):
            rule = "input.missing" if refusal.rule == "gate.missing" else "input.unreadable"
            raise UsageProblem(rule, f"--gate-state {ascii(str(args.gate_state))}: {refusal.detail}") from None
        _emit(args, {"command": "gate", "valid": False, "now": format_instant(now),
                     "refusal": {"rule": refusal.rule, "detail": refusal.detail},
                     "gate": {"mode": "block", "reason": refusal.rule, "warn_until": None, "state_sha256": refusal.sha256}},
              [f"gate: REFUSED [{refusal.rule}] {refusal.detail}; every reader treats this state as block"])
        return EXIT_FAIL
    decision = decide(state, now)
    _emit(args, {"command": "gate", "valid": True, "now": format_instant(now), "gate": decision.as_dict()},
          [_gate_line(decision.mode, decision.reason, decision.warn_until) + f" at {format_instant(now)}"])
    return EXIT_PASS


def _compat(args: argparse.Namespace, clock: Callable[[], datetime]) -> int:
    documents = {label: _read_json(getattr(args, label.replace("-", "_")), label)
                 for label in ("base-document", "revision-document", "oasdiff-breaking", "oasdiff-diff")}
    acknowledgement, acknowledgement_name = None, ACKNOWLEDGEMENT_NAME
    if args.acknowledgement is not None:
        if not args.acknowledgement.exists():
            raise UsageProblem("input.missing", f"--acknowledgement {ascii(str(args.acknowledgement))} does not exist")
        acknowledgement_name = args.acknowledgement.name
    gate = resolve_gate(args.gate_state, require_utc(clock()))
    try:
        if args.acknowledgement is not None:
            acknowledgement = read_acknowledgement(args.acknowledgement)
        result = check_compatibility(
            DetectorInputs(args.baseline, documents["base-document"], documents["revision-document"],
                           documents["oasdiff-breaking"], documents["oasdiff-diff"]),
            gate, acknowledgement, acknowledgement_name)
    except CompatRefused as refusal:
        raise UsageProblem(refusal.rule, refusal.detail) from None
    section = result.section()
    lines = [f"compat: {result.result.upper()} revision {result.revision_version} against {result.baseline}: "
             f"change class {result.change_class}",
             "  " + _gate_line(gate.mode, gate.reason, gate.warn_until)]
    lines += [f"  finding [{finding['id']}] {printable_text(finding['location'], MAX_LINE_TEXT)} ({finding['detector']})"
              for finding in section["findings"]]
    lines += [f"  warning: {printable_text(warning, MAX_LINE_TEXT)}" for warning in result.warnings]
    lines += [f"  problem: {printable_text(problem, MAX_LINE_TEXT)}" for problem in section["problems"]]
    if result.result == "warn":
        lines.append(f"  the warning gate downgrades these problems until {gate.warn_until}; they block from then on")
    _emit(args, {"command": "compat", "result": result.result, "compatibility": section, "gate": gate.as_dict(),
                 "warnings": [printable_text(warning, MAX_TEXT) for warning in result.warnings]}, lines)
    return EXIT_FAIL if result.result == "fail" else EXIT_PASS


def _report(args: argparse.Namespace, clock: Callable[[], datetime]) -> int:
    try:
        assessment = assess_report_file(args.report)
    except InputMissing:
        raise UsageProblem("input.missing", f"--report {ascii(str(args.report))} does not exist") from None
    except InputUnavailable as error:
        raise UsageProblem("input.unreadable", f"--report {ascii(str(args.report))} {error}") from None
    payload = {
        "command": "report", "valid": not assessment.problems, "verdict": assessment.verdict,
        "acceptance_eligible": assessment.acceptance_eligible, "missing_languages": list(assessment.missing_languages),
        "problems": [{"rule": note.rule, "subject": note.subject} for note in assessment.problems],
        "reasons": [{"rule": note.rule, "subject": note.subject} for note in assessment.reasons],
    }
    if assessment.problems:
        _emit(args, payload, ["report: INVALID"] + [f"  problem {note}" for note in assessment.problems])
        return EXIT_FAIL
    lines = [f"report: VALID, verdict {assessment.verdict}, acceptance_eligible {str(assessment.acceptance_eligible).lower()}"]
    lines += [f"  reason {note}" for note in assessment.reasons]
    _emit(args, payload, lines)
    return EXIT_FAIL if assessment.verdict == "fail" else EXIT_PASS


def main(argv: list[str] | None = None, *, clock: Callable[[], datetime] = utc_now) -> int:
    """Run one subcommand. `clock` is injected for tests; the command line always uses the real UTC clock."""
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        args = _parser().parse_args(argv)
    except ArgumentsRefused as refused:
        if "--json" in argv:
            command = next((item for item in argv if item in COMMANDS), None)
            sys.stdout.write(dumps({"command": command, "usage_error": {"rule": "usage.arguments", "detail": refused.message}}))
        else:
            sys.stderr.write(refused.usage)
            print(f"{refused.prog}: error: {refused.message}", file=sys.stderr)
        return EXIT_USAGE
    except SystemExit as exit_:  # --help
        return EXIT_PASS if exit_.code in (0, None) else EXIT_USAGE
    handlers = {"artifact": _artifact, "gate": _gate, "compat": _compat, "report": _report}
    try:
        return handlers[args.command](args, clock)
    except UsageProblem as problem:
        if args.json:
            sys.stdout.write(dumps({"command": args.command, "usage_error": {"rule": problem.rule, "detail": problem.detail}}))
        else:
            print(f"verify_contract.py {args.command}: [{problem.rule}] {problem.detail}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
