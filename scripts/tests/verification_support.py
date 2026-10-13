"""Synthetic published contract releases for the contract-verification tests (no committed archives, no network).

A SyntheticContracts instance owns one temporary directory, never a path inside this repository:

- repo/: a contracts-like repository with the specification, registry, provider sources, fixtures,
  toolchain pins, generator/golden.json, synthetic generator configurations, ignore override,
  templates and runtime files, a synthetic scripts/pl_contracts.py that declares the release format
  (PROVIDER_SOURCE_NAMES, LANGUAGES) and this repository's own scripts/release.py, at the same relative
  paths as this repository, committed and tagged with hermetic git;
- origin.git/: a temporary bare origin that main and the annotated tag are pushed to;
- generated/<language>/: synthetic generated client trees. Each carries a contracts-manifest.json
  with the keys generate_clients.py writes, computed with the real pl_contracts.tree_hash;
- dist/: release sets built by the real release.build_release_set, with its module-level paths
  (DIST, GENERATED, SPEC, REGISTRY, FIXTURES and pl_contracts.VERSIONS) patched to the temporary tree
  and its PROVIDER_SOURCE_NAMES to the synthetic tag's;
- downloads/: the published Release assets. release.publish uploads every file of the release set
  except RELEASE_NOTES.md.

Hermetic git: every command runs with `-c user.name`, `-c user.email`, `commit.gpgSign` and
`tag.gpgSign` off, no global ignore or attributes file, GIT_CONFIG_GLOBAL set to the null device,
GIT_CONFIG_NOSYSTEM=1, fixed author/committer dates and no inherited GIT_* variable. Commits and tag
objects are therefore deterministic. All content is synthetic.

The compatibility, gate, report and command-line tests also use in-memory inputs from here: parsed
OpenAPI 3.1.0 documents (synthetic_document), oasdiff-shaped detector output, and report parts
(fixture_report_parts, synthetic_report_parts). Fixture rows carry fixture names, never real repositories.
"""

from __future__ import annotations

import ast
import copy
import gzip
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Callable
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import generate_clients  # noqa: E402
import pl_contracts  # noqa: E402
import release  # noqa: E402
from contract_gate import parse_instant  # noqa: E402
from pl_contracts import combined_digest, sha256_bytes, tree_hash  # noqa: E402

SYNTHETIC_EPOCH = 1767323045  # 2026-01-02T03:04:05Z: fixed commit, tag and archive time
SYNTHETIC_DATE = f"{SYNTHETIC_EPOCH} +0000"
GIT_OPTIONS = (
    "-c", "user.name=PenniLogic synthetic", "-c", "user.email=synthetic@example.invalid",
    "-c", "commit.gpgSign=false", "-c", "tag.gpgSign=false", "-c", "init.defaultBranch=main",
    "-c", "core.autocrlf=false", "-c", f"core.excludesFile={os.devnull}", "-c", f"core.attributesFile={os.devnull}",
)
MANIFEST_NAME = generate_clients.MANIFEST_NAME
SPEC_RELATIVE = pl_contracts.SPEC.relative_to(pl_contracts.ROOT).as_posix()
REGISTRY_RELATIVE = pl_contracts.REGISTRY.relative_to(pl_contracts.ROOT).as_posix()
FIXTURES_RELATIVE = pl_contracts.FIXTURES.relative_to(pl_contracts.ROOT).as_posix()
VERSIONS_RELATIVE = pl_contracts.VERSIONS.relative_to(pl_contracts.ROOT).as_posix()
GOLDEN_RELATIVE = generate_clients.GOLDEN.relative_to(pl_contracts.ROOT).as_posix()
FORMAT_RELATIVE = Path(pl_contracts.__file__).resolve().relative_to(pl_contracts.ROOT).as_posix()
RELEASE_RELATIVE = Path(release.__file__).resolve().relative_to(pl_contracts.ROOT).as_posix()
IGNORE_RELATIVE = generate_clients.IGNORE_OVERRIDE.relative_to(pl_contracts.ROOT).as_posix()
SPEC_DIRECTORY = pl_contracts.SPEC.parent.relative_to(pl_contracts.ROOT).as_posix()
FIXTURE_NAMES = ("synthetic-money-wire.v1.json", "synthetic-probe.v1.json")
SPEC_TEMPLATE = (
    "openapi: 3.1.0\n"
    "info:\n"
    "  title: PenniLogic synthetic contract (verification tests only)\n"
    "  version: {version}\n"
    "  description: Synthetic document; not a product contract.\n"
    "paths: {{}}\n"
)
GENERATED_FILES = {
    "kotlin": {
        "build.gradle.kts": "// Synthetic generated Kotlin client (verification tests only).\n",
        "gradlew": "#!/bin/sh\n# Synthetic wrapper (verification tests only).\nexit 0\n",
        "src/main/kotlin/com/pennilogic/synthetic/SyntheticProbe.kt":
            "package com.pennilogic.synthetic\n\ndata class SyntheticProbe(val total: String)\n",
        # Longer than the 100-character ustar name field inside the archive prefix, so the builder writes an
        # extended header `path` record for it, as it does for the real Kotlin client's longest paths.
        "src/main/kotlin/com/pennilogic/synthetic/models/SyntheticProbeResponseWithALongGeneratedName.kt":
            "package com.pennilogic.synthetic.models\n\ndata class SyntheticProbeResponseWithALongGeneratedName(val total: String)\n",
    },
    "typescript": {
        "package.json": '{\n  "name": "@pennilogic/synthetic-client",\n  "private": true\n}\n',
        "src/index.ts": "export * from './models/SyntheticProbe';\n",
        "src/models/SyntheticProbe.ts": "export interface SyntheticProbe {\n  total: string;\n}\n",
    },
    "python": {
        "pyproject.toml": '[project]\nname = "pennilogic-synthetic-client"\nversion = "0.0.0"\n',
        "pennilogic_synthetic/__init__.py": '"""Synthetic generated Python client (verification tests only)."""\n',
        "pennilogic_synthetic/models/synthetic_probe.py": "class SyntheticProbe:\n    total: str\n",
    },
}
GENERATOR_NAMES = {"kotlin": "kotlin", "typescript": "typescript-fetch", "python": "python"}


def release_format_source(provider_source_names: tuple[str, ...], languages: tuple[str, ...] | None = None) -> str:
    """A synthetic scripts/pl_contracts.py that declares a release format the way the real one does."""
    languages = pl_contracts.LANGUAGES if languages is None else languages
    return ('"""Synthetic release-format constants (verification tests only)."""\n\n'
            f"PROVIDER_SOURCE_NAMES = {tuple(provider_source_names)!r}\nLANGUAGES = {tuple(languages)!r}\n")


def generator_relative(language: str, kind: str) -> str:
    """Where generate_clients.py reads a language's generator configuration, template overrides or runtime seams."""
    path = {"config": generate_clients.config_path(language), "templates": generate_clients.TEMPLATE_DIRS[language],
            "runtime": pl_contracts.RUNTIME_DIR / language}[kind]
    return path.relative_to(pl_contracts.ROOT).as_posix()


def synthetic_digest(label: str) -> str:
    return sha256_bytes(f"synthetic:{label}".encode("utf-8"))


def json_text(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=True) + "\n"


def hermetic_environment() -> dict[str, str]:
    """The process environment without inherited GIT_* variables, plus the hermetic git settings."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update({
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_DATE": SYNTHETIC_DATE, "GIT_COMMITTER_DATE": SYNTHETIC_DATE,
    })
    return environment


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *GIT_OPTIONS, *args], cwd=str(cwd), env=hermetic_environment(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=60, check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed with exit {completed.returncode}: {completed.stderr.strip()}")
    return completed.stdout.strip()


def temporary_root_problem(path: Path) -> str | None:
    """Why the tests may not write under `path`, or None.

    A temporary root is allowed outside this checkout, and inside its gitignored build/ directory:
    scripts/run_source_tests.py (the CI test runner on main) gives every lane its own TMPDIR under
    build/source-tests/run-*/state/<lane>/temp. Anywhere else inside the checkout is refused, so a test
    never writes into tracked or reviewable paths.
    """
    root = ROOT.resolve()
    if path != root and root not in path.parents:
        return None
    if (root / "build") in path.parents:
        return None
    return "the temporary directory is inside the repository but outside its gitignored build/; set TMPDIR elsewhere"


def temporary_root(prefix: str = "pl-verify-") -> Path:
    """A new temporary directory in the process temporary directory (see temporary_root_problem); the caller removes it."""
    path = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    problem = temporary_root_problem(path)
    if problem is not None:
        shutil.rmtree(path, ignore_errors=True)
        raise AssertionError(problem)
    return path


class SyntheticContracts:
    """One synthetic contracts repository with its bare origin, generated trees and release builds."""

    def __init__(self, root: Path, version: str = "1.2.3", provider_source_names: tuple[str, ...] | None = None) -> None:
        self.root = root
        self.version = version
        # The tag's own provider-source names; the runner's by default.
        self.provider_source_names = pl_contracts.PROVIDER_SOURCE_NAMES if provider_source_names is None else provider_source_names
        self.tag = f"v{version}"
        self.repo = root / "repo"
        self.origin = root / "origin.git"
        self.generated = root / "generated"
        self.dist = root / "dist"
        self.downloads = root / "downloads"
        self.commit = ""
        self.tag_object = ""

    # --- the tag's tree -------------------------------------------------------------------------

    def init_repository(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        git(self.root, "init", "--quiet", "--bare", str(self.origin))
        git(self.root, "init", "--quiet", str(self.repo))
        git(self.repo, "remote", "add", "origin", str(self.origin))

    def write(self, relative: str, content: str | bytes) -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
        return path

    def write_source(self) -> None:
        self.write(SPEC_RELATIVE, SPEC_TEMPLATE.format(version=self.version))
        self.write(REGISTRY_RELATIVE, json_text({"schema_version": 1, "synthetic": "currency registry placeholder"}))
        for name in self.provider_source_names:
            self.write(f"{SPEC_DIRECTORY}/{name}", json_text({"schema_version": 1, "synthetic": f"provider source {name}"}))
        self.write(FORMAT_RELATIVE, release_format_source(self.provider_source_names))
        self.write(RELEASE_RELATIVE, Path(release.__file__).read_bytes())
        self.write(IGNORE_RELATIVE, "# Synthetic ignore override (verification tests only).\n")
        for language in pl_contracts.LANGUAGES:
            self.write(generator_relative(language, "config"), json_text({
                "generatorName": GENERATOR_NAMES[language], "additionalProperties": {"synthetic": True}}))
            self.write(f"{generator_relative(language, 'templates')}/synthetic.mustache", "{{! Synthetic template override }}\n")
            self.write(f"{generator_relative(language, 'runtime')}/synthetic_seam.txt", f"Synthetic {language} runtime seam.\n")
        for name in FIXTURE_NAMES:
            self.write(f"{FIXTURES_RELATIVE}/{name}", json_text({"schema_version": 1, "synthetic": f"fixture {name}"}))
        self.write(VERSIONS_RELATIVE, json_text({
            "openapi_generator": {"version": "0.0.0-synthetic", "sha256": synthetic_digest("generator jar")},
            "spectral": {"version": "0.0.0-synthetic"},
            "oasdiff": {"version": "0.0.0-synthetic"},
        }))
        self.write("README.md", "# Synthetic contracts repository (verification tests only)\n")

    def write_generated(self) -> None:
        """Synthetic generated trees from the current source files, as generate_clients.py lays them out."""
        for language, files in GENERATED_FILES.items():
            output = self.generated / language
            if output.exists():
                shutil.rmtree(output)
            for relative, content in files.items():
                path = output / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content.encode("utf-8"))
            self.refresh_embedded_manifest(language)

    def refresh_embedded_manifest(self, language: str) -> dict:
        """Write contracts-manifest.json for the current generated tree, with generate_clients.py's keys."""
        output = self.generated / language
        spec = (self.repo / SPEC_RELATIVE).read_bytes().replace(b"\r\n", b"\n")
        pins = json.loads((self.repo / VERSIONS_RELATIVE).read_text(encoding="utf-8"))
        config = (self.repo / generator_relative(language, "config")).read_bytes()
        digest, entries = tree_hash(output, exclude=(MANIFEST_NAME,))
        manifest = {
            "schema_version": 1,
            "target": language,
            "spec_version": pl_contracts.spec_version(spec.decode("utf-8")),
            "spec_sha256": sha256_bytes(spec),
            "currency_registry_sha256": sha256_bytes((self.repo / REGISTRY_RELATIVE).read_bytes()),
            "provider_sources_sha256": {
                name: sha256_bytes((self.repo / SPEC_DIRECTORY / name).read_bytes()) for name in self.provider_source_names
            },
            "provider_constraints_sha256": synthetic_digest("provider constraints"),
            "generator": {
                "name": "openapi-generator-cli",
                "version": pins["openapi_generator"]["version"],
                "jar_sha256": pins["openapi_generator"]["sha256"],
                "generator_name": json.loads(config)["generatorName"],
                "config": generator_relative(language, "config"),
                "config_sha256": sha256_bytes(config.replace(b"\r\n", b"\n")),
                "ignore_override_sha256": sha256_bytes((self.repo / IGNORE_RELATIVE).read_bytes().replace(b"\r\n", b"\n")),
                "template_override_sha256": tree_hash(self.repo / generator_relative(language, "templates"))[0],
            },
            "runtime_sha256": tree_hash(self.repo / generator_relative(language, "runtime"))[0],
            "file_count": len(entries),
            "tree_sha256": digest,
            "files": [{"path": path, "sha256": file_digest} for path, file_digest in entries],
        }
        (output / MANIFEST_NAME).write_text(json_text(manifest), encoding="utf-8", newline="\n")
        return manifest

    def write_golden(self, edit: Callable[[dict], None] | None = None) -> None:
        """generator/golden.json exactly as `generate_clients.py --update-golden` records the generated trees."""
        golden = {}
        for language in sorted(pl_contracts.LANGUAGES):
            manifest = json.loads((self.generated / language / MANIFEST_NAME).read_text(encoding="utf-8"))
            golden[language] = {
                "tree_sha256": manifest["tree_sha256"], "spec_version": manifest["spec_version"],
                "spec_sha256": manifest["spec_sha256"], "file_count": manifest["file_count"],
                "files": {item["path"]: item["sha256"] for item in manifest["files"]},
            }
        if edit is not None:
            edit(golden)
        self.write(GOLDEN_RELATIVE, json_text(golden))

    def commit_all(self, message: str = "Synthetic contract source", uncommitted: tuple[str, ...] = ()) -> str:
        """Commit the whole tree except `uncommitted`, which stays in the working tree only."""
        git(self.repo, "add", "--all")
        for relative in uncommitted:
            git(self.repo, "rm", "--cached", "--quiet", "--", relative)
        git(self.repo, "commit", "--quiet", "-m", message)
        self.commit = git(self.repo, "rev-parse", "HEAD")
        return self.commit

    def tag_annotated(self, name: str | None = None, target: str | None = None, message: str | None = None,
                      force: bool = False) -> str:
        name = name or self.tag
        git(self.repo, "tag", *(["--force"] if force else []), "-a", name, "-m", message or f"PenniLogic contracts {name}",
            target or self.commit)
        object_id = git(self.repo, "rev-parse", f"refs/tags/{name}")
        if name == self.tag:
            self.tag_object = object_id
        return object_id

    def push_tag(self, name: str | None = None, force: bool = False) -> None:
        reference = f"refs/tags/{name or self.tag}"
        git(self.repo, "push", "--quiet", *(["--force"] if force else []), "origin", f"{reference}:{reference}")

    def push_main(self) -> None:
        """Publish the current commit as the origin's main, as the merge of the reviewed pull request would."""
        git(self.repo, "push", "--quiet", "origin", f"{self.commit}:refs/heads/main")

    # --- release builds -------------------------------------------------------------------------

    def build(self, head: str | None = None) -> Path:
        """Run the real release.build_release_set against this tree; return the release set directory."""
        with mock.patch.object(release, "DIST", self.dist), \
                mock.patch.object(release, "GENERATED", self.generated), \
                mock.patch.object(release, "SPEC", self.repo / SPEC_RELATIVE), \
                mock.patch.object(release, "REGISTRY", self.repo / REGISTRY_RELATIVE), \
                mock.patch.object(release, "FIXTURES", self.repo / FIXTURES_RELATIVE), \
                mock.patch.object(release, "PROVIDER_SOURCE_NAMES", self.provider_source_names), \
                mock.patch.object(pl_contracts, "VERSIONS", self.repo / VERSIONS_RELATIVE):
            return release.build_release_set(self.version, head or self.commit, SYNTHETIC_EPOCH)

    def download(self, release_set: Path, name: str = "assets") -> Path:
        """The Release assets as published: release.publish uploads every file except RELEASE_NOTES.md."""
        target = self.downloads / name
        target.mkdir(parents=True)
        for path in sorted(p for p in release_set.iterdir() if p.is_file() and p.name != "RELEASE_NOTES.md"):
            shutil.copyfile(path, target / path.name)
        return target

    def publish(self, *, after_source: Callable[["SyntheticContracts"], None] | None = None,
                golden_edit: Callable[[dict], None] | None = None, uncommitted: tuple[str, ...] = (),
                before_build: Callable[["SyntheticContracts"], None] | None = None) -> Path:
        """The standard sequence: source, generated trees, golden, commit pushed as main, annotated tag pushed,
        build, download.

        Each hook applies one mutation at its step; with no hook the result is the valid published release.
        """
        self.init_repository()
        self.write_source()
        if after_source is not None:
            after_source(self)
        self.write_generated()
        self.write_golden(golden_edit)
        self.commit_all(uncommitted=uncommitted)
        self.push_main()
        self.tag_annotated()
        self.push_tag()
        if before_build is not None:
            before_build(self)
        return self.download(self.build())

    def copy_to(self, root: Path) -> "SyntheticContracts":
        """An independent copy of the repository and its origin, for tests that change git state."""
        other = SyntheticContracts(root, self.version, self.provider_source_names)
        shutil.copytree(self.repo, other.repo, symlinks=True)
        shutil.copytree(self.origin, other.origin, symlinks=True)
        shutil.copytree(self.generated, other.generated, symlinks=True)
        git(other.repo, "remote", "set-url", "origin", str(other.origin))
        other.commit, other.tag_object = self.commit, self.tag_object
        return other


# --- release-set mutations ---------------------------------------------------------------------------

def copy_release(release_dir: Path, root: Path, name: str = "release") -> Path:
    target = root / name
    shutil.copytree(release_dir, target)
    return target


def read_sums(release_dir: Path) -> dict[str, str]:
    lines = (release_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    return {name: digest for digest, name in (line.split("  ", 1) for line in lines)}


def write_sums(release_dir: Path, entries: dict[str, str]) -> None:
    """SHA256SUMS in the builder's format."""
    text = "".join(f"{digest}  {name}\n" for name, digest in sorted(entries.items()))
    (release_dir / "SHA256SUMS").write_text(text, encoding="utf-8", newline="\n")


def reseal(release_dir: Path, edit: Callable[[dict], None] | None = None,
           text_edit: Callable[[str], str] | None = None) -> None:
    """Recompute every digest the release set derives from asset bytes, as a consistent asset swap would.

    Updates release-manifest.json (files, spec_sha256, provider_sources_sha256 and each client's
    archive and provider digests), then applies `edit`/`text_edit`, then rewrites SHA256SUMS from the
    bytes for the names it already lists. Without edits on an untouched release set this reproduces
    the builder's bytes exactly (test_consistent_reseal_reproduces_builder_bytes).
    """
    manifest_path = release_dir / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digests = {name: sha256_bytes((release_dir / name).read_bytes()) for name in manifest["files"] if (release_dir / name).is_file()}
    manifest["files"].update(digests)
    spec = f"openapi-{manifest['tag']}.yaml"
    if spec in digests:
        manifest["spec_sha256"] = digests[spec]
    manifest["provider_sources_sha256"].update({name: digests[name] for name in manifest["provider_sources_sha256"] if name in digests})
    for client in manifest["clients"].values():
        if client["archive"] in digests:
            client["archive_sha256"] = digests[client["archive"]]
        client["provider_sources_sha256"] = dict(manifest["provider_sources_sha256"])
    if edit is not None:
        edit(manifest)
    text = json.dumps(manifest, indent=2) + "\n"
    if text_edit is not None:
        text = text_edit(text)
    manifest_path.write_bytes(text.encode("utf-8"))
    names = read_sums(release_dir)
    write_sums(release_dir, {name: sha256_bytes((release_dir / name).read_bytes()) for name in names if (release_dir / name).is_file()})


Members = list[tuple[tarfile.TarInfo, bytes | None]]


def read_archive(data: bytes) -> Members:
    members: Members = []
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(data)), mode="r:") as archive:
        for member in archive.getmembers():
            handle = archive.extractfile(member) if member.isfile() else None
            members.append((member, None if handle is None else handle.read()))
    return members


def write_archive(members: Members) -> bytes:
    """A gzip-compressed PAX tar of exactly these members, in this order (no safety rules applied)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for info, data in members:
            info = copy.copy(info)
            if data is None:
                archive.addfile(info)
            else:
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return gzip_bytes(buffer.getvalue())


def member(name: str, data: bytes | None = b"synthetic\n", kind: bytes = tarfile.REGTYPE, linkname: str = "") -> tuple[tarfile.TarInfo, bytes | None]:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.mtime = SYNTHETIC_EPOCH
    info.mode = 0o755 if kind == tarfile.DIRTYPE else 0o644
    info.linkname = linkname
    return info, (data if kind in (tarfile.REGTYPE, tarfile.AREGTYPE) else None)


def refresh_embedded(members: Members, prefix: str) -> Members:
    """Rewrite the archive's contracts-manifest.json so that it describes the current members."""
    manifest_name = f"{prefix}/{MANIFEST_NAME}"
    files = {info.name[len(prefix) + 1:]: sha256_bytes(data) for info, data in members
             if data is not None and info.name != manifest_name}
    refreshed: Members = []
    for info, data in members:
        if info.name == manifest_name and data is not None:
            manifest = json.loads(data.decode("utf-8"))
            manifest["files"] = [{"path": path, "sha256": files[path]} for path in sorted(files)]
            manifest["file_count"] = len(files)
            manifest["tree_sha256"] = combined_digest(files)
            data = json_text(manifest).encode("utf-8")
        refreshed.append((info, data))
    return refreshed


def rewrite_archive(release_dir: Path, tag: str, language: str, transform: Callable[[Members], Members],
                    keep_order: bool = False) -> None:
    """Rewrite one client archive with `transform` applied to its members, as a consistent swap would.

    The members are written in the builder's sorted order (unless `keep_order`), so a swapped archive is
    in the builder's canonical form and reaches the checks after it.
    """
    path = release_dir / f"pennilogic-contracts-{language}-{tag}.tar.gz"
    members = transform(read_archive(path.read_bytes()))
    path.write_bytes(write_archive(members if keep_order else sorted(members, key=lambda item: item[0].name)))


# --- raw tar blocks (archive reader differential and canonical-form tests) ----------------------------

END_OF_ARCHIVE = b"\0" * (2 * tarfile.BLOCKSIZE)


def tar_padded(data: bytes) -> bytes:
    return data + b"\0" * (-len(data) % tarfile.BLOCKSIZE)


def pax_record(keyword: str, value: str | bytes) -> bytes:
    """One extended header record `<length> <keyword>=<value>\\n`; the length counts the whole record."""
    body = b" " + keyword.encode("ascii") + b"=" + (value if isinstance(value, bytes) else value.encode("utf-8")) + b"\n"
    length = len(body) + 1
    while len(str(length)) + len(body) != length:
        length = len(str(length)) + len(body)
    return str(length).encode("ascii") + body


def ustar_header(name: str, size: int, kind: bytes = tarfile.REGTYPE, mode: int = 0o644, mtime: int = SYNTHETIC_EPOCH) -> bytes:
    """One POSIX ustar header block for a name of at most 100 characters (no extended header)."""
    if len(name) > 100:
        raise AssertionError("ustar_header takes names of at most 100 characters")
    info = tarfile.TarInfo(name)
    info.type, info.size, info.mode, info.mtime = kind, size, mode, mtime
    return info.tobuf(format=tarfile.USTAR_FORMAT)


def extended_header(kind: bytes, records: bytes) -> bytes:
    """A pax extended (`x`) or global (`g`) header block followed by its padded records."""
    return ustar_header("././@PaxHeader", len(records), kind, mode=0, mtime=0) + tar_padded(records)


def gnu_long_header(name: str, kind: bytes = tarfile.GNUTYPE_LONGNAME) -> bytes:
    """A GNU long-name (`L`) or long-link (`K`) header block followed by its padded name."""
    data = name.encode("utf-8") + b"\0"
    info = tarfile.TarInfo("././@LongLink")
    info.type, info.size, info.mtime = kind, len(data), 0
    return info.tobuf(format=tarfile.GNU_FORMAT) + tar_padded(data)


def raw_member(info: tarfile.TarInfo, data: bytes | None) -> bytes:
    """One member exactly as the builder's tarfile PAX writer writes it."""
    return info.tobuf(format=tarfile.PAX_FORMAT) + (b"" if data is None else tar_padded(data))


def raw_tar(members: Members) -> bytes:
    """An uncompressed tar of these members in this order, ended by the two-block end-of-archive marker."""
    return b"".join(raw_member(info, data) for info, data in members) + END_OF_ARCHIVE


def gzip_bytes(raw: bytes) -> bytes:
    """One gzip member, as the builder compresses (mtime 0, no file name)."""
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0, filename="") as handle:
        handle.write(raw)
    return buffer.getvalue()


def member_offsets(raw: bytes) -> dict[str, tuple[int, int]]:
    """name -> (offset of its first header block, offset of its data), as tarfile reads `raw`."""
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        return {info.name: (info.offset, info.offset_data) for info in archive}


def patch_header(raw: bytes, offset: int, start: int, value: bytes) -> bytes:
    """Overwrite bytes of the header block at `offset` and recompute its checksum as tarfile writes it."""
    header = bytearray(raw[offset:offset + tarfile.BLOCKSIZE])
    header[start:start + len(value)] = value
    header[148:156] = b"        "
    header[148:156] = b"%06o\0 " % sum(header)
    return raw[:offset] + bytes(header) + raw[offset + tarfile.BLOCKSIZE:]


def replace_member_data(members: Members, name: str, change: Callable[[bytes], bytes]) -> Members:
    found = [index for index, (info, _) in enumerate(members) if info.name == name]
    if len(found) != 1:
        raise AssertionError(f"expected one archive member {name}, found {len(found)}")
    result = list(members)
    info, data = result[found[0]]
    assert data is not None
    result[found[0]] = (info, change(data))
    return result


def snapshot(root: Path) -> dict[str, tuple]:
    """Every path under root with its kind, content digest and modification time, for no-write assertions."""
    state: dict[str, tuple] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            state[relative] = ("link", os.readlink(path))
        elif path.is_file():
            state[relative] = ("file", sha256_bytes(path.read_bytes()), path.stat().st_mtime_ns)
        else:
            state[relative] = ("directory",)
    return state


def generator_manifest_keys() -> set[str]:
    """The top-level contracts-manifest.json keys generate_clients._generate_target writes (read from its source)."""
    tree = ast.parse(Path(generate_clients.__file__).read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_generate_target":
            for statement in ast.walk(node):
                if isinstance(statement, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "manifest"
                                                             for target in statement.targets):
                    if isinstance(statement.value, ast.Dict):
                        keys |= {key.value for key in statement.value.keys if isinstance(key, ast.Constant)}
                if isinstance(statement, ast.Assign):
                    keys |= {target.slice.value for target in statement.targets
                             if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                             and target.value.id == "manifest" and isinstance(target.slice, ast.Constant)}
    if not keys:
        raise AssertionError("generate_clients._generate_target no longer builds a `manifest` dict literal; "
                             "re-check contract_artifact.EMBEDDED_MANIFEST_KEYS against the generator")
    return keys


def generator_record_keys() -> set[str]:
    """The keys of the `generator` record generate_clients._generate_target writes (read from its source)."""
    tree = ast.parse(Path(generate_clients.__file__).read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_generate_target":
            for statement in ast.walk(node):
                if isinstance(statement, ast.Dict):
                    for key, value in zip(statement.keys, statement.values):
                        if isinstance(key, ast.Constant) and key.value == "generator" and isinstance(value, ast.Dict):
                            keys |= {item.value for item in value.keys if isinstance(item, ast.Constant)}
    if not keys:
        raise AssertionError("generate_clients._generate_target no longer writes a literal `generator` record; "
                             "re-check contract_artifact.GENERATOR_KEYS against the generator")
    return keys


# --- parsed documents, detector outputs and reports (compat, gate, report and command-line tests) ---------

SYNTHETIC_INSTANT = "2026-01-02T03:04:05.000Z"  # SYNTHETIC_EPOCH as the fixed 24-character UTC instant
FIXTURE_COMMIT = synthetic_digest("fixture tag commit")[:40]
PROBE_SCHEMA = "SyntheticProbe"


def synthetic_document(version: str, *, properties: tuple[str, ...] = ("amount", "currency", "note"),
                       required: tuple[str, ...] = ("amount", "currency"), paths: dict | None = None) -> dict:
    """A complete supported OpenAPI 3.1.0 document in the parsed form check_breaking_changes.source_documents returns."""
    return {
        "openapi": "3.1.0",
        "info": {"title": "PenniLogic synthetic contract (verification tests only)", "version": version},
        "paths": copy.deepcopy(paths or {}),
        "components": {
            "schemas": {PROBE_SCHEMA: {
                "type": "object", "additionalProperties": False, "required": list(required),
                "properties": {name: {"type": "string"} for name in properties},
            }},
            "responses": {},
        },
    }


def probe_path(operation_id: str = "getSyntheticProbe") -> dict:
    """One GET operation whose 200 response is the synthetic probe schema."""
    return {"get": {"operationId": operation_id, "description": "Synthetic read (verification tests only).",
                    "responses": {"200": {"description": "ok", "content": {"application/json": {
                        "schema": {"$ref": f"#/components/schemas/{PROBE_SCHEMA}"}}}}}}}


def version_diff(before: str, after: str) -> dict:
    """The `info` part of an `oasdiff diff --format json` record for a version change."""
    return {"info": {"version": {"from": before, "to": after}}}


def fixture_report_parts() -> dict:
    """build_report keyword arguments for a complete published-mode report whose rows are all live and passing.

    In-memory test fixture only: it exists to give the acceptance-eligibility rule a passing control. Every
    row name is a fixture name, not a real repository, and no such report is ever written or published.
    """
    rows = [{"language": language, "name": f"fixture-{language}-consumer", "version": "0.0.0-fixture",
             "verified_contract_version": "1.2.3", "mode": "live", "result": "pass"} for language in pl_contracts.LANGUAGES]
    return {
        "mode": "published",
        "contract": {"version": "1.2.3", "tag": "v1.2.3", "commit": FIXTURE_COMMIT, "spec_sha256": synthetic_digest("spec"),
                     "release_manifest_sha256": synthetic_digest("release manifest")},
        "compatibility": {"baseline": "v1.2.2", "revision_version": "1.2.3", "change_class": "additive", "findings": [],
                          "problems": [], "result": "pass"},
        "gate": {"mode": "block", "reason": "gate.block", "warn_until": None, "state_sha256": synthetic_digest("gate state")},
        "provider": {"name": "fixture-provider", "version": "0.0.0-fixture", "verified_contract_version": "1.2.3",
                     "mode": "live", "result": "pass"},
        "consumers": rows,
        "generated_at": parse_instant(SYNTHETIC_INSTANT),
    }


def synthetic_report_parts() -> dict:
    """The same report as this repository's own runs produce it: synthetic-fixture mode and synthetic rows."""
    parts = fixture_report_parts()
    parts["mode"] = "synthetic-fixture"
    parts["provider"] = dict(parts["provider"], name="synthetic-provider", mode="synthetic-fixture", version=None)
    parts["consumers"] = [dict(row, name=f"synthetic-{row['language']}", mode="synthetic-fixture", version=None)
                          for row in parts["consumers"]]
    return parts
