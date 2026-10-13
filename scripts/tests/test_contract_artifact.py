"""Published-artifact loader (contracts#3, T-QA-02): tag provenance, exact SHA256SUMS, manifest agreement,
tag-tree anchors and safe archive extraction.

The releases are synthetic and built by the real release.build_release_set (verification_support). Every
refusal test first loads an unmutated control, then applies exactly one mutation and asserts the exact
static rule ID. A "consistent swap" replaces Release assets and recomputes SHA256SUMS and the release
manifest the way an attacker would. Only the tag's own tree can catch it. No network; nothing is
written inside this repository.
"""

from __future__ import annotations

import contextlib
import copy
import gzip
import io
import json
import os
import shutil
import subprocess
import tarfile
import time
import unittest
from pathlib import Path
from unittest import mock

import verification_support as support
from verification_support import (
    END_OF_ARCHIVE, FIXTURE_NAMES, GOLDEN_RELATIVE, MANIFEST_NAME, SPEC_DIRECTORY, SYNTHETIC_EPOCH, VERSIONS_RELATIVE,
    SyntheticContracts, copy_release, extended_header, git, gnu_long_header, gzip_bytes, json_text, member, member_offsets,
    patch_header, pax_record, raw_member, read_archive, read_sums, refresh_embedded, release_format_source, replace_member_data, reseal,
    rewrite_archive, snapshot, synthetic_digest, tar_padded, temporary_root, ustar_header, write_archive, write_sums,
)

import contract_artifact  # noqa: E402 - verification_support puts scripts/ on sys.path
import pl_contracts  # noqa: E402
import release  # noqa: E402
from contract_artifact import ArtifactRefused, archive_name, archive_prefix, load_published_artifact  # noqa: E402
from pl_contracts import sha256_bytes  # noqa: E402

PROVIDER = pl_contracts.PROVIDER_SOURCE_NAMES[0]
FIXTURE = FIXTURE_NAMES[0]


_PRISTINE: list[tuple[SyntheticContracts, Path]] = []


def pristine_release() -> tuple[SyntheticContracts, Path]:
    """The module's one pristine published release (built once, removed by a module cleanup); tests only copy it."""
    if not _PRISTINE:
        root = temporary_root()
        unittest.addModuleCleanup(shutil.rmtree, root, True)
        base = SyntheticContracts(root / "base")
        _PRISTINE.append((base, base.publish()))
    return _PRISTINE[0]


class LoaderTestCase(unittest.TestCase):
    """One pristine published release for the module; tests mutate copies of it, or build their own.

    The passing control runs once per test method on the first unmutated copy. Later subTests start
    from byte-identical copies of that same input, so each subTest is still the control plus exactly
    one mutation. test_loader_leaves_repository_and_release_dir_unchanged shows a load never changes it.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.enterClassContext(mock.patch.dict(os.environ, support.hermetic_environment(), clear=True))
        cls.base, cls.release_dir = pristine_release()

    def setUp(self) -> None:
        self.work = temporary_root()
        self.addCleanup(shutil.rmtree, self.work, True)
        self.scratch = self.work / "scratch"
        self.scratch.mkdir()
        self.controls: set[str] = set()

    def load(self, release_dir: Path, *, contracts: SyntheticContracts | None = None, tag: str | None = None,
             repo: Path | None = None, remote: str = "origin", accepted: tuple[str, ...] | None = None,
             expect_tag_object: str | None = None) -> contract_artifact.PublishedArtifact:
        """Load with the synthetic origin standing in for the canonical repository (unless `accepted` is given)."""
        contracts = contracts or self.base
        return load_published_artifact(tag if tag is not None else contracts.tag, release_dir, repo=repo or contracts.repo,
                                       remote=remote, scratch_dir=self.scratch, expect_tag_object=expect_tag_object,
                                       accepted_remote_urls=(str(contracts.origin),) if accepted is None else accepted)

    def control_once(self, kind: str, release_dir: Path, contracts: SyntheticContracts | None = None) -> None:
        if kind not in self.controls:
            self.load(release_dir, contracts=contracts)
            self.controls.add(kind)

    def assertRefused(self, rule: str, release_dir: Path, **kwargs: object) -> ArtifactRefused:
        with self.assertRaises(ArtifactRefused) as caught:
            self.load(release_dir, **kwargs)  # type: ignore[arg-type]
        self.assertEqual(caught.exception.rule, rule, caught.exception.detail)
        self.assertEqual(list(self.scratch.iterdir()), [], "the loader left unpacked files behind")
        return caught.exception

    def fresh_release(self, name: str = "release") -> Path:
        """A copy of the pristine release set (loaded as the control), ready for exactly one mutation."""
        release_dir = copy_release(self.release_dir, self.work, name)
        self.control_once("release", release_dir)
        return release_dir

    def fresh_contracts(self, name: str = "contracts") -> SyntheticContracts:
        """A copy of the pristine repository and origin (loading the pristine release is the control)."""
        contracts = self.base.copy_to(self.work / name)
        self.control_once("contracts", self.release_dir, contracts)
        return contracts

    def own_release(self, name: str, **hooks: object) -> tuple[SyntheticContracts, Path]:
        """A separately built release whose standard publish sequence carries exactly one mutation hook.

        The control is the pristine class release, built by the same sequence without the hook.
        """
        self.control_once("pristine", self.release_dir)
        contracts = SyntheticContracts(self.work / name)
        return contracts, contracts.publish(**hooks)  # type: ignore[arg-type]


class PublishedArtifactTest(LoaderTestCase):
    """Positive baseline, builder agreement and loader hygiene."""

    def test_valid_published_artifact_loads(self) -> None:
        artifact = self.load(self.release_dir)
        base = self.base
        self.assertEqual((artifact.tag, artifact.version, artifact.commit, artifact.tag_object, artifact.remote, artifact.remote_url),
                         (base.tag, base.version, base.commit, base.tag_object, "origin", str(base.origin)))
        self.assertEqual(artifact.assets, read_sums(self.release_dir))
        self.assertEqual(set(artifact.assets) | {"SHA256SUMS"}, {path.name for path in self.release_dir.iterdir()})
        spec = (base.repo / support.SPEC_RELATIVE).read_bytes()
        self.assertEqual(artifact.spec_asset, f"openapi-{base.tag}.yaml")
        self.assertEqual(artifact.spec_sha256, sha256_bytes(spec))
        self.assertEqual(artifact.manifest_sha256, sha256_bytes((self.release_dir / "release-manifest.json").read_bytes()))
        golden = json.loads((base.repo / GOLDEN_RELATIVE).read_text(encoding="utf-8"))
        self.assertEqual(list(artifact.clients), list(pl_contracts.LANGUAGES))
        for language, client in artifact.clients.items():
            self.assertEqual(client, {
                "archive": archive_name(language, base.tag), "archive_sha256": artifact.assets[archive_name(language, base.tag)],
                "tree_sha256": golden[language]["tree_sha256"], "file_count": golden[language]["file_count"],
            })
        checks = dict(artifact.checks)
        self.assertEqual([checks.pop("tag.pinned_identity"), checks.pop("github_release.presence")], ["not_run", "not_run"])
        self.assertEqual((checks["tag.on_remote_main"], set(checks.values())), ("pass", {"pass"}))
        self.assertEqual(artifact.read_verified_asset(artifact.spec_asset), spec)
        self.assertEqual(artifact.as_dict()["checks"][-1], {"id": "github_release.presence", "status": "not_run"})
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_builder_and_loader_keys_agree(self) -> None:
        built = self.base.dist / self.base.tag
        manifest = json.loads((built / "release-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest), contract_artifact.RELEASE_MANIFEST_KEYS)
        self.assertEqual(set(manifest["toolchain"]), contract_artifact.TOOLCHAIN_KEYS)
        self.assertEqual(set(manifest["toolchain"]["openapi_generator"]), contract_artifact.GENERATOR_PIN_KEYS)
        self.assertEqual(set(manifest["clients"]), set(pl_contracts.LANGUAGES))
        for language, client in manifest["clients"].items():
            self.assertEqual(set(client), contract_artifact.CLIENT_KEYS, language)
        sources = contract_artifact.expected_asset_sources(self.base.tag, list(FIXTURE_NAMES))
        self.assertEqual(set(manifest["files"]) | {"release-manifest.json"}, set(sources))
        self.assertEqual({path.name for path in built.iterdir()}, set(sources) | {"SHA256SUMS", "RELEASE_NOTES.md"})
        self.assertEqual({path.name for path in self.release_dir.iterdir()}, set(sources) | {"SHA256SUMS"})
        # The builder copies these client values from contracts-manifest.json; the loader cross-checks every one,
        # and requires only keys that generate_clients.py writes.
        self.assertLessEqual(contract_artifact.CLIENT_KEYS - {"archive", "archive_sha256"}, contract_artifact.EMBEDDED_MANIFEST_KEYS)
        # The embedded manifest and its generator record are closed: exactly the keys generate_clients.py can write.
        self.assertEqual(contract_artifact.EMBEDDED_MANIFEST_KEYS | contract_artifact.EMBEDDED_OPTIONAL_KEYS,
                         support.generator_manifest_keys())
        self.assertEqual(contract_artifact.GENERATOR_KEYS, support.generator_record_keys())
        for language, client in manifest["clients"].items():
            self.assertEqual(set(client["generator"]), contract_artifact.GENERATOR_KEYS, language)
        # The publication text the loader expects is the literal this repository's release.py writes.
        self.assertEqual(contract_artifact._publication_literal((pl_contracts.ROOT / contract_artifact.RELEASE_SOURCE_PATH).read_bytes()),
                         manifest["publication"])
        # The committed golden file of this repository has exactly the record shape the loader requires.
        golden = json.loads((pl_contracts.ROOT / GOLDEN_RELATIVE).read_text(encoding="utf-8"))
        for language in pl_contracts.LANGUAGES:
            self.assertEqual(set(golden[language]), contract_artifact.GOLDEN_KEYS, language)

    def test_consistent_reseal_reproduces_builder_bytes(self) -> None:
        """The consistent-swap helper speaks the builder's format: without a change it rewrites identical bytes."""
        release_dir = self.fresh_release()
        before = {name: (release_dir / name).read_bytes() for name in ("release-manifest.json", "SHA256SUMS")}
        reseal(release_dir)
        self.assertEqual({name: (release_dir / name).read_bytes() for name in before}, before)
        self.load(release_dir)

    def test_loader_leaves_repository_and_release_dir_unchanged(self) -> None:
        release_dir = copy_release(self.release_dir, self.work)
        watched = (self.base.repo, self.base.origin, release_dir)
        before = [snapshot(path) for path in watched]
        self.load(release_dir)
        self.assertEqual([snapshot(path) for path in watched], before)
        self.assertEqual(list(self.scratch.iterdir()), [])
        prefix = archive_prefix("python", self.base.tag)
        rewrite_archive(release_dir, self.base.tag, "python", lambda members: members + [member(f"{prefix}/extra.py")])
        reseal(release_dir)
        self.assertRefused("archive.tree_mismatch", release_dir)  # refused after unpacking: still nothing left behind

    def test_read_verified_asset_refuses_changed_bytes(self) -> None:
        release_dir = copy_release(self.release_dir, self.work)
        artifact = self.load(release_dir)
        self.assertEqual(artifact.read_verified_asset(FIXTURE), (release_dir / FIXTURE).read_bytes())
        (release_dir / FIXTURE).write_bytes((release_dir / FIXTURE).read_bytes() + b" ")
        with self.assertRaises(ArtifactRefused) as caught:
            artifact.read_verified_asset(FIXTURE)
        self.assertEqual(caught.exception.rule, "sums.digest_mismatch")


class TagProvenanceTest(LoaderTestCase):
    """Tag grammar, annotated tag object, and publication on the remote."""

    def test_tag_leading_zero_refused(self) -> None:
        contracts = self.fresh_contracts()
        contracts.tag_annotated("v01.2.3")
        contracts.push_tag("v01.2.3")
        # The leading-zero tag exists, is annotated and published on the same commit; only its name is wrong.
        self.assertRefused("tag.grammar", self.release_dir, contracts=contracts, tag="v01.2.3")

    def test_branch_name_commit_or_decorated_tag_refused(self) -> None:
        self.load(self.release_dir)
        for candidate in ("main", self.base.commit, f"{self.base.tag}\n", f"refs/tags/{self.base.tag}", "V1.2.3", "v1.2",
                          "v1.2.3-rc.1", f" {self.base.tag}", "v1.2.3.0"):
            with self.subTest(candidate=candidate):
                self.assertRefused("tag.grammar", self.release_dir, tag=candidate)

    def test_missing_tag_refused(self) -> None:
        self.load(self.release_dir)
        self.assertRefused("tag.missing", self.release_dir, tag="v1.2.4")

    def test_lightweight_tag_refused(self) -> None:
        contracts = self.fresh_contracts()
        git(contracts.repo, "tag", "--force", contracts.tag, contracts.commit)
        self.assertRefused("tag.not_annotated", self.release_dir, contracts=contracts)

    def test_tag_object_for_another_name_or_object_refused(self) -> None:
        mutations = {
            "names another tag": lambda contracts: git(contracts.repo, "update-ref", f"refs/tags/{contracts.tag}",
                                                       contracts.tag_annotated("v1.2.2")),
            "tags a tag": lambda contracts: git(contracts.repo, "tag", "--force", "-a", contracts.tag, "-m", "nested",
                                                contracts.tag_object),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                contracts = self.fresh_contracts(label.replace(" ", "-"))
                mutate(contracts)
                self.assertRefused("tag.object_invalid", self.release_dir, contracts=contracts)

    def test_unpublished_tag_refused(self) -> None:
        contracts = self.fresh_contracts()
        git(contracts.repo, "push", "--quiet", "origin", f":refs/tags/{contracts.tag}")
        self.assertRefused("tag.unpublished", self.release_dir, contracts=contracts)

    def test_remote_tag_mismatch_refused(self) -> None:
        mutations = {
            "local tag re-created": lambda contracts: contracts.tag_annotated(message="Re-created locally", force=True),
            "remote tag is lightweight": lambda contracts: git(contracts.origin, "update-ref", f"refs/tags/{contracts.tag}",
                                                               contracts.commit),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                contracts = self.fresh_contracts(label.replace(" ", "-"))
                mutate(contracts)
                self.assertRefused("tag.remote_mismatch", self.release_dir, contracts=contracts)

    def test_remote_unreachable_refused(self) -> None:
        contracts = self.fresh_contracts()
        absent = str(self.work / "no-such-origin.git")
        git(contracts.repo, "remote", "set-url", "origin", absent)
        self.assertRefused("tag.remote_unreachable", self.release_dir, contracts=contracts, accepted=(absent,))

    def test_unknown_or_malformed_remote_refused(self) -> None:
        self.load(self.release_dir)
        for remote in ("upstream", "-origin", "origin/main", ""):
            with self.subTest(remote=remote):
                self.assertRefused("remote.invalid", self.release_dir, remote=remote)

    def test_repository_must_be_checkout_top_level(self) -> None:
        self.load(self.release_dir)
        outside = self.work / "not-a-checkout"
        outside.mkdir()
        for repo in (outside, self.base.repo / "spec"):
            with self.subTest(repo=repo.name):
                self.assertRefused("repository.invalid", self.release_dir, repo=repo)

    def test_partial_clone_refused_without_fetching(self) -> None:
        """Review round 3, PSEC-R3-1: in a blob-less clone (CI checkouts often use `filter: blob:none`) git fetched the
        tag's files from the clone's promisor remote, which can be a mirror rather than the verified remote, and wrote
        72 files into the checkout; with that remote offline the refusal read like a bad tag. Git now never fetches:
        the checkout is refused and stays unchanged. Control: a full clone of the same origin. The last case removes
        GIT_NO_LAZY_FETCH, as a git older than 2.45 ignores it; the empty transport allow list still stops the fetch."""
        contracts = self.fresh_contracts()
        git(contracts.origin, "config", "uploadpack.allowFilter", "true")
        url = contracts.origin.as_uri()
        full = self.work / "full-clone"
        git(self.work, "clone", "--quiet", "--no-checkout", url, str(full))
        self.load(self.release_dir, contracts=contracts, repo=full, accepted=(url,))  # control: the checkout has every object
        cases = (("a blob-less clone", "blob:none", ()), ("a treeless clone", "tree:0", ()),
                 ("a blob-less clone and a git that ignores GIT_NO_LAZY_FETCH", "blob:none", ("GIT_NO_LAZY_FETCH",)))
        for index, (label, specification, ignored) in enumerate(cases):
            with self.subTest(label):
                clone = self.work / f"partial-clone-{index}"
                git(self.work, "clone", "--quiet", "--no-checkout", f"--filter={specification}", url, str(clone))
                before = snapshot(clone / ".git")
                with mock.patch.dict(contract_artifact.GIT_ENVIRONMENT):
                    for key in ignored:
                        del contract_artifact.GIT_ENVIRONMENT[key]
                    refusal = self.assertRefused("repository.objects_missing", self.release_dir, contracts=contracts, repo=clone,
                                                 accepted=(url,))
                self.assertIn("partial clone", refusal.detail)
                self.assertEqual(snapshot(clone / ".git"), before, "git fetched or wrote objects into the checkout")

    def test_git_unavailable_refused(self) -> None:
        self.load(self.release_dir)
        with mock.patch.dict(os.environ, {"PATH": str(self.work)}):
            self.assertRefused("git.unavailable", self.release_dir)

    def test_git_never_comes_from_the_current_or_release_directory(self) -> None:
        """Review round 2, PSEC-R2-4: shutil.which searched an empty, '.' or relative PATH entry in the current directory
        (on Windows the current directory first, always), and that can be the downloaded release directory: a planted
        git ran seven times before the loader refused the directory. Only absolute entries outside it count now."""
        trusted = contract_artifact.trusted_git()
        self.assertIsNotNone(trusted)
        release_dir = self.fresh_release()
        elsewhere = self.work / "downloads"
        elsewhere.mkdir()
        for directory in (release_dir, elsewhere):
            planted = directory / contract_artifact.GIT_EXECUTABLE
            planted.write_bytes(b"#!/bin/sh\nexit 1\n")
            planted.chmod(0o755)
        started: list[str] = []
        run = subprocess.run

        def recording_run(argv, *args, **kwargs):
            started.append(argv[0])
            return run(argv, *args, **kwargs)

        relative = {"an empty entry": "", "the entry '.'": os.curdir}
        cases = [(f"{label}, the release directory as the current directory", release_dir, entry, "asset.unexpected")
                 for label, entry in {**relative, "a relative entry": os.path.join(os.pardir, release_dir.name),
                                      "the release directory as an absolute entry": str(release_dir)}.items()]
        cases += [(f"{label}, another current directory", elsewhere, entry, None)
                  for label, entry in {**relative, "a relative entry": os.path.join(os.pardir, elsewhere.name)}.items()]
        for label, current, entry, rule in cases:
            with self.subTest(label), contextlib.chdir(current), \
                    mock.patch.dict(os.environ, {"PATH": os.pathsep.join([entry, os.environ["PATH"]])}), \
                    mock.patch.object(contract_artifact.subprocess, "run", recording_run):
                started.clear()
                self.assertEqual(contract_artifact.trusted_git(release_dir), trusted)
                if rule is None:
                    self.load(self.release_dir)  # verified: the git in the current directory never ran
                else:
                    self.assertRefused(rule, Path(os.curdir))  # the planted file is no Release asset
                self.assertEqual(set(started), {trusted})
        with self.subTest("no git outside the release directory"), mock.patch.dict(os.environ, {"PATH": str(release_dir)}):
            self.assertRefused("git.unavailable", release_dir)


class PublicationIdentityTest(LoaderTestCase):
    """Where and what "published" means (review round 1, PSEC-2 and PSEC-3): the canonical repository, an optional
    pin of the tag object verified before, and the tag commit on the remote's main."""

    def test_remote_must_be_the_canonical_repository(self) -> None:
        cases = {
            "a configured remote that is the checkout itself": (
                "mirror", lambda contracts: git(contracts.repo, "remote", "add", "mirror", str(contracts.repo))),
            "origin set to the checkout ('.')": ("origin", lambda contracts: git(contracts.repo, "remote", "set-url", "origin", ".")),
            "origin rewritten to the checkout by insteadOf": (
                "origin", lambda contracts: git(contracts.repo, "config", f"url.{contracts.repo}.insteadOf", str(contracts.origin))),
        }
        for label, (remote, mutate) in cases.items():
            with self.subTest(label):
                contracts = self.fresh_contracts(label.replace(" ", "-").replace("'", "").replace("(", "").replace(")", ""))
                mutate(contracts)
                self.assertRefused("remote.not_canonical", self.release_dir, contracts=contracts, remote=remote)

    def test_canonical_repository_urls(self) -> None:
        keys = {contract_artifact._url_key(url) for url in contract_artifact.CANONICAL_REMOTE_URLS}
        for url in ("https://github.com/PenniLogic/contracts.git", "https://github.com/pennilogic/contracts/",
                    "https://x-access-token:synthetic-credential@github.com/PenniLogic/contracts",
                    "git@github.com:PenniLogic/contracts.git", "ssh://git@github.com/PenniLogic/contracts"):
            with self.subTest(accepted=url):
                self.assertIn(contract_artifact._url_key(url), keys)
        for url in ("http://github.com/PenniLogic/contracts", "file:///srv/contracts.git", "/srv/contracts", ".",
                    "https://github.com/PenniLogic/contracts-fork", "https://github.com.example/PenniLogic/contracts",
                    "https://github.com/OtherOrganization/contracts", "ssh://git:synthetic-credential@github.com/PenniLogic/contracts"):
            with self.subTest(refused=url):
                self.assertNotIn(contract_artifact._url_key(url), keys)
        self.assertEqual(contract_artifact.without_credentials(
            "https://x-access-token:synthetic-credential@github.com/PenniLogic/contracts.git?query#fragment"),
            "https://github.com/PenniLogic/contracts.git")

    def test_pinned_tag_object_refuses_a_recreated_tag(self) -> None:
        pinned = self.load(self.release_dir, expect_tag_object=self.base.tag_object)  # control: the pin of first use
        self.assertEqual(dict(pinned.checks)["tag.pinned_identity"], "pass")
        moved = self.base.copy_to(self.work / "moved")
        moved.write("README.md", "# Synthetic contracts repository (re-created tag)\n")
        moved.commit_all("Synthetic later commit on main")
        moved.push_main()
        moved.tag_annotated(force=True, message="Re-created on a later commit")
        moved.push_tag(force=True)
        release_dir = moved.download(moved.build(), "moved")
        clone = self.work / "fresh-clone"
        git(self.work, "clone", "--quiet", str(moved.origin), str(clone))
        self.assertEqual(self.load(release_dir, contracts=moved, repo=clone).commit, moved.commit)  # trust on first use
        for label, pin in (("the pin of first use", self.base.tag_object), ("a tag name instead of an object", moved.tag)):
            with self.subTest(label):
                self.assertRefused("tag.pin_mismatch", release_dir, contracts=moved, repo=clone, expect_tag_object=pin)

    def test_tag_commit_must_be_on_the_remote_main(self) -> None:
        """The reviewer's reproduction: an annotated tag on a commit that never reached main."""
        self.control_once("pristine", self.release_dir)
        contracts = SyntheticContracts(self.work / "unmerged")
        contracts.init_repository()
        contracts.write_source()
        contracts.write_generated()
        contracts.write_golden()
        contracts.commit_all("Synthetic reviewed main commit")
        contracts.push_main()
        contracts.write(support.SPEC_RELATIVE, support.SPEC_TEMPLATE.format(version=contracts.version).replace(
            "Synthetic document; not a product contract.", "Synthetic change that never reached main."))
        contracts.write_generated()
        contracts.write_golden()
        contracts.commit_all("Synthetic unreviewed branch commit")
        contracts.tag_annotated()
        contracts.push_tag()
        self.assertRefused("tag.not_on_main", contracts.download(contracts.build()), contracts=contracts)

    def test_main_ancestry_not_run_when_this_checkout_cannot_decide(self) -> None:
        self.assertEqual(dict(self.load(self.release_dir).checks)["tag.on_remote_main"], "pass")  # control

        def on_main(**kwargs: object) -> str:
            return dict(self.load(self.release_dir, **kwargs).checks)["tag.on_remote_main"]  # type: ignore[arg-type]

        with self.subTest("the remote has no main"):
            contracts = self.fresh_contracts("no-main")
            git(contracts.origin, "update-ref", "-d", "refs/heads/main")
            self.assertEqual(on_main(contracts=contracts), "not_run")
        with self.subTest("a shallow clone of the tag"):
            clone, url = self.work / "shallow", self.base.origin.as_uri()
            git(self.work, "clone", "--quiet", "--depth", "1", "--branch", self.base.tag, url, str(clone))
            self.assertEqual(on_main(repo=clone, accepted=(url,)), "not_run")
        with self.subTest("main moved on after this clone was made"):
            contracts = self.fresh_contracts("stale")
            clone = self.work / "stale-clone"
            git(self.work, "clone", "--quiet", str(contracts.origin), str(clone))
            contracts.write("README.md", "# Synthetic contracts repository (later main)\n")
            contracts.commit_all("Synthetic later commit on main")
            contracts.push_main()
            self.assertEqual(on_main(contracts=contracts, repo=clone), "not_run")


class ReleaseSetTest(LoaderTestCase):
    """The release directory, SHA256SUMS and their digests."""

    def test_release_dir_missing_refused(self) -> None:
        self.load(self.release_dir)
        self.assertRefused("release_dir.missing", self.work / "absent")
        with self.subTest("a path that cannot be resolved (an embedded NUL; the git lookup resolves the directory first)"):
            self.assertRefused("release_dir.missing", self.work / "absent\0name")

    def test_branch_build_refused(self) -> None:
        """A release set inside the checkout (build/dist/<tag>) is a working-tree build, not the downloaded assets."""
        contracts = self.fresh_contracts()
        inside = contracts.repo / "build" / "dist" / contracts.tag
        shutil.copytree(self.release_dir, inside)
        self.assertRefused("release_dir.inside_repository", inside, contracts=contracts)

    def test_sha256sums_extra_or_missing_entry_refused(self) -> None:
        def extra_listed(release_dir: Path) -> None:
            write_sums(release_dir, {**read_sums(release_dir), "synthetic-extra.v1.json": synthetic_digest("extra")})

        def extra_uploaded(release_dir: Path) -> None:
            (release_dir / "synthetic-extra.v1.json").write_text("{}\n", encoding="utf-8")
            write_sums(release_dir, {**read_sums(release_dir), "synthetic-extra.v1.json": sha256_bytes(b"{}\n")})

        def missing(release_dir: Path) -> None:
            sums = read_sums(release_dir)
            del sums[FIXTURE]
            write_sums(release_dir, sums)

        for label, mutate, rule in (("extra listed", extra_listed, "sums.extra_entry"),
                                    ("extra uploaded", extra_uploaded, "sums.extra_entry"),
                                    ("missing", missing, "sums.missing_entry")):
            with self.subTest(label):
                release_dir = self.fresh_release(label.replace(" ", "-"))
                mutate(release_dir)
                self.assertRefused(rule, release_dir)

    def test_sha256sums_absent_refused(self) -> None:
        release_dir = self.fresh_release()
        (release_dir / "SHA256SUMS").unlink()
        self.assertRefused("sums.missing_file", release_dir)

    def test_sha256sums_noncanonical_refused(self) -> None:
        def swap_first_lines(text: str) -> str:
            lines = text.splitlines(keepends=True)
            return lines[1] + lines[0] + "".join(lines[2:])

        mutations = {
            "unsorted": swap_first_lines,
            "crlf": lambda text: text.replace("\n", "\r\n"),
            "uppercase digest": lambda text: text[:64].upper() + text[64:],
            "one space": lambda text: text.replace("  ", " ", 1),
            "binary marker": lambda text: text.replace("  ", " *", 1),
            "duplicate line": lambda text: text.splitlines(keepends=True)[0] + text,
            "no final newline": lambda text: text[:-1],
            "empty": lambda text: "",
            "path separator": lambda text: text.replace(f"  {FIXTURE}", f"  sub/{FIXTURE}", 1),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                release_dir = self.fresh_release(label.replace(" ", "-"))
                sums = release_dir / "SHA256SUMS"
                sums.write_bytes(mutate(sums.read_text(encoding="utf-8")).encode("utf-8"))
                self.assertRefused("sums.malformed", release_dir)

    def test_release_dir_extra_or_missing_file_refused(self) -> None:
        def build_notes(release_dir: Path) -> None:
            shutil.copyfile(self.base.dist / self.base.tag / "RELEASE_NOTES.md", release_dir / "RELEASE_NOTES.md")

        mutations = (
            ("stray file", lambda release_dir: (release_dir / "notes.txt").write_text("synthetic\n", encoding="utf-8"),
             "asset.unexpected"),
            ("build notes", build_notes, "asset.unexpected"),
            ("asset deleted", lambda release_dir: (release_dir / FIXTURE).unlink(), "asset.missing"),
        )
        for label, mutate, rule in mutations:
            with self.subTest(label):
                release_dir = self.fresh_release(label.replace(" ", "-"))
                mutate(release_dir)
                self.assertRefused(rule, release_dir)

    def test_release_dir_entry_not_regular_file_refused(self) -> None:
        def symlinked(release_dir: Path) -> None:
            target = self.work / f"elsewhere-{FIXTURE}"
            shutil.copyfile(release_dir / FIXTURE, target)
            (release_dir / FIXTURE).unlink()
            try:
                (release_dir / FIXTURE).symlink_to(target)
            except OSError:  # Windows without the symlink privilege; the contracts CI runs on Linux
                self.skipTest("this platform cannot create symlinks")

        for label, mutate in (("symlink", symlinked), ("directory", lambda release_dir: (release_dir / "nested").mkdir())):
            with self.subTest(label):
                release_dir = self.fresh_release(label)
                mutate(release_dir)
                self.assertRefused("asset.not_regular_file", release_dir)

    def test_oversized_asset_refused(self) -> None:
        for label, bound in (("release asset", "MAX_ASSET_BYTES"), ("tag-tree file", "MAX_TAG_BLOB_BYTES")):
            with self.subTest(label):
                release_dir = self.fresh_release(label.replace(" ", "-"))
                with mock.patch.object(contract_artifact, bound, 64):
                    self.assertRefused("asset.too_large", release_dir)

    def test_digest_mismatch_refused(self) -> None:
        release_dir = self.fresh_release()
        data = bytearray((release_dir / FIXTURE).read_bytes())
        data[2] ^= 0x01
        (release_dir / FIXTURE).write_bytes(bytes(data))
        self.assertRefused("sums.digest_mismatch", release_dir)


class ReleaseManifestTest(LoaderTestCase):
    """release-manifest.json shape and agreement with the tag, SHA256SUMS and the shipped assets."""

    def assertSwapRefused(self, rule: str, edit=None, text_edit=None, name: str = "release") -> None:
        release_dir = self.fresh_release(name)
        reseal(release_dir, edit=edit, text_edit=text_edit)
        self.assertRefused(rule, release_dir)

    def test_manifest_malformed_refused(self) -> None:
        edits = {
            "unknown key": lambda manifest: manifest.update(signed_by="synthetic"),
            "missing key": lambda manifest: manifest.pop("publication"),
            "schema version 2": lambda manifest: manifest.update(schema_version=2),
            "boolean schema version": lambda manifest: manifest.update(schema_version=True),
            "uppercase digest": lambda manifest: manifest.update(spec_sha256=manifest["spec_sha256"].upper()),
            "short commit": lambda manifest: manifest.update(commit=manifest["commit"][:12]),
            "client key missing": lambda manifest: manifest["clients"]["python"].pop("runtime_sha256"),
            "toolchain key missing": lambda manifest: manifest["toolchain"].pop("oasdiff"),
        }
        for label, edit in edits.items():
            with self.subTest(label):
                self.assertSwapRefused("manifest.malformed", edit=edit, name=label.replace(" ", "-"))
        text_edits = {
            "duplicate key": lambda text: text.replace("{\n", '{\n  "schema_version": 1,\n', 1),
            "non-finite number": lambda text: text.replace('"schema_version": 1', '"schema_version": NaN', 1),
            "byte order mark": lambda text: "\ufeff" + text,
            "not json": lambda text: text[: len(text) // 2],
        }
        for label, text_edit in text_edits.items():
            with self.subTest(label):
                self.assertSwapRefused("manifest.malformed", text_edit=text_edit, name=label.replace(" ", "-"))

    def test_manifest_files_mismatch_refused(self) -> None:
        self.assertSwapRefused("manifest.files_mismatch", edit=lambda manifest: manifest["files"].update({FIXTURE: synthetic_digest("x")}))

    def test_refusal_details_are_bounded(self) -> None:
        """Review round 2, PSEC-R2-5: a detail listed every name the release chose (400000 unknown manifest keys made a
        6.4 MB refusal). Now at most five names, each cut to its bound, then how many more; one long value is cut too."""
        count = 100
        names = {f"synthetic-{index:03d}.json": synthetic_digest(str(index)) for index in range(count)}
        cases = {
            "unknown manifest keys": (lambda release_dir: reseal(release_dir, edit=lambda manifest: manifest.update(
                dict.fromkeys(names, 0))), "manifest.malformed"),
            "extra manifest files entries": (lambda release_dir: reseal(release_dir, edit=lambda manifest: manifest["files"].update(
                names)), "manifest.files_mismatch"),
            "extra SHA256SUMS entries": (lambda release_dir: write_sums(release_dir, {**read_sums(release_dir), **names}),
                                         "sums.extra_entry"),
            "unexpected files": (lambda release_dir: [(release_dir / name).write_text("{}\n", encoding="utf-8") for name in names],
                                 "asset.unexpected"),
        }
        for label, (mutate, rule) in cases.items():
            with self.subTest(label):
                release_dir = self.fresh_release(label.replace(" ", "-"))
                mutate(release_dir)
                detail = self.assertRefused(rule, release_dir).detail
                self.assertIn(f"'synthetic-004.json' and {count - 5} more", detail)
                self.assertNotIn("synthetic-005", detail)
        with self.subTest("a long tag value"):
            release_dir = self.fresh_release("long-tag")
            reseal(release_dir, edit=lambda manifest: manifest.update(tag="v" + "\u00e9" * 100_000))
            detail = self.assertRefused("manifest.tag_mismatch", release_dir).detail
            self.assertIn("\\xe9'... (100001 characters), not v1.2.3", detail)  # cut to 200 characters, then escaped
            self.assertLess(len(detail), 1000)

    def test_manifest_tag_mismatch_refused(self) -> None:
        self.assertSwapRefused("manifest.tag_mismatch", edit=lambda manifest: manifest.update(tag="v1.2.4"))

    def test_manifest_version_ne_spec_version_refused(self) -> None:
        self.assertSwapRefused("manifest.version_mismatch", edit=lambda manifest: manifest.update(spec_version="1.2.4"))

    def test_manifest_commit_ne_tag_commit_refused(self) -> None:
        """A release built from a later branch commit with identical assets: only the recorded commit differs."""
        contracts, published = self.own_release("branch")
        self.load(published, contracts=contracts)
        contracts.write("README.md", "# Synthetic contracts repository (a later branch commit)\n")
        later = contracts.commit_all("Synthetic later branch commit")
        branch_build = contracts.download(contracts.build(head=later), "branch-build")
        published_sums, branch_sums = read_sums(published), read_sums(branch_build)
        del published_sums["release-manifest.json"], branch_sums["release-manifest.json"]
        self.assertEqual(branch_sums, published_sums)  # every asset is byte-identical; only the manifest differs
        self.assertRefused("manifest.commit_mismatch", branch_build, contracts=contracts)

    def test_manifest_spec_digest_mismatch_refused(self) -> None:
        self.assertSwapRefused("manifest.spec_digest_mismatch", edit=lambda manifest: manifest.update(spec_sha256=synthetic_digest("x")))

    def test_manifest_provider_sources_mismatch_refused(self) -> None:
        edits = {
            "digest": lambda manifest: manifest["provider_sources_sha256"].update({PROVIDER: synthetic_digest("x")}),
            "source dropped": lambda manifest: manifest["provider_sources_sha256"].pop(PROVIDER),
        }
        for label, edit in edits.items():
            with self.subTest(label):
                self.assertSwapRefused("manifest.provider_sources_mismatch", edit=edit, name=label.replace(" ", "-"))

    def test_manifest_clients_mismatch_refused(self) -> None:
        tag = self.base.tag
        edits = {
            "client dropped": lambda manifest: manifest["clients"].pop("python"),
            "archive renamed": lambda manifest: manifest["clients"]["python"].update(archive=f"pennilogic-contracts-python-{tag}.tgz"),
            "archive digest": lambda manifest: manifest["clients"]["kotlin"].update(archive_sha256=synthetic_digest("x")),
            "client provider digest": lambda manifest: manifest["clients"]["typescript"]["provider_sources_sha256"].update(
                {PROVIDER: synthetic_digest("x")}),
        }
        for label, edit in edits.items():
            with self.subTest(label):
                self.assertSwapRefused("manifest.clients_mismatch", edit=edit, name=label.replace(" ", "-"))

    def test_spec_info_version_ne_tag_refused(self) -> None:
        """The tag's own specification declares another version or none (a tag made by hand, not by release.py).

        A swapped specification asset never gets this far: its tag-tree anchor refuses it before it is parsed.
        """
        def tagged_with(change):
            def hook(contracts: SyntheticContracts) -> None:
                path = contracts.repo / support.SPEC_RELATIVE
                path.write_text(change(path.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
                contracts.commit_all("Synthetic specification edited, then tagged by hand")
                contracts.push_main()
                contracts.tag_annotated(force=True)
                contracts.push_tag(force=True)
            return hook

        mutations = {
            "other version": lambda text: text.replace(f"  version: {self.base.version}\n", "  version: 1.2.4\n"),
            "no version": lambda text: text.replace(f"  version: {self.base.version}\n", ""),
        }
        for label, change in mutations.items():
            with self.subTest(label):
                contracts, published = self.own_release(label.replace(" ", "-"), before_build=tagged_with(change))
                self.assertRefused("spec.version_mismatch", published, contracts=contracts)

    def test_unanchored_specification_is_never_parsed(self) -> None:
        """Review round 1, PSEC-4: the info.version expression backtracks exponentially on `  a` lines under `info:`
        (26 lines took 6 s, 40 would take hours). A swapped specification is refused by its anchor before parsing."""
        hostile = "openapi: 3.1.0\ninfo:\n" + "  a\n" * 40 + "paths: {}\n"
        release_dir = self.fresh_release()
        (release_dir / f"openapi-{self.base.tag}.yaml").write_text(hostile, encoding="utf-8", newline="\n")
        reseal(release_dir)
        parsed: list[str] = []
        real = contract_artifact.spec_version

        def guarded(text: str) -> str:
            if text == hostile:
                parsed.append(text)
                raise AssertionError("info.version was parsed from an unanchored specification asset")
            return real(text)

        with mock.patch.object(contract_artifact, "spec_version", guarded):
            started = time.perf_counter()
            self.assertRefused("anchor.asset_blob_mismatch", release_dir)
            self.assertLess(time.perf_counter() - started, 10.0)
        self.assertEqual(parsed, [])


class ReleaseFormatTest(LoaderTestCase):
    """The expected release set follows the tag's own release format, read from the literals of its
    scripts/pl_contracts.py, not this runner's (review round 1, CVR-3)."""

    def test_release_format_comes_from_the_tag(self) -> None:
        """The reviewer's case: main's runner gained category-seed.v1.json (PR #38) after an older tag was cut.
        A synthetic name stands in for it, so the test holds on either side of that change."""
        self.load(self.release_dir)  # control: the tag and the runner share one format
        added = "synthetic-added-source.v1.json"
        self.assertNotIn(added, pl_contracts.PROVIDER_SOURCE_NAMES)
        extra = pl_contracts.PROVIDER_SOURCE_NAMES + (added,)
        with self.subTest("the runner knows a provider source the tag does not have"):
            with mock.patch.object(pl_contracts, "PROVIDER_SOURCE_NAMES", extra):
                self.load(self.release_dir)
        with self.subTest("the tag has a provider source the runner does not know"):
            contracts = SyntheticContracts(self.work / "added-source", provider_source_names=extra)
            artifact = self.load(contracts.publish(), contracts=contracts)
            self.assertIn(added, artifact.assets)

    def test_unsupported_release_format_refused(self) -> None:
        def format_module(source: str):
            return lambda contracts: contracts.write(support.FORMAT_RELATIVE, source)

        names, languages = pl_contracts.PROVIDER_SOURCE_NAMES, pl_contracts.LANGUAGES
        cases = {
            "no format module at the tag": {"uncommitted": (support.FORMAT_RELATIVE,)},
            "another client language": {"after_source": format_module(release_format_source(names, languages + ("swift",)))},
            "provider names computed, not a literal": {"after_source": format_module(
                f"PROVIDER_SOURCE_NAMES = tuple(sorted({names!r}))\nLANGUAGES = {languages!r}\n")},
            "provider names assigned twice": {"after_source": format_module(
                release_format_source(names) + f"PROVIDER_SOURCE_NAMES = {names!r}\n")},
            "a provider name with a path": {"after_source": format_module(release_format_source(names + ("../escape.json",)))},
        }
        for label, hooks in cases.items():
            with self.subTest(label):
                contracts, published = self.own_release(label.replace(" ", "-").replace(",", ""), **hooks)
                self.assertRefused("release.format_unsupported", published, contracts=contracts)

    def test_this_repository_declares_its_release_format_as_literals(self) -> None:
        source = (pl_contracts.ROOT / contract_artifact.FORMAT_PATH).read_bytes()
        self.assertEqual(contract_artifact._literal_constants(source, contract_artifact.FORMAT_CONSTANTS),
                         {"PROVIDER_SOURCE_NAMES": pl_contracts.PROVIDER_SOURCE_NAMES, "LANGUAGES": pl_contracts.LANGUAGES})


class ManifestMetadataTest(LoaderTestCase):
    """Manifest metadata anchored to the tag's own files (review round 1, PSEC-6). A consistent forgery edits the
    embedded contracts-manifest.json (outside the tree digest) and release-manifest.json together."""

    def forge(self, name: str, release_edit=None, embedded_edit=None, language: str = "python") -> Path:
        release_dir = self.fresh_release(name)
        if embedded_edit is not None:
            embedded = f"{archive_prefix(language, self.base.tag)}/{MANIFEST_NAME}"
            rewrite_archive(release_dir, self.base.tag, language, lambda members: replace_member_data(
                members, embedded, lambda data: json_text(embedded_edit(json.loads(data))).encode("utf-8")))
        reseal(release_dir, edit=release_edit)
        return release_dir

    def test_forged_manifest_metadata_refused(self) -> None:
        forged = synthetic_digest("forged")

        def both(update):
            def embedded(manifest: dict) -> dict:
                update(manifest)
                return manifest
            return {"release_edit": lambda manifest: update(manifest["clients"]["python"]), "embedded_edit": embedded}

        cases = {
            "runtime digest": both(lambda record: record.update(runtime_sha256=forged)),
            "generator name": both(lambda record: record["generator"].update(generator_name="forged-generator")),
            "generator configuration digest": both(lambda record: record["generator"].update(config_sha256=forged)),
            "generator configuration path": both(lambda record: record["generator"].update(config="generator/forged.json")),
            "ignore-override digest": both(lambda record: record["generator"].update(ignore_override_sha256=forged)),
            "publication text": {"release_edit": lambda manifest: manifest.update(publication="forged publication note")},
        }
        for label, edits in cases.items():
            with self.subTest(label):
                self.assertRefused("anchor.manifest_metadata_mismatch", self.forge(label.replace(" ", "-"), **edits))

    def test_manifest_key_sets_are_closed(self) -> None:
        with self.subTest("boundary control: an optional key generate_clients.py writes"):
            self.load(self.forge("optional", embedded_edit=lambda manifest: dict(manifest, generation_examples={"count": 0})))
        cases = {
            "an unknown key in contracts-manifest.json": (
                {"embedded_edit": lambda manifest: dict(manifest, forged=True)}, "archive.manifest_invalid"),
            "an unknown key in a generator record": ({
                "embedded_edit": lambda manifest: dict(manifest, generator=dict(manifest["generator"], forged="x")),
                "release_edit": lambda manifest: manifest["clients"]["python"]["generator"].update(forged="x")},
                "manifest.malformed"),
        }
        for label, (edits, rule) in cases.items():
            with self.subTest(label):
                self.assertRefused(rule, self.forge(label.replace(" ", "-"), **edits))

    def test_unreadable_metadata_sources_refused(self) -> None:
        def retag(change):
            def hook(contracts: SyntheticContracts) -> None:
                change(contracts)
                contracts.commit_all("Synthetic metadata source edited, then tagged by hand")
                contracts.push_main()
                contracts.tag_annotated(force=True)
                contracts.push_tag(force=True)
            return hook

        release_source = (pl_contracts.ROOT / contract_artifact.RELEASE_SOURCE_PATH).read_text(encoding="utf-8")
        cases = {
            "a release.py with two publication texts": lambda contracts: contracts.write(
                support.RELEASE_RELATIVE, release_source + '\nSYNTHETIC = {"publication": "another text"}\n'),
            "a generator configuration without generatorName": lambda contracts: contracts.write(
                support.generator_relative("python", "config"), json_text({"additionalProperties": {"synthetic": True}})),
        }
        for label, change in cases.items():
            with self.subTest(label):
                contracts, published = self.own_release(label.replace(" ", "-"), before_build=retag(change))
                self.assertRefused("release.format_unsupported", published, contracts=contracts)


class TagTreeAnchorTest(LoaderTestCase):
    """Assets, toolchain pins and golden digests anchored to the tag's own tree (consistent swaps included)."""

    def test_asset_differs_from_tag_blob_refused(self) -> None:
        tag = self.base.tag
        swaps = {
            "specification": (f"openapi-{tag}.yaml", lambda data: data.replace(b"not a product contract", b"swapped after tagging")),
            "registry": ("currency-registry.v1.json", lambda data: data.replace(b"placeholder", b"swapped")),
            "provider source": (PROVIDER, lambda data: data.replace(b"provider source", b"swapped source")),
            "fixture": (FIXTURE, lambda data: data.replace(b"fixture", b"swapped")),
        }
        for label, (name, change) in swaps.items():
            with self.subTest(f"consistent swap of the {label}"):
                release_dir = self.fresh_release(label.replace(" ", "-"))
                (release_dir / name).write_bytes(change((release_dir / name).read_bytes()))
                reseal(release_dir)
                self.assertRefused("anchor.asset_blob_mismatch", release_dir)
        with self.subTest("real build from a dirty working tree"):
            def dirty_fixture(contracts: SyntheticContracts) -> None:
                contracts.write(f"{support.FIXTURES_RELATIVE}/{FIXTURE}", json_text({"schema_version": 1, "synthetic": "edited after tagging"}))

            contracts, published = self.own_release("dirty", before_build=dirty_fixture)
            self.assertRefused("anchor.asset_blob_mismatch", published, contracts=contracts)

    def test_toolchain_pins_differ_from_tag_refused(self) -> None:
        swaps = {
            "release toolchain": lambda manifest: manifest["toolchain"].update(oasdiff="9.9.9-synthetic"),
            "client generator": lambda manifest: manifest["clients"]["python"]["generator"].update(version="9.9.9-synthetic"),
        }
        for label, edit in swaps.items():
            with self.subTest(label):
                release_dir = self.fresh_release(label.replace(" ", "-"))
                reseal(release_dir, edit=edit)
                self.assertRefused("anchor.toolchain_mismatch", release_dir)
        with self.subTest("real build with uncommitted pins"):
            def edit_pins(contracts: SyntheticContracts) -> None:
                path = contracts.repo / VERSIONS_RELATIVE
                pins = json.loads(path.read_text(encoding="utf-8"))
                pins["spectral"]["version"] = "9.9.9-synthetic"
                path.write_text(json_text(pins), encoding="utf-8")

            contracts, published = self.own_release("pins", before_build=edit_pins)
            self.assertRefused("anchor.toolchain_mismatch", published, contracts=contracts)

    def test_tag_tree_missing_source_file_refused(self) -> None:
        for relative in (f"{SPEC_DIRECTORY}/{PROVIDER}", GOLDEN_RELATIVE, VERSIONS_RELATIVE):
            with self.subTest(relative):
                contracts, published = self.own_release(relative.replace("/", "-"), uncommitted=(relative,))
                self.assertRefused("anchor.tag_file_missing", published, contracts=contracts)

    def test_asset_name_collision_refused(self) -> None:
        def colliding_fixture(contracts: SyntheticContracts) -> None:
            contracts.write(f"{support.FIXTURES_RELATIVE}/{PROVIDER}", json_text({"synthetic": "fixture named like a provider source"}))

        contracts, published = self.own_release("collision", after_source=colliding_fixture)
        self.assertRefused("anchor.asset_name_collision", published, contracts=contracts)

    def test_golden_invalid_refused(self) -> None:
        def unfolded(golden: dict) -> None:
            path = sorted(golden["python"]["files"])[0]
            golden["python"]["files"][path] = synthetic_digest("edited without refolding")

        edits = {
            "language missing": lambda golden: golden.pop("python"),
            "files edited without refolding": unfolded,
            "unknown record key": lambda golden: golden["kotlin"].update(note="synthetic"),
        }
        for label, edit in edits.items():
            with self.subTest(label):
                contracts, published = self.own_release(label.replace(" ", "-"), golden_edit=edit)
                self.assertRefused("anchor.golden_invalid", published, contracts=contracts)

    def test_golden_spec_digest_mismatch_refused(self) -> None:
        def stale(golden: dict) -> None:
            golden["kotlin"]["spec_sha256"] = sha256_bytes(b"an older synthetic specification\n")

        contracts, published = self.own_release("stale-golden", golden_edit=stale)
        self.assertRefused("anchor.golden_spec_mismatch", published, contracts=contracts)

    def test_archive_tree_digest_mismatch_vs_golden_refused(self) -> None:
        with self.subTest("real build from regenerated output"):
            def regenerate_differently(contracts: SyntheticContracts) -> None:
                path = contracts.generated / "python" / "pennilogic_synthetic" / "models" / "synthetic_probe.py"
                path.write_text("class SyntheticProbe:\n    total: int\n", encoding="utf-8", newline="\n")
                contracts.refresh_embedded_manifest("python")

            contracts, published = self.own_release("regenerated", before_build=regenerate_differently)
            self.assertRefused("anchor.golden_tree_mismatch", published, contracts=contracts)
        with self.subTest("consistent swap of the archive"):
            tag, prefix = self.base.tag, archive_prefix("python", self.base.tag)
            release_dir = self.fresh_release("swapped-archive")
            target = f"{prefix}/pennilogic_synthetic/models/synthetic_probe.py"
            rewrite_archive(release_dir, tag, "python", lambda members: refresh_embedded(
                replace_member_data(members, target, lambda data: data.replace(b"str", b"int")), prefix))
            embedded = json.loads(dict((info.name, data) for info, data in read_archive(
                (release_dir / archive_name("python", tag)).read_bytes()))[f"{prefix}/{MANIFEST_NAME}"])
            reseal(release_dir, edit=lambda manifest: manifest["clients"]["python"].update(tree_sha256=embedded["tree_sha256"]))
            self.assertRefused("anchor.golden_tree_mismatch", release_dir)


class ArchiveContentTest(LoaderTestCase):
    """Archive decompression bounds, the embedded manifest and the recomputed tree (consistent swaps)."""

    def swap_python_archive(self, transform, name: str = "release", edit=None) -> Path:
        release_dir = self.fresh_release(name)
        rewrite_archive(release_dir, self.base.tag, "python", transform)
        reseal(release_dir, edit=edit)
        return release_dir

    @property
    def prefix(self) -> str:
        return archive_prefix("python", self.base.tag)

    def test_archive_path_traversal_refused(self) -> None:
        release_dir = self.swap_python_archive(lambda members: members + [member(f"{self.prefix}/../escape.txt")])
        self.assertRefused("archive.unsafe_path", release_dir)
        self.assertEqual(list(self.work.rglob("escape.txt")), [])

    def test_archive_not_gzip_or_not_tar_refused(self) -> None:
        mutations = {
            "not gzip": lambda path: path.write_bytes(b"synthetic, not an archive\n"),
            "gzip of non-tar bytes": lambda path: path.write_bytes(gzip.compress(b"synthetic, not a tar stream\n" * 64, mtime=0)),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                release_dir = self.fresh_release(label.replace(" ", "-"))
                mutate(release_dir / archive_name("python", self.base.tag))
                reseal(release_dir)
                self.assertRefused("archive.malformed", release_dir)

    def test_archive_uncompressed_size_bounded(self) -> None:
        release_dir = self.fresh_release()
        raw = gzip.decompress((release_dir / archive_name("kotlin", self.base.tag)).read_bytes())
        with mock.patch.object(contract_artifact, "MAX_ARCHIVE_TAR_BYTES", len(raw) - 1):
            self.assertRefused("archive.too_large", release_dir)

    def test_archive_embedded_manifest_missing_refused(self) -> None:
        manifest = f"{self.prefix}/{MANIFEST_NAME}"
        release_dir = self.swap_python_archive(lambda members: [item for item in members if item[0].name != manifest])
        self.assertRefused("archive.manifest_missing", release_dir)

    def test_archive_embedded_manifest_invalid_refused(self) -> None:
        def edit_embedded(change):
            def transform(members):
                return replace_member_data(members, f"{self.prefix}/{MANIFEST_NAME}", change)
            return transform

        def drop(key: str):
            def change(data: bytes) -> bytes:
                manifest = json.loads(data)
                manifest.pop(key)
                return json_text(manifest).encode("utf-8")
            return change

        mutations = {
            "not json": lambda data: data[: len(data) // 2],
            "duplicate key": lambda data: data.replace(b"{\n", b'{\n  "target": "python",\n', 1),
            "target missing": drop("target"),
            "files malformed": lambda data: json_text({**json.loads(data), "files": {"path": "x"}}).encode("utf-8"),
            "schema version 2": lambda data: json_text({**json.loads(data), "schema_version": 2}).encode("utf-8"),
        }
        for label, change in mutations.items():
            with self.subTest(label):
                release_dir = self.swap_python_archive(edit_embedded(change), name=label.replace(" ", "-"))
                self.assertRefused("archive.manifest_invalid", release_dir)

    def test_archive_content_differs_from_embedded_manifest_refused(self) -> None:
        source = f"{self.prefix}/pennilogic_synthetic/__init__.py"
        mutations = {
            "file changed": lambda members: replace_member_data(members, source, lambda data: data + b"# changed\n"),
            "file added": lambda members: members + [member(f"{self.prefix}/pennilogic_synthetic/extra.py")],
        }
        for label, transform in mutations.items():
            with self.subTest(label):
                release_dir = self.swap_python_archive(transform, name=label.replace(" ", "-"))
                self.assertRefused("archive.tree_mismatch", release_dir)

    def test_archive_embedded_manifest_disagrees_with_release_refused(self) -> None:
        def embedded(key: str, value: object):
            def transform(members):
                return replace_member_data(members, f"{self.prefix}/{MANIFEST_NAME}",
                                           lambda data: json_text({**json.loads(data), key: value}).encode("utf-8"))
            return transform

        mutations = {
            "target": (embedded("target", "kotlin"), None),
            "spec version": (embedded("spec_version", "1.2.4"), None),
            "registry digest": (embedded("currency_registry_sha256", synthetic_digest("x")), None),
            "runtime digest": (embedded("runtime_sha256", synthetic_digest("x")), None),
            "release tree digest": (lambda members: members,
                                    lambda manifest: manifest["clients"]["python"].update(tree_sha256=synthetic_digest("x"))),
        }
        for label, (transform, edit) in mutations.items():
            with self.subTest(label):
                release_dir = self.swap_python_archive(transform, name=label.replace(" ", "-"), edit=edit)
                self.assertRefused("archive.manifest_mismatch", release_dir)


class ArchiveReaderDifferentialTest(LoaderTestCase):
    """Consistent swaps that only a lenient tar reader accepts (review round 1). Each archive verified as golden
    under Python's tarfile alone, while GNU tar or npm's node-tar unpacked other bytes, or the loader crashed."""

    @property
    def prefix(self) -> str:
        return archive_prefix("python", self.base.tag)

    def swap_raw(self, release_dir: Path, language: str, raw: bytes) -> None:
        (release_dir / archive_name(language, self.base.tag)).write_bytes(gzip_bytes(raw))
        reseal(release_dir)

    def test_pax_global_header_size_differential_refused(self) -> None:
        """A pax global `size`: tarfile skips the last member by its ustar size and reads the golden bytes; GNU tar
        and node-tar skip by the global size and unpack the hidden member over pennilogic_synthetic/__init__.py."""
        release_dir = self.fresh_release()
        *head, (last, last_data) = read_archive((release_dir / archive_name("python", self.base.tag)).read_bytes())
        assert last_data is not None
        init = f"{self.prefix}/pennilogic_synthetic/__init__.py"
        hidden = b"raise SystemExit('unverified synthetic bytes')".ljust(len(last_data) - 1) + b"\n"
        tail = tar_padded(last_data) + extended_header(tarfile.XHDTYPE, pax_record("size", str(len(hidden))) + pax_record("path", init))
        tail += ustar_header(init, len(hidden)) + tar_padded(hidden)
        raw = b"".join(raw_member(info, data) for info, data in head)
        raw += extended_header(tarfile.XGLTYPE, pax_record("size", str(len(last_data))))
        raw += ustar_header(last.name, len(tail)) + tail + END_OF_ARCHIVE
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:  # tarfile alone sees only the golden bytes
            seen = [archive.extractfile(info).read() for info in archive if info.isfile()]  # type: ignore[union-attr]
        self.assertEqual((seen[-1], hidden in seen), (last_data, False))
        self.swap_raw(release_dir, "python", raw)
        self.assertRefused("archive.ambiguous", release_dir)

    def test_gnu_long_name_with_pax_path_differential_refused(self) -> None:
        """A GNU long name followed by a pax `path`: tarfile keeps the long name, GNU tar and node-tar the pax path."""
        init = f"{self.prefix}/pennilogic_synthetic/__init__.py"
        model = f"{self.prefix}/pennilogic_synthetic/models/synthetic_probe.py"
        cases = {
            "two golden files cross-named": {init: model, model: init},
            "a pax path outside the prefix": {f"{self.prefix}/pyproject.toml": f"{self.prefix}/../outside-prefix.toml"},
        }
        for label, renames in cases.items():
            with self.subTest(label):
                release_dir = self.fresh_release(label.replace(" ", "-"))
                raw = b""
                for info, data in read_archive((release_dir / archive_name("python", self.base.tag)).read_bytes()):
                    if info.name in renames:
                        assert data is not None
                        raw += gnu_long_header(info.name) + extended_header(tarfile.XHDTYPE, pax_record("path", renames[info.name]))
                        raw += ustar_header(info.name, len(data)) + tar_padded(data)
                    else:
                        raw += raw_member(info, data)
                self.swap_raw(release_dir, "python", raw + END_OF_ARCHIVE)
                self.assertRefused("archive.ambiguous", release_dir)

    def test_extended_header_link_name_differential_refused(self) -> None:
        """Review round 2, PSEC-R2-1: one link-name byte in the Kotlin archive's extended header block. tarfile and GNU
        tar unpacked the golden tree; node-tar dropped the long golden file and wrote .../models/SyntheticProbeRes."""
        release_dir = self.fresh_release()
        raw = gzip.decompress((release_dir / archive_name("kotlin", self.base.tag)).read_bytes())
        long_name = (f"{archive_prefix('kotlin', self.base.tag)}/src/main/kotlin/com/pennilogic/synthetic/models/"
                     "SyntheticProbeResponseWithALongGeneratedName.kt")
        start = member_offsets(raw)[long_name][0]
        self.assertEqual(raw[start + 156:start + 157], tarfile.XHDTYPE)
        self.swap_raw(release_dir, "kotlin", patch_header(raw, start, 157, b"x"))
        self.assertRefused("archive.noncanonical", release_dir)

    def test_windows_alias_member_refused(self) -> None:
        """Review round 2, PSEC-R2-2: Windows drops a trailing '.' or ' ' from a name, so a member `__init__.py.` after
        `__init__.py` overwrites it there and nowhere else. With the golden bytes in the alias and other bytes at the
        golden path, a Windows verifier hashed the golden tree while GNU tar, node-tar and tarfile on Linux unpacked
        the other bytes (the reviewer emulated the Win32 rewrite on Linux). Here: the alias member alone."""
        init = f"{self.prefix}/pennilogic_synthetic/__init__.py"

        def with_alias(members, suffix: str):
            golden = dict((info.name, data) for info, data in members)[init]
            return members + [member(init + suffix, golden)]

        for suffix in (".", " "):
            with self.subTest(alias=f"__init__.py{suffix}"):
                release_dir = self.fresh_release(f"alias-{ord(suffix)}")
                rewrite_archive(release_dir, self.base.tag, "python", lambda members, suffix=suffix: with_alias(members, suffix))
                reseal(release_dir)
                self.assertRefused("archive.unsafe_path", release_dir)

    def test_noncanonical_mode_or_extra_directory_refused(self) -> None:
        """Modes and directories are outside the tree digest; GNU tar unpacked gradlew without its executable bit."""
        prefix = archive_prefix("kotlin", self.base.tag)

        def not_executable(members):
            for info, _ in members:
                if info.name == f"{prefix}/gradlew":
                    info.mode = 0o644
            return members

        cases = {
            "gradlew not executable": not_executable,
            "an extra empty directory": lambda members: members + [member(f"{prefix}/.git", kind=tarfile.DIRTYPE)],
        }
        for label, transform in cases.items():
            with self.subTest(label):
                release_dir = self.fresh_release(label.replace(" ", "-"))
                rewrite_archive(release_dir, self.base.tag, "kotlin", transform)
                reseal(release_dir)
                self.assertRefused("archive.noncanonical", release_dir)

    def test_gzip_stream_must_be_exactly_one_member(self) -> None:
        name = archive_name("python", self.base.tag)
        cases = {
            "two gzip members": (lambda data: gzip_bytes(gzip.decompress(data)[:1024]) + gzip_bytes(gzip.decompress(data)[1024:]),
                                 "archive.ambiguous"),
            "zero bytes after the gzip member": (lambda data: data + b"\0" * 8, "archive.ambiguous"),
            "a truncated gzip member": (lambda data: data[:-8], "archive.malformed"),
        }
        for label, (mutate, rule) in cases.items():
            with self.subTest(label):
                release_dir = self.fresh_release(label.replace(" ", "-"))
                (release_dir / name).write_bytes(mutate((release_dir / name).read_bytes()))
                reseal(release_dir)
                self.assertRefused(rule, release_dir)

    def test_hostile_archives_refused_without_a_crash(self) -> None:
        """Each of these raised RecursionError or UnicodeEncodeError out of the loader instead of a static rule."""
        prefix = self.prefix
        chained = b"".join(gnu_long_header(f"{prefix}/chain-{index}.py") for index in range(5000))
        chained += ustar_header(f"{prefix}/x.py", 2) + tar_padded(b"x\n") + END_OF_ARCHIVE
        cases = {
            "5000 chained GNU long-name headers": (lambda release_dir: self.swap_raw(release_dir, "python", chained), "archive.ambiguous"),
            "a member name of 1502 components": (lambda release_dir: rewrite_archive(
                release_dir, self.base.tag, "python", lambda members: members + [member(prefix + "/a" * 1500 + "/x.py")]),
                "archive.unsafe_path"),
            "a member name that is not UTF-8": (lambda release_dir: rewrite_archive(
                release_dir, self.base.tag, "python", lambda members: members + [member(f"{prefix}/pennilogic_synthetic/x\udcff.py")]),
                "archive.ambiguous"),
        }
        for label, (mutate, rule) in cases.items():
            with self.subTest(label):
                release_dir = self.fresh_release(label.replace(" ", "-"))
                mutate(release_dir)
                reseal(release_dir)
                self.assertRefused(rule, release_dir)

    def test_unpacking_failure_refused(self) -> None:
        release_dir = self.fresh_release()
        with mock.patch.object(tarfile.TarFile, "extractall", side_effect=RecursionError("synthetic")):
            self.assertRefused("archive.malformed", release_dir)


class ArchiveMemberRulesTest(unittest.TestCase):
    """Member checks before unpacking, on archives written by the real release.deterministic_tar_gz.

    The control (setUp) is the builder's archive of a tree with a nested package, an executable gradlew, and a
    file and a directory longer than the ustar name field (so the builder writes extended header `path`
    records for them, the directory's with its trailing slash).
    """

    PREFIX = "pennilogic-contracts-python-v1.2.3"
    NAME = f"{PREFIX}.tar.gz"
    LONG = f"pkg/models/{'long_' * 18}name.py"
    LONG_DIRECTORY = f"pkg/{'deep_' * 18}dir"

    def setUp(self) -> None:
        self.root = temporary_root()
        self.addCleanup(shutil.rmtree, self.root, True)
        tree = self.root / "tree"
        for relative, content in (("README.md", "synthetic\n"), ("gradlew", "#!/bin/sh\nexit 0\n"), ("pkg/__init__.py", ""),
                                  ("pkg/models/probe.py", "x = 1\n"), (self.LONG, "y = 2\n"),
                                  (f"{self.LONG_DIRECTORY}/leaf.py", "z = 3\n")):
            (tree / relative).parent.mkdir(parents=True, exist_ok=True)
            (tree / relative).write_text(content, encoding="utf-8", newline="\n")
        built = release.deterministic_tar_gz(tree, self.PREFIX, SYNTHETIC_EPOCH)
        self.clean = read_archive(built)
        self.raw = gzip.decompress(built)
        self.assertEqual(gzip.decompress(write_archive(self.clean)), self.raw)  # the helpers write the builder's bytes
        self.assertEqual(len(self.inspect(self.clean)), len(self.clean))  # control
        self.offsets = {name[len(self.PREFIX) + 1:]: offsets for name, offsets in member_offsets(self.raw).items()}
        self.assertLess(self.offsets[self.LONG_DIRECTORY][0], self.header(self.LONG_DIRECTORY))  # it has an extended header

    def inspect(self, members) -> list[tarfile.TarInfo]:
        return contract_artifact.inspect_archive_members(gzip.decompress(write_archive(members)), self.PREFIX, self.NAME)

    def assertRule(self, rule: str, members) -> None:
        with self.assertRaises(ArtifactRefused) as caught:
            self.inspect(members)
        self.assertEqual(caught.exception.rule, rule, caught.exception.detail)

    def assertRawRule(self, rule: str, raw: bytes) -> None:
        with self.assertRaises(ArtifactRefused) as caught:
            contract_artifact.inspect_archive_members(raw, self.PREFIX, self.NAME)
        self.assertEqual(caught.exception.rule, rule, caught.exception.detail)

    def header(self, relative: str) -> int:
        """Offset of the member's own ustar header block (after its extended header, if it has one)."""
        return self.offsets[relative][1] - tarfile.BLOCKSIZE

    def with_extended_records(self, records: bytes) -> bytes:
        """The control archive with the long member's extended header records replaced."""
        start, data = self.offsets[self.LONG]
        return self.raw[:start] + extended_header(tarfile.XHDTYPE, records) + self.raw[data - tarfile.BLOCKSIZE:]

    def edited(self, relative: str, **changes: object) -> list:
        """The control members, in the same order, with one member's header fields changed."""
        members = []
        for info, data in self.clean:
            if info.name == f"{self.PREFIX}/{relative}":
                info = copy.copy(info)
                for key, value in changes.items():
                    setattr(info, key, value)
            members.append((info, data))
        return members

    def test_header_types_that_readers_apply_differently_refused(self) -> None:
        """The parser-differential mechanisms: tarfile and GNU tar or node-tar would read different members."""
        readme = self.header("README.md")
        long_name = f"{self.PREFIX}/README.md".encode("ascii") + b"\0"
        cases = {
            "a pax global header": extended_header(tarfile.XGLTYPE, pax_record("comment", "synthetic")) + self.raw,
            "a GNU long name (GNU header)": self.raw[:readme] + gnu_long_header(f"{self.PREFIX}/README.md") + self.raw[readme:],
            "a GNU long name (POSIX header)": (self.raw[:readme] + ustar_header("././@LongLink", len(long_name), tarfile.GNUTYPE_LONGNAME)
                                               + tar_padded(long_name) + self.raw[readme:]),
            "a GNU long link": self.raw[:readme] + gnu_long_header("README.md", tarfile.GNUTYPE_LONGLINK) + self.raw[readme:],
            "an old-style regular file type": patch_header(self.raw, readme, 156, tarfile.AREGTYPE),
            "a GNU sparse member": patch_header(self.raw, readme, 156, tarfile.GNUTYPE_SPARSE),
            "a Solaris extended header": patch_header(self.raw, readme, 156, tarfile.SOLARIS_XHDTYPE),
        }
        for label, raw in cases.items():
            with self.subTest(label):
                self.assertRawRule("archive.ambiguous", raw)

    def test_extended_header_other_than_one_path_record_refused(self) -> None:
        path = f"{self.PREFIX}/{self.LONG}"
        record = pax_record("path", path)
        self.assertEqual(self.with_extended_records(record), self.raw)  # control: the builder's own extended header
        end_of_members = len(self.raw.rstrip(b"\0")) + -len(self.raw.rstrip(b"\0")) % tarfile.BLOCKSIZE
        start, data = self.offsets[self.LONG]
        cases = {
            "a size record": self.with_extended_records(record + pax_record("size", "6")),
            "a linkpath record": self.with_extended_records(record + pax_record("linkpath", "README.md")),
            "an mtime record": self.with_extended_records(record + pax_record("mtime", str(SYNTHETIC_EPOCH))),
            "a hdrcharset record": self.with_extended_records(pax_record("hdrcharset", "BINARY") + record),
            "a second path record": self.with_extended_records(record + record),
            "no path record": self.with_extended_records(pax_record("comment", "synthetic")),
            "a record whose length is one too long": self.with_extended_records(
                str(len(record) + 1).encode("ascii") + record[len(str(len(record))):]),
            "two extended headers before one member": (self.raw[:start] + extended_header(tarfile.XHDTYPE, record)
                                                       + self.raw[start:]),
            "an extended header that precedes no member": (self.raw[:end_of_members] + extended_header(tarfile.XHDTYPE, record)
                                                           + END_OF_ARCHIVE),
        }
        self.assertLess(data, end_of_members)
        for label, raw in cases.items():
            with self.subTest(label):
                self.assertRawRule("archive.ambiguous", raw)

    def test_extended_header_block_other_than_the_builders_refused(self) -> None:
        """The extended header block's own fields (review round 2, PSEC-R2-1). node-tar refuses a link name or an empty
        name there and unpacks the member under its truncated ustar name; tarfile and GNU tar use the `path` record."""
        start = self.offsets[self.LONG][0]
        self.assertEqual((self.raw[start + 156:start + 157], self.raw[start:start + 15]), (tarfile.XHDTYPE, b"././@PaxHeader\0"))
        older_python = patch_header(patch_header(self.raw, start, 329, b"0000000\0"), start, 337, b"0000000\0")
        self.assertEqual(len(contract_artifact.inspect_archive_members(older_python, self.PREFIX, self.NAME)), len(self.clean))
        cases = {
            "a link name": (157, b"x"),
            "an empty name": (0, b"\0" * 100),
            "another name": (0, b"../../../../tmp/synthetic".ljust(100, b"\0")),
            "a mode": (100, b"0007777\0"),
            "a numeric owner": (108, b"0000001\0"),
            "a numeric group": (116, b"0000001\0"),
            "a modification time": (136, b"%011o\0" % SYNTHETIC_EPOCH),
            "an owner name": (265, b"root"),
            "a group name": (297, b"root"),
            "a device major number": (329, b"0000001\0"),
            "a device minor number": (337, b"0000001\0"),
            "header padding": (500, b"Z"),
        }
        for label, (field, value) in cases.items():
            with self.subTest(label):
                self.assertRawRule("archive.noncanonical", patch_header(self.raw, start, field, value))

    def test_header_fields_that_readers_decode_differently_refused(self) -> None:
        readme, package = self.header("README.md"), self.header("pkg")
        with_data = patch_header(self.raw, package, 124, b"%011o\0" % tarfile.BLOCKSIZE)
        cases = {
            "GNU magic": patch_header(self.raw, readme, 257, b"ustar  \0"),
            "no magic (V7)": patch_header(self.raw, readme, 257, b"\0" * 8),
            "a base-256 size": patch_header(self.raw, readme, 124, b"\x80" + (10).to_bytes(11, "big")),
            "a space-terminated mode": patch_header(self.raw, readme, 100, b"000644 \0"),
            "the ustar prefix field": patch_header(self.raw, readme, 345, b"pkg"),
            "a directory that declares data": (with_data[:package + tarfile.BLOCKSIZE] + b"x" * tarfile.BLOCKSIZE
                                               + with_data[package + tarfile.BLOCKSIZE:]),
        }
        for label, raw in cases.items():
            with self.subTest(label):
                self.assertRawRule("archive.ambiguous", raw)

    def test_tarfile_must_read_what_the_strict_reader_read(self) -> None:
        strict = contract_artifact._strict_members
        with mock.patch.object(contract_artifact, "_strict_members", lambda raw, prefix, name: strict(raw, prefix, name)[:-1]):
            self.assertRawRule("archive.ambiguous", self.raw)

    def test_noncanonical_member_refused(self) -> None:
        """Valid, unambiguous tar members that release.deterministic_tar_gz never writes."""
        readme, package = self.header("README.md"), self.header("pkg")
        size = len("synthetic\n")
        records_end = self.offsets[self.LONG][0] + tarfile.BLOCKSIZE + len(pax_record("path", f"{self.PREFIX}/{self.LONG}"))
        members = {
            "a plain file with mode 600": self.edited("README.md", mode=0o600),
            "a plain file marked executable": self.edited("README.md", mode=0o755),
            "gradlew not executable": self.edited("gradlew", mode=0o644),
            "a directory with mode 700": self.edited("pkg", mode=0o700),
            "a numeric owner": self.edited("README.md", uid=1000),
            "an owner name": self.edited("README.md", uname="synthetic"),
            "a link name on a plain file": self.edited("README.md", linkname="pkg/__init__.py"),
            "another modification time": self.edited("README.md", mtime=SYNTHETIC_EPOCH + 1),
            "members out of order": [self.clean[1], self.clean[0], *self.clean[2:]],
            "a missing parent directory": [item for item in self.clean if item[0].name != f"{self.PREFIX}/pkg/models"],
            "an empty directory": sorted(self.clean + [member(f"{self.PREFIX}/pkg/empty", kind=tarfile.DIRTYPE)],
                                         key=lambda item: item[0].name),
        }
        for label, edited in members.items():
            with self.subTest(label):
                self.assertRule("archive.noncanonical", edited)
        raws = {
            "device numbers": patch_header(self.raw, readme, 329, b"0000001\0"),
            "header padding": patch_header(self.raw, readme, 500, b"x"),
            "bytes after the end of the name": patch_header(self.raw, readme, len(f"{self.PREFIX}/README.md") + 1, b"x"),
            "a directory name without its trailing slash": patch_header(self.raw, package, len(f"{self.PREFIX}/pkg"), b"\0"),
            "data in the padding after a file": (self.raw[:readme + tarfile.BLOCKSIZE + size] + b"x"
                                                 + self.raw[readme + tarfile.BLOCKSIZE + size + 1:]),
            "data in the padding after extended header records": self.raw[:records_end] + b"x" + self.raw[records_end + 1:],
            "an extended header for a short name": (self.raw[:readme]
                                                    + extended_header(tarfile.XHDTYPE, pax_record("path", f"{self.PREFIX}/README.md"))
                                                    + self.raw[readme:]),
            "a ustar name that is not the start of the extended path": patch_header(self.raw, self.header(self.LONG), 0, b"q"),
        }
        for label, raw in raws.items():
            with self.subTest(label):
                self.assertRawRule("archive.noncanonical", raw)

    def test_malformed_tar_structure_refused(self) -> None:
        readme = self.header("README.md")
        _, last = self.offsets["pkg/models/probe.py"]
        last_end = last + tarfile.BLOCKSIZE
        bad_checksum = bytearray(self.raw)
        bad_checksum[readme + 265] = ord("x")  # the owner name field, without recomputing the checksum
        later = bytearray(self.raw)
        later[self.header("pkg/__init__.py") + 265] = ord("x")  # tarfile stops quietly there instead of failing
        cases = {
            "a bad header checksum": bytes(bad_checksum),
            "a bad header checksum after the first member": bytes(later),
            "member data cut off": self.raw[:last + 3],
            "no end-of-archive marker": self.raw[:last_end],
            "a single zero block": self.raw[:last_end] + b"\0" * tarfile.BLOCKSIZE,
            "data after the end-of-archive marker": self.raw + b"\x01" + b"\0" * (tarfile.BLOCKSIZE - 1),
            "a length that is not a multiple of 512": self.raw + b"\0" * 100,
        }
        self.assertEqual(self.raw[last_end:].strip(b"\0"), b"")  # the last member really is the last
        for label, raw in cases.items():
            with self.subTest(label):
                self.assertRawRule("archive.malformed", raw)

    def test_member_names_are_bounded_printable_ascii(self) -> None:
        problem = contract_artifact._member_path_problem
        prefix = self.PREFIX

        def of_length(length: int) -> str:
            name = prefix
            while len(name) < length:
                name += "/" + "a" * min(200, length - len(name) - 1)
            return name

        pairs = {
            "name length": (of_length(contract_artifact.MAX_MEMBER_NAME_LENGTH),
                            of_length(contract_artifact.MAX_MEMBER_NAME_LENGTH + 1)),
            "component count": (prefix + "/a" * (contract_artifact.MAX_MEMBER_DEPTH - 1),
                                prefix + "/a" * contract_artifact.MAX_MEMBER_DEPTH),
            "component length": (prefix + "/" + "a" * contract_artifact.MAX_MEMBER_COMPONENT_LENGTH,
                                 prefix + "/" + "a" * (contract_artifact.MAX_MEMBER_COMPONENT_LENGTH + 1)),
            "non-ASCII character": (prefix + "/cafe.py", prefix + "/caf\u00e9.py"),
            "surrogate (a name that is not UTF-8)": (prefix + "/x.py", prefix + "/x\udcff.py"),
        }
        for label, (passing, failing) in pairs.items():
            with self.subTest(label):
                self.assertIsNone(problem(passing, prefix))
                self.assertEqual(problem(failing, prefix)[0], "archive.unsafe_path")
        path = f"{prefix}/{self.LONG}".encode("ascii")
        raws = {
            "a non-ASCII byte in the ustar name": patch_header(self.raw, self.header("README.md"), len(prefix) + 1, b"\xff"),
            "a UTF-8 extended path": self.with_extended_records(pax_record("path", path[:-3] + "\u00e9.py".encode("utf-8"))),
            "an extended path that is not UTF-8": self.with_extended_records(pax_record("path", path[:-3] + b"\xff.py")),
        }
        for label, raw in raws.items():
            with self.subTest(label):
                self.assertRawRule("archive.unsafe_path", raw)

    def test_absolute_parent_or_drive_member_path_refused(self) -> None:
        for name in ("/etc/synthetic", f"{self.PREFIX}/../escape", f"{self.PREFIX}/./x", f"{self.PREFIX}//x",
                     f"{self.PREFIX}\\..\\escape", "C:/synthetic", f"{self.PREFIX}/file:stream", f"{self.PREFIX}/bell\x07"):
            with self.subTest(name=ascii(name)):
                self.assertRule("archive.unsafe_path", self.clean + [member(name)])

    def test_member_names_windows_rewrites_or_reserves_refused(self) -> None:
        """Review round 2, PSEC-R2-2: components outside the POSIX portable file name characters, ending in '.', or
        naming a Windows device. Windows drops a trailing '.' or ' ', opens a device for CON or nul.py, and resolves an
        8.3 short name such as PKG~1 to another directory, so it would unpack another tree than every other reader."""
        problem = contract_artifact._member_path_problem
        pairs = {
            "a trailing dot": ("pkg/__init__.py", "pkg/__init__.py."),
            "a trailing dot on a directory": ("pkg/models/probe.py", "pkg./models/probe.py"),
            "three dots": ("pkg/x..y.py", "pkg/..."),
            "a trailing space": ("pkg/__init__.py", "pkg/__init__.py "),
            "a space inside a name": ("pkg/two_words.py", "pkg/two words.py"),
            "an 8.3 short name": ("pkg/PKG_1", "pkg/PKG~1"),
            "a device name": ("pkg/console.py", "pkg/CON"),
            "a device name in lower case with an extension": ("pkg/nullable.py", "pkg/nul.py"),
            "a numbered device name": ("pkg/com10.kt", "pkg/com1.kt"),
            "a printer device name": ("pkg/lpt.ts", "pkg/LPT0.ts"),
            "a character outside the portable set that Windows accepts": ("pkg/a-b_c.d", "pkg/a+b.py"),
            **{f"the Windows reserved character {character}": ("pkg/x.py", f"pkg/x{character}.py") for character in '*?"<>|'},
        }
        for label, (passing, failing) in pairs.items():
            with self.subTest(label):
                self.assertIsNone(problem(f"{self.PREFIX}/{passing}", self.PREFIX))
                self.assertEqual(problem(f"{self.PREFIX}/{failing}", self.PREFIX)[0], "archive.unsafe_path")
        for name in ("pkg/__init__.py.", "pkg/__init__.py ", "pkg/NUL", "pkg/aux.py", "PKG~1/x.py"):
            with self.subTest(member=name):
                self.assertRule("archive.unsafe_path", self.clean + [member(f"{self.PREFIX}/{name}")])

    def test_member_outside_prefix_refused(self) -> None:
        for name, kind in (("other/x.py", tarfile.REGTYPE), (self.PREFIX, tarfile.DIRTYPE),
                           ("pennilogic-contracts-kotlin-v1.2.3/x.kt", tarfile.REGTYPE)):
            with self.subTest(name=name):
                self.assertRule("archive.outside_prefix", self.clean + [member(name, kind=kind)])

    def test_link_member_refused(self) -> None:
        for label, kind in (("symlink", tarfile.SYMTYPE), ("hardlink", tarfile.LNKTYPE)):
            with self.subTest(label):
                self.assertRule("archive.link_member", self.clean + [member(f"{self.PREFIX}/link", kind=kind, linkname="README.md")])

    def test_device_fifo_or_other_member_refused(self) -> None:
        for label, kind in (("character device", tarfile.CHRTYPE), ("block device", tarfile.BLKTYPE),
                            ("fifo", tarfile.FIFOTYPE), ("contiguous file", tarfile.CONTTYPE)):
            with self.subTest(label):
                self.assertRule("archive.special_member", self.clean + [member(f"{self.PREFIX}/special", kind=kind)])

    def test_duplicate_member_refused(self) -> None:
        self.assertRule("archive.duplicate_member", self.clean + [member(f"{self.PREFIX}/README.md", b"replaced\n")])

    def test_case_collision_refused(self) -> None:
        self.assertRule("archive.case_collision", self.clean + [member(f"{self.PREFIX}/readme.MD")])

    def test_member_count_bounded(self) -> None:
        with mock.patch.object(contract_artifact, "MAX_ARCHIVE_MEMBERS", len(self.clean)):
            self.assertEqual(len(self.inspect(self.clean)), len(self.clean))
            self.assertRule("archive.too_many_members", self.clean + [member(f"{self.PREFIX}/one-more.py")])

    def test_member_size_bounded(self) -> None:
        largest = max(len(data) for _, data in self.clean if data is not None)
        with mock.patch.object(contract_artifact, "MAX_MEMBER_BYTES", largest):
            self.assertEqual(len(self.inspect(self.clean)), len(self.clean))
            self.assertRule("archive.too_large", self.clean + [member(f"{self.PREFIX}/big.bin", b"x" * (largest + 1))])

    def test_member_hidden_after_unreadable_header_refused(self) -> None:
        """tarfile stops at a garbage block after the first member; GNU tar skips it and unpacks what follows."""
        raw = gzip.decompress(write_archive(self.clean))
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            last = list(archive)[-1]
        end = last.offset_data + -(-last.size // tarfile.BLOCKSIZE) * tarfile.BLOCKSIZE
        hidden, payload = member(f"{self.PREFIX}/hidden.py", b"hidden = True\n")
        assert payload is not None
        hidden.size = len(payload)
        padding = b"\0" * (-len(payload) % tarfile.BLOCKSIZE)
        crafted = raw[:end] + b"\x01" * tarfile.BLOCKSIZE + hidden.tobuf(format=tarfile.PAX_FORMAT) + payload + padding + b"\0" * 1024
        with self.assertRaises(ArtifactRefused) as caught:
            contract_artifact.inspect_archive_members(crafted, self.PREFIX, self.NAME)
        self.assertEqual(caught.exception.rule, "archive.malformed", caught.exception.detail)


class RuleCoverageTest(unittest.TestCase):
    def test_every_rule_has_a_named_refusal_test(self) -> None:
        source = Path(__file__).read_text(encoding="utf-8")
        self.assertEqual([rule for rule in contract_artifact.RULES if f'"{rule}"' not in source], [])


if __name__ == "__main__":
    unittest.main()
