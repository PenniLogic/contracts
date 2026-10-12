"""Compatibility orchestration over the existing breaking-change detectors (PenniLogic/contracts#3, T-QA-02).

scripts/check_breaking_changes.py owns classification and the acknowledgement protocol. This module
imports it and never forks it:

- operation_findings classifies the `oasdiff breaking` output and component_findings classifies the
  `oasdiff diff` output against both source documents. They are composed and de-duplicated by
  (id, location) exactly as check_breaking_changes.compare composes them; a test runs compare itself
  over the same parsed inputs and fails if the two ever differ;
- evaluate applies the acknowledgement protocol (baseline, every finding named, no blanket entries,
  version_bump_ok, target_version) and load_acknowledgement reads the acknowledgement file.

This module adds the change class (none, additive, breaking), the gate decision of contract_gate and the
result: pass when evaluate reports no problem, otherwise warn under an unexpired warning gate and fail
under a blocking gate.

The inputs are already parsed: the published baseline's OpenAPI document and the revision document as
the pinned parser reads them (check_breaking_changes.source_documents), and the parsed JSON output of the
pinned `oasdiff breaking` and `oasdiff diff`. Producing them from a published artifact needs Node 24.14.0
and the managed toolchain; that parse path is held for the second pull request, so nothing here parses
YAML or runs oasdiff.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_breaking_changes as cbc  # noqa: E402
from contract_gate import MODES, GateDecision  # noqa: E402
from contract_schema import json_equal, printable_text  # noqa: E402
from pl_contracts import SEMVER, PipelineError  # noqa: E402

CHANGE_CLASSES = ("none", "additive", "breaking")
RESULTS = ("pass", "warn", "fail")
RULES = (
    "compat.baseline_invalid", "compat.document_invalid", "compat.version_invalid", "compat.baseline_version_mismatch",
    "compat.detector_output_invalid", "compat.acknowledgement_invalid",
)
ACKNOWLEDGEMENT_NAME = cbc.ACKNOWLEDGEMENT.name
MAX_ACKNOWLEDGEMENT_BYTES = 1024 * 1024
MAX_REPORT_TEXT = 2000
CHANGE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
OASDIFF_LEVELS = (1, 2, 3)  # INFO, WARN, ERR; operation_findings counts 3 as breaking and 2 as a warning


class CompatRefused(Exception):
    """An input cannot be compared. `rule` is a static ID from RULES."""

    def __init__(self, rule: str, detail: str) -> None:
        if rule not in RULES:
            raise ValueError(f"unknown compatibility rule {rule!r}")
        super().__init__(f"{rule}: {detail}")
        self.rule = rule
        self.detail = detail


@dataclass(frozen=True)
class DetectorInputs:
    """Already-parsed inputs of the two existing detectors for one baseline/revision pair."""

    baseline: str
    base_document: object
    revision_document: object
    oasdiff_breaking: object
    oasdiff_diff: object


@dataclass(frozen=True)
class CompatResult:
    baseline: str
    revision_version: str
    change_class: str
    findings: tuple[cbc.Finding, ...]
    warnings: tuple[str, ...]
    problems: tuple[str, ...]
    gate: GateDecision
    result: str

    def section(self) -> dict:
        """The report's `compatibility` section (scripts/verification/compat-report.schema.v1.json)."""
        return {
            "baseline": self.baseline, "revision_version": self.revision_version, "change_class": self.change_class,
            "findings": [{"id": finding.id, "location": printable_text(finding.location, MAX_REPORT_TEXT),
                          "detector": finding.detector} for finding in self.findings],
            "problems": [printable_text(problem, MAX_REPORT_TEXT) for problem in self.problems],
            "result": self.result,
        }


def _is_oasdiff_change(change: object) -> bool:
    """The fields operation_findings reads, with the types it assumes; other oasdiff fields pass through."""
    return (isinstance(change, dict) and isinstance(change.get("id"), str) and CHANGE_ID.fullmatch(change["id"]) is not None
            and type(change.get("level")) is int and change["level"] in OASDIFF_LEVELS
            and all(isinstance(change.get(key, ""), str) for key in ("operation", "path", "section", "text")))


def check_inputs(inputs: DetectorInputs) -> str:
    """Refuse inputs the comparison cannot rely on; return the revision's info.version."""
    if not isinstance(inputs.baseline, str) or cbc.TAG.fullmatch(inputs.baseline) is None:
        raise CompatRefused("compat.baseline_invalid", f"{ascii(inputs.baseline)} is not a published version tag vMAJOR.MINOR.PATCH")
    versions: dict[str, str] = {}
    for label, document in (("base", inputs.base_document), ("revision", inputs.revision_document)):
        if not isinstance(document, dict) or not isinstance(document.get("info"), dict):
            raise CompatRefused("compat.document_invalid", f"the {label} document is not an OpenAPI object with an info object")
        version = document["info"].get("version")
        if not isinstance(version, str) or SEMVER.fullmatch(version) is None:
            raise CompatRefused("compat.version_invalid", f"the {label} document's info.version is not MAJOR.MINOR.PATCH")
        versions[label] = version
    if versions["base"] != inputs.baseline[1:]:
        raise CompatRefused("compat.baseline_version_mismatch",
                            f"the base document declares {versions['base']}; the published baseline {inputs.baseline} declares {inputs.baseline[1:]}")
    breaking, diff = inputs.oasdiff_breaking, inputs.oasdiff_diff
    if not isinstance(breaking, list) or not all(_is_oasdiff_change(change) for change in breaking):
        raise CompatRefused("compat.detector_output_invalid",
                            "the oasdiff breaking output is not a list of changes with an id, a level 1-3 and string locations")
    if not isinstance(diff, dict):
        raise CompatRefused("compat.detector_output_invalid", "the oasdiff diff output is not a JSON object")
    if (breaking or diff) and json_equal(inputs.base_document, inputs.revision_document):
        raise CompatRefused("compat.detector_output_invalid", "the detector output reports changes between identical documents")
    return versions["revision"]


def classify(inputs: DetectorInputs) -> tuple[list[cbc.Finding], list[str]]:
    """Findings and warnings of both existing detectors, composed as check_breaking_changes.compare composes them."""
    try:
        findings, warnings = cbc.operation_findings(inputs.oasdiff_breaking)
        findings.extend(cbc.component_findings(inputs.oasdiff_diff, inputs.base_document, inputs.revision_document))
    except (AttributeError, KeyError, TypeError, ValueError, RecursionError):
        # Malformed detector output can crash the imported classifier (for example comparing a string bound
        # with a number); that is a refusal, never an empty result.
        raise CompatRefused("compat.detector_output_invalid", "the existing classifier cannot process the detector output") from None
    unique: dict[tuple[str, str], cbc.Finding] = {}
    for finding in findings:
        unique.setdefault(finding.key(), finding)
    return list(unique.values()), warnings


def read_acknowledgement(path: Path) -> dict:
    """check_breaking_changes.load_acknowledgement for a named file; a named file must exist."""
    try:
        if not path.is_file():
            raise CompatRefused("compat.acknowledgement_invalid", f"the acknowledgement {ascii(path.name)} is not a file")
        if path.stat().st_size > MAX_ACKNOWLEDGEMENT_BYTES:
            raise CompatRefused("compat.acknowledgement_invalid", f"the acknowledgement exceeds {MAX_ACKNOWLEDGEMENT_BYTES} bytes")
        acknowledgement = cbc.load_acknowledgement(path)
    except PipelineError as error:
        raise CompatRefused("compat.acknowledgement_invalid", printable_text(str(error), MAX_REPORT_TEXT)) from None
    except (OSError, UnicodeDecodeError) as error:
        raise CompatRefused("compat.acknowledgement_invalid", f"the acknowledgement cannot be read ({type(error).__name__})") from None
    assert acknowledgement is not None
    return acknowledgement


def check_compatibility(inputs: DetectorInputs, gate: GateDecision, acknowledgement: dict | None = None,
                        acknowledgement_name: str = ACKNOWLEDGEMENT_NAME) -> CompatResult:
    """Classify the revision against its published baseline, apply the acknowledgement protocol and the gate."""
    if not isinstance(gate, GateDecision) or gate.mode not in MODES:
        raise ValueError("check_compatibility needs a contract_gate.GateDecision")
    revision_version = check_inputs(inputs)
    findings, warnings = classify(inputs)
    if findings:
        change_class = "breaking"
    elif json_equal(inputs.base_document, inputs.revision_document):
        change_class = "none"
    else:
        change_class = "additive"
    problems = cbc.evaluate(findings, acknowledgement, inputs.baseline, revision_version, Path(acknowledgement_name))
    result = "pass" if not problems else ("warn" if gate.mode == "warn" else "fail")
    return CompatResult(
        baseline=inputs.baseline, revision_version=revision_version, change_class=change_class, findings=tuple(findings),
        warnings=tuple(warnings), problems=tuple(problems), gate=gate, result=result,
    )
