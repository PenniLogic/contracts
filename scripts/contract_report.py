"""Closed compatibility report (PenniLogic/contracts#3, T-QA-02): builder and validator.

The report is the observability output of the verification runner: the contract version it verified,
the compatibility class and gate decision, the provider row, one row per consumer with the consumer's
version and the contract version it was verified against, the verdict, and whether the run is eligible
as acceptance evidence. scripts/verification/compat-report.schema.v1.json closes its shape; this module
adds the rules the schema subset cannot express:

- required_languages is [kotlin, typescript, python]. A language without a consumer row, any not_run
  row and a not_run compatibility section make the verdict fail: missing is a failure, not an omission.
- A published report lists every check of the artifact loader once in contract.checks, with its status
  (pass, or not_run when the checkout or this runner could not decide it). A synthetic-fixture report
  may carry null there.
- A synthetic-fixture report is never acceptance_eligible. acceptance_eligible is true exactly when the
  verdict is pass, the mode is published, every required language has a row, the provider and every
  consumer row are live and every artifact check passed: a fail or warn verdict is never eligible
  (Fable review round 1, F1), so acceptance_eligible true alone means a passing live run. The GitHub
  Release presence check is held and always not_run in this runner, so none of its reports is
  acceptance-eligible yet (review round 2, PSEC-R2-3).
- Synthetic-fixture rows are named synthetic-<language> (synthetic-provider) and live rows never are
  (a not_run row may carry either kind of name); live rows name the version and the repository commit
  they verified; every row that ran names the report's contract version.
- A published report names the tag, the tag commit, the annotated tag object and the release manifest.
  The join keys other records use are contract.{version, tag, tag_commit, spec_sha256} and
  consumers[].{name, language, version, commit, verified_contract_version}.
- The compatibility section is consistent with itself, the gate and the contract version; a breaking change
  that passed carries the version bump check_breaking_changes.version_bump_ok requires (imported, not copied).
- The gate decision is consistent with itself and generated_at, and carries the state's digest exactly when
  the state was read (only gate.missing and gate.unreadable have none).
- The declared verdict, missing_languages and acceptance_eligible must equal the recomputed values.

Every problem and every verdict reason carries one static rule ID and a subject: a required language,
a row as <language>/<name> or provider/<name>, a field or section name (contract, compatibility, gate,
missing_languages, verdict, acceptance_eligible) or a JSON pointer. Row names are schema-checked before
they appear in a subject; other values from the document are never echoed.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_breaking_changes import version_bump_ok  # noqa: E402
from contract_gate import UNREAD_REASONS, VERIFICATION, format_instant, parse_instant  # noqa: E402
from contract_schema import (  # noqa: E402
    InputTooLarge, SchemaViolation, StrictJsonError, first_violation, load_schema, parse_strict_json, read_bounded,
)

REPORT_SCHEMA = VERIFICATION / "compat-report.schema.v1.json"
SCHEMA_ID = "pennilogic-compat-report@1"
REQUIRED_LANGUAGES = ("kotlin", "typescript", "python")
# The checks contract_artifact.PublishedArtifact reports, in its order; the schema's check enum and the loader's
# PASSED_CHECKS, CONDITIONAL_CHECKS and HELD_CHECKS must stay equal to it (drift tests).
ARTIFACT_CHECKS = (
    "tag.grammar", "tag.annotated", "remote.canonical_repository", "tag.published_remote_ref", "release.sha256sums_exact",
    "release.manifest_agreement", "anchor.tag_tree_blobs", "anchor.toolchain_pins", "anchor.manifest_metadata", "anchor.golden",
    "archive.canonical_form", "archive.safe_extraction", "archive.tree_digest", "tag.pinned_identity", "tag.on_remote_main",
    "github_release.presence",
)
SYNTHETIC_PROVIDER = "synthetic-provider"
MAX_REPORT_BYTES = 4 * 1024 * 1024
MAX_CONSUMERS = 64
MAX_FINDINGS = 2000
MAX_PROBLEMS = 2000
MAX_TEXT = 2000
MAX_NAME = 64
MAX_IDENTIFIER = 128

PROBLEM_RULES = (
    "report.not_json", "report.schema", "report.bounds", "report.contract_identity", "report.duplicate_consumer",
    "report.row_status_inconsistent", "report.synthetic_row_name", "report.live_row_unidentified",
    "report.contract_version_mismatch", "report.compat_inconsistent", "report.gate_inconsistent",
    "report.missing_languages_inconsistent", "report.verdict_inconsistent", "report.acceptance_eligible_inconsistent",
    "report.contract_checks_incomplete",
)
REASON_RULES = (
    "report.required_language_missing", "report.row_not_run", "report.row_failed", "report.compat_not_run",
    "report.compat_failed", "report.compat_warned",
)
RULES = PROBLEM_RULES + REASON_RULES


@dataclass(frozen=True)
class Note:
    rule: str
    subject: str

    def __post_init__(self) -> None:
        if self.rule not in RULES:
            raise ValueError(f"unknown report rule {self.rule!r}")

    def __str__(self) -> str:
        return f"[{self.rule}] {self.subject}"


@dataclass(frozen=True)
class Assessment:
    """The validator's view of one report. A report with problems is invalid and has no verdict."""

    problems: tuple[Note, ...]
    reasons: tuple[Note, ...]
    verdict: str | None
    acceptance_eligible: bool | None
    missing_languages: tuple[str, ...]


def _rows(report: dict) -> list[tuple[str, dict, str]]:
    """(subject, row, the only name a synthetic row may carry) for the provider and every consumer."""
    provider = report["provider"]
    rows = [(f"provider/{provider['name']}", provider, SYNTHETIC_PROVIDER)]
    rows += [(f"{row['language']}/{row['name']}", row, f"synthetic-{row['language']}") for row in report["consumers"]]
    return rows


def _derive(report: dict) -> tuple[tuple[str, ...], tuple[Note, ...], str, bool]:
    """missing_languages, verdict reasons, verdict and acceptance_eligible of a schema-valid report."""
    consumers = report["consumers"]
    present = {row["language"] for row in consumers}
    missing = tuple(language for language in REQUIRED_LANGUAGES if language not in present)
    reasons = [Note("report.required_language_missing", language) for language in missing]
    for subject, row, _ in _rows(report):
        if row["result"] == "not_run":
            reasons.append(Note("report.row_not_run", subject))
        elif row["result"] == "fail":
            reasons.append(Note("report.row_failed", subject))
    compat_rule = {"not_run": "report.compat_not_run", "fail": "report.compat_failed", "warn": "report.compat_warned"}
    if report["compatibility"]["result"] in compat_rule:
        reasons.append(Note(compat_rule[report["compatibility"]["result"]], "compatibility"))
    if any(note.rule != "report.compat_warned" for note in reasons):
        verdict = "fail"
    else:
        verdict = "warn" if reasons else "pass"
    checks = report["contract"]["checks"]
    eligible = (verdict == "pass" and report["mode"] == "published" and report["provider"]["mode"] == "live" and not missing
                and all(row["mode"] == "live" for row in consumers)
                and checks is not None and all(check["status"] == "pass" for check in checks))
    return missing, tuple(reasons), verdict, eligible


def _bounds_problem(report: dict) -> Note | None:
    compat = report["compatibility"]
    for pointer, items, bound in (("/consumers", report["consumers"], MAX_CONSUMERS),
                                  ("/compatibility/findings", compat["findings"], MAX_FINDINGS),
                                  ("/compatibility/problems", compat["problems"], MAX_PROBLEMS)):
        if len(items) > bound:
            return Note("report.bounds", pointer)
    texts: list[tuple[str, str, int]] = []  # in the schema's property order, so the first offender is reported
    for index, finding in enumerate(compat["findings"]):
        texts += [(f"/compatibility/findings/{index}/id", finding["id"], MAX_IDENTIFIER),
                  (f"/compatibility/findings/{index}/location", finding["location"], MAX_TEXT),
                  (f"/compatibility/findings/{index}/detector", finding["detector"], MAX_NAME)]
    texts += [(f"/compatibility/problems/{index}", problem, MAX_TEXT) for index, problem in enumerate(compat["problems"])]
    rows = [("/provider", report["provider"])] + [(f"/consumers/{index}", row) for index, row in enumerate(report["consumers"])]
    for pointer, row in rows:
        texts += [(f"{pointer}/name", row["name"], MAX_NAME), (f"{pointer}/version", row["version"] or "", MAX_IDENTIFIER)]
    for pointer, text, bound in texts:
        if len(text) > bound:
            return Note("report.bounds", pointer)
    return None


def _instant_or_none(text: str | None) -> datetime | None:
    try:
        return None if text is None else parse_instant(text)
    except ValueError:
        return None


def _consistency_problems(report: dict) -> list[Note]:
    problems: list[Note] = []
    contract, compat, gate = report["contract"], report["compatibility"], report["gate"]
    version = contract["version"]
    if (contract["tag"] is not None and contract["tag"] != f"v{version}") or (report["mode"] == "published" and any(
            contract[key] is None for key in ("tag", "tag_commit", "tag_object", "release_manifest_sha256"))):
        problems.append(Note("report.contract_identity", "contract"))
    checks = contract["checks"]
    if (checks is None and report["mode"] == "published") or (
            checks is not None and sorted(check["id"] for check in checks) != sorted(ARTIFACT_CHECKS)):
        problems.append(Note("report.contract_checks_incomplete", "contract"))
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(report["consumers"]):
        if (row["language"], row["name"]) in seen:
            problems.append(Note("report.duplicate_consumer", f"/consumers/{index}"))
        seen.add((row["language"], row["name"]))
    for subject, row, synthetic_name in _rows(report):
        not_run = row["mode"] == "not_run"
        if not_run != (row["result"] == "not_run") or (not_run and any(
                row[key] is not None for key in ("version", "commit", "verified_contract_version"))):
            problems.append(Note("report.row_status_inconsistent", subject))
            continue
        if (row["mode"] == "synthetic-fixture" and row["name"] != synthetic_name) or (
                row["mode"] == "live" and row["name"].startswith("synthetic-")):
            problems.append(Note("report.synthetic_row_name", subject))
        if row["mode"] == "live" and (row["version"] is None or row["commit"] is None):
            problems.append(Note("report.live_row_unidentified", subject))
        if not not_run and row["verified_contract_version"] != version:
            problems.append(Note("report.contract_version_mismatch", subject))
    ran = compat["result"] != "not_run"
    consistent = (compat["change_class"] != "not_run") == ran and compat["revision_version"] in (None, version)
    if ran:
        consistent = consistent and compat["baseline"] is not None and compat["revision_version"] == version and (
            (compat["change_class"] == "breaking") == bool(compat["findings"])
            and (compat["change_class"] != "none" or compat["baseline"] == f"v{version}")
            and (compat["result"] == "pass") == (not compat["problems"])
            and (compat["result"] != "warn" or gate["mode"] == "warn")
            and (compat["result"] != "fail" or gate["mode"] == "block")
            # A breaking change passes the acknowledgement protocol only with the bump check_breaking_changes
            # requires (review round 3, CVR3-2): from 1.x a MAJOR bump, from 0.y a MINOR one.
            and (compat["change_class"] != "breaking" or compat["result"] != "pass"
                 or version_bump_ok(compat["baseline"], version)[0]))
    else:
        consistent = consistent and not compat["findings"] and not compat["problems"]
    if not consistent:
        problems.append(Note("report.compat_inconsistent", "compatibility"))
    generated, until = parse_instant(report["generated_at"]), _instant_or_none(gate["warn_until"])
    if gate["mode"] == "warn":
        consistent = gate["reason"] == "gate.warn_active" and until is not None and generated < until
    elif gate["reason"] == "gate.warn_expired":
        consistent = until is not None and generated >= until
    else:
        consistent = gate["reason"] != "gate.warn_active" and gate["warn_until"] is None
    # contract_gate records the state's digest for every decision that read it (review round 3, CVR3-3).
    if not consistent or (gate["state_sha256"] is None) != (gate["reason"] in UNREAD_REASONS):
        problems.append(Note("report.gate_inconsistent", "gate"))
    return problems


def _structure_problem(report: object) -> Note | None:
    violation = first_violation(report, load_schema(REPORT_SCHEMA))
    if violation is not None:
        return Note("report.schema", str(violation))
    assert isinstance(report, dict)
    if _instant_or_none(report["generated_at"]) is None:
        return Note("report.schema", str(SchemaViolation("/generated_at", "instant")))
    return _bounds_problem(report)


def _invalid(note: Note) -> Assessment:
    return Assessment((note,), (), None, None, ())


def assess_report(report: object) -> Assessment:
    """Validate a parsed report against the closed schema and every rule above; recompute its verdict."""
    structure = _structure_problem(report)
    if structure is not None:
        return _invalid(structure)
    assert isinstance(report, dict)
    problems = _consistency_problems(report)
    if problems:  # an inconsistent row or section has no meaningful verdict; report the cause only
        return Assessment(tuple(problems), (), None, None, ())
    missing, reasons, verdict, eligible = _derive(report)
    if report["missing_languages"] != list(missing):
        problems.append(Note("report.missing_languages_inconsistent", "missing_languages"))
    if report["verdict"] != verdict:
        problems.append(Note("report.verdict_inconsistent", "verdict"))
    if report["acceptance_eligible"] is not eligible:
        problems.append(Note("report.acceptance_eligible_inconsistent", "acceptance_eligible"))
    if problems:
        return Assessment(tuple(problems), reasons, None, None, missing)
    return Assessment((), reasons, verdict, eligible, missing)


def assess_report_file(path: Path) -> Assessment:
    """Assess a report file. Raise InputUnavailable when it is missing or unreadable (a usage problem);
    a file that is too large or not strict JSON is an invalid report."""
    try:
        data = read_bounded(Path(path), MAX_REPORT_BYTES)
    except InputTooLarge:
        return _invalid(Note("report.bounds", "/"))
    try:
        report = parse_strict_json(data)
    except StrictJsonError:
        return _invalid(Note("report.not_json", "/"))
    return assess_report(report)


# --- building -------------------------------------------------------------------------------------------

def contract_section(artifact: object) -> dict:
    """The `contract` section for a contract_artifact.PublishedArtifact, its checks and their statuses included."""
    return {
        "version": getattr(artifact, "version"), "tag": getattr(artifact, "tag"), "tag_commit": getattr(artifact, "commit"),
        "tag_object": getattr(artifact, "tag_object"), "spec_sha256": getattr(artifact, "spec_sha256"),
        "release_manifest_sha256": getattr(artifact, "manifest_sha256"),
        "checks": [{"id": check, "status": status} for check, status in getattr(artifact, "checks")],
    }


def not_run_compatibility(baseline: str | None = None) -> dict:
    return {"baseline": baseline, "revision_version": None, "change_class": "not_run", "findings": [], "problems": [],
            "result": "not_run"}


def build_report(*, mode: str, contract: dict, compatibility: dict, gate: dict, provider: dict, consumers: list[dict],
                 generated_at: datetime) -> dict:
    """Assemble a report and derive missing_languages, verdict and acceptance_eligible; refuse an invalid one."""
    report = {
        "schema": SCHEMA_ID, "generated_at": format_instant(generated_at), "mode": mode, "contract": dict(contract),
        "required_languages": list(REQUIRED_LANGUAGES), "compatibility": dict(compatibility), "gate": dict(gate),
        "provider": dict(provider), "consumers": [dict(entry) for entry in consumers],
        "missing_languages": [], "verdict": "fail", "acceptance_eligible": False,
    }
    violation = first_violation(report, load_schema(REPORT_SCHEMA))
    if violation is not None:
        raise ValueError(f"the report sections are outside {REPORT_SCHEMA.name}: {violation}")
    missing, _, verdict, eligible = _derive(report)
    report.update(missing_languages=list(missing), verdict=verdict, acceptance_eligible=eligible)
    assessment = assess_report(report)
    if assessment.problems:
        raise ValueError(f"the report is inconsistent: {', '.join(map(str, assessment.problems))}")
    return report
