"""Shared helpers for the pipeline script tests (text-level specification mutations, no YAML dependency)."""

from __future__ import annotations

import os
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

PROBE_PATHS = """security:
  - probe: []
components:
  securitySchemes:
    probe:
      type: http
      scheme: bearer
tags:
  - name: probe
    description: Probe operations used only by the pipeline tests.
paths:
  /probe:
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
    """Add a synthetic path item (one GET, one POST) so operation rules have something to check.

    The security scheme and tags are added under a second `components:`/`tags:` block; YAML forbids
    duplicate keys, so the probe block replaces the empty `paths: {}` line and moves `components:`
    entries by merging the probe security scheme into the existing components mapping.
    """
    assert "paths: {}\n" in text
    probe = PROBE_PATHS.replace("components:\n  securitySchemes:\n    probe:\n      type: http\n      scheme: bearer\n", "")
    text = text.replace("paths: {}\n", probe)
    return text.replace("components:\n  schemas:\n", "components:\n  securitySchemes:\n    probe:\n      type: http\n      scheme: bearer\n  schemas:\n", 1)


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise AssertionError(f"expected exactly one occurrence of {old!r}, found {text.count(old)}")
    return text.replace(old, new)


class SpecDir:
    """A temporary directory holding a mutated specification next to a copy of the registry."""

    def __init__(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="pl-spec-"))
        shutil.copyfile(REGISTRY, self.path / "currency-registry.v1.json")

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
