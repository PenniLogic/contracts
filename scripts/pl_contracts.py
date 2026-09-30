"""Shared helpers for the contracts pipeline scripts (standard library only)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "spec" / "openapi.yaml"
RULESET = ROOT / "spec" / ".spectral.yaml"
REGISTRY = ROOT / "spec" / "currency-registry.v1.json"
FIXTURES = ROOT / "spec" / "fixtures"
VERSIONS = ROOT / "toolchain" / "versions.json"
GENERATOR_DIR = ROOT / "generator"
RUNTIME_DIR = ROOT / "runtime"
BUILD = ROOT / "build"
TOOLCHAIN = ROOT / ".toolchain"
NODE_MODULES = ROOT / "node_modules"
LANGUAGES = ("kotlin", "typescript", "python")
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_INFO_VERSION = re.compile(r"^info:\n(?:^ {2}\S.*\n|^ {2,}.*\n)*?^ {2}version:[ \t]*['\"]?([^'\"\s]+)['\"]?[ \t]*$", re.MULTILINE)


class PipelineError(Exception):
    """A failure the pipeline reports with an actionable message and a non-zero exit."""


for _stream in (sys.stdout, sys.stderr):
    # Tool output (Spectral, Gradle) is UTF-8; keep it readable on Windows consoles as well.
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def versions() -> dict:
    return json.loads(VERSIONS.read_text(encoding="utf-8"))


def spec_version(text: str | None = None) -> str:
    """Return info.version from the OpenAPI document without a YAML dependency.

    The document is owned by this repository and keeps `info:` as a two-space block mapping with a
    plain scalar `version`; the lint rule pl-info-version-semver guards the value itself.
    """
    text = SPEC.read_text(encoding="utf-8") if text is None else text
    match = _INFO_VERSION.search(text)
    if not match or not SEMVER.match(match.group(1)):
        raise PipelineError("spec/openapi.yaml: info.version must be a plain MAJOR.MINOR.PATCH scalar under the info block")
    return match.group(1)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tree_hash(root: Path, exclude: tuple[str, ...] = ()) -> tuple[str, list[tuple[str, str]]]:
    """Hash a directory: sorted POSIX-relative paths and per-file SHA-256, combined into one digest.

    File modes and timestamps are deliberately excluded so the hash is a function of content only.
    """
    entries: list[tuple[str, str]] = []
    # Sort by the POSIX relative path string: Path ordering is case-insensitive on Windows and would
    # change the combined digest between platforms.
    for path in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if relative in exclude:
            continue
        entries.append((relative, sha256_file(path)))
    combined = hashlib.sha256()
    for relative, digest in entries:
        combined.update(f"{digest}  {relative}\n".encode("utf-8"))
    return combined.hexdigest(), entries


def node_executable() -> str:
    node = shutil.which("node")
    if not node:
        raise PipelineError("node was not found on PATH; install Node 24.14.0 (see .nvmrc)")
    return node


def java_executable() -> str:
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidate = Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.is_file():
            return str(candidate)
    java = shutil.which("java")
    if not java:
        raise PipelineError("java was not found on PATH or JAVA_HOME; install a JDK 21")
    return java


def run(command: list[str], *, cwd: Path | None = None, env: dict | None = None, capture: bool = False,
        check: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess with the repository as default working directory."""
    merged = dict(os.environ)
    if env:
        merged.update(env)
    completed = subprocess.run(
        command, cwd=str(cwd or ROOT), env=merged, text=True, encoding="utf-8", errors="replace",
        capture_output=capture, check=False,
    )
    if check and completed.returncode != 0:
        detail = ""
        if capture:
            detail = "\n" + (completed.stdout or "") + (completed.stderr or "")
        raise PipelineError(f"command failed with exit {completed.returncode}: {' '.join(command)}{detail}")
    return completed


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, onexc=_on_remove_error)


def _on_remove_error(function, path, _exc_info):
    os.chmod(path, 0o700)
    function(path)
