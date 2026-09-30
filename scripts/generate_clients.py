"""Generate the Kotlin, TypeScript and Python clients deterministically from spec/openapi.yaml.

Consumers pin a published tag of this repository and run this script from that tag, one
invocation per target language; they never generate from a branch (docs/publication.md).

Usage:
  python scripts/generate_clients.py                      generate every target into build/generated/<language>
  python scripts/generate_clients.py --language python    generate one target
  python scripts/generate_clients.py --verify             generate twice, require byte-identical output and the committed golden hash
  python scripts/generate_clients.py --update-golden      regenerate and rewrite generator/golden.json (commit the result)

Every output carries contracts-manifest.json: specification version and SHA-256, generator name,
version and jar SHA-256, configuration and runtime hashes, and the SHA-256 of the generated tree.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pl_contracts import (  # noqa: E402
    BUILD, GENERATOR_DIR, LANGUAGES, REGISTRY, ROOT, RUNTIME_DIR, SPEC, PipelineError, combined_digest, fail, java_executable,
    remove_tree, run, sha256_bytes, sha256_file, spec_version, tree_hash, versions,
)
from toolchain import ensure_installed  # noqa: E402

MANIFEST_NAME = "contracts-manifest.json"
GOLDEN = GENERATOR_DIR / "golden.json"
IGNORE_OVERRIDE = GENERATOR_DIR / "openapi-generator-ignore"
TEMPLATE_DIRS = {"python": GENERATOR_DIR / "templates" / "python", "kotlin": GENERATOR_DIR / "templates" / "kotlin"}
TEXT_SUFFIXES = {".kt", ".kts", ".ts", ".py", ".md", ".json", ".txt", ".properties", ".gradle", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".mustache"}


def normalized_text(path: Path) -> bytes:
    """Read a text file with LF line endings so a checkout's line-ending setting cannot change output."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def spec_digest(spec: Path) -> str:
    return sha256_bytes(normalized_text(spec))


def config_path(language: str) -> Path:
    return GENERATOR_DIR / f"{language}.json"


def version_properties(language: str, version: str) -> str:
    return {
        "kotlin": f"artifactVersion={version}",
        "typescript": f"npmVersion={version}",
        "python": f"packageVersion={version}",
    }[language]


def registry_entries() -> list[dict]:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    entries = sorted(data["currencies"], key=lambda e: e["code"])
    for entry in entries:
        if not (isinstance(entry["code"], str) and len(entry["code"]) == 3 and entry["code"].isupper() and entry["code"].isalpha()):
            raise PipelineError(f"currency registry code {entry['code']!r} is not an upper-case three-letter code")
        if type(entry["exponent"]) is not int or not 0 <= entry["exponent"] <= 6:
            raise PipelineError(f"currency registry exponent for {entry['code']} must be an integer 0..6")
        if not isinstance(entry["minor_unit_name"], str) or not entry["minor_unit_name"].isascii():
            raise PipelineError(f"currency registry minor_unit_name for {entry['code']} must be an ASCII string")
    return entries


def render_registry(language: str) -> tuple[str, str]:
    """Render the currency registry as source in the target language; returns (relative path, content)."""
    entries = registry_entries()
    header = "Generated from spec/currency-registry.v1.json by scripts/generate_clients.py (PenniLogic/contracts); do not edit."
    if language == "python":
        rows = "".join(
            f'    "{e["code"]}": CurrencyEntry("{e["code"]}", {e["exponent"]}, "{e["minor_unit_name"]}"),\n' for e in entries
        )
        return "pennilogic_contracts/models/currency_registry.py", (
            f'"""{header}\n\nISO 4217 codes accepted by the money codec with their minor-unit exponents (ADR-015 §1.4).\n"""\n\n'
            "from __future__ import annotations\n\nfrom typing import Final, Mapping, NamedTuple\n\n\n"
            "class CurrencyEntry(NamedTuple):\n    code: str\n    exponent: int\n    minor_unit_name: str\n\n\n"
            f"REGISTRY: Final[Mapping[str, CurrencyEntry]] = {{\n{rows}}}\n"
        )
    if language == "typescript":
        rows = "".join(
            f'  {e["code"]}: {{ code: "{e["code"]}", exponent: {e["exponent"]}, minorUnitName: "{e["minor_unit_name"]}" }},\n' for e in entries
        )
        return "src/models/currencyRegistry.ts", (
            f"// {header}\n// ISO 4217 codes accepted by the money codec with their minor-unit exponents (ADR-015 §1.4).\n\n"
            "export interface CurrencyEntry {\n  readonly code: string;\n  readonly exponent: number;\n  readonly minorUnitName: string;\n}\n\n"
            f"export const CURRENCY_REGISTRY: Readonly<Record<string, CurrencyEntry>> = Object.freeze({{\n{rows}}});\n\n"
            "export function currencyExponent(code: string): number | undefined {\n"
            "  const entry = Object.prototype.hasOwnProperty.call(CURRENCY_REGISTRY, code) ? CURRENCY_REGISTRY[code] : undefined;\n"
            "  return entry === undefined ? undefined : entry.exponent;\n}\n"
        )
    if language == "kotlin":
        rows = "".join(
            f'        "{e["code"]}" to CurrencyEntry("{e["code"]}", {e["exponent"]}, "{e["minor_unit_name"]}"),\n' for e in entries
        )
        return "src/main/kotlin/com/pennilogic/contracts/money/CurrencyRegistry.kt", (
            f"// {header}\n// ISO 4217 codes accepted by the money codec with their minor-unit exponents (ADR-015 §1.4).\n"
            "package com.pennilogic.contracts.money\n\n"
            "data class CurrencyEntry(val code: String, val exponent: Int, val minorUnitName: String)\n\n"
            "object CurrencyRegistry {\n    val entries: Map<String, CurrencyEntry> = mapOf(\n"
            f"{rows}    )\n\n    fun exponentOf(code: String): Int? = entries[code]?.exponent\n}}\n"
        )
    raise PipelineError(f"unknown language {language}")


def copy_runtime(language: str, output: Path) -> str:
    """Copy the hand-written seam files into the output with LF line endings; returns their tree hash."""
    source = RUNTIME_DIR / language
    if not source.is_dir():
        raise PipelineError(f"runtime directory {source} is missing")
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        relative = path.relative_to(source)
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(normalized_text(path) if path.suffix in TEXT_SUFFIXES else path.read_bytes())
    return tree_hash(source)[0]


def generate(language: str, output: Path, tools: dict, spec: Path = SPEC) -> dict:
    """Generate one target into `output` and return its manifest."""
    if language not in LANGUAGES:
        raise PipelineError(f"unknown language {language}; choose from {', '.join(LANGUAGES)}")
    pins = versions()
    version = spec_version(spec.read_text(encoding="utf-8"))
    config = config_path(language)
    generator_name = json.loads(config.read_text(encoding="utf-8"))["generatorName"]
    remove_tree(output)
    output.mkdir(parents=True)
    # The generator honours the ignore file it finds in the output directory; the committed override is
    # seeded there before generation and stays part of the output.
    shutil.copyfile(IGNORE_OVERRIDE, output / ".openapi-generator-ignore")
    command = [
        java_executable(), "-Dline.separator=\n", "-Dfile.encoding=UTF-8", "-Dorg.slf4j.simpleLogger.defaultLogLevel=warn",
        "-jar", tools["openapi_generator"], "generate",
        "-i", str(spec), "-c", str(config), "-o", str(output),
        "--global-property", "apiTests=false,modelTests=false,apis,models,supportingFiles,apiDocs,modelDocs",
        "--additional-properties", version_properties(language, version),
    ]
    template_dir = TEMPLATE_DIRS.get(language)
    if template_dir is not None:
        command += ["-t", str(template_dir)]
    completed = run(command, capture=True, check=False)
    if completed.returncode != 0:
        raise PipelineError(f"openapi-generator failed for {language} (exit {completed.returncode}):\n{completed.stdout}\n{completed.stderr}")
    runtime_hash = copy_runtime(language, output)
    relative, content = render_registry(language)
    registry_target = output / relative
    registry_target.parent.mkdir(parents=True, exist_ok=True)
    registry_target.write_bytes(content.encode("utf-8"))
    digest, entries = tree_hash(output, exclude=(MANIFEST_NAME,))
    manifest = {
        "schema_version": 1,
        "target": language,
        "spec_version": version,
        "spec_sha256": spec_digest(spec),
        "currency_registry_sha256": sha256_bytes(normalized_text(REGISTRY)),
        "generator": {
            "name": "openapi-generator-cli",
            "version": pins["openapi_generator"]["version"],
            "jar_sha256": pins["openapi_generator"]["sha256"],
            "generator_name": generator_name,
            "config": config.relative_to(ROOT).as_posix(),
            "config_sha256": sha256_bytes(normalized_text(config)),
            "ignore_override_sha256": sha256_bytes(normalized_text(IGNORE_OVERRIDE)),
            "template_override_sha256": tree_hash(template_dir)[0] if template_dir else None,
        },
        "runtime_sha256": runtime_hash,
        "file_count": len(entries),
        "tree_sha256": digest,
        "files": [{"path": path, "sha256": file_digest} for path, file_digest in entries],
    }
    (output / MANIFEST_NAME).write_bytes((json.dumps(manifest, indent=2, sort_keys=False) + "\n").encode("utf-8"))
    return manifest


def golden_inconsistency(record: dict) -> str | None:
    """Return why a golden record is internally inconsistent, or None.

    The per-file map is not diagnostic only: its fold must reproduce the recorded tree digest, so a
    committed golden file whose entries were edited independently is refused before any comparison.
    """
    files = record.get("files")
    if not isinstance(files, dict) or not files:
        return "generator/golden.json record has no per-file map; run python scripts/generate_clients.py --update-golden"
    if len(files) != record.get("file_count"):
        return f"generator/golden.json file_count {record.get('file_count')} does not match its {len(files)} per-file entries"
    folded = combined_digest(files)
    if folded != record.get("tree_sha256"):
        return f"generator/golden.json per-file hashes fold to {folded}, not the recorded tree_sha256 {record.get('tree_sha256')}; the record was edited inconsistently"
    return None


def load_golden() -> dict:
    if not GOLDEN.is_file():
        return {}
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def verify(languages: list[str], tools: dict, update_golden: bool) -> int:
    golden = load_golden()
    problems: list[str] = []
    for language in languages:
        first = generate(language, BUILD / "generated" / language, tools)
        second = generate(language, BUILD / "generated-verify" / language, tools)
        if first["tree_sha256"] != second["tree_sha256"]:
            changed = sorted({f["path"] for f in first["files"]} ^ {f["path"] for f in second["files"]})
            first_hashes = {f["path"]: f["sha256"] for f in first["files"]}
            changed += sorted(p for p, h in ((f["path"], f["sha256"]) for f in second["files"]) if first_hashes.get(p) not in (None, h))
            problems.append(f"{language}: two generations from the same input differ ({', '.join(changed[:20])}); the generator is not deterministic for this input")
            continue
        print(f"{language}: deterministic ({first['file_count']} files, tree sha256 {first['tree_sha256']})")
        record = golden.get(language)
        current_files = {f["path"]: f["sha256"] for f in first["files"]}
        if update_golden:
            golden[language] = {
                "tree_sha256": first["tree_sha256"], "spec_version": first["spec_version"], "spec_sha256": first["spec_sha256"],
                "file_count": first["file_count"], "files": current_files,
            }
        elif record is None:
            problems.append(f"{language}: generator/golden.json has no entry; run python scripts/generate_clients.py --update-golden and commit it")
        elif golden_inconsistency(record):
            problems.append(f"{language}: {golden_inconsistency(record)}")
        elif record["tree_sha256"] != first["tree_sha256"]:
            golden_files = record.get("files", {})
            differing = sorted(p for p in set(golden_files) | set(current_files) if golden_files.get(p) != current_files.get(p))
            problems.append(
                f"{language}: generated tree sha256 {first['tree_sha256']} differs from golden {record['tree_sha256']} "
                f"(golden spec {record.get('spec_version')} sha256 {record.get('spec_sha256', '')[:12]}..., current spec {first['spec_version']} sha256 {first['spec_sha256'][:12]}...); "
                f"differing files: {', '.join(differing) or 'unknown'}. "
                "If the specification, generator configuration, runtime or generator version changed on purpose, run "
                "python scripts/generate_clients.py --update-golden and commit generator/golden.json; otherwise the output drifted."
            )
        remove_tree(BUILD / "generated-verify" / language)
    if update_golden:
        golden = {language: golden[language] for language in sorted(golden)}
        GOLDEN.write_bytes((json.dumps(golden, indent=2) + "\n").encode("utf-8"))
        print(f"golden hashes written to {GOLDEN.relative_to(ROOT).as_posix()}")
    if problems:
        for problem in problems:
            print(f"determinism check failed: {problem}", file=sys.stderr)
        return 1
    if not update_golden:
        print("determinism check passed: byte-identical double generation and golden hashes match for " + ", ".join(languages))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--language", action="append", choices=LANGUAGES, help="target language (repeatable); default: all")
    parser.add_argument("--output-dir", type=Path, default=BUILD / "generated", help="parent directory for <language>/ outputs")
    parser.add_argument("--spec", type=Path, default=SPEC)
    parser.add_argument("--verify", action="store_true", help="generate twice and compare with generator/golden.json")
    parser.add_argument("--update-golden", action="store_true", help="rewrite generator/golden.json from a verified double generation")
    args = parser.parse_args()
    languages = args.language or list(LANGUAGES)
    try:
        tools = ensure_installed()
        if args.verify or args.update_golden:
            if args.spec != SPEC or args.output_dir != BUILD / "generated":
                raise PipelineError("--verify and --update-golden always use spec/openapi.yaml and build/generated")
            return verify(languages, tools, args.update_golden)
        for language in languages:
            manifest = generate(language, args.output_dir / language, tools, args.spec)
            print(f"{language}: generated {manifest['file_count']} files into {(args.output_dir / language).as_posix()} (tree sha256 {manifest['tree_sha256']})")
        return 0
    except (PipelineError, OSError) as error:
        return fail(f"generation failed: {error}")


if __name__ == "__main__":
    sys.exit(main())
