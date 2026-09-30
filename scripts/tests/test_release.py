"""Release set: deterministic archives, digests and preconditions of the owner-run release script."""

from __future__ import annotations

import gzip
import io
import json
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path

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
