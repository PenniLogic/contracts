"""Load a published contract artifact and verify it against its annotated tag's own tree (T-QA-02).

A published contract version is an annotated tag plus the GitHub Release assets that
scripts/release.py builds for it: the specification, the currency registry, the provider sources,
the fixtures, one archive per generated client, release-manifest.json and SHA256SUMS
(docs/publication.md). The verification runner accepts a contract version only through this
loader, and refuses it unless every rule below holds. Each refusal carries one static rule ID;
docs/contract-verification.md lists them.

1. Tag. The name matches check_breaking_changes.TAG in full, refs/tags/<tag> is an annotated tag
   object that names itself <tag> and targets a commit, and the remote's refs/tags/<tag>
   (git ls-remote) is that same tag object. That is what "published" means here.
2. Release set. The directory holds exactly the assets release.build_release_set produces for the
   tag, plus SHA256SUMS. SHA256SUMS is in its canonical form, lists exactly those assets, and every
   digest matches the bytes.
3. Manifest. release-manifest.json has exactly the builder's keys. It agrees with the tag (tag,
   version, commit), with SHA256SUMS and with the info.version of the specification it ships.
4. Tag-tree anchors. The specification, registry, provider-source and fixture assets equal their
   blobs at the tag commit. The toolchain pins equal toolchain/versions.json there. The tag's
   generator/golden.json is self-consistent and records the shipped specification for every language.
5. Archives. Each client archive is read with bounded sizes and checked member by member: regular
   files and directories only, one prefix, no absolute or parent paths, no links or devices, no
   duplicates or case collisions. It is then unpacked with the tarfile data filter. The tree digest
   is recomputed with pl_contracts.tree_hash, excluding contracts-manifest.json exactly as
   generate_clients.py does. It must equal the archive's own manifest, the release manifest and
   golden.json[language].tree_sha256 at the tag.

The GitHub Release presence check needs the network and is held. The result reports it as not_run,
so a loaded artifact alone never claims that the Release exists.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pl_contracts  # noqa: E402
from check_breaking_changes import TAG  # noqa: E402
from generate_clients import GOLDEN, MANIFEST_NAME, golden_inconsistency  # noqa: E402
from pl_contracts import PipelineError, sha256_bytes, spec_version, tree_hash  # noqa: E402

RULES = (
    "tag.grammar", "git.unavailable", "repository.invalid", "release_dir.missing", "release_dir.inside_repository",
    "remote.invalid", "tag.missing", "tag.not_annotated", "tag.object_invalid", "tag.remote_unreachable",
    "tag.unpublished", "tag.remote_mismatch", "anchor.asset_name_collision", "anchor.tag_file_missing",
    "asset.not_regular_file", "sums.missing_file", "asset.too_large", "sums.malformed", "sums.missing_entry",
    "sums.extra_entry", "asset.missing", "asset.unexpected", "sums.digest_mismatch", "manifest.malformed",
    "manifest.files_mismatch", "manifest.tag_mismatch", "manifest.version_mismatch", "manifest.commit_mismatch",
    "manifest.spec_digest_mismatch", "manifest.provider_sources_mismatch", "manifest.clients_mismatch",
    "spec.version_mismatch", "anchor.asset_blob_mismatch", "anchor.toolchain_mismatch", "anchor.golden_invalid",
    "anchor.golden_spec_mismatch", "archive.malformed", "archive.too_large", "archive.too_many_members",
    "archive.unsafe_path", "archive.outside_prefix", "archive.link_member", "archive.special_member",
    "archive.duplicate_member", "archive.case_collision", "archive.manifest_missing", "archive.manifest_invalid",
    "archive.tree_mismatch", "archive.manifest_mismatch", "anchor.golden_tree_mismatch",
)

RELEASE_MANIFEST = "release-manifest.json"
SUMS = "SHA256SUMS"
# The keys release.build_release_set writes; test_builder_and_loader_keys_agree fails when they diverge.
RELEASE_MANIFEST_KEYS = frozenset({
    "schema_version", "tag", "spec_version", "spec_sha256", "provider_sources_sha256", "commit", "publication",
    "toolchain", "clients", "files",
})
CLIENT_KEYS = frozenset({
    "archive", "archive_sha256", "tree_sha256", "file_count", "generator", "runtime_sha256", "provider_sources_sha256",
})
TOOLCHAIN_KEYS = frozenset({"openapi_generator", "spectral", "oasdiff"})
GENERATOR_PIN_KEYS = frozenset({"version", "jar_sha256"})
# The contracts-manifest.json keys this loader cross-checks. generate_clients.py writes all of them;
# the release builder copies a subset into release-manifest.json.
EMBEDDED_MANIFEST_KEYS = frozenset({
    "schema_version", "target", "spec_version", "spec_sha256", "currency_registry_sha256", "provider_sources_sha256",
    "generator", "runtime_sha256", "file_count", "tree_sha256", "files",
})
GOLDEN_KEYS = frozenset({"tree_sha256", "spec_version", "spec_sha256", "file_count", "files"})

# Paths inside the tag's tree, from the same constants the release builder and the generator use.
SPEC_PATH = pl_contracts.SPEC.relative_to(pl_contracts.ROOT).as_posix()
SPEC_DIR = PurePosixPath(SPEC_PATH).parent.as_posix()
REGISTRY_PATH = pl_contracts.REGISTRY.relative_to(pl_contracts.ROOT).as_posix()
FIXTURES_PATH = pl_contracts.FIXTURES.relative_to(pl_contracts.ROOT).as_posix()
GOLDEN_PATH = GOLDEN.relative_to(pl_contracts.ROOT).as_posix()
VERSIONS_PATH = pl_contracts.VERSIONS.relative_to(pl_contracts.ROOT).as_posix()
# Asset sources that are not files of the tag's tree.
ARCHIVE_SOURCE = "archive:"
MANIFEST_SOURCE = "release:manifest"

# Bounds. The real archives are a few megabytes; these limits only stop hostile input.
MAX_ASSET_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_TAR_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20_000
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_TAG_BLOB_BYTES = 64 * 1024 * 1024
GIT_TIMEOUT_SECONDS = 120

HEX64 = re.compile(r"[0-9a-f]{64}")
OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
REMOTE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
SUMS_LINE = re.compile(r"([0-9a-f]{64})  ([^\x00-\x1f\x7f/\\]{1,255})")
REGULAR_BLOB_MODES = ("100644", "100755")
FILE_TYPES = (tarfile.REGTYPE, tarfile.AREGTYPE)
LINK_TYPES = (tarfile.SYMTYPE, tarfile.LNKTYPE)
# Variables that would point git at another repository, index or object store than the one given.
REPOSITORY_ENVIRONMENT = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE", "GIT_COMMON_DIR", "GIT_REPLACE_REF_BASE",
)
PASSED_CHECKS = (
    "tag.grammar", "tag.annotated", "tag.published_remote_ref", "release.sha256sums_exact", "release.manifest_agreement",
    "anchor.tag_tree_blobs", "anchor.toolchain_pins", "anchor.golden", "archive.safe_extraction", "archive.tree_digest",
)
HELD_CHECKS = ("github_release.presence",)


class ArtifactRefused(Exception):
    """The artifact failed one rule. `rule` is a static ID from RULES; `detail` never echoes git stderr."""

    def __init__(self, rule: str, detail: str) -> None:
        if rule not in RULES:
            raise ValueError(f"unknown artifact rule {rule!r}")
        super().__init__(f"{rule}: {detail}")
        self.rule = rule
        self.detail = detail


@dataclass(frozen=True)
class PublishedArtifact:
    """A verified published artifact. Every check passed except the held GitHub Release presence check."""

    tag: str
    version: str
    tag_object: str
    commit: str
    remote: str
    release_dir: Path
    spec_asset: str
    spec_sha256: str
    manifest_sha256: str
    assets: dict[str, str]
    clients: dict[str, dict]
    checks: tuple[tuple[str, str], ...]

    def read_verified_asset(self, name: str) -> bytes:
        """Read one asset again and refuse it if its bytes changed after the artifact was loaded."""
        if name not in self.assets:
            raise ArtifactRefused("asset.unexpected", f"{ascii(name)} is not an asset of {self.tag}")
        data = _read_bounded(self.release_dir / name, name)
        if sha256_bytes(data) != self.assets[name]:
            raise ArtifactRefused("sums.digest_mismatch", f"{ascii(name)} changed after {self.tag} was loaded")
        return data

    def as_dict(self) -> dict:
        return {
            "tag": self.tag, "version": self.version, "tag_object": self.tag_object, "commit": self.commit,
            "remote": self.remote, "spec_asset": self.spec_asset, "spec_sha256": self.spec_sha256,
            "manifest_sha256": self.manifest_sha256, "assets": dict(sorted(self.assets.items())),
            "clients": {language: dict(client) for language, client in self.clients.items()},
            "checks": [{"id": check, "status": status} for check, status in self.checks],
        }


def archive_prefix(language: str, tag: str) -> str:
    return f"pennilogic-contracts-{language}-{tag}"


def archive_name(language: str, tag: str) -> str:
    return f"{archive_prefix(language, tag)}.tar.gz"


def spec_asset_name(tag: str) -> str:
    return f"openapi-{tag}.yaml"


def is_tag_path(source: str) -> bool:
    return not source.startswith(ARCHIVE_SOURCE) and source != MANIFEST_SOURCE


def expected_asset_sources(tag: str, fixture_names: list[str]) -> dict[str, str]:
    """Map every asset name release.build_release_set writes for `tag` to its source.

    The source is a path in the tag's tree, `archive:<language>` or `release:manifest`. The builder
    writes every asset into one flat directory, so two sources with one name would silently
    overwrite each other; that is refused instead.
    """
    sources: dict[str, str] = {}

    def add(name: str, source: str) -> None:
        if name in sources and sources[name] != source:
            raise ArtifactRefused("anchor.asset_name_collision",
                                  f"{ascii(name)} would be written from both {ascii(sources[name])} and {ascii(source)}")
        sources[name] = source

    add(spec_asset_name(tag), SPEC_PATH)
    add(PurePosixPath(REGISTRY_PATH).name, REGISTRY_PATH)
    for name in pl_contracts.PROVIDER_SOURCE_NAMES:
        add(name, f"{SPEC_DIR}/{name}")
    for name in fixture_names:
        add(name, f"{FIXTURES_PATH}/{name}")
    for language in pl_contracts.LANGUAGES:
        add(archive_name(language, tag), f"{ARCHIVE_SOURCE}{language}")
    add(RELEASE_MANIFEST, MANIFEST_SOURCE)
    return sources


def load_published_artifact(tag: str, release_dir: Path, *, repo: Path = pl_contracts.ROOT, remote: str = "origin",
                            scratch_dir: Path | None = None) -> PublishedArtifact:
    """Verify the downloaded Release assets in `release_dir` against `tag` in `repo`; raise ArtifactRefused.

    Archives are unpacked into a temporary directory (under `scratch_dir` when given) that is removed
    before this function returns; nothing is written anywhere else.
    """
    if not isinstance(tag, str) or TAG.fullmatch(tag) is None:
        raise ArtifactRefused("tag.grammar", f"{ascii(tag)} is not a published version tag vMAJOR.MINOR.PATCH")
    version = tag[1:]
    repo, release_dir = Path(repo), Path(release_dir)
    git = _Git(repo)
    top_level = _check_repository(git, repo)
    _check_release_dir(release_dir, top_level)
    _check_remote(git, remote)
    tag_object, commit = _resolve_annotated_tag(git, tag)
    _check_published(git, remote, tag, tag_object, commit)
    tree = _TagTree(git, commit)
    sources = expected_asset_sources(tag, tree.fixture_names())
    tree.read(sorted({source for source in sources.values() if is_tag_path(source)} | {GOLDEN_PATH, VERSIONS_PATH}))
    sums, data = _read_release_set(release_dir, sources)
    manifest = _check_manifest(data[RELEASE_MANIFEST], sums, tag, version, commit)
    spec_name = spec_asset_name(tag)
    _check_spec_version(data[spec_name], version)
    for name, source in sorted(sources.items()):
        if is_tag_path(source):
            _check_blob(tree, source, name, data[name])
    _check_toolchain(tree, manifest)
    golden = _check_golden(tree, sums[spec_name], version)
    clients: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="pl-artifact-", dir=None if scratch_dir is None else str(scratch_dir)) as work:
        for language in pl_contracts.LANGUAGES:
            clients[language] = _check_archive(language, tag, version, data, sums, manifest, golden[language], Path(work))
    return PublishedArtifact(
        tag=tag, version=version, tag_object=tag_object, commit=commit, remote=remote, release_dir=release_dir,
        spec_asset=spec_name, spec_sha256=sums[spec_name], manifest_sha256=sums[RELEASE_MANIFEST],
        assets=dict(sorted(sums.items())), clients=clients,
        checks=tuple((check, "pass") for check in PASSED_CHECKS) + tuple((check, "not_run") for check in HELD_CHECKS),
    )


# --- git ---------------------------------------------------------------------------------------------

class _Git:
    """Plumbing commands against exactly one repository, with a resolved executable and bounded time."""

    def __init__(self, repo: Path) -> None:
        executable = shutil.which("git")
        if executable is None:
            raise ArtifactRefused("git.unavailable", "git was not found on PATH")
        self.executable = os.path.abspath(executable)
        self.repo = repo
        environment = {key: value for key, value in os.environ.items() if key not in REPOSITORY_ENVIRONMENT}
        environment.update({
            "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_LITERAL_PATHSPECS": "1",
            "LC_ALL": "C", "LANGUAGE": "C",
        })
        self.environment = environment

    def run(self, *args: str, input_data: bytes = b"") -> subprocess.CompletedProcess[bytes] | None:
        """Return the completed process, or None when git could not start or exceeded the time bound."""
        try:
            return subprocess.run(
                [self.executable, "-C", str(self.repo), *args], input=input_data, capture_output=True, env=self.environment,
                timeout=GIT_TIMEOUT_SECONDS, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None


def _read_objects(git: _Git, names: list[str], rule: str, what: str) -> list[tuple[str, str, bytes] | None]:
    """Read objects with one `git cat-file --batch`: (object ID, type, content), or None for a missing name."""
    completed = git.run("cat-file", "--batch", input_data="".join(f"{name}\n" for name in names).encode("utf-8"))
    if completed is None or completed.returncode != 0:
        raise ArtifactRefused(rule, f"{what} cannot be read")
    output, position = completed.stdout, 0
    objects: list[tuple[str, str, bytes] | None] = []
    for name in names:
        newline = output.find(b"\n", position)
        if newline < 0:
            raise ArtifactRefused(rule, f"{what}: git returned a truncated object stream")
        header = output[position:newline].decode("utf-8", "replace")
        position = newline + 1
        if header == f"{name} missing":
            objects.append(None)
            continue
        fields = header.split(" ")
        if len(fields) != 3 or OBJECT_ID.fullmatch(fields[0]) is None or not fields[2].isdigit():
            raise ArtifactRefused(rule, f"{what}: git returned an unexpected object header")
        size = int(fields[2])
        content = output[position:position + size]
        if len(content) != size or output[position + size:position + size + 1] != b"\n":
            raise ArtifactRefused(rule, f"{what}: git returned a truncated object stream")
        objects.append((fields[0], fields[1], content))
        position += size + 1
    return objects


def _check_repository(git: _Git, repo: Path) -> Path:
    completed = git.run("rev-parse", "--show-toplevel")
    if completed is None or completed.returncode != 0:
        raise ArtifactRefused("repository.invalid", "the repository path is not a git checkout")
    top_level = Path(completed.stdout.decode("utf-8", "surrogateescape").strip()).resolve()
    if top_level != repo.resolve():
        raise ArtifactRefused("repository.invalid", "the repository path must be the top level of a git checkout")
    return top_level


def _check_release_dir(release_dir: Path, top_level: Path) -> None:
    if not release_dir.is_dir():
        raise ArtifactRefused("release_dir.missing", "the release directory does not exist")
    resolved = release_dir.resolve()
    if resolved == top_level or top_level in resolved.parents:
        raise ArtifactRefused("release_dir.inside_repository",
                              "the release directory is inside the repository checkout; verify downloaded Release "
                              "assets, never a working-tree build such as build/dist")


def _check_remote(git: _Git, remote: str) -> None:
    if not isinstance(remote, str) or REMOTE_NAME.fullmatch(remote) is None:
        raise ArtifactRefused("remote.invalid", f"{ascii(remote)} is not a remote name")
    completed = git.run("config", "--get", f"remote.{remote}.url")
    if completed is None or completed.returncode != 0 or not completed.stdout.strip():
        raise ArtifactRefused("remote.invalid", f"remote {remote} is not configured in the repository")


def _resolve_annotated_tag(git: _Git, tag: str) -> tuple[str, str]:
    ref = f"refs/tags/{tag}"
    tag_entry, commit_entry = _read_objects(git, [ref, f"{ref}^{{commit}}"], "tag.missing", ref)
    if tag_entry is None:
        raise ArtifactRefused("tag.missing", f"{ref} does not exist in the repository")
    tag_object, kind, body = tag_entry
    if kind != "tag":
        raise ArtifactRefused("tag.not_annotated",
                              f"{ref} is a lightweight tag (it points at a {ascii(kind)}); a published version is an annotated tag")
    headers: dict[str, str] = {}
    for line in body.split(b"\n"):
        if not line:
            break
        key, _, value = line.decode("utf-8", "replace").partition(" ")
        headers.setdefault(key, value)
    commit = headers.get("object", "")
    if headers.get("tag") != tag:
        raise ArtifactRefused("tag.object_invalid", f"the tag object of {ref} names {ascii(headers.get('tag'))}")
    if headers.get("type") != "commit" or OBJECT_ID.fullmatch(commit) is None:
        raise ArtifactRefused("tag.object_invalid", f"the tag object of {ref} does not target a commit")
    if commit_entry is None or commit_entry[:2] != (commit, "commit"):
        raise ArtifactRefused("tag.object_invalid", f"the tag object of {ref} targets a missing or non-commit object")
    return tag_object, commit


def _check_published(git: _Git, remote: str, tag: str, tag_object: str, commit: str) -> None:
    ref = f"refs/tags/{tag}"
    completed = git.run("ls-remote", remote, ref)
    if completed is None or completed.returncode != 0:
        outcome = "no exit status (start failure or timeout)" if completed is None else f"exit {completed.returncode}"
        raise ArtifactRefused("tag.remote_unreachable", f"git ls-remote {remote} failed ({outcome}); publication is unknown")
    advertised: dict[str, str] = {}
    for line in completed.stdout.decode("utf-8", "replace").splitlines():
        object_id, _, name = line.partition("\t")
        if name in (ref, f"{ref}^{{}}"):
            advertised[name] = object_id
    if ref not in advertised:
        raise ArtifactRefused("tag.unpublished", f"{remote} does not carry {ref}; the tag was never pushed")
    if advertised[ref] != tag_object:
        raise ArtifactRefused("tag.remote_mismatch",
                              f"{remote} {ref} is {ascii(advertised[ref])}, the local annotated tag is {tag_object}")
    peeled = advertised.get(f"{ref}^{{}}")
    if peeled is not None and peeled != commit:
        raise ArtifactRefused("tag.remote_mismatch", f"{remote} {ref} peels to {ascii(peeled)}, not {commit}")


class _TagTree:
    """The files of the tag commit that the release set is built from."""

    def __init__(self, git: _Git, commit: str) -> None:
        self.git = git
        self.commit = commit
        pathspecs = sorted({SPEC_DIR, PurePosixPath(REGISTRY_PATH).parent.as_posix(), FIXTURES_PATH, GOLDEN_PATH, VERSIONS_PATH})
        completed = git.run("ls-tree", "-r", "-z", "--full-tree", commit, "--", *pathspecs)
        if completed is None or completed.returncode != 0:
            raise ArtifactRefused("anchor.tag_file_missing", f"the tree of the tag commit {commit} cannot be listed")
        self.entries: dict[str, tuple[str, str, str]] = {}
        for record in completed.stdout.split(b"\0"):
            if record:
                meta, _, path = record.partition(b"\t")
                mode, kind, object_id = meta.decode("ascii", "replace").split(" ", 2)
                self.entries[path.decode("utf-8", "surrogateescape")] = (mode, kind, object_id)
        self.contents: dict[str, bytes] = {}

    def fixture_names(self) -> list[str]:
        """Direct `*.json` children of the fixtures directory: the set the builder's glob selects."""
        prefix = f"{FIXTURES_PATH}/"
        return sorted(path[len(prefix):] for path in self.entries
                      if path.startswith(prefix) and "/" not in path[len(prefix):] and path.endswith(".json"))

    def require_regular_file(self, path: str) -> str:
        entry = self.entries.get(path)
        if entry is None or entry[1] != "blob" or entry[0] not in REGULAR_BLOB_MODES:
            raise ArtifactRefused("anchor.tag_file_missing", f"{ascii(path)} is not a regular file at the tag commit {self.commit}")
        return entry[2]

    def read(self, paths: list[str]) -> None:
        """Read the listed files of the tag commit with one git process, in order; refuse a missing one."""
        object_ids = {path: self.require_regular_file(path) for path in paths}
        unique = sorted(set(object_ids.values()))
        what = f"the files of the tag commit {self.commit}"
        objects = dict(zip(unique, _read_objects(self.git, unique, "anchor.tag_file_missing", what)))
        for path, object_id in object_ids.items():
            entry = objects[object_id]
            if entry is None or entry[1] != "blob":
                raise ArtifactRefused("anchor.tag_file_missing", f"{ascii(path)} cannot be read at the tag commit {self.commit}")
            if len(entry[2]) > MAX_TAG_BLOB_BYTES:
                raise ArtifactRefused("asset.too_large", f"{ascii(path)} at the tag commit exceeds {MAX_TAG_BLOB_BYTES} bytes")
            self.contents[path] = entry[2]

    def blob(self, path: str) -> bytes:
        if path not in self.contents:
            self.read([path])
        return self.contents[path]


# --- release set -------------------------------------------------------------------------------------

def _read_bounded(path: Path, name: str) -> bytes:
    try:
        if path.stat().st_size > MAX_ASSET_BYTES:
            raise ArtifactRefused("asset.too_large", f"{ascii(name)} exceeds {MAX_ASSET_BYTES} bytes")
        with path.open("rb") as handle:
            data = handle.read(MAX_ASSET_BYTES + 1)
    except OSError as error:
        raise ArtifactRefused("asset.missing", f"{ascii(name)} cannot be read ({type(error).__name__})") from None
    if len(data) > MAX_ASSET_BYTES:
        raise ArtifactRefused("asset.too_large", f"{ascii(name)} exceeds {MAX_ASSET_BYTES} bytes")
    return data


def parse_sums(data: bytes) -> dict[str, str]:
    """Parse SHA256SUMS in exactly the form release.build_release_set writes: `<sha256>  <name>\\n`, sorted."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ArtifactRefused("sums.malformed", "SHA256SUMS is not UTF-8") from None
    if not text.endswith("\n"):
        raise ArtifactRefused("sums.malformed", "SHA256SUMS is empty or lacks its final newline")
    entries: dict[str, str] = {}
    for number, line in enumerate(text[:-1].split("\n"), start=1):
        match = SUMS_LINE.fullmatch(line)
        if match is None or match.group(2) in (".", ".."):
            raise ArtifactRefused("sums.malformed", f"SHA256SUMS line {number} is not `<lowercase sha256>  <file name>`")
        digest, name = match.groups()
        if name in entries:
            raise ArtifactRefused("sums.malformed", f"SHA256SUMS lists {ascii(name)} twice")
        entries[name] = digest
    if "".join(f"{entries[name]}  {name}\n" for name in sorted(entries)) != text:
        raise ArtifactRefused("sums.malformed", "SHA256SUMS is not sorted by file name")
    return entries


def _read_release_set(release_dir: Path, sources: dict[str, str]) -> tuple[dict[str, str], dict[str, bytes]]:
    present: dict[str, Path] = {}
    with os.scandir(release_dir) as listing:
        for entry in sorted(listing, key=lambda item: item.name):
            if not entry.is_file(follow_symlinks=False):
                raise ArtifactRefused("asset.not_regular_file",
                                      f"{ascii(entry.name)} in the release directory is not a regular file")
            present[entry.name] = Path(entry.path)
    if SUMS not in present:
        raise ArtifactRefused("sums.missing_file", "SHA256SUMS is not in the release directory")
    sums = parse_sums(_read_bounded(present[SUMS], SUMS))
    missing = sorted(set(sources) - set(sums))
    if missing:
        raise ArtifactRefused("sums.missing_entry", f"SHA256SUMS does not list {', '.join(map(ascii, missing))}")
    extra = sorted(set(sums) - set(sources))
    if extra:
        raise ArtifactRefused("sums.extra_entry",
                              f"SHA256SUMS lists assets the tag does not produce: {', '.join(map(ascii, extra))}")
    absent = sorted(set(sources) - set(present))
    if absent:
        raise ArtifactRefused("asset.missing", f"the release directory lacks {', '.join(map(ascii, absent))}")
    unexpected = sorted(set(present) - set(sources) - {SUMS})
    if unexpected:
        raise ArtifactRefused("asset.unexpected",
                              f"the release directory holds files that are not Release assets: {', '.join(map(ascii, unexpected))}")
    data: dict[str, bytes] = {}
    for name in sorted(sources):
        data[name] = _read_bounded(present[name], name)
        if sha256_bytes(data[name]) != sums[name]:
            raise ArtifactRefused("sums.digest_mismatch", f"{ascii(name)} does not match its SHA256SUMS digest")
    return sums, data


# --- strict JSON shapes ------------------------------------------------------------------------------

def strict_json(data: bytes, rule: str, what: str) -> object:
    """Parse UTF-8 JSON without a byte order mark, duplicate keys or non-finite numbers."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ArtifactRefused(rule, f"{what} is not UTF-8") from None
    if text.startswith("\ufeff"):
        raise ArtifactRefused(rule, f"{what} starts with a byte order mark")

    def unique(pairs: list[tuple[str, object]]) -> dict:
        value: dict = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    def non_finite(_: str) -> object:
        raise ValueError("non-finite number")

    try:
        return json.loads(text, object_pairs_hook=unique, parse_constant=non_finite)
    except (ValueError, RecursionError):
        raise ArtifactRefused(rule, f"{what} is not strict JSON (syntax, duplicate key or non-finite number)") from None


def _is_hex64(value: object) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _is_count(value: object) -> bool:
    return type(value) is int and value >= 0


def _is_digest_map(value: object) -> bool:
    return isinstance(value, dict) and all(_is_hex64(item) for item in value.values())


def _client_shape_problem(language: str, client: object) -> str | None:
    if not isinstance(client, dict) or set(client) != CLIENT_KEYS:
        return f"clients[{ascii(language)}] keys differ from the release builder's"
    if not isinstance(client["archive"], str) or not all(
            _is_hex64(client[key]) for key in ("archive_sha256", "tree_sha256", "runtime_sha256")):
        return f"clients[{ascii(language)}] archive name or digests are malformed"
    if not _is_count(client["file_count"]) or not _is_digest_map(client["provider_sources_sha256"]):
        return f"clients[{ascii(language)}] file_count or provider_sources_sha256 is malformed"
    pins = client["generator"]
    if not isinstance(pins, dict) or not isinstance(pins.get("version"), str) or not _is_hex64(pins.get("jar_sha256")):
        return f"clients[{ascii(language)}] generator lacks a version string and a jar SHA-256"
    return None


def _manifest_shape_problem(manifest: object) -> str | None:
    if not isinstance(manifest, dict):
        return "is not a JSON object"
    if set(manifest) != RELEASE_MANIFEST_KEYS:
        return (f"keys differ from the release builder's (missing {sorted(RELEASE_MANIFEST_KEYS - set(manifest))}, "
                f"unknown {sorted(map(ascii, set(manifest) - RELEASE_MANIFEST_KEYS))})")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        return "schema_version is not 1"
    if not all(isinstance(manifest[key], str) for key in ("tag", "spec_version", "publication")):
        return "tag, spec_version and publication must be strings"
    if not _is_hex64(manifest["spec_sha256"]):
        return "spec_sha256 is not a lowercase SHA-256"
    if not isinstance(manifest["commit"], str) or OBJECT_ID.fullmatch(manifest["commit"]) is None:
        return "commit is not a full lowercase object ID"
    if not _is_digest_map(manifest["provider_sources_sha256"]) or not _is_digest_map(manifest["files"]):
        return "provider_sources_sha256 and files must map names to lowercase SHA-256 digests"
    toolchain = manifest["toolchain"]
    if not isinstance(toolchain, dict) or set(toolchain) != TOOLCHAIN_KEYS:
        return "toolchain keys differ from the release builder's"
    pins = toolchain["openapi_generator"]
    if (not isinstance(pins, dict) or set(pins) != GENERATOR_PIN_KEYS or not isinstance(pins["version"], str)
            or not _is_hex64(pins["jar_sha256"]) or not isinstance(toolchain["spectral"], str)
            or not isinstance(toolchain["oasdiff"], str)):
        return "toolchain pins are not version strings and a jar SHA-256"
    if not isinstance(manifest["clients"], dict):
        return "clients is not an object"
    for language, client in manifest["clients"].items():
        problem = _client_shape_problem(language, client)
        if problem is not None:
            return problem
    return None


def _check_manifest(data: bytes, sums: dict[str, str], tag: str, version: str, commit: str) -> dict:
    manifest = strict_json(data, "manifest.malformed", RELEASE_MANIFEST)
    problem = _manifest_shape_problem(manifest)
    if problem is not None:
        raise ArtifactRefused("manifest.malformed", f"{RELEASE_MANIFEST} {problem}")
    assert isinstance(manifest, dict)
    listed = {name: digest for name, digest in sums.items() if name != RELEASE_MANIFEST}
    if manifest["files"] != listed:
        differing = sorted(name for name in set(listed) | set(manifest["files"]) if listed.get(name) != manifest["files"].get(name))
        raise ArtifactRefused("manifest.files_mismatch",
                              f"{RELEASE_MANIFEST} files disagree with SHA256SUMS for {', '.join(map(ascii, differing))}")
    if manifest["tag"] != tag:
        raise ArtifactRefused("manifest.tag_mismatch", f"{RELEASE_MANIFEST} names tag {ascii(manifest['tag'])}, not {tag}")
    if manifest["spec_version"] != version:
        raise ArtifactRefused("manifest.version_mismatch",
                              f"{RELEASE_MANIFEST} spec_version {ascii(manifest['spec_version'])} is not the tag version {version}")
    if manifest["commit"] != commit:
        raise ArtifactRefused("manifest.commit_mismatch",
                              f"{RELEASE_MANIFEST} was built from {manifest['commit']}, the tag commit is {commit}")
    spec_name = spec_asset_name(tag)
    if manifest["spec_sha256"] != sums[spec_name]:
        raise ArtifactRefused("manifest.spec_digest_mismatch", f"{RELEASE_MANIFEST} spec_sha256 is not the digest of {spec_name}")
    providers = manifest["provider_sources_sha256"]
    if providers != {name: sums[name] for name in pl_contracts.PROVIDER_SOURCE_NAMES}:
        raise ArtifactRefused("manifest.provider_sources_mismatch",
                              f"{RELEASE_MANIFEST} provider_sources_sha256 is not the digest map of "
                              f"{', '.join(pl_contracts.PROVIDER_SOURCE_NAMES)}")
    clients = manifest["clients"]
    if set(clients) != set(pl_contracts.LANGUAGES):
        raise ArtifactRefused("manifest.clients_mismatch",
                              f"{RELEASE_MANIFEST} clients {sorted(map(ascii, clients))} are not {sorted(pl_contracts.LANGUAGES)}")
    for language in pl_contracts.LANGUAGES:
        client, name = clients[language], archive_name(language, tag)
        if client["archive"] != name:
            raise ArtifactRefused("manifest.clients_mismatch", f"clients[{language}] archive is {ascii(client['archive'])}, not {name}")
        if client["archive_sha256"] != sums[name]:
            raise ArtifactRefused("manifest.clients_mismatch", f"clients[{language}] archive_sha256 is not the digest of {name}")
        if client["provider_sources_sha256"] != providers:
            raise ArtifactRefused("manifest.clients_mismatch", f"clients[{language}] provider_sources_sha256 differs from the release's")
    return manifest


def _check_spec_version(data: bytes, version: str) -> None:
    try:
        declared = spec_version(data.decode("utf-8"))
    except (UnicodeDecodeError, PipelineError):
        raise ArtifactRefused("spec.version_mismatch", "the specification asset declares no readable info.version") from None
    if declared != version:
        raise ArtifactRefused("spec.version_mismatch", f"the specification asset declares info.version {declared}, not {version}")


# --- anchors at the tag's own tree -------------------------------------------------------------------

def _check_blob(tree: _TagTree, path: str, name: str, data: bytes) -> None:
    # The builder normalizes CRLF to LF before writing an asset; the same normalization of the
    # committed blob gives the exact bytes a release built from the tag commit carries.
    expected = tree.blob(path).replace(b"\r\n", b"\n")
    if data != expected:
        raise ArtifactRefused("anchor.asset_blob_mismatch",
                              f"{ascii(name)} ({sha256_bytes(data)}) is not {ascii(path)} at the tag commit ({sha256_bytes(expected)})")


def _check_toolchain(tree: _TagTree, manifest: dict) -> None:
    pins = strict_json(tree.blob(VERSIONS_PATH), "anchor.toolchain_mismatch", f"{VERSIONS_PATH} at the tag commit")
    try:
        generator = {"version": pins["openapi_generator"]["version"], "jar_sha256": pins["openapi_generator"]["sha256"]}
        expected = {"openapi_generator": generator, "spectral": pins["spectral"]["version"], "oasdiff": pins["oasdiff"]["version"]}
    except (KeyError, TypeError):
        raise ArtifactRefused("anchor.toolchain_mismatch",
                              f"{VERSIONS_PATH} at the tag commit lacks the generator, spectral or oasdiff pin") from None
    if manifest["toolchain"] != expected:
        raise ArtifactRefused("anchor.toolchain_mismatch", f"{RELEASE_MANIFEST} toolchain differs from {VERSIONS_PATH} at the tag commit")
    for language in pl_contracts.LANGUAGES:
        pins_used = manifest["clients"][language]["generator"]
        if {"version": pins_used["version"], "jar_sha256": pins_used["jar_sha256"]} != generator:
            raise ArtifactRefused("anchor.toolchain_mismatch",
                                  f"clients[{language}] generator is not the openapi-generator pinned in {VERSIONS_PATH} at the tag commit")


def _golden_shape_problem(record: object) -> str | None:
    if not isinstance(record, dict) or set(record) != GOLDEN_KEYS:
        return "is not a record of exactly tree_sha256, spec_version, spec_sha256, file_count and files"
    if not _is_hex64(record["tree_sha256"]) or not _is_hex64(record["spec_sha256"]) or not isinstance(record["spec_version"], str):
        return "has malformed digests or a non-string spec_version"
    if not _is_count(record["file_count"]) or not _is_digest_map(record["files"]):
        return "has a malformed file_count or files map"
    return golden_inconsistency(record)


def _check_golden(tree: _TagTree, spec_digest: str, version: str) -> dict[str, dict]:
    golden = strict_json(tree.blob(GOLDEN_PATH), "anchor.golden_invalid", f"{GOLDEN_PATH} at the tag commit")
    if not isinstance(golden, dict):
        raise ArtifactRefused("anchor.golden_invalid", f"{GOLDEN_PATH} at the tag commit is not a JSON object")
    records: dict[str, dict] = {}
    for language in pl_contracts.LANGUAGES:
        if language not in golden:
            raise ArtifactRefused("anchor.golden_invalid", f"{GOLDEN_PATH} at the tag commit has no {language} record")
        problem = _golden_shape_problem(golden[language])
        if problem is not None:
            raise ArtifactRefused("anchor.golden_invalid", f"{GOLDEN_PATH}[{language}] at the tag commit: {problem}")
        record = golden[language]
        if record["spec_sha256"] != spec_digest or record["spec_version"] != version:
            raise ArtifactRefused("anchor.golden_spec_mismatch",
                                  f"{GOLDEN_PATH}[{language}] at the tag commit pins specification {ascii(record['spec_version'])} "
                                  f"({record['spec_sha256']}); the release ships {version} ({spec_digest})")
        records[language] = record
    return records


# --- archives ----------------------------------------------------------------------------------------

def _gunzip_bounded(data: bytes, name: str) -> bytes:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as stream:
            raw = stream.read(MAX_ARCHIVE_TAR_BYTES + 1)
    except (OSError, EOFError, zlib.error):
        raise ArtifactRefused("archive.malformed", f"{name} is not a gzip-compressed archive") from None
    if len(raw) > MAX_ARCHIVE_TAR_BYTES:
        raise ArtifactRefused("archive.too_large", f"{name} expands beyond {MAX_ARCHIVE_TAR_BYTES} bytes")
    return raw


def _member_path_problem(name: str, prefix: str) -> tuple[str, str] | None:
    if not name or any(ord(character) < 0x20 or ord(character) == 0x7F for character in name):
        return "archive.unsafe_path", "a member name is empty or contains control characters"
    if "\\" in name or name.startswith("/"):
        return "archive.unsafe_path", f"member {ascii(name)} is absolute or uses a backslash separator"
    parts = name.split("/")
    if any(part in ("", ".", "..") or ":" in part for part in parts):
        return "archive.unsafe_path", f"member {ascii(name)} has an empty, '.', '..' or drive/stream component"
    if parts[0] != prefix or len(parts) < 2:
        return "archive.outside_prefix", f"member {ascii(name)} is not inside {prefix}/"
    return None


def inspect_archive_members(raw_tar: bytes, prefix: str, name: str) -> list[tarfile.TarInfo]:
    """Check every member of an uncompressed tar before anything is unpacked; return the members.

    Stricter than the data filter alone, which accepts `prefix/../x` while it stays inside the
    destination and accepts links that stay inside it. Data hidden after an unreadable header is
    refused, so this reader and GNU tar see the same members.
    """
    members: list[tarfile.TarInfo] = []
    folded: dict[str, str] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as archive:
            for member in archive:
                if len(members) >= MAX_ARCHIVE_MEMBERS:
                    raise ArtifactRefused("archive.too_many_members", f"{name} has more than {MAX_ARCHIVE_MEMBERS} members")
                problem = _member_path_problem(member.name, prefix)
                if problem is not None:
                    raise ArtifactRefused(problem[0], f"{name}: {problem[1]}")
                if member.type in LINK_TYPES:
                    raise ArtifactRefused("archive.link_member", f"{name}: member {ascii(member.name)} is a link")
                if member.type not in FILE_TYPES and member.type != tarfile.DIRTYPE:
                    raise ArtifactRefused("archive.special_member",
                                          f"{name}: member {ascii(member.name)} is not a regular file or directory")
                if member.type in FILE_TYPES and member.size > MAX_MEMBER_BYTES:
                    raise ArtifactRefused("archive.too_large", f"{name}: member {ascii(member.name)} exceeds {MAX_MEMBER_BYTES} bytes")
                key = member.name.casefold()
                if key in folded:
                    if folded[key] == member.name:
                        raise ArtifactRefused("archive.duplicate_member", f"{name}: member {ascii(member.name)} appears twice")
                    raise ArtifactRefused("archive.case_collision",
                                          f"{name}: members {ascii(folded[key])} and {ascii(member.name)} differ only in case")
                folded[key] = member.name
                members.append(member)
            # tarfile stops quietly at an unreadable header after the first member, where GNU tar skips
            # ahead and unpacks what follows; only end-of-archive zero padding may follow the last member.
            if raw_tar[archive.offset:].strip(b"\0"):
                raise ArtifactRefused("archive.malformed", f"{name} has data after its last readable member")
    except tarfile.TarError:
        raise ArtifactRefused("archive.malformed", f"{name} is not a readable tar archive") from None
    return members


def _embedded_manifest_problem(embedded: object) -> str | None:
    if not isinstance(embedded, dict):
        return "is not a JSON object"
    missing = sorted(EMBEDDED_MANIFEST_KEYS - set(embedded))
    if missing:
        return f"lacks {', '.join(missing)}"
    if type(embedded["schema_version"]) is not int or embedded["schema_version"] != 1:
        return "schema_version is not 1"
    if not isinstance(embedded["target"], str) or not isinstance(embedded["spec_version"], str):
        return "target and spec_version must be strings"
    if not all(_is_hex64(embedded[key]) for key in ("spec_sha256", "currency_registry_sha256", "runtime_sha256", "tree_sha256")):
        return "digests are not lowercase SHA-256"
    if not _is_count(embedded["file_count"]) or not _is_digest_map(embedded["provider_sources_sha256"]):
        return "file_count or provider_sources_sha256 is malformed"
    if not isinstance(embedded["generator"], dict):
        return "generator is not an object"
    files = embedded["files"]
    if not isinstance(files, list) or not all(
            isinstance(item, dict) and set(item) == {"path", "sha256"} and isinstance(item["path"], str) and _is_hex64(item["sha256"])
            for item in files):
        return "files is not a list of {path, sha256} records"
    return None


def _check_archive(language: str, tag: str, version: str, data: dict[str, bytes], sums: dict[str, str], manifest: dict,
                   golden: dict, work: Path) -> dict:
    name, prefix = archive_name(language, tag), archive_prefix(language, tag)
    raw = _gunzip_bounded(data[name], name)
    members = inspect_archive_members(raw, prefix, name)
    destination = work / language
    destination.mkdir()
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            archive.extractall(destination, members=members, filter="data")
    except (tarfile.TarError, OSError):
        raise ArtifactRefused("archive.malformed", f"{name} could not be unpacked under the data filter") from None
    root = destination / prefix
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ArtifactRefused("archive.manifest_missing", f"{name} has no {prefix}/{MANIFEST_NAME}")
    embedded = strict_json(manifest_path.read_bytes(), "archive.manifest_invalid", f"{name} {MANIFEST_NAME}")
    problem = _embedded_manifest_problem(embedded)
    if problem is not None:
        raise ArtifactRefused("archive.manifest_invalid", f"{name} {MANIFEST_NAME} {problem}")
    assert isinstance(embedded, dict)
    digest, entries = tree_hash(root, exclude=(MANIFEST_NAME,))
    recorded = [{"path": path, "sha256": file_digest} for path, file_digest in entries]
    if embedded["tree_sha256"] != digest or embedded["file_count"] != len(entries) or embedded["files"] != recorded:
        raise ArtifactRefused("archive.tree_mismatch",
                              f"{name} content (tree {digest}, {len(entries)} files) differs from its {MANIFEST_NAME} "
                              f"(tree {embedded['tree_sha256']}, {embedded['file_count']} files)")
    client = manifest["clients"][language]
    agreement = {
        "target": language, "spec_version": version, "spec_sha256": sums[spec_asset_name(tag)],
        "currency_registry_sha256": sums[PurePosixPath(REGISTRY_PATH).name],
        "provider_sources_sha256": manifest["provider_sources_sha256"], "generator": client["generator"],
        "runtime_sha256": client["runtime_sha256"], "tree_sha256": client["tree_sha256"], "file_count": client["file_count"],
    }
    for key, expected in agreement.items():
        if embedded[key] != expected:
            raise ArtifactRefused("archive.manifest_mismatch",
                                  f"{name} {MANIFEST_NAME} {key} disagrees with {RELEASE_MANIFEST} and the shipped assets")
    if digest != golden["tree_sha256"] or len(entries) != golden["file_count"]:
        current = dict(entries)
        differing = sorted(path for path in set(golden["files"]) | set(current) if golden["files"].get(path) != current.get(path))
        raise ArtifactRefused("anchor.golden_tree_mismatch",
                              f"{name} tree {digest} is not {GOLDEN_PATH}[{language}].tree_sha256 {golden['tree_sha256']} "
                              f"at the tag commit; differing paths: {', '.join(map(ascii, differing[:5]))}")
    return {"archive": name, "archive_sha256": sums[name], "tree_sha256": digest, "file_count": len(entries)}
