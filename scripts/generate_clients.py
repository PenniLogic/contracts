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
    BUILD, GENERATOR_DIR, LANGUAGES, PROVIDER_SOURCE_NAMES, REGISTRY, ROOT, RUNTIME_DIR, SPEC, PipelineError, combined_digest, fail, java_executable,
    remove_tree, run, sha256_bytes, sha256_file, spec_version, tree_hash, versions, node_executable,
)
from toolchain import ensure_installed  # noqa: E402

MANIFEST_NAME = "contracts-manifest.json"
GOLDEN = GENERATOR_DIR / "golden.json"
IGNORE_OVERRIDE = GENERATOR_DIR / "openapi-generator-ignore"
TEMPLATE_DIRS = {language: GENERATOR_DIR / "templates" / language for language in LANGUAGES}
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


def render_error_catalogue(language: str, catalogue: dict) -> tuple[str, str]:
    """Publish a typed policy table from the canonical catalogue, never hand-maintained client copies."""
    header = "Generated from spec/error-catalogue.v1.json by scripts/generate_clients.py; do not edit."
    entries = catalogue["codes"]
    if language == "python":
        rows = "".join(
            f'    ProblemCode.{entry["code"].upper()}: ErrorPolicy('
            f'{entry["status"]}, {json.dumps(entry["title"])}, {json.dumps(entry["detail"])}, '
            f'ClientState.{entry["state"].upper()}, {entry["retryable"]}, '
            f'RetryClass.{entry["retry_class"].upper()}, IdempotencyTreatment.{entry["idempotency"].upper()}),\n'
            for entry in entries
        )
        return "pennilogic_contracts/error_catalogue.py", (
            f'"""{header}"""\n\nfrom __future__ import annotations\n\n'
            "from dataclasses import dataclass\nfrom types import MappingProxyType\n"
            "from typing import Final, Mapping\nfrom uuid import uuid4\n"
            "from pennilogic_contracts.models.problem_code import ProblemCode\n"
            "from pennilogic_contracts.models.problem_field import ProblemField\n"
            "from pennilogic_contracts.models.client_state import ClientState\n"
            "from pennilogic_contracts.models.retry_class import RetryClass\n"
            "from pennilogic_contracts.models.idempotency_treatment import IdempotencyTreatment\n\n\n"
            "@dataclass(frozen=True)\nclass ErrorPolicy:\n"
            "    status: int\n    title: str\n    detail: str\n    state: ClientState\n"
            "    retryable: bool\n    retry_class: RetryClass\n    idempotency: IdempotencyTreatment\n\n\n"
            f"ERROR_POLICIES: Final[Mapping[ProblemCode, ErrorPolicy]] = MappingProxyType({{\n{rows}}})\n\n\n"
            "def error_policy(code: ProblemCode) -> ErrorPolicy:\n"
            "    if not isinstance(code, ProblemCode):\n        raise TypeError('problem code rejected')\n"
            "    return ERROR_POLICIES[code]\n\n\n"
            "def error_status(code: ProblemCode, field: ProblemField | None = None) -> int:\n"
            "    if code == ProblemCode.VALIDATION_REJECTED and field == ProblemField.DUPLICATE_OVERRIDE:\n        return 400\n"
            "    return error_policy(code).status\n\n\n"
            "def new_correlation_id() -> str:\n    return 'cor_' + str(uuid4())\n"
        )
    if language == "kotlin":
        rows = "".join(
            f'        ProblemCode.{entry["code"].upper()} to ErrorPolicy('
            f'{entry["status"]}, {json.dumps(entry["title"])}, {json.dumps(entry["detail"])}, '
            f'ClientState.{entry["state"].upper()}, {str(entry["retryable"]).lower()}, '
            f'RetryClass.{entry["retry_class"].upper()}, IdempotencyTreatment.{entry["idempotency"].upper()}),\n'
            for entry in entries
        )
        return "src/main/kotlin/com/pennilogic/contracts/errors/ErrorCatalogue.kt", (
            f"// {header}\npackage com.pennilogic.contracts.errors\n\n"
            "import com.pennilogic.contracts.models.ProblemCode\nimport com.pennilogic.contracts.models.ClientState\n"
            "import com.pennilogic.contracts.models.ProblemField\n"
            "import com.pennilogic.contracts.models.RetryClass\nimport com.pennilogic.contracts.models.IdempotencyTreatment\n"
            "import java.util.UUID\n\n"
            "data class ErrorPolicy(val status: Int, val title: String, val detail: String, "
            "val state: ClientState, val retryable: Boolean, val retryClass: RetryClass, "
            "val idempotency: IdempotencyTreatment)\n\n"
            f"object ErrorCatalogue {{\n    private val policies = mapOf(\n{rows}    )\n\n"
            "    fun policy(code: ProblemCode): ErrorPolicy = policies.getValue(code)\n"
            "    fun status(code: ProblemCode, field: ProblemField? = null): Int =\n"
            "        if (code == ProblemCode.VALIDATION_REJECTED && field == ProblemField.DUPLICATE_OVERRIDE) 400 else policy(code).status\n"
            '    fun newCorrelationId(): String = "cor_${UUID.randomUUID()}"\n}\n'
        )
    if language == "typescript":
        def symbol(value: str) -> str:
            return "".join(word.capitalize() for word in value.split("_"))

        rows = "".join(
            f'    [ProblemCode.{symbol(entry["code"])}]: Object.freeze({{ status: {entry["status"]}, '
            f'title: {json.dumps(entry["title"])}, detail: {json.dumps(entry["detail"])}, '
            f'state: ClientState.{symbol(entry["state"])}, retryable: {str(entry["retryable"]).lower()}, '
            f'retryClass: RetryClass.{symbol(entry["retry_class"])}, '
            f'idempotency: IdempotencyTreatment.{symbol(entry["idempotency"])} }}),\n'
            for entry in entries
        )
        return "src/errorCatalogue.ts", (
            f"// {header}\nimport {{ ProblemCode }} from './models/ProblemCode.js';\n"
            "import { ProblemField } from './models/ProblemField.js';\n"
            "import { ClientState } from './models/ClientState.js';\n"
            "import { RetryClass } from './models/RetryClass.js';\n"
            "import { IdempotencyTreatment } from './models/IdempotencyTreatment.js';\n\n"
            "export interface ErrorPolicy {\n    readonly status: number;\n    readonly title: string;\n"
            "    readonly detail: string;\n    readonly state: ClientState;\n    readonly retryable: boolean;\n"
            "    readonly retryClass: RetryClass;\n    readonly idempotency: IdempotencyTreatment;\n}\n\n"
            f"export const ERROR_POLICIES: Readonly<Record<ProblemCode, ErrorPolicy>> = Object.freeze({{\n{rows}}});\n\n"
            "export function errorPolicy(code: ProblemCode): ErrorPolicy {\n"
            "    if (!Object.prototype.hasOwnProperty.call(ERROR_POLICIES, code)) throw new TypeError('problem code rejected');\n"
            "    return ERROR_POLICIES[code];\n}\n\n"
            "export function errorStatus(code: ProblemCode, field?: ProblemField): number {\n"
            "    return code === ProblemCode.ValidationRejected && field === ProblemField.DuplicateOverride ? 400 : errorPolicy(code).status;\n}\n\n"
            "export function newCorrelationId(): string {\n    return 'cor_' + globalThis.crypto.randomUUID();\n}\n"
        )
    raise PipelineError(f"unknown language {language}")


def render_import_policy(language: str, policy: dict) -> tuple[str, str]:
    header = "Generated from spec/import-group.v1.json by scripts/generate_clients.py; do not edit."
    sources = policy["source_precedence"]
    windows = policy["source_pair_windows"]
    if language == "python":
        ordered = ", ".join(f"DedupSource.from_wire({json.dumps(source)})" for source in sources)
        rows = "".join(
            f'    (DedupSource.from_wire({json.dumps(row["sources"][0])}), DedupSource.from_wire({json.dumps(row["sources"][1])})): '
            f'DedupWindow.from_wire({json.dumps(row["window"])}),\n' for row in windows
        )
        return "pennilogic_contracts/import_policy.py", (
            f'"""{header}"""\n\nfrom __future__ import annotations\n'
            "from types import MappingProxyType\nfrom typing import Final, Mapping\n"
            "from pennilogic_contracts.models.dedup_source import DedupSource\n"
            "from pennilogic_contracts.models.dedup_window import DedupWindow\n\n"
            f'IMPORT_GROUP_VERSION: Final = {json.dumps(policy["group_version"])}\n'
            f"MAX_IMPORT_ROWS: Final = {policy['preview']['maximum_rows']}\n"
            f"MAX_IMPORT_COLUMNS: Final = {policy['preview']['maximum_columns']}\n"
            f"SOURCE_PRECEDENCE: Final = ({ordered})\n"
            f"_WINDOWS: Final[Mapping[tuple[DedupSource, DedupSource], DedupWindow]] = MappingProxyType({{\n{rows}}})\n\n"
            "def source_window(left: DedupSource, right: DedupSource) -> DedupWindow:\n"
            "    if not isinstance(left, DedupSource) or not isinstance(right, DedupSource):\n        raise TypeError('source pair rejected')\n"
            "    result = _WINDOWS.get((left, right), _WINDOWS.get((right, left)))\n"
            "    if result is None:\n        raise ValueError('source pair unbound')\n    return result\n"
        )
    if language == "typescript":
        ordered = ", ".join(f"DedupSourceFromJSON({json.dumps(source)})" for source in sources)
        rows = "".join(
            f'    [DedupSourceFromJSON({json.dumps(row["sources"][0])}), DedupSourceFromJSON({json.dumps(row["sources"][1])}), '
            f'DedupWindowFromJSON({json.dumps(row["window"])})],\n' for row in windows
        )
        return "src/importPolicy.ts", (
            f"// {header}\nimport {{ DedupSource, DedupSourceFromJSON }} from './models/DedupSource.js';\n"
            "import { DedupWindow, DedupWindowFromJSON } from './models/DedupWindow.js';\n\n"
            f'export const IMPORT_GROUP_VERSION = {json.dumps(policy["group_version"])};\n'
            f"export const MAX_IMPORT_ROWS = {policy['preview']['maximum_rows']};\n"
            f"export const MAX_IMPORT_COLUMNS = {policy['preview']['maximum_columns']};\n"
            f"export const SOURCE_PRECEDENCE: readonly DedupSource[] = Object.freeze([{ordered}]);\n"
            f"const WINDOWS: ReadonlyArray<readonly [DedupSource, DedupSource, DedupWindow]> = [\n{rows}];\n\n"
            "export function sourceWindow(left: DedupSource, right: DedupSource): DedupWindow {\n"
            "    const result = WINDOWS.find(([a, b]) => (a === left && b === right) || (a === right && b === left));\n"
            "    if (result === undefined) throw new TypeError('source pair unbound');\n    return result[2];\n}\n"
        )
    if language == "kotlin":
        ordered = ", ".join(f'DedupSource.entries.single {{ it.value == {json.dumps(source)} }}' for source in sources)
        rows = "".join(
            f'        (DedupSource.entries.single {{ it.value == {json.dumps(row["sources"][0])} }} to '
            f'DedupSource.entries.single {{ it.value == {json.dumps(row["sources"][1])} }}) to '
            f'DedupWindow.entries.single {{ it.value == {json.dumps(row["window"])} }},\n' for row in windows
        )
        return "src/main/kotlin/com/pennilogic/contracts/imports/ImportPolicy.kt", (
            f"// {header}\npackage com.pennilogic.contracts.imports\n\n"
            "import com.pennilogic.contracts.models.DedupSource\nimport com.pennilogic.contracts.models.DedupWindow\n\n"
            "object ImportPolicy {\n"
            f'    const val VERSION = {json.dumps(policy["group_version"])}\n'
            f"    const val MAX_ROWS = {policy['preview']['maximum_rows']}\n"
            f"    const val MAX_COLUMNS = {policy['preview']['maximum_columns']}\n"
            f"    val sourcePrecedence: List<DedupSource> = listOf({ordered})\n"
            f"    private val windows = mapOf(\n{rows}    )\n\n"
            "    fun sourceWindow(left: DedupSource, right: DedupSource): DedupWindow =\n"
            '        windows[left to right] ?: windows[right to left] ?: error("source pair unbound")\n}\n'
        )
    raise PipelineError(f"unknown language {language}")


def generate(language: str, output: Path, tools: dict, spec: Path = SPEC) -> dict:
    """Generate one target into `output` and return its manifest."""
    if language not in LANGUAGES:
        raise PipelineError(f"unknown language {language}; choose from {', '.join(LANGUAGES)}")
    pins = versions()
    version = spec_version(spec.read_text(encoding="utf-8"))
    config = config_path(language)
    generator_name = json.loads(config.read_text(encoding="utf-8"))["generatorName"]
    constraints = run([node_executable(), str(ROOT / "scripts" / "provider_constraints.cjs"), str(spec)],
                      capture=True, check=False)
    if constraints.returncode:
        raise PipelineError(constraints.stderr.strip() or "provider constraint generation failed")
    declarations = json.loads(constraints.stdout)
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
    if language == "python":
        target = output / "pennilogic_contracts" / "provider_constraint_data.py"
        content = (
            '"""Generated declared provider constraints; do not edit."""\n'
            "import json\nfrom typing import Any, Final\n\n"
            f"SCHEMAS: Final[dict[str, dict[str, Any]]] = json.loads({json.dumps(json.dumps(declarations['schemas'], sort_keys=True))})\n"
        )
        target.write_bytes(content.encode("utf-8"))
    elif language == "kotlin":
        rows = []
        for name, declaration in sorted(declarations["schemas"].items()):
            literal = json.dumps(json.dumps(declaration, sort_keys=True)).replace("$", r"\$")
            rows.append(f'        {json.dumps(name)} to Json.parseToJsonElement({literal}).jsonObject,\n')
        target = output / "src" / "main" / "kotlin" / "com" / "pennilogic" / "contracts" / "serialization" / "ProviderConstraintData.kt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((
            "// Generated declared provider constraints; do not edit.\n"
            "package com.pennilogic.contracts.serialization\n\nimport kotlinx.serialization.json.*\n\n"
            "internal object ProviderConstraintData {\n    val schemas: Map<String, JsonObject> = mapOf(\n" +
            "".join(rows) + "    )\n}\n"
        ).encode("utf-8"))
    elif language == "typescript":
        target = output / "src" / "providerConstraintData.ts"
        target.write_bytes((
            "// Generated declared provider constraints; do not edit.\n"
            "import type { ProviderSchema } from './providerConstraints.js';\n\n"
            "export const PROVIDER_SCHEMAS: Readonly<Record<string, ProviderSchema>> = " +
            json.dumps(declarations["schemas"], sort_keys=True) + ";\n"
        ).encode("utf-8"))
    relative, content = render_registry(language)
    registry_target = output / relative
    registry_target.parent.mkdir(parents=True, exist_ok=True)
    registry_target.write_bytes(content.encode("utf-8"))
    provider_hashes = {}
    for name in PROVIDER_SOURCE_NAMES:
        source = spec.parent / name
        if not source.is_file():
            raise PipelineError(f"{source.name} is missing beside the specification")
        data = normalized_text(source)
        (output / name).write_bytes(data)
        provider_hashes[name] = sha256_bytes(data)
    relative, content = render_error_catalogue(language, json.loads((spec.parent / "error-catalogue.v1.json").read_text(encoding="utf-8")))
    catalogue_target = output / relative
    catalogue_target.parent.mkdir(parents=True, exist_ok=True)
    catalogue_target.write_bytes(content.encode("utf-8"))
    relative, content = render_import_policy(language, json.loads((spec.parent / "import-group.v1.json").read_text(encoding="utf-8")))
    policy_target = output / relative
    policy_target.parent.mkdir(parents=True, exist_ok=True)
    policy_target.write_bytes(content.encode("utf-8"))
    digest, entries = tree_hash(output, exclude=(MANIFEST_NAME,))
    manifest = {
        "schema_version": 1,
        "target": language,
        "spec_version": version,
        "spec_sha256": spec_digest(spec),
        "currency_registry_sha256": sha256_bytes(normalized_text(REGISTRY)),
        "provider_sources_sha256": provider_hashes,
        "provider_constraints_sha256": sha256_bytes(json.dumps(declarations, sort_keys=True).encode("utf-8")),
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
