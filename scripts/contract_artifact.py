"""Load a published contract artifact and verify it against its annotated tag's own tree (T-QA-02).

A published contract version is an annotated tag plus the GitHub Release assets that
scripts/release.py builds for it: the specification, the currency registry, the provider sources,
the fixtures, one archive per generated client, release-manifest.json and SHA256SUMS
(docs/publication.md). The verification runner accepts a contract version only through this
loader, and refuses it unless every rule below holds. Each refusal carries one static rule ID;
docs/contract-verification.md lists them.

1. Tag. The name matches check_breaking_changes.TAG in full, refs/tags/<tag> is an annotated tag
   object that names itself <tag> and targets a commit, and the remote's refs/tags/<tag>
   (git ls-remote) is that same tag object. The remote must fetch from the canonical
   PenniLogic/contracts repository (its URL after insteadOf rewriting). That is what "published"
   means here. Publication is trust on first use: a caller may pin the tag object it verified
   before. When this checkout has the remote main's commit and full history, the tag commit must be
   on that main; otherwise that check is reported not_run.
2. Release set. The directory holds exactly the assets release.build_release_set produces for the
   tag, plus SHA256SUMS: the provider-source names and client languages come from the tag's own
   scripts/pl_contracts.py (read without running it), the fixture names from the tag's tree.
   SHA256SUMS is in its canonical form, lists exactly those assets, and every digest matches the bytes.
3. Manifest. release-manifest.json has exactly the builder's keys. It agrees with the tag (tag,
   version, commit), with SHA256SUMS and with the info.version of the specification it ships.
4. Tag-tree anchors. The specification, registry, provider-source and fixture assets equal their
   blobs at the tag commit, before any of them is parsed. The toolchain pins equal
   toolchain/versions.json there. Each client's generator name, configuration path and digest,
   ignore-override digest and runtime digest are the values the tag's own generator/ and runtime/
   files give, and the publication text is the literal of the tag's scripts/release.py. The tag's
   generator/golden.json is self-consistent and records the shipped specification for every language.
5. Archives. Each client archive is one gzip member, decompressed with a size bound. A strict reader
   accepts only the tar subset release.deterministic_tar_gz writes, so that GNU tar, npm's node-tar
   and Python's tarfile read the same members: POSIX ustar headers with plain octal numbers, regular
   files and directories only, at most one extended header before a member, exactly as tarfile writes
   it (its own fields included) and holding one `path` record, nothing after the end-of-archive marker.
   The members must be the builder's canonical form (its modes, owner 0, one modification time,
   sorted names, directories exactly the parents of the files) with bounded names inside one prefix,
   made of the POSIX portable file name characters: no absolute or parent paths, no component Windows
   rewrites or reserves (a trailing dot, a device name), no links or devices, no duplicates or case
   collisions. tarfile must read the same members at the same offsets.
   The archive is then unpacked with the tarfile data filter. The tree digest is recomputed with
   pl_contracts.tree_hash, excluding contracts-manifest.json exactly as generate_clients.py does. It
   must equal the archive's own manifest, the release manifest and golden.json[language].tree_sha256
   at the tag.

The GitHub Release presence check needs the network and is held. The result reports it as not_run,
so a loaded artifact alone never claims that the Release exists. Git reads only the checkout apart from
that one `git ls-remote`: every git process runs with GIT_NO_LAZY_FETCH=1, and every one but ls-remote
with an empty GIT_ALLOW_PROTOCOL (no transport at all), so a partial clone that lacks the tag's files is
refused (repository.objects_missing) instead of fetching them from its promisor remote.
"""

from __future__ import annotations

import ast
import functools
import io
import os
import re
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
from contract_schema import StrictJsonError, parse_strict_json  # noqa: E402
from generate_clients import GOLDEN, IGNORE_OVERRIDE, MANIFEST_NAME, config_path, golden_inconsistency  # noqa: E402
from pl_contracts import PipelineError, combined_digest, sha256_bytes, spec_version, tree_hash  # noqa: E402

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
    "archive.tree_mismatch", "archive.manifest_mismatch", "anchor.golden_tree_mismatch", "archive.ambiguous",
    "archive.noncanonical", "release.format_unsupported", "remote.not_canonical", "tag.pin_mismatch",
    "tag.not_on_main", "anchor.manifest_metadata_mismatch", "repository.objects_missing",
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
# The other keys generate_clients.py writes. They need Node to recompute, so they are shape-checked only
# (docs/contract-verification.md lists them as not anchored).
EMBEDDED_OPTIONAL_KEYS = frozenset({"provider_constraints_sha256", "generation_examples", "generation_model_projection"})
# The generator record of contracts-manifest.json, copied into each release-manifest.json client.
GENERATOR_KEYS = frozenset({
    "name", "version", "jar_sha256", "generator_name", "config", "config_sha256", "ignore_override_sha256",
    "template_override_sha256",
})
GOLDEN_KEYS = frozenset({"tree_sha256", "spec_version", "spec_sha256", "file_count", "files"})

# Paths inside the tag's tree, from the same constants the release builder and the generator use.
SPEC_PATH = pl_contracts.SPEC.relative_to(pl_contracts.ROOT).as_posix()
SPEC_DIR = PurePosixPath(SPEC_PATH).parent.as_posix()
REGISTRY_PATH = pl_contracts.REGISTRY.relative_to(pl_contracts.ROOT).as_posix()
FIXTURES_PATH = pl_contracts.FIXTURES.relative_to(pl_contracts.ROOT).as_posix()
GOLDEN_PATH = GOLDEN.relative_to(pl_contracts.ROOT).as_posix()
VERSIONS_PATH = pl_contracts.VERSIONS.relative_to(pl_contracts.ROOT).as_posix()
# The module whose PROVIDER_SOURCE_NAMES and LANGUAGES literals release.build_release_set uses at the tag.
FORMAT_PATH = Path(pl_contracts.__file__).resolve().relative_to(pl_contracts.ROOT).as_posix()
# The builder at the tag (its publication literal) and the generator inputs the manifests describe.
RELEASE_SOURCE_PATH = (Path(__file__).resolve().parent / "release.py").relative_to(pl_contracts.ROOT).as_posix()
IGNORE_PATH = IGNORE_OVERRIDE.relative_to(pl_contracts.ROOT).as_posix()
CONFIG_PATHS = {language: config_path(language).relative_to(pl_contracts.ROOT).as_posix() for language in pl_contracts.LANGUAGES}
RUNTIME_PATHS = {language: (pl_contracts.RUNTIME_DIR / language).relative_to(pl_contracts.ROOT).as_posix()
                 for language in pl_contracts.LANGUAGES}
FORMAT_CONSTANTS = ("PROVIDER_SOURCE_NAMES", "LANGUAGES")
FORMAT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
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
# Names a refusal detail lists before it only counts the rest, and the characters it shows of one value.
MAX_LISTED_NAMES = 5
MAX_SHOWN_TEXT = 200

HEX64 = re.compile(r"[0-9a-f]{64}")
OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
REMOTE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
# The only repository a contract version is published from (docs/publication.md). Compared without
# credentials, case, a trailing slash or `.git`.
CANONICAL_REMOTE_URLS = (
    "https://github.com/PenniLogic/contracts.git",
    "git@github.com:PenniLogic/contracts.git",
    "ssh://git@github.com/PenniLogic/contracts.git",
)
MAIN_REF = "refs/heads/main"
SUMS_LINE = re.compile(r"([0-9a-f]{64})  ([^\x00-\x1f\x7f/\\]{1,255})")
REGULAR_BLOB_MODES = ("100644", "100755")
LINK_TYPES = (tarfile.SYMTYPE, tarfile.LNKTYPE)
# Variables that would point git at another repository, index or object store than the one given.
REPOSITORY_ENVIRONMENT = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE", "GIT_COMMON_DIR", "GIT_REPLACE_REF_BASE",
)
PASSED_CHECKS = (
    "tag.grammar", "tag.annotated", "remote.canonical_repository", "tag.published_remote_ref", "release.sha256sums_exact",
    "release.manifest_agreement", "anchor.tag_tree_blobs", "anchor.toolchain_pins", "anchor.manifest_metadata", "anchor.golden",
    "archive.canonical_form", "archive.safe_extraction", "archive.tree_digest",
)
# pass or not_run: tag.pinned_identity runs only with a pin; tag.on_remote_main only with the remote main's history.
CONDITIONAL_CHECKS = ("tag.pinned_identity", "tag.on_remote_main")
HELD_CHECKS = ("github_release.presence",)


class ArtifactRefused(Exception):
    """The artifact failed one rule. `rule` is a static ID from RULES; `detail` never echoes git stderr."""

    def __init__(self, rule: str, detail: str) -> None:
        if rule not in RULES:
            raise ValueError(f"unknown artifact rule {rule!r}")
        super().__init__(f"{rule}: {detail}")
        self.rule = rule
        self.detail = detail


def shown(value: object, limit: int = MAX_SHOWN_TEXT) -> str:
    """ascii(value) of a string or number for a refusal detail; a longer string is cut to `limit` characters before it
    is escaped, so a value from the release is never echoed or copied whole. A number is cut after its digits are
    written (the strict JSON reader refuses an integer of more than 4300 digits, Python's default limit)."""
    if isinstance(value, str):
        return f"{ascii(value[:limit])}... ({len(value)} characters)" if len(value) > limit else ascii(value)
    text = ascii(value)
    return text if len(text) <= limit else f"{text[:limit]}... ({len(text)} characters)"


def listed(names, limit: int = MAX_LISTED_NAMES) -> str:
    """Sorted names for a refusal detail (each as `shown`): at most `limit` of them and how many more, or 'none'.

    The names often come from the release (asset names, manifest keys), so a detail never echoes an
    unbounded list (review round 2, PSEC-R2-5: 400000 unknown manifest keys made a 6.4 MB refusal).
    """
    names = sorted(names)
    text = ", ".join(shown(name) for name in names[:limit]) or "none"
    return text if len(names) <= limit else f"{text} and {len(names) - limit} more"


@dataclass(frozen=True)
class PublishedArtifact:
    """A verified published artifact. Every check passed, except the held GitHub Release presence check and
    the conditional checks reported not_run."""

    tag: str
    version: str
    tag_object: str
    commit: str
    remote: str
    remote_url: str
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
            "remote": self.remote, "remote_url": self.remote_url, "spec_asset": self.spec_asset, "spec_sha256": self.spec_sha256,
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


def expected_asset_sources(tag: str, fixture_names: list[str], provider_names: tuple[str, ...] | None = None) -> dict[str, str]:
    """Map every asset name release.build_release_set writes for `tag` to its source.

    `provider_names` are the tag's own provider-source names (default: this runner's). The source is a
    path in the tag's tree, `archive:<language>` or `release:manifest`. The builder writes every asset
    into one flat directory, so two sources with one name would silently overwrite each other; that is
    refused instead.
    """
    sources: dict[str, str] = {}

    def add(name: str, source: str) -> None:
        if name in sources and sources[name] != source:
            raise ArtifactRefused("anchor.asset_name_collision",
                                  f"{ascii(name)} would be written from both {ascii(sources[name])} and {ascii(source)}")
        sources[name] = source

    add(spec_asset_name(tag), SPEC_PATH)
    add(PurePosixPath(REGISTRY_PATH).name, REGISTRY_PATH)
    for name in pl_contracts.PROVIDER_SOURCE_NAMES if provider_names is None else provider_names:
        add(name, f"{SPEC_DIR}/{name}")
    for name in fixture_names:
        add(name, f"{FIXTURES_PATH}/{name}")
    for language in pl_contracts.LANGUAGES:
        add(archive_name(language, tag), f"{ARCHIVE_SOURCE}{language}")
    add(RELEASE_MANIFEST, MANIFEST_SOURCE)
    return sources


def load_published_artifact(tag: str, release_dir: Path, *, repo: Path = pl_contracts.ROOT, remote: str = "origin",
                            scratch_dir: Path | None = None, expect_tag_object: str | None = None,
                            accepted_remote_urls: tuple[str, ...] | None = None) -> PublishedArtifact:
    """Verify the downloaded Release assets in `release_dir` against `tag` in `repo`; raise ArtifactRefused.

    `expect_tag_object` pins the tag object a consumer verified before (trust on first use otherwise).
    `accepted_remote_urls` replaces CANONICAL_REMOTE_URLS for tests with a temporary origin. Archives
    are unpacked into a temporary directory (under `scratch_dir` when given) that is removed before
    this function returns; nothing is written anywhere else.
    """
    if not isinstance(tag, str) or TAG.fullmatch(tag) is None:
        raise ArtifactRefused("tag.grammar", f"{ascii(tag)} is not a published version tag vMAJOR.MINOR.PATCH")
    version = tag[1:]
    repo, release_dir = Path(repo), Path(release_dir)
    git = _Git(repo, release_dir)
    top_level, shallow = _check_repository(git, repo)
    _check_release_dir(release_dir, top_level)
    remote_url = _check_remote(git, remote, CANONICAL_REMOTE_URLS if accepted_remote_urls is None else accepted_remote_urls)
    tag_object, commit = _resolve_annotated_tag(git, tag)
    pinned = _check_pin(tag, tag_object, expect_tag_object)
    main = _check_published(git, remote, tag, tag_object, commit)
    on_main = _check_on_main(git, commit, main, shallow)
    tree = _TagTree(git, commit)
    provider_names = tree.release_format()
    sources = expected_asset_sources(tag, tree.fixture_names(), provider_names)
    for path in sorted({source for source in sources.values() if is_tag_path(source)} | {GOLDEN_PATH, VERSIONS_PATH}):
        tree.require_regular_file(path)
    sums, data = _read_release_set(release_dir, sources)
    manifest = _check_manifest(data[RELEASE_MANIFEST], sums, tag, version, commit, provider_names)
    spec_name = spec_asset_name(tag)
    # Anchor every asset to its blob before anything parses it: an unanchored specification could make
    # the info.version expression backtrack for hours (review round 1, PSEC-4).
    for name, source in sorted(sources.items()):
        if is_tag_path(source):
            _check_blob(tree, source, name, data[name])
    _check_spec_version(data[spec_name], version)
    _check_toolchain(tree, manifest)
    _check_manifest_metadata(tree, manifest)
    golden = _check_golden(tree, sums[spec_name], version)
    clients: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="pl-artifact-", dir=None if scratch_dir is None else str(scratch_dir)) as work:
        for language in pl_contracts.LANGUAGES:
            clients[language] = _check_archive(language, tag, version, data, sums, manifest, golden[language], Path(work))
    return PublishedArtifact(
        tag=tag, version=version, tag_object=tag_object, commit=commit, remote=remote, remote_url=remote_url,
        release_dir=release_dir, spec_asset=spec_name, spec_sha256=sums[spec_name], manifest_sha256=sums[RELEASE_MANIFEST],
        assets=dict(sorted(sums.items())), clients=clients,
        checks=(tuple((check, "pass") for check in PASSED_CHECKS) + tuple(zip(CONDITIONAL_CHECKS, (pinned, on_main)))
                + tuple((check, "not_run") for check in HELD_CHECKS)),
    )


# --- git ---------------------------------------------------------------------------------------------

GIT_EXECUTABLE = "git.exe" if os.name == "nt" else "git"
# The environment of every git process: no prompt, no replace objects, literal pathspecs, the C locale, and no lazy
# fetching. A partial clone (`git clone --filter=blob:none`, common in CI checkouts) lacks the tag's files; git would
# fetch each missing object from the clone's promisor remote, which can be another remote than the one verified (a
# mirror), and write packs into the checkout (review round 3, PSEC-R3-1). git 2.45 and later honour GIT_NO_LAZY_FETCH.
GIT_ENVIRONMENT = {
    "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_LITERAL_PATHSPECS": "1", "GIT_NO_LAZY_FETCH": "1",
    "LC_ALL": "C", "LANGUAGE": "C",
}
# Added for every git process except `git ls-remote`: an empty transport allow list, so even a git older than 2.45,
# which ignores GIT_NO_LAZY_FETCH, cannot fetch (git reads GIT_ALLOW_PROTOCOL as a colon-separated allow list).
OFFLINE_ENVIRONMENT = {"GIT_ALLOW_PROTOCOL": ""}
# Why the checkout cannot provide an object of the tag commit that it lists.
OBJECTS_MISSING = ("the checkout lacks objects of the tag commit, as a partial clone (git clone --filter=...) does, or its "
                   "object store is damaged; the loader never fetches objects, so verify from a full clone")


def _resolved(path: str | Path) -> Path | None:
    """The absolute path with its symlinks resolved, or None when it cannot be resolved (an embedded NUL, say)."""
    try:
        return Path(os.path.abspath(path)).resolve()
    except (OSError, ValueError):
        return None


def trusted_git(release_dir: Path | None = None) -> str | None:
    """The first git executable in an absolute PATH entry, never one inside `release_dir`; None if there is none.

    shutil.which also searches an empty, '.' or relative PATH entry, which stands for the current
    directory, and on Windows it searches the current directory first. The current directory can be the
    downloaded release directory (`gh release download` without --dir), whose files anyone who can
    replace a Release asset controls, and git runs before that directory is checked (review round 2,
    PSEC-R2-4). So only absolute entries are searched, a git that resolves into the release directory is
    skipped, and on Windows only git.exe is taken, never a .bat or .cmd file. A release directory that
    cannot be resolved holds no git; the loader refuses it as release_dir.missing.
    """
    path = os.environ.get("PATH")
    excluded = None if release_dir is None else _resolved(release_dir)
    for entry in (os.defpath if path is None else path).split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            continue
        candidate = Path(os.path.abspath(os.path.join(entry, GIT_EXECUTABLE)))
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        resolved = _resolved(candidate)
        if resolved is None or (excluded is not None and (resolved == excluded or excluded in resolved.parents)):
            continue
        return str(candidate)
    return None


class _Git:
    """Plumbing commands against exactly one repository, with a trusted executable and bounded time.

    Only `git ls-remote` (run with network=True) may use a transport; every other command reads the checkout alone.
    """

    def __init__(self, repo: Path, release_dir: Path | None = None) -> None:
        executable = trusted_git(release_dir)
        if executable is None:
            raise ArtifactRefused("git.unavailable", "git was not found in an absolute PATH entry outside the release directory")
        self.executable = executable
        self.repo = repo
        environment = {key: value for key, value in os.environ.items() if key not in REPOSITORY_ENVIRONMENT}
        environment.update(GIT_ENVIRONMENT)
        self.environment = environment
        self.offline_environment = {**environment, **OFFLINE_ENVIRONMENT}

    def run(self, *args: str, input_data: bytes = b"", network: bool = False) -> subprocess.CompletedProcess[bytes] | None:
        """Return the completed process, or None when git could not start or exceeded the time bound."""
        try:
            return subprocess.run(
                [self.executable, "-C", str(self.repo), *args], input=input_data, capture_output=True,
                env=self.environment if network else self.offline_environment, timeout=GIT_TIMEOUT_SECONDS, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None


def _outcome(completed: subprocess.CompletedProcess[bytes] | None) -> str:
    return "no exit status (start failure or timeout)" if completed is None else f"exit {completed.returncode}"


def _read_objects(git: _Git, names: list[str], rule: str, what: str, why: str = "") -> list[tuple[str, str, bytes] | None]:
    """Read objects with one `git cat-file --batch`: (object ID, type, content), or None for a missing name.

    A refusal names `what` and then `why`, when given.
    """
    completed = git.run("cat-file", "--batch", input_data="".join(f"{name}\n" for name in names).encode("utf-8"))
    if completed is None or completed.returncode != 0:
        raise ArtifactRefused(rule, f"{what} cannot be read ({_outcome(completed)}){why}")
    output, position = completed.stdout, 0
    objects: list[tuple[str, str, bytes] | None] = []
    for name in names:
        newline = output.find(b"\n", position)
        if newline < 0:
            raise ArtifactRefused(rule, f"{what}: git returned a truncated object stream{why}")
        header = output[position:newline].decode("utf-8", "replace")
        position = newline + 1
        if header == f"{name} missing":
            objects.append(None)
            continue
        fields = header.split(" ")
        if len(fields) != 3 or OBJECT_ID.fullmatch(fields[0]) is None or not fields[2].isdigit():
            raise ArtifactRefused(rule, f"{what}: git returned an unexpected object header{why}")
        size = int(fields[2])
        content = output[position:position + size]
        if len(content) != size or output[position + size:position + size + 1] != b"\n":
            raise ArtifactRefused(rule, f"{what}: git returned a truncated object stream{why}")
        objects.append((fields[0], fields[1], content))
        position += size + 1
    return objects


def _check_repository(git: _Git, repo: Path) -> tuple[Path, bool]:
    """The checkout's top level, and whether its history may be incomplete (shallow, or not known to be complete)."""
    completed = git.run("rev-parse", "--show-toplevel", "--is-shallow-repository")
    lines = [] if completed is None else completed.stdout.decode("utf-8", "surrogateescape").splitlines()
    if completed is None or completed.returncode != 0 or len(lines) != 2:
        raise ArtifactRefused("repository.invalid", "the repository path is not a git checkout")
    top_level = Path(lines[0].strip()).resolve()
    if top_level != repo.resolve():
        raise ArtifactRefused("repository.invalid", "the repository path must be the top level of a git checkout")
    return top_level, lines[1].strip() != "false"


def _check_release_dir(release_dir: Path, top_level: Path) -> None:
    if not release_dir.is_dir():
        raise ArtifactRefused("release_dir.missing", "the release directory does not exist")
    resolved = release_dir.resolve()
    if resolved == top_level or top_level in resolved.parents:
        raise ArtifactRefused("release_dir.inside_repository",
                              "the release directory is inside the repository checkout; verify downloaded Release "
                              "assets, never a working-tree build such as build/dist")


def without_credentials(url: str) -> str:
    """The URL without a query, a fragment or credentials: HTTP(S) user information, or any user:password.

    An ssh user without a password (`ssh://git@host/...`, scp-like `git@host:path`) is not a credential.
    """
    url = url.strip().split("?", 1)[0].split("#", 1)[0]
    scheme, separator, rest = url.partition("://")
    if not separator:
        return url
    authority, slash, path = rest.partition("/")
    user, at, host = authority.rpartition("@")
    if at and (scheme.lower() in ("http", "https") or ":" in user):
        authority = host
    return f"{scheme}://{authority}{slash}{path}"


def _url_key(url: str) -> str:
    key = without_credentials(url).rstrip("/").lower()
    return key[:-4] if key.endswith(".git") else key


def _check_remote(git: _Git, remote: str, accepted: tuple[str, ...]) -> str:
    """Return the remote's fetch URL without credentials; refuse a remote that is not the canonical repository.

    The URL is the one `git ls-remote` uses (`git remote get-url`, after url.<base>.insteadOf rewriting),
    so neither the checkout itself, a local mirror nor a rewrite stands in for the canonical repository.
    """
    if not isinstance(remote, str) or REMOTE_NAME.fullmatch(remote) is None:
        raise ArtifactRefused("remote.invalid", f"{ascii(remote)} is not a remote name")
    completed = git.run("remote", "get-url", remote)
    if completed is None or completed.returncode != 0 or not completed.stdout.strip():
        raise ArtifactRefused("remote.invalid", f"remote {remote} is not configured in the repository")
    url = completed.stdout.decode("utf-8", "replace").strip().splitlines()[0]
    if _url_key(url) not in {_url_key(candidate) for candidate in accepted}:
        raise ArtifactRefused("remote.not_canonical",
                              f"remote {remote} fetches from {ascii(without_credentials(url))}, not from the canonical "
                              "PenniLogic/contracts repository; a contract version is published only there")
    return without_credentials(url)


def _check_pin(tag: str, tag_object: str, expected: str | None) -> str:
    if expected is None:
        return "not_run"
    if not isinstance(expected, str) or OBJECT_ID.fullmatch(expected) is None or expected != tag_object:
        raise ArtifactRefused("tag.pin_mismatch", f"refs/tags/{tag} is the tag object {tag_object}, not the pinned "
                                                  f"{ascii(expected)}; the tag was re-created or the pin is wrong")
    return "pass"


def _check_on_main(git: _Git, commit: str, main: str | None, shallow: bool) -> str:
    """pass when the tag commit is on the remote's main; not_run when this checkout cannot decide it.

    Undecidable without fetching: the remote has no main, the checkout is shallow, or it lacks the
    remote main's commit (merge-base then exits 128). Refused when the full history shows the tag
    commit is not on main.
    """
    if main is None or shallow:
        return "not_run"
    ancestry = git.run("merge-base", "--is-ancestor", commit, main)
    if ancestry is not None and ancestry.returncode == 1:
        raise ArtifactRefused("tag.not_on_main", f"the tag commit {commit} is not on the remote's main ({main}); a "
                                                 "contract version is cut from the reviewed main (docs/publication.md)")
    return "pass" if ancestry is not None and ancestry.returncode == 0 else "not_run"


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


def _check_published(git: _Git, remote: str, tag: str, tag_object: str, commit: str) -> str | None:
    """Refuse a tag the remote does not carry as this tag object; return the remote main's commit, if any."""
    ref = f"refs/tags/{tag}"
    completed = git.run("ls-remote", remote, ref, MAIN_REF, network=True)
    if completed is None or completed.returncode != 0:
        raise ArtifactRefused("tag.remote_unreachable",
                              f"git ls-remote {remote} failed ({_outcome(completed)}); publication is unknown")
    advertised: dict[str, str] = {}
    for line in completed.stdout.decode("utf-8", "replace").splitlines():
        object_id, _, name = line.partition("\t")
        if name in (ref, f"{ref}^{{}}", MAIN_REF):
            advertised[name] = object_id
    if ref not in advertised:
        raise ArtifactRefused("tag.unpublished", f"{remote} does not carry {ref}; the tag was never pushed")
    if advertised[ref] != tag_object:
        raise ArtifactRefused("tag.remote_mismatch",
                              f"{remote} {ref} is {ascii(advertised[ref])}, the local annotated tag is {tag_object}")
    peeled = advertised.get(f"{ref}^{{}}")
    if peeled is not None and peeled != commit:
        raise ArtifactRefused("tag.remote_mismatch", f"{remote} {ref} peels to {ascii(peeled)}, not {commit}")
    main = advertised.get(MAIN_REF)
    return main if main is not None and OBJECT_ID.fullmatch(main) else None


class _TagTree:
    """The files of the tag commit that the release set and its manifests are built from.

    Every regular file under these paths is read with one git process when the tree is listed. A path the tree
    does not list as a regular file is anchor.tag_file_missing; an object the tree lists but the checkout cannot
    provide is repository.objects_missing, because git never fetches it here.
    """

    def __init__(self, git: _Git, commit: str) -> None:
        self.git = git
        self.commit = commit
        pathspecs = sorted({SPEC_DIR, PurePosixPath(REGISTRY_PATH).parent.as_posix(), FIXTURES_PATH, GOLDEN_PATH, VERSIONS_PATH,
                            FORMAT_PATH, RELEASE_SOURCE_PATH, IGNORE_PATH, *CONFIG_PATHS.values(), *RUNTIME_PATHS.values()})
        completed = git.run("ls-tree", "-r", "-z", "--full-tree", commit, "--", *pathspecs)
        if completed is None or completed.returncode != 0:
            raise ArtifactRefused("repository.objects_missing",
                                  f"the tree of the tag commit {commit} cannot be listed ({_outcome(completed)}): {OBJECTS_MISSING}")
        self.entries: dict[str, tuple[str, str, str]] = {}
        for record in completed.stdout.split(b"\0"):
            if record:
                meta, _, path = record.partition(b"\t")
                mode, kind, object_id = meta.decode("ascii", "replace").split(" ", 2)
                self.entries[path.decode("utf-8", "surrogateescape")] = (mode, kind, object_id)
        self.contents: dict[str, bytes] = {}
        self.read(sorted(path for path, (mode, kind, _) in self.entries.items() if kind == "blob" and mode in REGULAR_BLOB_MODES))

    def fixture_names(self) -> list[str]:
        """Direct `*.json` children of the fixtures directory: the set the builder's glob selects."""
        prefix = f"{FIXTURES_PATH}/"
        return sorted(path[len(prefix):] for path in self.entries
                      if path.startswith(prefix) and "/" not in path[len(prefix):] and path.endswith(".json"))

    def release_format(self) -> tuple[str, ...]:
        """The tag's provider-source names, from the literals in its own scripts/pl_contracts.py (never run).

        Refuse a tag that declares no such literals or other client languages than this runner verifies.
        """
        entry = self.entries.get(FORMAT_PATH)
        if entry is None or entry[1] != "blob" or entry[0] not in REGULAR_BLOB_MODES:
            raise ArtifactRefused("release.format_unsupported", f"the tag commit has no {FORMAT_PATH} declaring its release format")
        values = _literal_constants(self.blob(FORMAT_PATH), FORMAT_CONSTANTS)
        providers, languages = values.get("PROVIDER_SOURCE_NAMES"), values.get("LANGUAGES")
        if (not isinstance(languages, tuple) or len(set(languages)) != len(languages)
                or set(languages) != set(pl_contracts.LANGUAGES)):
            raise ArtifactRefused("release.format_unsupported",
                                  f"{FORMAT_PATH} at the tag commit does not declare the client languages "
                                  f"{', '.join(pl_contracts.LANGUAGES)} as one literal; this runner verifies exactly those")
        if (not isinstance(providers, tuple) or not providers or len(set(providers)) != len(providers)
                or not all(isinstance(name, str) and FORMAT_NAME.fullmatch(name) for name in providers)):
            raise ArtifactRefused("release.format_unsupported",
                                  f"{FORMAT_PATH} at the tag commit does not declare PROVIDER_SOURCE_NAMES as one literal "
                                  "tuple of distinct plain file names")
        return providers

    def require_regular_file(self, path: str) -> str:
        entry = self.entries.get(path)
        if entry is None or entry[1] != "blob" or entry[0] not in REGULAR_BLOB_MODES:
            raise ArtifactRefused("anchor.tag_file_missing", f"{ascii(path)} is not a regular file at the tag commit {self.commit}")
        return entry[2]

    def read(self, paths: list[str]) -> None:
        """Read the listed files of the tag commit with one git process, in order; refuse a missing one."""
        object_ids = {path: self.require_regular_file(path) for path in paths}
        if not object_ids:
            return
        unique = sorted(set(object_ids.values()))
        what = f"the files of the tag commit {self.commit}"
        objects = dict(zip(unique, _read_objects(self.git, unique, "repository.objects_missing", what, f": {OBJECTS_MISSING}")))
        for path, object_id in object_ids.items():
            entry = objects[object_id]
            if entry is None or entry[1] != "blob":
                raise ArtifactRefused("repository.objects_missing",
                                      f"{ascii(path)} at the tag commit {self.commit} cannot be read: {OBJECTS_MISSING}")
            if len(entry[2]) > MAX_TAG_BLOB_BYTES:
                raise ArtifactRefused("asset.too_large", f"{ascii(path)} at the tag commit exceeds {MAX_TAG_BLOB_BYTES} bytes")
            self.contents[path] = entry[2]

    def blob(self, path: str) -> bytes:
        if path not in self.contents:
            self.read([path])
        return self.contents[path]

    def digest_under(self, directory: str) -> str:
        """pl_contracts.tree_hash of a directory of the tag commit, computed from its blobs."""
        prefix = f"{directory}/"
        return combined_digest({path[len(prefix):]: sha256_bytes(self.blob(path)) for path in sorted(self.entries)
                                if path.startswith(prefix)})


@functools.lru_cache(maxsize=16)
def _parsed_module(source: bytes) -> ast.Module | None:
    """Python source parsed (never run), cached by content; None when it does not parse."""
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None


def _literal_constants(source: bytes, names: tuple[str, ...]) -> dict[str, object]:
    """The module-level literal values of `names` in Python source, parsed and never run.

    A name assigned twice, or to something other than a literal, has no value here.
    """
    module = _parsed_module(source)
    if module is None:
        return {}
    values: dict[str, object] = {}
    assigned: set[str] = set()
    for statement in module.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target, value = statement.targets[0], statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            target, value = statement.target, statement.value
        else:
            continue
        if not isinstance(target, ast.Name) or target.id not in names:
            continue
        if target.id in assigned:
            values.pop(target.id, None)
            continue
        assigned.add(target.id)
        try:
            values[target.id] = ast.literal_eval(value)
        except (ValueError, TypeError, SyntaxError, RecursionError, MemoryError):
            pass
    return values


@functools.lru_cache(maxsize=16)
def _publication_literal(source: bytes) -> str | None:
    """The literal `publication` value of the release manifest that a release.py writes, parsed and never run."""
    module = _parsed_module(source)
    if module is None:
        return None
    values: list[object] = []
    for node in ast.walk(module):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "publication":
                    try:
                        values.append(ast.literal_eval(value))
                    except (ValueError, TypeError, SyntaxError, RecursionError, MemoryError):
                        return None
    return values[0] if len(values) == 1 and isinstance(values[0], str) else None


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
        raise ArtifactRefused("sums.missing_entry", f"SHA256SUMS does not list {listed(missing)}")
    extra = sorted(set(sums) - set(sources))
    if extra:
        raise ArtifactRefused("sums.extra_entry", f"SHA256SUMS lists assets the tag does not produce: {listed(extra)}")
    absent = sorted(set(sources) - set(present))
    if absent:
        raise ArtifactRefused("asset.missing", f"the release directory lacks {listed(absent)}")
    unexpected = sorted(set(present) - set(sources) - {SUMS})
    if unexpected:
        raise ArtifactRefused("asset.unexpected",
                              f"the release directory holds files that are not Release assets: {listed(unexpected)}")
    data: dict[str, bytes] = {}
    for name in sorted(sources):
        data[name] = _read_bounded(present[name], name)
        if sha256_bytes(data[name]) != sums[name]:
            raise ArtifactRefused("sums.digest_mismatch", f"{ascii(name)} does not match its SHA256SUMS digest")
    return sums, data


# --- strict JSON shapes ------------------------------------------------------------------------------

def strict_json(data: bytes, rule: str, what: str) -> object:
    """Parse UTF-8 JSON without a byte order mark, duplicate keys or non-finite numbers (contract_schema)."""
    try:
        return parse_strict_json(data)
    except StrictJsonError as error:
        raise ArtifactRefused(rule, f"{what} {error}") from None


def _is_hex64(value: object) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _is_count(value: object) -> bool:
    return type(value) is int and value >= 0


def _is_digest_map(value: object) -> bool:
    return isinstance(value, dict) and all(_is_hex64(item) for item in value.values())


def _client_shape_problem(language: str, client: object) -> str | None:
    """Why one release-manifest.json client record is malformed, or None. `language` is a key of the release's
    manifest, so it is shown cut to its bound (review round 3, PSEC-R3-2: a 4000000-character key was echoed whole)."""
    if not isinstance(client, dict) or set(client) != CLIENT_KEYS:
        return f"clients[{shown(language)}] keys differ from the release builder's"
    if not isinstance(client["archive"], str) or not all(
            _is_hex64(client[key]) for key in ("archive_sha256", "tree_sha256", "runtime_sha256")):
        return f"clients[{shown(language)}] archive name or digests are malformed"
    if not _is_count(client["file_count"]) or not _is_digest_map(client["provider_sources_sha256"]):
        return f"clients[{shown(language)}] file_count or provider_sources_sha256 is malformed"
    problem = _generator_record_problem(client["generator"])
    if problem is not None:
        return f"clients[{shown(language)}] generator {problem}"
    return None


def _generator_record_problem(record: object) -> str | None:
    """Why a contracts-manifest.json generator record (copied into release-manifest.json) is malformed, or None."""
    if not isinstance(record, dict) or set(record) != GENERATOR_KEYS:
        return "keys differ from the generator record generate_clients.py writes"
    if not all(isinstance(record[key], str) for key in ("name", "version", "generator_name", "config")):
        return "name, version, generator_name and config must be strings"
    if not all(_is_hex64(record[key]) for key in ("jar_sha256", "config_sha256", "ignore_override_sha256")) or not (
            record["template_override_sha256"] is None or _is_hex64(record["template_override_sha256"])):
        return "digests are not lowercase SHA-256"
    return None


def _manifest_shape_problem(manifest: object) -> str | None:
    if not isinstance(manifest, dict):
        return "is not a JSON object"
    if set(manifest) != RELEASE_MANIFEST_KEYS:
        return (f"keys differ from the release builder's (missing {listed(RELEASE_MANIFEST_KEYS - set(manifest))}; "
                f"unknown {listed(set(manifest) - RELEASE_MANIFEST_KEYS)})")
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


def _check_manifest(data: bytes, sums: dict[str, str], tag: str, version: str, commit: str,
                    provider_names: tuple[str, ...]) -> dict:
    manifest = strict_json(data, "manifest.malformed", RELEASE_MANIFEST)
    problem = _manifest_shape_problem(manifest)
    if problem is not None:
        raise ArtifactRefused("manifest.malformed", f"{RELEASE_MANIFEST} {problem}")
    assert isinstance(manifest, dict)
    expected_files = {name: digest for name, digest in sums.items() if name != RELEASE_MANIFEST}
    if manifest["files"] != expected_files:
        differing = [name for name in set(expected_files) | set(manifest["files"])
                     if expected_files.get(name) != manifest["files"].get(name)]
        raise ArtifactRefused("manifest.files_mismatch", f"{RELEASE_MANIFEST} files disagree with SHA256SUMS for {listed(differing)}")
    if manifest["tag"] != tag:
        raise ArtifactRefused("manifest.tag_mismatch", f"{RELEASE_MANIFEST} names tag {shown(manifest['tag'])}, not {tag}")
    if manifest["spec_version"] != version:
        raise ArtifactRefused("manifest.version_mismatch",
                              f"{RELEASE_MANIFEST} spec_version {shown(manifest['spec_version'])} is not the tag version {version}")
    if manifest["commit"] != commit:
        raise ArtifactRefused("manifest.commit_mismatch",
                              f"{RELEASE_MANIFEST} was built from {manifest['commit']}, the tag commit is {commit}")
    spec_name = spec_asset_name(tag)
    if manifest["spec_sha256"] != sums[spec_name]:
        raise ArtifactRefused("manifest.spec_digest_mismatch", f"{RELEASE_MANIFEST} spec_sha256 is not the digest of {spec_name}")
    providers = manifest["provider_sources_sha256"]
    if providers != {name: sums[name] for name in provider_names}:
        raise ArtifactRefused("manifest.provider_sources_mismatch",
                              f"{RELEASE_MANIFEST} provider_sources_sha256 is not the digest map of {listed(provider_names)}")
    clients = manifest["clients"]
    if set(clients) != set(pl_contracts.LANGUAGES):
        raise ArtifactRefused("manifest.clients_mismatch",
                              f"{RELEASE_MANIFEST} clients {listed(clients)} are not {', '.join(sorted(pl_contracts.LANGUAGES))}")
    for language in pl_contracts.LANGUAGES:
        client, name = clients[language], archive_name(language, tag)
        if client["archive"] != name:
            raise ArtifactRefused("manifest.clients_mismatch", f"clients[{language}] archive is {shown(client['archive'])}, not {name}")
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


def _check_manifest_metadata(tree: _TagTree, manifest: dict) -> None:
    """The publication text, generator configuration, ignore override and runtime the tag's own files give.

    generator.name, template_override_sha256 and the embedded provider-constraint and generation records
    are not anchored here (docs/contract-verification.md says why); the generated code they describe is
    anchored by golden.json.
    """
    publication = _publication_literal(tree.blob(RELEASE_SOURCE_PATH))
    if publication is None:
        raise ArtifactRefused("release.format_unsupported",
                              f"{RELEASE_SOURCE_PATH} at the tag commit does not write one literal publication text")
    if manifest["publication"] != publication:
        raise ArtifactRefused("anchor.manifest_metadata_mismatch",
                              f"{RELEASE_MANIFEST} publication is not the text {RELEASE_SOURCE_PATH} writes at the tag commit")
    ignore = sha256_bytes(tree.blob(IGNORE_PATH).replace(b"\r\n", b"\n"))
    for language in pl_contracts.LANGUAGES:
        configuration = tree.blob(CONFIG_PATHS[language])
        parsed = strict_json(configuration, "release.format_unsupported", f"{CONFIG_PATHS[language]} at the tag commit")
        if not isinstance(parsed, dict) or not isinstance(parsed.get("generatorName"), str):
            raise ArtifactRefused("release.format_unsupported",
                                  f"{CONFIG_PATHS[language]} at the tag commit has no generatorName string")
        expected = {
            "generator_name": parsed["generatorName"], "config": CONFIG_PATHS[language],
            "config_sha256": sha256_bytes(configuration.replace(b"\r\n", b"\n")), "ignore_override_sha256": ignore,
        }
        client = manifest["clients"][language]
        differing = [key for key, value in expected.items() if client["generator"][key] != value]
        if client["runtime_sha256"] != tree.digest_under(RUNTIME_PATHS[language]):
            differing.append("runtime_sha256")
        if differing:
            raise ArtifactRefused("anchor.manifest_metadata_mismatch",
                                  f"clients[{language}] {', '.join(differing)}: not the values {CONFIG_PATHS[language]}, "
                                  f"{IGNORE_PATH} and {RUNTIME_PATHS[language]}/ at the tag commit give")


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
#
# Every reader of a client archive must see the tree that was verified. Python's tarfile is lenient where
# other readers (GNU tar, npm's node-tar) are not: it skips by the header size and then applies a pax
# global `size`, keeps a GNU long name over a following pax `path`, stops quietly at an unreadable header
# and reads the next header right after a directory that declares data. node-tar in turn refuses a link name
# or an empty name in a pax extended header block and then falls back to the member's truncated ustar name.
# So a strict reader first accepts only the subset release.deterministic_tar_gz writes, every field of every
# header block included, and refuses everything else; tarfile must then read the same members at the same
# offsets before anything is unpacked.

TAR_BLOCK = tarfile.BLOCKSIZE
POSIX_MAGIC = b"ustar\x0000"
# (name, offset, length) of the numeric fields of a ustar header block, and (offset, length) of the others.
NUMERIC_FIELDS = (("mode", 100, 8), ("uid", 108, 8), ("gid", 116, 8), ("size", 124, 12), ("mtime", 136, 12))
NAME_FIELD, LINKNAME_FIELD, UNAME_FIELD, GNAME_FIELD = (0, 100), (157, 100), (265, 32), (297, 32)
DEVICE_FIELDS, PREFIX_FIELD, HEADER_PADDING = ((329, 8), (337, 8)), (345, 155), (500, 12)
# tarfile leaves the device numbers of files and directories empty in current Python versions and wrote
# zeros in earlier ones; both mean "no device" to every reader.
ZERO_DEVICE_FIELDS = (b"\0" * 8, b"0000000\0")
USTAR_NAME_LENGTH = 100
# The name field of the extended header block tarfile writes before a member whose name needs a `path` record.
PAX_HEADER_NAME = b"././@PaxHeader".ljust(USTAR_NAME_LENGTH, b"\0")
MEMBER_TYPES = (tarfile.REGTYPE, tarfile.DIRTYPE)
SPECIAL_TYPES = (tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE, tarfile.CONTTYPE)
# Bounds on member names; the real clients' longest path is under 130 characters and 10 components.
MAX_MEMBER_NAME_LENGTH = 1024
MAX_MEMBER_DEPTH = 64
MAX_MEMBER_COMPONENT_LENGTH = 255
# Member name components: the POSIX portable file name characters, which every real generated path uses.
# Windows rewrites or reserves what lies outside them, and the loader unpacks onto the platform's file
# system: it drops a trailing '.' or ' ' (`__init__.py.` is `__init__.py`), opens a device for CON, NUL,
# COM1 and the like (also with an extension), and resolves an 8.3 short name such as PENNIL~1 to another
# directory. So the unpacked tree a Windows verifier hashes could differ from what every other reader
# unpacks (review round 2, PSEC-R2-2).
PORTABLE_COMPONENT = re.compile(r"[A-Za-z0-9._-]+")
WINDOWS_DEVICE_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{digit}" for digit in "0123456789"),
                                  *(f"LPT{digit}" for digit in "0123456789")})


@dataclass(frozen=True)
class _Member:
    """One archive member as the strict reader sees it."""

    name: str
    kind: bytes
    size: int
    mtime: int
    data_offset: int


def _gunzip_bounded(data: bytes, name: str) -> bytes:
    """Decompress exactly one gzip member with a size bound; anything after that member is refused."""
    decompressor = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    try:
        raw = decompressor.decompress(data, MAX_ARCHIVE_TAR_BYTES + 1)
    except zlib.error:
        raise ArtifactRefused("archive.malformed", f"{name} is not a gzip-compressed archive") from None
    if len(raw) > MAX_ARCHIVE_TAR_BYTES:
        raise ArtifactRefused("archive.too_large", f"{name} expands beyond {MAX_ARCHIVE_TAR_BYTES} bytes")
    if not decompressor.eof:
        raise ArtifactRefused("archive.malformed", f"{name} is a truncated gzip stream")
    if decompressor.unused_data:
        raise ArtifactRefused("archive.ambiguous", f"{name} has data after its gzip member (another member or trailing "
                                                   "bytes), which gzip readers handle differently")
    return raw


def _member_path_problem(name: str, prefix: str) -> tuple[str, str] | None:
    if not name or not all(" " <= character <= "~" for character in name):
        return "archive.unsafe_path", "a member name is empty or not printable ASCII (a control or non-ASCII character)"
    if len(name) > MAX_MEMBER_NAME_LENGTH:
        return "archive.unsafe_path", f"a member name is longer than {MAX_MEMBER_NAME_LENGTH} characters"
    if "\\" in name or name.startswith("/"):
        return "archive.unsafe_path", f"member {ascii(name)} is absolute or uses a backslash separator"
    parts = name.split("/")
    if len(parts) > MAX_MEMBER_DEPTH or any(len(part) > MAX_MEMBER_COMPONENT_LENGTH for part in parts):
        return "archive.unsafe_path", (f"a member name has more than {MAX_MEMBER_DEPTH} components or a component longer "
                                       f"than {MAX_MEMBER_COMPONENT_LENGTH} characters")
    if any(part in ("", ".", "..") or ":" in part for part in parts):
        return "archive.unsafe_path", f"member {ascii(name)} has an empty, '.', '..' or drive/stream component"
    if any(PORTABLE_COMPONENT.fullmatch(part) is None or part.endswith(".")
           or part.partition(".")[0].upper() in WINDOWS_DEVICE_NAMES for part in parts):
        return "archive.unsafe_path", (f"member {ascii(name)} has a component outside the portable file name characters "
                                       "[A-Za-z0-9._-], ending in '.', or naming a Windows device (CON, NUL, COM1, ...)")
    if parts[0] != prefix or len(parts) < 2:
        return "archive.outside_prefix", f"member {ascii(name)} is not inside {prefix}/"
    return None


def _field(header: bytes, field: tuple[int, int]) -> bytes:
    start, length = field
    return header[start:start + length]


def _octal(field: bytes) -> int | None:
    """A numeric field as tarfile writes it: octal digits that fill the field, then one NUL."""
    digits = field[:-1]
    if field[-1:] != b"\0" or not digits or digits.strip(b"01234567"):
        return None
    return int(digits, 8)


def _text(field: bytes) -> tuple[bytes, bool]:
    """A NUL-terminated text field: its value, and whether only NUL bytes follow that value."""
    value, _, rest = field.partition(b"\0")
    return value, not rest.strip(b"\0")


def builder_mode(name: str, kind: bytes) -> int:
    """The mode release.deterministic_tar_gz gives a member."""
    if kind == tarfile.DIRTYPE:
        return 0o755
    path = PurePosixPath(name)
    return 0o755 if path.name == "gradlew" or path.suffix == ".sh" else 0o644


def _extended_path(records: bytes, where: str) -> str:
    """The value of an extended header that holds exactly one `path` record and nothing else."""
    position, keywords, value = 0, [], b""
    while position < len(records):
        space = records.find(b" ", position, position + 20)
        digits = records[position:space] if space > position else b""
        end = position + int(digits) if digits.isdigit() and not digits.startswith(b"0") else -1
        keyword, equals, value = records[space + 1:end - 1].partition(b"=")
        if end <= space or end > len(records) or records[end - 1:end] != b"\n" or not keyword or not equals:
            raise ArtifactRefused("archive.ambiguous", f"{where} holds a malformed extended header record, which readers "
                                                       "handle differently")
        keywords.append(keyword.decode("ascii", "replace")[:40])
        position = end
    if keywords != ["path"]:
        raise ArtifactRefused("archive.ambiguous", f"{where} holds extended header records other than one path "
                                                   f"({listed(keywords)}), which readers apply differently")
    try:
        return value.decode("ascii")
    except UnicodeDecodeError:
        raise ArtifactRefused("archive.unsafe_path", f"{where}: a member name is not printable ASCII") from None


def _extended_header_problem(header: bytes, numbers: dict[str, int | None]) -> str | None:
    """Why an extended header block is not the one release.deterministic_tar_gz writes, or None.

    tarfile writes the name ././@PaxHeader, mode, owner and modification time 0, no link or owner names, no
    device numbers and NUL header padding. Readers differ on anything else there: node-tar refuses a link
    name or an empty name in this block and then unpacks the member under its truncated ustar name, while
    tarfile and GNU tar use the `path` record (review round 2, PSEC-R2-1).
    """
    if _field(header, NAME_FIELD) != PAX_HEADER_NAME:
        return "with a name other than the builder's ././@PaxHeader"
    if any(numbers[field] for field in ("mode", "uid", "gid", "mtime")):
        return "with a mode, owner or modification time other than 0"
    if any(_field(header, field).strip(b"\0") for field in (LINKNAME_FIELD, UNAME_FIELD, GNAME_FIELD)):
        return "with a link name or an owner name"
    if any(_field(header, field) not in ZERO_DEVICE_FIELDS for field in DEVICE_FIELDS) or _field(header, HEADER_PADDING).strip(b"\0"):
        return "with device numbers or header padding the builder never writes"
    return None


def _member_header_problem(header: bytes, member: str, kind: bytes, full_name: str, extended: bool,
                           padding: bytes) -> str | None:
    """Why a readable member is not one release.deterministic_tar_gz writes, or None."""
    numbers = {field: _octal(header[start:start + length]) for field, start, length in NUMERIC_FIELDS}
    owners = [_text(_field(header, field)) for field in (UNAME_FIELD, GNAME_FIELD, LINKNAME_FIELD)]
    name_field, name_clean = _text(_field(header, NAME_FIELD))
    if numbers["mode"] != builder_mode(member, kind):
        return f"has mode {numbers['mode']:o}, not the builder's {builder_mode(member, kind):o}"
    if numbers["uid"] or numbers["gid"] or owners != [(b"", True)] * 3:
        return "has an owner other than 0 without names, or a link name"
    if any(_field(header, field) not in ZERO_DEVICE_FIELDS for field in DEVICE_FIELDS) or _field(header, HEADER_PADDING).strip(b"\0"):
        return "has device numbers or header padding the builder never writes"
    if not name_clean or (kind == tarfile.DIRTYPE) != full_name.endswith("/"):
        return "has a name field the builder never writes"
    if extended and (len(full_name) <= USTAR_NAME_LENGTH or name_field != full_name.encode("ascii")[:USTAR_NAME_LENGTH]):
        return "has an extended header path that the builder would not write for its ustar name"
    if padding.strip(b"\0"):
        return "has data in the padding after its content"
    return None


def _strict_members(raw: bytes, prefix: str, name: str) -> list[_Member]:
    """Read an uncompressed tar as the subset release.deterministic_tar_gz writes; refuse anything else.

    The subset: POSIX ustar headers with a valid checksum, plain octal numbers and no prefix field;
    regular files and directories only; at most one extended header, directly before a member, whose
    own block is the one tarfile writes (name ././@PaxHeader, mode, owner and time 0, no link, owner or
    device fields) and whose data is exactly one `path` record; NUL padding; a two-block end-of-archive
    marker with nothing after it. The members are in the builder's canonical form: portable names inside
    one prefix, the builder's modes, owner 0 without names, sorted names, one shared modification time,
    and directories that are exactly the parents of the files.
    """
    members: list[_Member] = []
    folded: dict[str, str] = {}
    offset, path, path_offset = 0, None, 0
    while True:
        header = raw[offset:offset + TAR_BLOCK]
        where = f"{name}: the header at byte {offset}"
        if len(header) < TAR_BLOCK:
            raise ArtifactRefused("archive.malformed", f"{name} ends without an end-of-archive marker")
        if not header.strip(b"\0"):
            if path is not None:
                raise ArtifactRefused("archive.ambiguous", f"{name}: the extended header at byte {path_offset} precedes no member")
            break
        if header[148:156] != b"%06o\0 " % (256 + sum(header[:148]) + sum(header[156:])):
            raise ArtifactRefused("archive.malformed", f"{where} has a bad or non-standard checksum")
        if header[257:265] != POSIX_MAGIC:
            raise ArtifactRefused("archive.ambiguous", f"{where} is not a POSIX ustar header; GNU and V7 headers are read "
                                                       "differently")
        numbers = {field: _octal(header[start:start + length]) for field, start, length in NUMERIC_FIELDS}
        if None in numbers.values() or _field(header, PREFIX_FIELD).strip(b"\0"):
            raise ArtifactRefused("archive.ambiguous", f"{where} has a numeric field that is not plain octal, or uses the "
                                                       "ustar prefix field; readers decode these differently")
        kind, size = header[156:157], numbers["size"]
        assert size is not None
        data_offset = offset + TAR_BLOCK
        end = data_offset + size + (-size % TAR_BLOCK)
        if end > len(raw):
            raise ArtifactRefused("archive.malformed", f"{where} declares data beyond the end of the archive")
        padding = raw[data_offset + size:end]
        if kind == tarfile.XHDTYPE:
            problem_text = _extended_header_problem(header, numbers)
            if problem_text is not None:
                raise ArtifactRefused("archive.noncanonical", f"{where} is an extended header {problem_text}")
            if path is not None:
                raise ArtifactRefused("archive.ambiguous", f"{where} is a second extended header for one member")
            path, path_offset, offset = _extended_path(raw[data_offset:data_offset + size], where), offset, end
            if padding.strip(b"\0"):
                raise ArtifactRefused("archive.noncanonical", f"{where} has data in the padding after its records")
            continue
        if kind not in MEMBER_TYPES + LINK_TYPES + SPECIAL_TYPES:
            raise ArtifactRefused("archive.ambiguous", f"{where} has type {ascii(kind)} (a pax global header, a GNU long "
                                                       "name or long link, sparse or vendor data), which readers apply "
                                                       "differently")
        if len(members) >= MAX_ARCHIVE_MEMBERS:
            raise ArtifactRefused("archive.too_many_members", f"{name} has more than {MAX_ARCHIVE_MEMBERS} members")
        name_field, _ = _text(_field(header, NAME_FIELD))
        if path is None and not name_field.isascii():
            raise ArtifactRefused("archive.unsafe_path", f"{where}: a member name is not printable ASCII")
        full_name = path if path is not None else name_field.decode("ascii")
        member = full_name[:-1] if kind == tarfile.DIRTYPE and full_name.endswith("/") else full_name
        problem = _member_path_problem(member, prefix)
        if problem is not None:
            raise ArtifactRefused(problem[0], f"{name}: {problem[1]}")
        if kind in LINK_TYPES:
            raise ArtifactRefused("archive.link_member", f"{name}: member {ascii(member)} is a link")
        if kind in SPECIAL_TYPES:
            raise ArtifactRefused("archive.special_member", f"{name}: member {ascii(member)} is not a regular file or directory")
        if kind == tarfile.DIRTYPE and size:
            raise ArtifactRefused("archive.ambiguous", f"{name}: directory {ascii(member)} declares data, which readers skip "
                                                       "differently")
        if size > MAX_MEMBER_BYTES:
            raise ArtifactRefused("archive.too_large", f"{name}: member {ascii(member)} exceeds {MAX_MEMBER_BYTES} bytes")
        key = member.casefold()
        if key in folded:
            if folded[key] == member:
                raise ArtifactRefused("archive.duplicate_member", f"{name}: member {ascii(member)} appears twice")
            raise ArtifactRefused("archive.case_collision",
                                  f"{name}: members {ascii(folded[key])} and {ascii(member)} differ only in case")
        problem_text = _member_header_problem(header, member, kind, full_name, path is not None, padding)
        if problem_text is not None:
            raise ArtifactRefused("archive.noncanonical", f"{name}: member {ascii(member)} {problem_text}")
        folded[key] = member
        assert numbers["mtime"] is not None
        members.append(_Member(member, kind, size, numbers["mtime"], data_offset))
        path, offset = None, end
    # tarfile and GNU tar stop at the first zero block, but data hidden after it is still data a reader
    # with other options (GNU tar --ignore-zeros) unpacks; only zero blocks may follow the last member.
    if len(raw) - offset < 2 * TAR_BLOCK or raw[offset:].strip(b"\0") or len(raw) % TAR_BLOCK:
        raise ArtifactRefused("archive.malformed", f"{name} has data after its last readable member, or no two-block "
                                                   "end-of-archive marker")
    names = [member.name for member in members]
    if names != sorted(names):
        raise ArtifactRefused("archive.noncanonical", f"{name}: members are not in the builder's sorted order")
    directories = {member.name for member in members if member.kind == tarfile.DIRTYPE}
    parents = {"/".join(parts[:index]) for parts in (member.name.split("/") for member in members
                                                     if member.kind == tarfile.REGTYPE) for index in range(2, len(parts))}
    if directories != parents:
        raise ArtifactRefused("archive.noncanonical", f"{name}: the directories are not exactly the parents of its files "
                                                      f"(not a parent: {listed(directories - parents)}; missing: "
                                                      f"{listed(parents - directories)})")
    if len({member.mtime for member in members}) > 1:
        raise ArtifactRefused("archive.noncanonical", f"{name}: the members do not share one modification time")
    return members


def inspect_archive_members(raw_tar: bytes, prefix: str, name: str) -> list[tarfile.TarInfo]:
    """Check every member of an uncompressed tar before anything is unpacked; return tarfile's members.

    The strict reader decides (see _strict_members). Its member rules are stricter than the data filter
    alone, which accepts `prefix/../x` while it stays inside the destination and accepts links that stay
    inside it. tarfile must then read exactly the same names, types, sizes and data offsets, so the members
    unpacked are the members checked.
    """
    members = _strict_members(raw_tar, prefix, name)
    try:
        with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as archive:
            infos = list(archive)
            global_headers = dict(archive.pax_headers)
    except (tarfile.TarError, RecursionError):
        raise ArtifactRefused("archive.malformed", f"{name} is not a readable tar archive") from None
    read = [(info.name, info.type, info.size, info.offset_data) for info in infos]
    if global_headers or read != [(member.name, member.kind, member.size, member.data_offset) for member in members]:
        raise ArtifactRefused("archive.ambiguous", f"{name} is read differently by Python's tarfile and by the strict reader")
    return infos


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
    unknown = set(embedded) - EMBEDDED_MANIFEST_KEYS - EMBEDDED_OPTIONAL_KEYS
    if unknown:
        return f"has keys generate_clients.py never writes ({listed(unknown)})"
    if "provider_constraints_sha256" in embedded and not _is_hex64(embedded["provider_constraints_sha256"]):
        return "provider_constraints_sha256 is not a lowercase SHA-256"
    if not all(isinstance(embedded[key], dict) for key in ("generation_examples", "generation_model_projection") if key in embedded):
        return "generation_examples and generation_model_projection must be objects"
    generator = _generator_record_problem(embedded["generator"])
    if generator is not None:
        return f"generator {generator}"
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
    except (tarfile.TarError, OSError, RecursionError):
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
                              f"(tree {embedded['tree_sha256']}, {shown(embedded['file_count'])} files)")
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
        differing = [path for path in set(golden["files"]) | set(current) if golden["files"].get(path) != current.get(path)]
        raise ArtifactRefused("anchor.golden_tree_mismatch",
                              f"{name} tree {digest} is not {GOLDEN_PATH}[{language}].tree_sha256 {golden['tree_sha256']} "
                              f"at the tag commit; differing paths: {listed(differing)}")
    return {"archive": name, "archive_sha256": sums[name], "tree_sha256": digest, "file_count": len(entries)}
