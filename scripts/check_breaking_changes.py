"""Detect breaking specification changes against the previously published tag.

Two detectors run against the same pair of documents:

1. `oasdiff breaking` classifies every change that is reachable through an operation (request and
   response semantics, parameters, security) with oasdiff's stable change identifiers.
2. A component guard classifies changes to the shared components themselves (schemas, parameters,
   headers) from `oasdiff diff --format json`, because a consumer generates the component types
   directly from this document whether or not an operation references them yet.

Any finding fails the run unless `spec/breaking-change-acknowledgement.json` names every finding
(id + location) with a reason and the revision bumps the version as semantic versioning requires.
See docs/publication.md#breaking-changes for the expand-and-contract protocol.

Usage:
  python scripts/check_breaking_changes.py
  python scripts/check_breaking_changes.py --base <tag-or-file> --revision <file> [--acknowledgement <file>]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pl_contracts import PipelineError, ROOT, SEMVER, SPEC, fail, run, spec_version  # noqa: E402
from toolchain import ensure_installed  # noqa: E402

ACKNOWLEDGEMENT = ROOT / "spec" / "breaking-change-acknowledgement.json"
TAG = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
NARROWING = {
    # (diff key, direction that breaks): a lower maximum or a higher minimum rejects values it accepted before.
    "maxLength": "decreased", "maxItems": "decreased", "maxProperties": "decreased", "maximum": "decreased",
    "minLength": "increased", "minItems": "increased", "minProperties": "increased", "minimum": "increased",
}
EXACT_KEYS = ("type", "format", "pattern", "multipleOf", "exclusiveMinimum", "exclusiveMaximum")


class Finding:
    def __init__(self, id: str, location: str, text: str, detector: str) -> None:
        self.id, self.location, self.text, self.detector = id, location, text, detector

    def key(self) -> tuple[str, str]:
        return self.id, self.location

    def as_dict(self) -> dict:
        return {"id": self.id, "location": self.location, "text": self.text, "detector": self.detector}


# --- baseline resolution -------------------------------------------------------------------------

def published_tags() -> list[str]:
    completed = subprocess.run(["git", "tag", "--list", "v*"], cwd=str(ROOT), capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise PipelineError("git tag --list failed; run inside the repository checkout")
    tags = [t.strip() for t in completed.stdout.splitlines() if TAG.match(t.strip())]
    return sorted(tags, key=lambda t: tuple(int(x) for x in t[1:].split(".")))


def spec_at_tag(tag: str, target: Path) -> None:
    completed = subprocess.run(["git", "show", f"{tag}:spec/openapi.yaml"], cwd=str(ROOT), capture_output=True, check=False)
    if completed.returncode != 0:
        raise PipelineError(f"could not read spec/openapi.yaml at tag {tag}: {completed.stderr.decode('utf-8', 'replace').strip()}")
    target.write_bytes(completed.stdout)


# --- detectors --------------------------------------------------------------------------------------

def oasdiff_json(oasdiff: str, subcommand: str, base: Path, revision: Path) -> object:
    completed = run([oasdiff, subcommand, str(base), str(revision), "--format", "json"], capture=True, check=False)
    if completed.returncode not in (0, 1):
        raise PipelineError(f"oasdiff {subcommand} failed (exit {completed.returncode}):\n{completed.stderr}\n{completed.stdout}")
    text = completed.stdout.strip()
    if not text:
        return [] if subcommand == "breaking" else {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise PipelineError(f"oasdiff {subcommand} returned unparsable JSON: {error}\n{text[:2000]}") from error


def operation_findings(changes: object) -> tuple[list[Finding], list[str]]:
    findings, warnings = [], []
    for change in changes if isinstance(changes, list) else []:
        location = " ".join(part for part in (change.get("operation"), change.get("path")) if part) or change.get("section", "")
        text = change.get("text", change.get("id", "change"))
        if change.get("level", 0) >= 3:
            findings.append(Finding(change["id"], location, text, "oasdiff"))
        elif change.get("level", 0) == 2:
            warnings.append(f"warning [{change['id']}] {location}: {text}")
    return findings, warnings


def _member_identity(value: object) -> bool:
    if not isinstance(value, dict) or set(value) not in ({"index"}, {"index", "component"}):
        return False
    if type(value["index"]) is not int or value["index"] < 0:
        return False
    return "component" not in value or (
        isinstance(value["component"], str) and re.fullmatch(r"[A-Za-z0-9._-]+", value["component"]) is not None
    )


def _only_enum_additions(change: object) -> bool:
    if not isinstance(change, dict) or set(change) != {"added"}:
        return False
    added = change["added"]
    if not isinstance(added, list) or not added:
        return False
    identities = set()
    for value in added:
        if type(value) not in (str, int, float, bool, type(None)) or \
                (type(value) is float and not math.isfinite(value)):
            return False
        identity = ("number" if type(value) in (int, float) else type(value).__name__, value)
        if identity in identities:
            return False
        identities.add(identity)
    return True


def _only_optional_property_additions(diff: object, *, enum_description: bool = False) -> bool:
    """Recognise only additive property diffs, including dereferenced allOf inheritance.

    Response proofs may also opt into scalar enum additions and inert description changes.
    Required, removed, narrowed, conditional and unknown changes are never waived here.
    """
    allowed = {"properties", "items", "allOf"}
    if enum_description:
        allowed.update(("enum", "description"))
    if not isinstance(diff, dict) or not diff or set(diff) - allowed:
        return False
    if "enum" in diff and not _only_enum_additions(diff["enum"]):
        return False
    if "description" in diff:
        description = diff["description"]
        if not isinstance(description, dict) or set(description) != {"from", "to"} or \
                any(value is not None and not isinstance(value, str) for value in description.values()) or \
                description["from"] == description["to"]:
            return False
    if "properties" in diff:
        properties = diff["properties"]
        if not isinstance(properties, dict) or not properties or set(properties) - {"added", "modified"}:
            return False
        if "added" in properties:
            added = properties["added"]
            if not isinstance(added, list) or not added or any(not isinstance(name, str) or not name for name in added) or len(set(added)) != len(added):
                return False
        if "modified" in properties:
            modified = properties["modified"]
            if not isinstance(modified, dict) or not modified or any(not isinstance(name, str) or not name for name in modified):
                return False
            if any(not _only_optional_property_additions(child, enum_description=enum_description) for child in modified.values()):
                return False
    if "items" in diff and not _only_optional_property_additions(diff["items"], enum_description=enum_description):
        return False
    if "allOf" in diff:
        composition = diff["allOf"]
        if not isinstance(composition, dict) or set(composition) != {"modified"}:
            return False
        records = composition["modified"]
        if not isinstance(records, list) or not records:
            return False
        identities: set[int] = set()
        for change in records:
            if not isinstance(change, dict) or set(change) != {"base", "revision", "diff"} or \
                    not _member_identity(change["base"]) or not _member_identity(change["revision"]) or \
                    change["base"] != change["revision"] or not \
                    _only_optional_property_additions(change["diff"], enum_description=enum_description):
                return False
            index = change["base"]["index"]
            if index in identities:
                return False
            identities.add(index)
    return True


def _only_additive_response_schema(change: object) -> bool:
    if not isinstance(change, dict) or set(change) != {"content"}:
        return False
    content = change["content"]
    if not isinstance(content, dict) or set(content) != {"modified"} or \
            not isinstance(content["modified"], dict) or not content["modified"] or \
            any(not isinstance(name, str) or not name for name in content["modified"]):
        return False
    return all(isinstance(media, dict) and set(media) == {"schema"} and
               _only_optional_property_additions(media["schema"], enum_description=True) for media in content["modified"].values())


def _schema_findings(pointer: str, diff: dict, findings: list[Finding]) -> None:
    for key in EXACT_KEYS:
        if key in diff:
            findings.append(Finding(f"component-schema-{key.lower()}-changed", pointer, f"{key} changed at {pointer}: {json.dumps(diff[key])}", "component-guard"))
    for key, breaking_direction in NARROWING.items():
        change = diff.get(key)
        if isinstance(change, dict) and "from" in change and "to" in change:
            before, after = change["from"], change["to"]
            if before is None or after is None:
                if after is not None:
                    findings.append(Finding(f"component-schema-{key.lower()}-added", pointer, f"{key} constraint added at {pointer}", "component-guard"))
                continue
            if (after < before) if breaking_direction == "decreased" else (after > before):
                findings.append(Finding(f"component-schema-{key.lower()}-{breaking_direction}", pointer, f"{key} {breaking_direction} from {before} to {after} at {pointer}", "component-guard"))
    required = diff.get("required")
    if isinstance(required, dict):
        for name in required.get("added", []) or []:
            findings.append(Finding("component-schema-required-added", f"{pointer}/required/{name}", f"'{name}' became required at {pointer}", "component-guard"))
        for name in required.get("deleted", []) or []:
            findings.append(Finding("component-schema-required-removed", f"{pointer}/required/{name}", f"'{name}' is no longer required at {pointer}", "component-guard"))
    apa = diff.get("additionalPropertiesAllowed")
    if isinstance(apa, dict) and apa.get("to") is False:
        findings.append(Finding("component-schema-additional-properties-forbidden", pointer, f"additionalProperties became false at {pointer}", "component-guard"))
    enum = diff.get("enum")
    if isinstance(enum, dict):
        if enum.get("enumAdded"):
            findings.append(Finding("component-schema-enum-added", pointer, f"an enum constraint was added at {pointer}", "component-guard"))
        for value in enum.get("deleted", []) or []:
            findings.append(Finding("component-schema-enum-value-removed", f"{pointer}/enum/{value}", f"enum value '{value}' removed at {pointer}", "component-guard"))
    properties = diff.get("properties")
    if isinstance(properties, dict):
        for name in properties.get("deleted", []) or []:
            findings.append(Finding("component-schema-property-removed", f"{pointer}/properties/{name}", f"property '{name}' removed from {pointer}", "component-guard"))
        for name, child in (properties.get("modified") or {}).items():
            _schema_findings(f"{pointer}/properties/{name}", child, findings)
    items = diff.get("items")
    if isinstance(items, dict):
        _schema_findings(f"{pointer}/items", items, findings)
    for combinator in ("oneOf", "anyOf", "allOf"):
        change = diff.get(combinator)
        if combinator == "allOf" and combinator in diff and (
                not isinstance(change, dict) or not change or set(change) - {"added", "deleted", "modified"} or
                any(key in change and (not isinstance(change[key], list) or not change[key]) for key in ("added", "deleted"))):
            findings.append(Finding("component-schema-allof-changed", pointer, f"unrecognised allOf change at {pointer}", "component-guard"))
        elif isinstance(change, dict) and (change.get("deleted") or "modified" in change):
            if combinator == "allOf" and _only_optional_property_additions({"allOf": change}):
                continue
            findings.append(Finding(f"component-schema-{combinator.lower()}-changed", pointer, f"{combinator} members changed at {pointer}", "component-guard"))
        if combinator == "allOf" and isinstance(change, dict) and change.get("added"):
            findings.append(Finding("component-schema-allof-added", pointer, f"allOf constraints added at {pointer}", "component-guard"))


def component_findings(diff: object) -> list[Finding]:
    findings: list[Finding] = []
    components = diff.get("components", {}) if isinstance(diff, dict) else {}
    for kind in ("schemas", "parameters", "headers", "responses", "requestBodies", "securitySchemes"):
        section = components.get(kind)
        if not isinstance(section, dict):
            continue
        for name in section.get("deleted", []) or []:
            findings.append(Finding(f"component-{kind}-removed", f"components/{kind}/{name}", f"{kind[:-1]} '{name}' removed from components/{kind}", "component-guard"))
        for name, change in (section.get("modified") or {}).items():
            pointer = f"components/{kind}/{name}"
            if kind == "schemas":
                _schema_findings(pointer, change, findings)
            elif kind in ("parameters", "headers"):
                for key in ("in", "name", "style", "explode"):
                    if key not in change:
                        continue
                    if key == "name" and isinstance(change[key], dict) and str(change[key].get("from", "")).lower() == str(change[key].get("to", "")).lower():
                        continue  # header names are case-insensitive; the differ lowercases referenced ones
                    findings.append(Finding(f"component-{kind}-{key}-changed", pointer, f"{key} changed at {pointer}: {json.dumps(change[key])}", "component-guard"))
                req = change.get("required")
                if isinstance(req, dict) and req.get("to") is True:
                    findings.append(Finding(f"component-{kind}-required-added", pointer, f"{pointer} became required", "component-guard"))
                if isinstance(change.get("schema"), dict):
                    _schema_findings(f"{pointer}/schema", change["schema"], findings)
            elif kind == "responses" and _only_additive_response_schema(change):
                continue
            else:
                findings.append(Finding(f"component-{kind}-changed", pointer, f"{pointer} changed; review manually", "component-guard"))
    return findings


# --- acknowledgement --------------------------------------------------------------------------------

def load_acknowledgement(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PipelineError(f"{path.name}: invalid JSON ({error})") from error
    if not isinstance(data, dict) or not isinstance(data.get("acknowledged"), list) or not isinstance(data.get("baseline"), str):
        raise PipelineError(f"{path.name}: expected {{\"baseline\": \"vX.Y.Z\", \"target_version\": \"X.Y.Z\", \"acknowledged\": [{{\"id\", \"location\", \"reason\"}}]}}")
    for index, entry in enumerate(data["acknowledged"]):
        if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) and entry.get(k).strip() for k in ("id", "location", "reason")):
            raise PipelineError(f"{path.name}: acknowledged[{index}] needs non-empty string members id, location and reason")
    return data


def version_bump_ok(baseline_tag: str, revision_version: str) -> tuple[bool, str]:
    base = tuple(int(x) for x in baseline_tag[1:].split("."))
    new = tuple(int(x) for x in revision_version.split("."))
    if base[0] == 0:
        ok = new > base and (new[0] > 0 or new[1] > base[1])
        return ok, f"pre-1.0 baseline {baseline_tag}: a breaking change needs a MINOR (or MAJOR) bump; info.version is {revision_version}"
    ok = new[0] > base[0]
    return ok, f"baseline {baseline_tag}: a breaking change needs a MAJOR bump; info.version is {revision_version}"


def evaluate(findings: list[Finding], acknowledgement: dict | None, baseline_label: str, revision_version: str | None,
             acknowledgement_path: Path) -> list[str]:
    """Return the list of problems; empty means the run passes."""
    problems: list[str] = []
    if not findings:
        if acknowledgement is not None:
            problems.append(f"{acknowledgement_path.name} is present but no breaking change was detected against {baseline_label}; remove the file so it cannot pre-approve a future break")
        return problems
    if acknowledgement is None:
        problems.append(f"{len(findings)} breaking change(s) against {baseline_label} and no {acknowledgement_path.name}")
        return problems
    if acknowledgement["baseline"] != baseline_label:
        problems.append(f"{acknowledgement_path.name} acknowledges baseline {acknowledgement['baseline']!r} but the comparison baseline is {baseline_label!r}; a stale acknowledgement never carries over")
    acknowledged = {(e["id"], e["location"]) for e in acknowledgement["acknowledged"]}
    detected = {f.key() for f in findings}
    for finding in findings:
        if finding.key() not in acknowledged:
            problems.append(f"unacknowledged: [{finding.id}] {finding.location} - {finding.text}")
    for key in sorted(acknowledged - detected):
        problems.append(f"acknowledgement names a change that was not detected: [{key[0]}] {key[1]}; remove it (blanket acknowledgements are refused)")
    if revision_version is not None and TAG.match(baseline_label):
        ok, message = version_bump_ok(baseline_label, revision_version)
        if not ok:
            problems.append(message)
        target = acknowledgement.get("target_version")
        if target is not None and target != revision_version:
            problems.append(f"{acknowledgement_path.name} target_version {target!r} differs from info.version {revision_version!r}")
    return problems


# --- entry point -------------------------------------------------------------------------------------

def compare(base: Path, revision: Path, oasdiff: str) -> tuple[list[Finding], list[str]]:
    findings, warnings = operation_findings(oasdiff_json(oasdiff, "breaking", base, revision))
    findings.extend(component_findings(oasdiff_json(oasdiff, "diff", base, revision)))
    unique: dict[tuple[str, str], Finding] = {}
    for finding in findings:
        unique.setdefault(finding.key(), finding)
    return list(unique.values()), warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", help="published tag (vX.Y.Z) or a file path; default: the highest v* tag")
    parser.add_argument("--revision", type=Path, default=SPEC, help="document to check; default spec/openapi.yaml")
    parser.add_argument("--acknowledgement", type=Path, default=ACKNOWLEDGEMENT)
    parser.add_argument("--require-baseline", action="store_true", help="fail instead of passing when no published tag exists")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()
    try:
        tools = ensure_installed()
        with tempfile.TemporaryDirectory(prefix="pl-breaking-") as tmp:
            base_path = Path(tmp) / "base.yaml"
            if args.base and Path(args.base).is_file():
                base_label = str(args.base)
                base_path = Path(args.base)
            else:
                tag = args.base or (published_tags()[-1] if published_tags() else None)
                if tag is None:
                    if args.require_baseline:
                        raise PipelineError("no published v* tag exists to compare against")
                    print("breaking-change check: no published v* tag exists yet, so there is no baseline to compare against. "
                          "The first publication defines the baseline; from then on every change is compared with the highest tag.")
                    return 0
                if not TAG.match(tag):
                    raise PipelineError(f"--base must be a published tag vX.Y.Z or an existing file, got {tag!r}")
                spec_at_tag(tag, base_path)
                base_label = tag
            findings, warnings = compare(base_path, args.revision, tools["oasdiff"])
            revision_version = None
            try:
                revision_version = spec_version(args.revision.read_text(encoding="utf-8"))
            except PipelineError as error:
                warnings.append(str(error))
            problems = evaluate(findings, load_acknowledgement(args.acknowledgement), base_label, revision_version, args.acknowledgement)
        if args.format == "json":
            print(json.dumps({"baseline": base_label, "findings": [f.as_dict() for f in findings], "warnings": warnings, "problems": problems}, indent=2))
        else:
            for warning in warnings:
                print(warning)
            if not findings:
                print(f"breaking-change check: no breaking change against {base_label}")
            else:
                print(f"breaking-change check: {len(findings)} breaking change(s) against {base_label}:")
                for finding in findings:
                    print(f"  [{finding.id}] {finding.location} - {finding.text} ({finding.detector})")
            for problem in problems:
                print(f"  problem: {problem}", file=sys.stderr)
        if problems:
            print(
                "Breaking changes are published only through the expand-and-contract protocol. To proceed deliberately, add "
                f"{args.acknowledgement.relative_to(ROOT) if args.acknowledgement.is_relative_to(ROOT) else args.acknowledgement} naming every finding "
                "(id + location + reason), bump info.version as semantic versioning requires, and record the consumer migration; "
                "see docs/publication.md#breaking-changes.",
                file=sys.stderr,
            )
            return 1
        if findings:
            print(f"breaking-change check: every finding is acknowledged in {args.acknowledgement.name}; publication requires the documented version bump")
        return 0
    except (PipelineError, OSError) as error:
        return fail(f"breaking-change check failed: {error}")


if __name__ == "__main__":
    sys.exit(main())
