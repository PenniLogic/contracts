"""Published-artifact loader (contracts#3, T-QA-02): tag provenance, exact SHA256SUMS, manifest agreement,
tag-tree anchors and safe archive extraction.

The releases are synthetic and built by the real release.build_release_set (verification_support). Every
refusal test first loads an unmutated control, then applies exactly one mutation and asserts the exact
static rule ID. A "consistent swap" replaces Release assets and recomputes SHA256SUMS and the release
manifest the way an attacker would. Only the tag's own tree can catch it. No network; nothing is
written inside this repository.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import shutil
import tarfile
import unittest
from pathlib import Path
from unittest import mock

import verification_support as support
from verification_support import (
    FIXTURE_NAMES, GOLDEN_RELATIVE, MANIFEST_NAME, SPEC_DIRECTORY, SYNTHETIC_EPOCH, VERSIONS_RELATIVE, SyntheticContracts,
    copy_release, git, json_text, member, read_archive, read_sums, refresh_embedded, replace_member_data, reseal,
    rewrite_archive, snapshot, synthetic_digest, temporary_root, write_archive, write_sums,
)

import contract_artifact  # noqa: E402 - verification_support puts scripts/ on sys.path
import pl_contracts  # noqa: E402
import release  # noqa: E402
from contract_artifact import ArtifactRefused, archive_name, archive_prefix, load_published_artifact  # noqa: E402
from pl_contracts import sha256_bytes  # noqa: E402

PROVIDER = pl_contracts.PROVIDER_SOURCE_NAMES[0]
FIXTURE = FIXTURE_NAMES[0]


class LoaderTestCase(unittest.TestCase):
    """One pristine published release per class; tests mutate copies of it, or build their own.

    The passing control runs once per test method on the first unmutated copy. Later subTests start
    from byte-identical copies of that same input, so each subTest is still the control plus exactly
    one mutation.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.enterClassContext(mock.patch.dict(os.environ, support.hermetic_environment(), clear=True))
        cls.root = temporary_root()
        cls.addClassCleanup(shutil.rmtree, cls.root, True)
        cls.base = SyntheticContracts(cls.root / "base")
        cls.release_dir = cls.base.publish()

    def setUp(self) -> None:
        self.work = temporary_root()
        self.addCleanup(shutil.rmtree, self.work, True)
        self.scratch = self.work / "scratch"
        self.scratch.mkdir()
        self.controls: set[str] = set()

    def load(self, release_dir: Path, *, contracts: SyntheticContracts | None = None, tag: str | None = None,
             repo: Path | None = None, remote: str = "origin") -> contract_artifact.PublishedArtifact:
        contracts = contracts or self.base
        return load_published_artifact(tag if tag is not None else contracts.tag, release_dir, repo=repo or contracts.repo,
                                       remote=remote, scratch_dir=self.scratch)

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
        self.assertEqual((artifact.tag, artifact.version, artifact.commit, artifact.tag_object, artifact.remote),
                         (base.tag, base.version, base.commit, base.tag_object, "origin"))
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
        self.assertEqual(checks.pop("github_release.presence"), "not_run")
        self.assertEqual(set(checks.values()), {"pass"})
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
        self.assertLessEqual(contract_artifact.EMBEDDED_MANIFEST_KEYS, support.generator_manifest_keys())
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
        git(contracts.repo, "remote", "set-url", "origin", str(self.work / "no-such-origin.git"))
        self.assertRefused("tag.remote_unreachable", self.release_dir, contracts=contracts)

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

    def test_git_unavailable_refused(self) -> None:
        self.load(self.release_dir)
        with mock.patch.object(contract_artifact.shutil, "which", return_value=None):
            self.assertRefused("git.unavailable", self.release_dir)


class ReleaseSetTest(LoaderTestCase):
    """The release directory, SHA256SUMS and their digests."""

    def test_release_dir_missing_refused(self) -> None:
        self.load(self.release_dir)
        self.assertRefused("release_dir.missing", self.work / "absent")

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
        spec = f"openapi-{self.base.tag}.yaml"
        mutations = {
            "other version": lambda text: text.replace(f"  version: {self.base.version}\n", "  version: 1.2.4\n"),
            "no version": lambda text: text.replace(f"  version: {self.base.version}\n", ""),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                release_dir = self.fresh_release(label.replace(" ", "-"))
                path = release_dir / spec
                path.write_text(mutate(path.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
                reseal(release_dir)
                self.assertRefused("spec.version_mismatch", release_dir)


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


class ArchiveMemberRulesTest(unittest.TestCase):
    """Member checks before unpacking, on archives written by the real release.deterministic_tar_gz."""

    PREFIX = "pennilogic-contracts-python-v1.2.3"
    NAME = f"{PREFIX}.tar.gz"

    def setUp(self) -> None:
        self.root = temporary_root()
        self.addCleanup(shutil.rmtree, self.root, True)
        tree = self.root / "tree"
        for relative, content in (("README.md", "synthetic\n"), ("pkg/__init__.py", ""), ("pkg/models/probe.py", "x = 1\n")):
            (tree / relative).parent.mkdir(parents=True, exist_ok=True)
            (tree / relative).write_text(content, encoding="utf-8", newline="\n")
        self.clean = read_archive(release.deterministic_tar_gz(tree, self.PREFIX, SYNTHETIC_EPOCH))
        self.assertEqual(len(self.inspect(self.clean)), len(self.clean))  # control

    def inspect(self, members) -> list[tarfile.TarInfo]:
        return contract_artifact.inspect_archive_members(gzip.decompress(write_archive(members)), self.PREFIX, self.NAME)

    def assertRule(self, rule: str, members) -> None:
        with self.assertRaises(ArtifactRefused) as caught:
            self.inspect(members)
        self.assertEqual(caught.exception.rule, rule, caught.exception.detail)

    def test_absolute_parent_or_drive_member_path_refused(self) -> None:
        for name in ("/etc/synthetic", f"{self.PREFIX}/../escape", f"{self.PREFIX}/./x", f"{self.PREFIX}//x",
                     f"{self.PREFIX}\\..\\escape", "C:/synthetic", f"{self.PREFIX}/file:stream", f"{self.PREFIX}/bell\x07"):
            with self.subTest(name=ascii(name)):
                self.assertRule("archive.unsafe_path", self.clean + [member(name)])

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
