"""Lint the OpenAPI document with Spectral and the committed ruleset; any finding fails.

  python scripts/lint_spec.py                       lint spec/openapi.yaml with spec/.spectral.yaml
  python scripts/lint_spec.py --spec <file>         lint another document (used by the planted-defect tests)

Every problem is printed with its rule code, JSON path and message, followed by one actionable line
naming the file to fix and the ruleset documentation. Exit code 1 on findings, 2 when Spectral itself
could not run.
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


def describe(finding: dict, spec: Path) -> str:
    path = ".".join(str(part) for part in finding.get("path", [])) or "<document>"
    start = finding.get("range", {}).get("start", {})
    location = f"{spec.as_posix()}:{start.get('line', 0) + 1}:{start.get('character', 0) + 1}"
    return f"{SEVERITIES.get(finding.get('severity', 0), 'error')} [{finding.get('code')}] at {location} ({path}): {finding.get('message')}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--spec", type=Path, default=SPEC)
    parser.add_argument("--ruleset", type=Path, default=RULESET)
    args = parser.parse_args()
    try:
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
