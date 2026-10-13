"""Lint the OpenAPI document with Spectral and the committed ruleset; any finding fails.

  python scripts/lint_spec.py                       lint spec/openapi.yaml with spec/.spectral.yaml
  python scripts/lint_spec.py --spec <file>         lint another document (used by the planted-defect tests)
  python scripts/lint_spec.py --refusal-specs <files...>  batch negative fixtures; each must have findings

Every problem is printed with its rule code, JSON path and message, followed by one actionable line
naming the file to fix and the ruleset documentation. Exit code 1 on findings, 2 when Spectral itself
could not run. The additive refusal batch prints JSON with the actual global CLI exit and complete
findings attributed to each input; a clean, missing or unattributable input fails the batch with 2.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pl_contracts import NODE_MODULES, ROOT, RULESET, SPEC, PipelineError, fail, node_executable, run  # noqa: E402

SPECTRAL = NODE_MODULES / "@stoplight" / "spectral-cli" / "dist" / "index.js"
SEVERITIES = {0: "error", 1: "warning", 2: "info", 3: "hint"}


def lint(spec: Path, ruleset: Path = RULESET) -> tuple[int, list[dict]]:
    """Run Spectral and return (exit code, findings). Exit code 0 = clean, 1 = findings, 2 = tool failure."""
    if not SPECTRAL.is_file():
        raise PipelineError("Spectral is not installed; run: npm ci --no-audit --no-fund")
    if not spec.is_file():
        raise PipelineError(f"specification {spec} does not exist")
    if not (spec.parent / "currency-registry.v1.json").is_file():
        raise PipelineError(f"{spec.parent}/currency-registry.v1.json is missing; the money example rule reads the registry next to the document")
    completed = run(
        [node_executable(), str(SPECTRAL), "lint", str(spec), "--ruleset", str(ruleset), "--fail-severity", "warn",
         "--display-only-failures", "--format", "json"],
        capture=True, check=False,
    )
    if completed.returncode not in (0, 1):
        raise PipelineError(f"Spectral could not lint (exit {completed.returncode}):\n{completed.stderr}\n{completed.stdout}")
    findings: list[dict] = []
    if completed.stdout.strip():
        try:
            # Spectral appends a human summary line after the JSON array on a clean run.
            decoded, _ = json.JSONDecoder().raw_decode(completed.stdout.lstrip())
            findings = decoded if isinstance(decoded, list) else []
        except json.JSONDecodeError as error:
            raise PipelineError(f"Spectral returned unparsable JSON: {error}\n{completed.stdout[:2000]}") from error
    findings = [f for f in findings if f.get("severity", 0) <= 1]
    return (1 if findings else 0), findings


def _unique_fields(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PipelineError(f"Spectral returned duplicate JSON field {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise PipelineError(f"Spectral returned non-finite JSON value {value}")


def lint_refusals(specs: list[Path], ruleset: Path = RULESET) -> dict:
    """Run one real CLI for immutable negative fixtures, never invent per-document exit codes."""
    if not specs:
        raise PipelineError("refusal batch requires at least one specification")
    if not SPECTRAL.is_file():
        raise PipelineError("Spectral is not installed; run: npm ci --no-audit --no-fund")
    documents: dict[Path, list[dict]] = {}
    for spec in specs:
        spec = spec.resolve()
        if any(character in str(spec) for character in "*?[]{}()!"):
            raise PipelineError(f"refusal batch requires literal paths without glob syntax: {spec}")
        if spec in documents:
            raise PipelineError(f"duplicate refusal specification: {spec}")
        if not spec.is_file():
            raise PipelineError(f"specification {spec} does not exist")
        if not (spec.parent / "currency-registry.v1.json").is_file():
            raise PipelineError(f"{spec.parent}/currency-registry.v1.json is missing; the money example rule reads the registry next to the document")
        documents[spec] = []
    completed = run(
        [node_executable(), str(SPECTRAL), "lint", *(str(spec) for spec in documents),
         "--ruleset", str(ruleset), "--fail-severity", "warn", "--display-only-failures", "--format", "json"],
        capture=True, check=False,
    )
    if completed.returncode not in (0, 1):
        raise PipelineError(f"Spectral could not lint (exit {completed.returncode}):\n{completed.stderr}\n{completed.stdout}")
    if completed.returncode != 1:
        raise PipelineError("refusal batch requires findings for every input; Spectral returned global exit 0")
    try:
        findings = json.loads(completed.stdout, object_pairs_hook=_unique_fields, parse_constant=_invalid_constant)
    except json.JSONDecodeError as error:
        raise PipelineError(f"Spectral returned unparsable batch JSON: {error}\n{completed.stdout[:2000]}") from error
    if not isinstance(findings, list) or not findings:
        raise PipelineError("Spectral returned no batch finding array for global exit 1")
    seen = set()
    for finding in findings:
        if not isinstance(finding, dict):
            raise PipelineError("Spectral returned a malformed batch finding")
        source = finding.get("source")
        if not isinstance(source, str) or not Path(source).is_absolute() or Path(source) not in documents:
            raise PipelineError(f"Spectral returned an unattributable batch finding source: {source!r}")
        if (type(finding.get("code")) not in (str, int) or not isinstance(finding.get("message"), str)
                or type(finding.get("severity")) is not int or finding["severity"] not in (0, 1)
                or not isinstance(finding.get("path"), list)
                or any(type(part) not in (str, int) or isinstance(part, int) and part < 0 for part in finding["path"])):
            raise PipelineError(f"Spectral returned malformed batch finding fields for {source}")
        location = finding.get("range")
        if not isinstance(location, dict) or any(
            not isinstance(location.get(edge), dict) or any(
                type(location[edge].get(field)) is not int or location[edge][field] < 0
                for field in ("line", "character")
            ) for edge in ("start", "end")
        ):
            raise PipelineError(f"Spectral returned a malformed batch finding range for {source}")
        if (location["end"]["line"], location["end"]["character"]) < (location["start"]["line"], location["start"]["character"]):
            raise PipelineError(f"Spectral returned a reversed batch finding range for {source}")
        identity = (Path(source), json.dumps({key: value for key, value in finding.items() if key != "source"}, sort_keys=True))
        if identity in seen:
            raise PipelineError(f"Spectral returned a duplicate batch finding for {source}")
        seen.add(identity)
        documents[Path(source)].append(finding)
    missing = [str(spec) for spec, values in documents.items() if not values]
    if missing:
        raise PipelineError("refusal batch has no findings for: " + ", ".join(missing))
    return {"cli_exit_code": completed.returncode, "documents": [
        {"source": spec.as_posix(), "findings": values} for spec, values in documents.items()
    ]}


def describe(finding: dict, spec: Path) -> str:
    path = ".".join(str(part) for part in finding.get("path", [])) or "<document>"
    start = finding.get("range", {}).get("start", {})
    location = f"{spec.as_posix()}:{start.get('line', 0) + 1}:{start.get('character', 0) + 1}"
    return f"{SEVERITIES.get(finding.get('severity', 0), 'error')} [{finding.get('code')}] at {location} ({path}): {finding.get('message')}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--spec", type=Path, default=SPEC)
    inputs.add_argument("--refusal-specs", type=Path, nargs="+",
                        help="immutable negative fixtures; JSON report, exit 2 if any input has no attributable finding")
    parser.add_argument("--ruleset", type=Path, default=RULESET)
    args = parser.parse_args()
    try:
        if args.refusal_specs is not None:
            batch = lint_refusals(args.refusal_specs, args.ruleset)
            print(json.dumps(batch))
            return batch["cli_exit_code"]
        code, findings = lint(args.spec, args.ruleset)
    except (PipelineError, OSError) as error:
        fail(f"specification lint could not run: {error}")
        return 2
    relative = args.spec.relative_to(ROOT).as_posix() if args.spec.is_relative_to(ROOT) else args.spec.as_posix()
    if findings:
        for finding in findings:
            print(describe(finding, args.spec), file=sys.stderr)
        print(
            f"Specification lint failed: {len(findings)} problem(s) in {relative}. Fix each listed rule at the given path "
            "(rule descriptions: spec/.spectral.yaml; ADR-015 rules: docs/development.md#lint) and re-run python scripts/lint_spec.py.",
            file=sys.stderr,
        )
        return code
    print(f"specification lint passed: {relative} satisfies spec/.spectral.yaml (OpenAPI 3.1 + ADR-015 rules)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
