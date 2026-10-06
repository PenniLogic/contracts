"""Shared helpers for the pipeline script tests (text-level specification mutations, no YAML dependency)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
SPEC = ROOT / "spec" / "openapi.yaml"
REGISTRY = ROOT / "spec" / "currency-registry.v1.json"

sys.path.insert(0, str(SCRIPTS))
from pl_contracts import PROVIDER_SOURCE_NAMES  # noqa: E402

PROBE_PATHS = """  /probe:
    get:
      operationId: getProbe
      description: Probe read.
      tags: [probe]
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                properties:
                  total:
                    $ref: '#/components/schemas/Money'
                  when:
                    $ref: '#/components/schemas/Instant'
    post:
      operationId: postProbe
      description: Probe write.
      tags: [probe]
      parameters:
        - $ref: '#/components/parameters/IdempotencyKey'
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                total:
                  $ref: '#/components/schemas/Money'
      responses:
        '204':
          description: ok
"""


def spec_text() -> str:
    return SPEC.read_text(encoding="utf-8")


def with_probe_paths(text: str) -> str:
    """Add only the probe path and tag, retaining the actual document and inherited security."""
    paths = section_text(text, ("paths",))
    heading, _, entries = paths.partition("\n")
    assert heading in ("paths:", "paths: {}")
    assert not re.search(r"(?m)^  ['\"]?/probe['\"]?:", entries)
    text = replace_section(text, ("paths",), "paths:\n" + PROBE_PATHS + entries)
    tags = section_text(text, ("tags",))
    heading, _, entries = tags.partition("\n")
    assert heading in ("tags:", "tags: []")
    assert not re.search(r"(?m)^  - name: probe$", entries)
    return replace_section(text, ("tags",), "tags:\n  - name: probe\n"
                           "    description: Probe operations used only by the pipeline tests.\n" + entries)


def section_span(text: str, path: tuple[str, ...]) -> tuple[int, int]:
    """Locate a named mapping in the repository's two-space block YAML without reformatting it."""
    start, end = 0, len(text)
    for depth, name in enumerate(path):
        key = re.escape(name)
        pattern = re.compile(rf"(?m)^{'  ' * depth}(?:{key}|'{key}'|\"{key}\"):[^\n]*\n")
        matches = list(pattern.finditer(text, start, end))
        if len(matches) != 1:
            raise AssertionError(f"expected one YAML section {'/'.join(path[:depth + 1])}, found {len(matches)}")
        match = matches[0]
        start = match.start()
        offset = match.end()
        for line in text[offset:end].splitlines(keepends=True):
            if line.strip() and not line.lstrip().startswith("#") and len(line) - len(line.lstrip()) <= depth * 2:
                end = offset
                break
            offset += len(line)
    return start, end


def section_text(text: str, path: tuple[str, ...]) -> str:
    start, end = section_span(text, path)
    return text[start:end]


def replace_section(text: str, path: tuple[str, ...], replacement: str) -> str:
    start, end = section_span(text, path)
    return text[:start] + replacement + text[end:]


def replace_in_section(text: str, path: tuple[str, ...], old: str, new: str) -> str:
    return replace_section(text, path, replace_once(section_text(text, path), old, new))


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise AssertionError(f"expected exactly one occurrence of {old!r}, found {text.count(old)}")
    return text.replace(old, new)


class SpecDir:
    """A temporary directory holding a mutated specification next to a copy of the registry."""

    def __init__(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="pl-spec-"))
        shutil.copyfile(REGISTRY, self.path / "currency-registry.v1.json")
        for name in PROVIDER_SOURCE_NAMES:
            shutil.copyfile(ROOT / "spec" / name, self.path / name)

    def write(self, text: str, name: str = "openapi.yaml") -> Path:
        target = self.path / name
        target.write_text(text, encoding="utf-8", newline="\n")
        return target

    def cleanup(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def run_script(name: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    merged = dict(os.environ)
    merged["PYTHONIOENCODING"] = "utf-8"
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args], cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=merged, check=False,
    )
