"""Publish a contract version as an immutable git tag plus a GitHub Release (owner-run, outside CI).

  python scripts/release.py --version X.Y.Z --dry-run     build the release set into build/dist/vX.Y.Z and stop
  python scripts/release.py --version X.Y.Z               verify, build, tag vX.Y.Z (annotated), push the tag, create the Release

Run it from a clean checkout of main in a token-removed PowerShell/shell process authenticated as the
repository owner (`gh auth status`). The script:

1. requires a clean working tree, HEAD on main, info.version == --version and no existing tag;
2. runs every CI command from .github/agent-policy.json (the same checks the pull request passed);
3. builds deterministic archives of the three generated clients (sorted entries, fixed mtime = commit
   time, no owner names), copies the specification, registry and fixtures, writes
   release-manifest.json (specification version and SHA-256, generator name/version/jar SHA-256,
   per-archive SHA-256 and generated-tree SHA-256) and SHA256SUMS;
4. creates the annotated tag and the GitHub Release carrying those files.

Consumers pin the tag and generate from it; rollback is re-pinning the previous tag. A published tag
or Release is never rewritten (docs/publication.md).
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pl_contracts import (  # noqa: E402
    BUILD, FIXTURES, LANGUAGES, REGISTRY, ROOT, SEMVER, SPEC, PipelineError, fail, run, sha256_bytes, sha256_file,
    spec_version, versions,
)

POLICY = ROOT / ".github" / "agent-policy.json"
DIST = BUILD / "dist"
GENERATED = BUILD / "generated"


def git_output(*args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise PipelineError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def preconditions(version: str, dry_run: bool, allow_branch: bool) -> str:
    if not SEMVER.match(version):
        raise PipelineError(f"--version must be MAJOR.MINOR.PATCH, got {version!r}")
    declared = spec_version()
    if declared != version:
        raise PipelineError(f"spec/openapi.yaml declares info.version {declared}; bump it to {version} in a reviewed pull request first")
    head = git_output("rev-parse", "HEAD")
    dirty = git_output("status", "--porcelain")
    if dirty:
        if not dry_run:
            raise PipelineError("working tree is not clean; a release is built only from committed, reviewed content")
        print("warning: working tree is not clean (allowed for --dry-run only)")
    branch = git_output("rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main" and not allow_branch:
        if not dry_run:
            raise PipelineError(f"HEAD is on {branch!r}; releases are cut from main (or pass --allow-branch deliberately)")
        print(f"warning: HEAD is on {branch!r} (allowed for --dry-run only)")
    tag = f"v{version}"
    if git_output("tag", "--list", tag):
        raise PipelineError(f"tag {tag} already exists locally; a published version is immutable, publish a new version instead")
    return head


def run_checks() -> None:
    commands = json.loads(POLICY.read_text(encoding="utf-8"))["commands"]
    print(f"running the {len(commands)} CI command(s) from .github/agent-policy.json")
    for command in commands:
        print(f"  $ {command}")
        completed = subprocess.run(command, cwd=str(ROOT), shell=True, check=False)  # noqa: S602 - reviewed policy commands
        if completed.returncode != 0:
            raise PipelineError(f"check failed (exit {completed.returncode}): {command}")


def deterministic_tar_gz(source: Path, prefix: str, mtime: int) -> bytes:
    """Archive a directory with sorted entries, fixed mtime, numeric owner 0 and normalized modes."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(source.rglob("*"), key=lambda p: p.relative_to(source).as_posix()):
            relative = path.relative_to(source).as_posix()
            info = tarfile.TarInfo(name=f"{prefix}/{relative}")
            info.mtime = mtime
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if path.is_dir():
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                archive.addfile(info)
            elif path.is_file():
                data = path.read_bytes()
                info.size = len(data)
                info.mode = 0o755 if path.name == "gradlew" or path.suffix == ".sh" else 0o644
                archive.addfile(info, io.BytesIO(data))
            else:
                raise PipelineError(f"refusing to archive a non-regular entry: {path}")
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", mtime=0, filename="") as handle:
        handle.write(buffer.getvalue())
    return compressed.getvalue()


def build_release_set(version: str, head: str, mtime: int) -> Path:
    tag = f"v{version}"
    target = DIST / tag
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    pins = versions()
    files: dict[str, str] = {}
    spec_bytes = SPEC.read_bytes().replace(b"\r\n", b"\n")
    (target / f"openapi-{tag}.yaml").write_bytes(spec_bytes)
    files[f"openapi-{tag}.yaml"] = sha256_bytes(spec_bytes)
    for extra in [REGISTRY, *sorted(FIXTURES.glob("*.json"))]:
        data = extra.read_bytes().replace(b"\r\n", b"\n")
        (target / extra.name).write_bytes(data)
        files[extra.name] = sha256_bytes(data)
    clients: dict[str, dict] = {}
    for language in LANGUAGES:
        source = GENERATED / language
        manifest_path = source / "contracts-manifest.json"
        if not manifest_path.is_file():
            raise PipelineError(f"{source} has no manifest; run python scripts/generate_clients.py --verify first")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["spec_version"] != version:
            raise PipelineError(f"{language} client was generated from specification {manifest['spec_version']}, not {version}; regenerate")
        name = f"pennilogic-contracts-{language}-{tag}.tar.gz"
        archive = deterministic_tar_gz(source, f"pennilogic-contracts-{language}-{tag}", mtime)
        (target / name).write_bytes(archive)
        files[name] = sha256_bytes(archive)
        clients[language] = {
            "archive": name, "archive_sha256": files[name], "tree_sha256": manifest["tree_sha256"],
            "file_count": manifest["file_count"], "generator": manifest["generator"], "runtime_sha256": manifest["runtime_sha256"],
        }
    release_manifest = {
        "schema_version": 1,
        "tag": tag,
        "spec_version": version,
        "spec_sha256": files[f"openapi-{tag}.yaml"],
        "commit": head,
        "publication": "git tag + GitHub Release assets; consumers pin the tag and generate with scripts/generate_clients.py (docs/publication.md)",
        "toolchain": {
            "openapi_generator": {"version": pins["openapi_generator"]["version"], "jar_sha256": pins["openapi_generator"]["sha256"]},
            "spectral": pins["spectral"]["version"],
            "oasdiff": pins["oasdiff"]["version"],
        },
        "clients": clients,
        "files": files,
    }
    manifest_bytes = (json.dumps(release_manifest, indent=2) + "\n").encode("utf-8")
    (target / "release-manifest.json").write_bytes(manifest_bytes)
    files["release-manifest.json"] = sha256_bytes(manifest_bytes)
    sums = "".join(f"{digest}  {name}\n" for name, digest in sorted(files.items()))
    (target / "SHA256SUMS").write_text(sums, encoding="utf-8", newline="\n")
    notes = [
        f"# PenniLogic contracts {tag}", "",
        f"Specification `spec/openapi.yaml` version {version} (SHA-256 `{files[f'openapi-{tag}.yaml']}`), commit `{head}`.", "",
        "| Client | Archive SHA-256 | Generated tree SHA-256 | Generator |", "| --- | --- | --- | --- |",
    ]
    for language, client in clients.items():
        generator = client["generator"]
        notes.append(f"| {language} | `{client['archive_sha256']}` | `{client['tree_sha256']}` | {generator['name']} {generator['version']} ({generator['generator_name']}) |")
    notes += ["", "Consumers pin this tag and generate their client from it with `python scripts/generate_clients.py --language <kotlin|typescript|python>`;",
              "the archives are the same output for convenience. Verify downloads against `SHA256SUMS`. Rollback is re-pinning the previous tag.", ""]
    (target / "RELEASE_NOTES.md").write_text("\n".join(notes), encoding="utf-8", newline="\n")
    return target


def publish(version: str, target: Path, remote: str) -> None:
    tag = f"v{version}"
    print(f"creating annotated tag {tag}")
    run(["git", "tag", "-a", tag, "-m", f"PenniLogic contracts {tag}"])
    print(f"pushing {tag} to {remote}")
    run(["git", "push", remote, tag])
    assets = sorted(str(p) for p in target.iterdir() if p.is_file() and p.name != "RELEASE_NOTES.md")
    print("creating the GitHub Release")
    run(["gh", "release", "create", tag, "--verify-tag", "--title", f"PenniLogic contracts {tag}", "--notes-file", str(target / "RELEASE_NOTES.md"), *assets])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", required=True)
    parser.add_argument("--dry-run", action="store_true", help="build the release set and stop before tagging or publishing")
    parser.add_argument("--skip-checks", action="store_true", help="do not re-run the CI commands (dry-run only)")
    parser.add_argument("--allow-branch", action="store_true", help="allow a release from a branch other than main (deliberate use only)")
    parser.add_argument("--source-date-epoch", type=int, help="archive mtime; default: the HEAD commit time")
    parser.add_argument("--remote", default="origin")
    args = parser.parse_args()
    if args.skip_checks and not args.dry_run:
        return fail("--skip-checks is allowed with --dry-run only")
    try:
        head = preconditions(args.version, args.dry_run, args.allow_branch)
        if not args.skip_checks:
            run_checks()
        mtime = args.source_date_epoch if args.source_date_epoch is not None else int(git_output("show", "-s", "--format=%ct", "HEAD"))
        target = build_release_set(args.version, head, mtime)
        print(f"release set built in {target.relative_to(ROOT).as_posix()}:")
        print((target / "SHA256SUMS").read_text(encoding="utf-8"))
        if args.dry_run:
            print("dry run: no tag, push or release was created")
            return 0
        publish(args.version, target, args.remote)
        print(f"published v{args.version}; record the tag and asset digests on the ticket")
        return 0
    except (PipelineError, OSError) as error:
        return fail(f"release failed: {error}")


if __name__ == "__main__":
    sys.exit(main())
