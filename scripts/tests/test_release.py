"""Release set: deterministic archives, digests and preconditions of the owner-run release script."""

from __future__ import annotations

import gzip
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import ROOT, run_script

import release


class DeterministicArchiveTest(unittest.TestCase):
    def test_archive_bytes_depend_only_on_content_names_and_mtime(self) -> None:
        first = Path(tempfile.mkdtemp())
        second = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, first, True)
        self.addCleanup(shutil.rmtree, second, True)
        for root in (second, first):  # different creation order, same content
            (root / "b").mkdir()
            (root / "b" / "y.txt").write_bytes(b"y\n")
            (root / "a.txt").write_bytes(b"a\n")
        one = release.deterministic_tar_gz(first, "pkg", 1790000000)
        two = release.deterministic_tar_gz(second, "pkg", 1790000000)
        self.assertEqual(one, two)
        self.assertNotEqual(one, release.deterministic_tar_gz(second, "pkg", 1790000001))
        with tarfile.open(fileobj=io.BytesIO(gzip.decompress(one)), mode="r:") as archive:
            names = archive.getnames()
            self.assertEqual(names, ["pkg/a.txt", "pkg/b", "pkg/b/y.txt"])
            for member in archive.getmembers():
                self.assertEqual((member.uid, member.gid, member.uname, member.gname, member.mtime), (0, 0, "", "", 1790000000))


class PublishedMainGuardTest(unittest.TestCase):
    """A release is cut only from the commit origin/main carries: an unpushed local commit is refused before any tag."""

    def git(self, cwd: Path, *args: str) -> str:
        completed = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout.strip()

    def test_published_main_follows_the_remote_not_the_local_branch(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        origin, clone = root / "origin.git", root / "clone"
        self.git(root, "init", "--bare", "--initial-branch=main", str(origin))
        self.git(root, "clone", "--quiet", str(origin), str(clone))
        self.git(clone, "config", "user.email", "test@example.invalid")
        self.git(clone, "config", "user.name", "test")
        self.git(clone, "checkout", "-q", "-b", "main")
        (clone / "a.txt").write_text("a\n", encoding="utf-8")
        self.git(clone, "add", "a.txt")
        self.git(clone, "commit", "-q", "-m", "reviewed")
        self.git(clone, "push", "-q", "-u", "origin", "main")
        head = self.git(clone, "rev-parse", "HEAD")
        self.assertEqual(release.published_main("origin", cwd=clone), head)
        (clone / "b.txt").write_text("b\n", encoding="utf-8")
        self.git(clone, "add", "b.txt")
        self.git(clone, "commit", "-q", "-m", "unpushed")
        self.assertNotEqual(release.published_main("origin", cwd=clone), self.git(clone, "rev-parse", "HEAD"))

    def test_unpushed_head_is_refused_and_nothing_is_tagged(self) -> None:
        state = {"HEAD": "a" * 40, "status": "", "branch": "main", "tags": ""}

        def fake_git(*args: str, cwd: Path = release.ROOT) -> str:
            key = " ".join(args)
            if key == "rev-parse HEAD":
                return state["HEAD"]
            if key == "status --porcelain":
                return state["status"]
            if key == "rev-parse --abbrev-ref HEAD":
                return state["branch"]
            if key.startswith("tag --list"):
                return state["tags"]
            if key.startswith("tag -a") or key.startswith("push"):
                raise AssertionError(f"git {key} must not run when preconditions fail")
            raise AssertionError(f"unexpected git call: {key}")

        with mock.patch.object(release, "git_output", side_effect=fake_git), \
                mock.patch.object(release, "spec_version", return_value="0.1.0"), \
                mock.patch.object(release, "published_main", return_value="b" * 40), \
                mock.patch.object(release, "run_checks") as checks, \
                mock.patch.object(release, "build_release_set") as build, \
                mock.patch.object(release, "publish") as publish, \
                mock.patch.object(sys, "argv", ["release.py", "--version", "0.1.0"]):
            self.assertEqual(release.main(), 1)
            checks.assert_not_called()
            build.assert_not_called()
            publish.assert_not_called()
        with mock.patch.object(release, "git_output", side_effect=fake_git), \
                mock.patch.object(release, "spec_version", return_value="0.1.0"), \
                mock.patch.object(release, "published_main", return_value="b" * 40):
            with self.assertRaises(release.PipelineError) as caught:
                release.preconditions("0.1.0", dry_run=False, allow_branch=False)
            self.assertIn("is not origin/main", str(caught.exception))
            self.assertEqual(release.preconditions("0.1.0", dry_run=False, allow_branch=True), "a" * 40)
        with mock.patch.object(release, "git_output", side_effect=fake_git), \
                mock.patch.object(release, "spec_version", return_value="0.1.0"), \
                mock.patch.object(release, "published_main", return_value="a" * 40):
            self.assertEqual(release.preconditions("0.1.0", dry_run=False, allow_branch=False), "a" * 40)


class DryRunTest(unittest.TestCase):
    def test_dry_run_builds_a_complete_release_set_with_matching_digests(self) -> None:
        if not (ROOT / "build" / "generated" / "python" / "contracts-manifest.json").is_file():
            self.skipTest("generated clients are absent; run python scripts/generate_clients.py first")
        version = release.spec_version()
        completed = run_script("release.py", "--version", version, "--dry-run", "--skip-checks", "--source-date-epoch", "1790000000")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("dry run: no tag, push or release was created", completed.stdout)
        target = ROOT / "build" / "dist" / f"v{version}"
        sums = dict(line.split("  ", 1)[::-1] for line in (target / "SHA256SUMS").read_text(encoding="utf-8").splitlines())
        for name, digest in sums.items():
            self.assertEqual(release.sha256_file(target / name), digest, name)
        manifest = json.loads((target / "release-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["spec_version"], version)
        self.assertEqual(set(manifest["clients"]), set(release.LANGUAGES))
        for language, client in manifest["clients"].items():
            self.assertEqual(client["archive_sha256"], sums[client["archive"]], language)
            self.assertEqual(client["generator"]["version"], release.versions()["openapi_generator"]["version"])
        self.assertTrue((target / f"openapi-v{version}.yaml").is_file())
        self.assertTrue((target / "RELEASE_NOTES.md").is_file())
        # A second dry run reproduces every digest.
        again = run_script("release.py", "--version", version, "--dry-run", "--skip-checks", "--source-date-epoch", "1790000000")
        self.assertEqual(again.returncode, 0)
        self.assertEqual(sums, dict(line.split("  ", 1)[::-1] for line in (target / "SHA256SUMS").read_text(encoding="utf-8").splitlines()))

    def test_version_must_match_the_specification(self) -> None:
        completed = run_script("release.py", "--version", "9.9.9", "--dry-run", "--skip-checks")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("declares info.version", completed.stderr)

    def test_skip_checks_needs_dry_run(self) -> None:
        completed = run_script("release.py", "--version", "0.1.0", "--skip-checks")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("--skip-checks is allowed with --dry-run only", completed.stderr)


if __name__ == "__main__":
    unittest.main()
