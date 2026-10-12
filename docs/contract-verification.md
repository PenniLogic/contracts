# Contract verification

Verification of the published contract artifact for provider and consumer contract testing
(PenniLogic/contracts#3, plan item T-QA-02). This guide covers the part implemented so far: the
published-artifact loader. Compatibility orchestration, the compatibility report, the warn/block
gate state and the command line follow in the same pull request. Provider and consumer adapters
follow in a second pull request. Nothing here is product verification. No published tag or Release
exists yet, so every run in this repository is synthetic.

## What "published artifact" means

A contract version is an annotated tag `vMAJOR.MINOR.PATCH` plus the GitHub Release assets that
`scripts/release.py` builds for it ([publication.md](publication.md)). The verification runner
accepts a version only through `scripts/contract_artifact.py`, and never accepts a branch name, a
commit, or a build in the working tree.

```python
from contract_artifact import ArtifactRefused, load_published_artifact

artifact = load_published_artifact("v1.2.3", release_dir, repo=checkout, remote="origin")
```

- `release_dir` holds exactly the Release assets of the tag, for example from
  `gh release download v1.2.3 --dir <directory outside the checkout>`. `RELEASE_NOTES.md` is the
  Release body, not an asset, so it must not be there.
- `repo` is the top level of a checkout that has the tag.
- `remote` is a configured remote name, usually `origin`.

The loader downloads nothing. It unpacks the archives into a temporary directory and removes it
before it returns. It refuses with `ArtifactRefused`, whose `rule` is one of the static IDs below.
It stops at the first failing rule.

A loaded artifact reports `github_release.presence` as `not_run`. Checking that the GitHub Release
exists needs the network and is held. A loaded artifact therefore never shows by itself that the
Release was published.

## Checks and rule IDs

### Tag and publication

| Rule | Refused when |
| --- | --- |
| `tag.grammar` | The tag does not fully match `check_breaking_changes.TAG`. That covers leading zeros, a trailing newline, branch names, commit IDs, `refs/tags/...` and pre-release suffixes. |
| `git.unavailable` | `git` is not on `PATH`. |
| `repository.invalid` | `repo` is not the top level of a git checkout. |
| `release_dir.missing` | The release directory does not exist. |
| `release_dir.inside_repository` | The release directory is inside the checkout, for example `build/dist/<tag>`. That is a working-tree build, not the downloaded assets. |
| `remote.invalid` | The remote is not a plain name configured in the checkout. An unconfigured name could resolve to a local path. |
| `tag.missing` | `refs/tags/<tag>` does not exist locally. |
| `tag.not_annotated` | The tag is lightweight. |
| `tag.object_invalid` | The tag object names another tag, targets something other than a commit, or targets a missing commit. |
| `tag.remote_unreachable` | `git ls-remote <remote> refs/tags/<tag>` fails or times out, so publication is unknown. |
| `tag.unpublished` | The remote does not carry `refs/tags/<tag>`. |
| `tag.remote_mismatch` | The remote's `refs/tags/<tag>` is not the local annotated tag object, for example a re-created tag or a lightweight tag on the remote. |

### Release set and SHA256SUMS

The expected asset set is computed from the tag's own tree: the specification, registry, provider
sources (`pl_contracts.PROVIDER_SOURCE_NAMES`), `spec/fixtures/*.json`, the three client archives and
`release-manifest.json`.

| Rule | Refused when |
| --- | --- |
| `anchor.asset_name_collision` | Two sources at the tag would produce the same asset name in the flat release directory. |
| `anchor.tag_file_missing` | A file the release is built from (specification, registry, provider source, fixture, `generator/golden.json` or `toolchain/versions.json`) is not a regular file at the tag commit. |
| `asset.not_regular_file` | An entry in the release directory is a symlink, a directory or another non-regular file. |
| `sums.missing_file` | There is no `SHA256SUMS`. |
| `asset.too_large` | An asset, or a file at the tag commit, exceeds its size bound. |
| `sums.malformed` | `SHA256SUMS` is not in the builder's exact form: lowercase SHA-256, two spaces, a plain file name, sorted, LF line endings, a final newline, no duplicates. |
| `sums.missing_entry` / `sums.extra_entry` | `SHA256SUMS` does not list exactly the expected assets. |
| `asset.missing` / `asset.unexpected` | The directory does not hold exactly the expected assets plus `SHA256SUMS`. |
| `sums.digest_mismatch` | An asset does not match its `SHA256SUMS` digest. |

### Release manifest

| Rule | Refused when |
| --- | --- |
| `manifest.malformed` | `release-manifest.json` is not strict JSON (UTF-8, no byte order mark, no duplicate keys, no `NaN`), or its keys or value types differ from what `release.build_release_set` writes. |
| `manifest.files_mismatch` | `files` differs from `SHA256SUMS`. |
| `manifest.tag_mismatch` / `manifest.version_mismatch` / `manifest.commit_mismatch` | `tag`, `spec_version` or `commit` differs from the tag, its version or the tag commit. A build from another commit is refused here. |
| `manifest.spec_digest_mismatch` | `spec_sha256` is not the digest of the specification asset. |
| `manifest.provider_sources_mismatch` | `provider_sources_sha256` is not the digest map of the provider-source assets. |
| `manifest.clients_mismatch` | The clients are not exactly Kotlin, TypeScript and Python, or an archive name, archive digest or provider-source map disagrees. |
| `spec.version_mismatch` | The specification asset's `info.version` is missing or is not the tag version. |

### Anchors at the tag's own tree

A Release asset can be replaced after publication. A consistent replacement of the assets,
`SHA256SUMS` and the manifest passes every check above. These rules compare the release with the
tag commit instead:

| Rule | Refused when |
| --- | --- |
| `anchor.asset_blob_mismatch` | The specification, registry, a provider source or a fixture differs from its blob at the tag commit. The blob gets the same CRLF-to-LF normalization the builder applies. |
| `anchor.toolchain_mismatch` | The manifest `toolchain`, or a client's generator version or jar digest, differs from `toolchain/versions.json` at the tag commit. |
| `anchor.golden_invalid` | `generator/golden.json` at the tag commit lacks a language, has a malformed record or is internally inconsistent (`generate_clients.golden_inconsistency`). |
| `anchor.golden_spec_mismatch` | A golden record pins another specification digest or version than the release ships. |
| `anchor.golden_tree_mismatch` | An archive's recomputed tree digest is not `golden.json[language].tree_sha256` at the tag commit. |

### Client archives

Each archive is decompressed with a size bound and checked member by member before anything is
unpacked. The rules are stricter than the tarfile `data` filter, which accepts `prefix/../x` while it
stays inside the destination. The archive is then unpacked with `filter="data"`. The tree digest is
recomputed with `pl_contracts.tree_hash`, excluding `contracts-manifest.json`, exactly as
`generate_clients.py` computes it.

| Rule | Refused when |
| --- | --- |
| `archive.malformed` | The archive is not gzip or tar. Data that tarfile would skip but GNU tar would unpack follows the last readable member. Or unpacking fails. |
| `archive.too_large` / `archive.too_many_members` | The decompressed size, a member's size or the member count exceeds its bound. |
| `archive.unsafe_path` | A member name is absolute, uses a backslash, or has an empty, `.`, `..`, drive or stream component, or a control character. |
| `archive.outside_prefix` | A member is not inside `pennilogic-contracts-<language>-<tag>/`. |
| `archive.link_member` / `archive.special_member` | A member is a symlink or hard link, or anything other than a regular file or directory (a device, a FIFO, a contiguous file). |
| `archive.duplicate_member` / `archive.case_collision` | Two members share a name, or differ only in case and would collide on Windows or macOS. |
| `archive.manifest_missing` / `archive.manifest_invalid` | `contracts-manifest.json` is absent or malformed. |
| `archive.tree_mismatch` | The unpacked files differ from the archive's own `contracts-manifest.json` (tree digest, file count or per-file list). |
| `archive.manifest_mismatch` | `contracts-manifest.json` disagrees with the release on target, version, specification, registry, provider sources, generator, runtime, tree digest or file count. |

## Tests

```text
python -B -m unittest discover -s scripts/tests -p "test_contract_artifact.py" -v
```

`scripts/tests/verification_support.py` builds synthetic releases with the real
`release.build_release_set`, with its module-level paths patched into a temporary directory. It
uses hermetic git (`-c user.name`, `-c user.email`, signing off, no global or system configuration,
fixed dates) and a temporary bare origin. No archive is committed and no network is used. Nothing is
written inside the checkout. Every refusal test runs a passing control, applies exactly one mutation
and asserts the exact rule ID. `test_builder_and_loader_keys_agree` fails when the builder's output
keys and the loader's expected keys diverge.

## Not claimed

- No published tag or Release exists. Acceptance criterion 5 (verification against the published
  artifact) is held until the owner publishes a version. Synthetic runs are never relabelled as
  product verification.
- GitHub Release presence is not checked (`not_run`). Tags are not signed, so there is no signature
  verification.
- When `git ls-remote` times out, git itself is stopped. Transport helpers that git started may
  outlive it.
- The live provider (api, PenniLogic/api#3), the real consumers (criteria 2 and 6), contracts#1
  integration, CI wiring, runner-lane registration and run budget, branch protection, core
  attestation and the PenniLogic/infra#22 hold remain open.
