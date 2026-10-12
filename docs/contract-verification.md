# Contract verification

Verification of the published contract artifact for provider and consumer contract testing
(PenniLogic/contracts#3, plan item T-QA-02). This first pull request implements the published-artifact
loader, compatibility orchestration over the existing breaking-change detectors, the warn/block gate
state, the closed compatibility report and the command line `scripts/verify_contract.py`. Provider and
consumer adapters follow in a second pull request. Nothing here is product verification. No published
tag or Release exists yet, so every run in this repository is synthetic and no report it produces is
acceptance-eligible.

## Command line

```text
python scripts/verify_contract.py artifact --tag vX.Y.Z --release-dir DIR [--repo DIR] [--remote NAME] [--json]
python scripts/verify_contract.py gate [--gate-state FILE] [--json]
python scripts/verify_contract.py compat --baseline vX.Y.Z --base-document FILE --revision-document FILE
                                         --oasdiff-breaking FILE --oasdiff-diff FILE
                                         [--acknowledgement FILE] [--gate-state FILE] [--json]
python scripts/verify_contract.py report --report FILE [--json]
```

| Command | Does |
| --- | --- |
| `artifact` | Verifies downloaded Release assets against their annotated, published tag (`scripts/contract_artifact.py`, below). |
| `gate` | Validates the gate state (default: the committed `scripts/verification/compat-gate.json`) and prints the effective gate now. |
| `compat` | Classifies a revision against its published baseline from already-parsed detector inputs, then applies the acknowledgement protocol and the gate (`scripts/contract_compat.py`). |
| `report` | Validates a compatibility report and recomputes its verdict (`scripts/contract_report.py`). |

| Exit code | Meaning |
| --- | --- |
| 0 | Pass. `compat` also exits 0 for a warning under an unexpired warning gate; the output says WARN and when the warning ends. |
| 1 | Fail or refused: a refused artifact or gate state, a failing comparison, an invalid report, or a report whose verdict is fail. |
| 2 | Usage error, or a missing, unreadable or malformed input, including detector output or an acknowledgement the comparison cannot use. |

Results go to stdout. With `--json` the command prints one JSON document (two-space indent, ASCII);
a usage error then becomes `{"command": ..., "usage_error": {"rule": ..., "detail": ...}}`. Without
`--json`, usage errors go to stderr. Usage rule IDs are `input.missing`, `input.unreadable` and
`input.not_json`, the loader's `git.unavailable`, `repository.invalid`, `release_dir.missing` and
`remote.invalid`, and the `compat.*` refusals below.

There is no clock option: the commands use the real UTC clock, and tests inject one through
`verify_contract.main(argv, clock=...)`. Nothing is written to disk except the loader's temporary
unpack directory, which is removed before the command returns. The only network access is the
loader's `git ls-remote` against the configured remote.

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

From the command line: `python scripts/verify_contract.py artifact --tag v1.2.3 --release-dir <directory>
[--repo <checkout>] [--remote origin] [--json]`.

The loader downloads nothing. It unpacks the archives into a temporary directory and removes it
before it returns. It refuses with `ArtifactRefused`, whose `rule` is one of the static IDs below.
It stops at the first failing rule.

A loaded artifact reports `github_release.presence` as `not_run`. Checking that the GitHub Release
exists needs the network and is held. A loaded artifact therefore never shows by itself that the
Release was published.

## Artifact checks and rule IDs

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

## Compatibility

`scripts/contract_compat.py` classifies a revision against its published baseline. It imports
`scripts/check_breaking_changes.py` and never forks it:

- `operation_findings` classifies the `oasdiff breaking` output, and `component_findings` classifies
  the `oasdiff diff` output against both source documents. They are composed and de-duplicated by
  (id, location) exactly as `check_breaking_changes.compare` composes them.
  `test_classification_matches_check_breaking_changes_compare` runs `compare` itself over the same
  inputs and fails if the two ever differ.
- `evaluate` applies the acknowledgement protocol of [publication.md](publication.md#breaking-changes):
  the baseline must match, every finding must be named, blanket entries are refused, and
  `version_bump_ok` requires the bump. `load_acknowledgement` reads the acknowledgement file.

The change class is `none` (identical parsed documents and no detector output), `additive` (the
documents differ and neither detector reports a breaking change, so it covers any compatible change)
or `breaking` (at least one finding). The result is `pass` when `evaluate` reports no problem; an
acknowledged break with the required bump therefore passes, as it does in `check_breaking_changes.py`.
Otherwise the result is `warn` under an unexpired warning gate and `fail` under a blocking gate.

The inputs are already parsed: the published baseline's OpenAPI document and the revision document in
the form the pinned parser returns (`check_breaking_changes.source_documents`), and the parsed JSON
output of the pinned `oasdiff breaking` and `oasdiff diff`. Producing them from a published artifact
needs Node 24.14.0 and the managed toolchain. That parse path is held for the second pull request, so
nothing in this one parses YAML or runs oasdiff.

| Rule | Refused when (exit 2) |
| --- | --- |
| `compat.baseline_invalid` | The baseline does not fully match `check_breaking_changes.TAG`. |
| `compat.document_invalid` | A document is not an object with an `info` object. |
| `compat.version_invalid` | A document's `info.version` is not `MAJOR.MINOR.PATCH`. |
| `compat.baseline_version_mismatch` | The base document does not declare the baseline's version. |
| `compat.detector_output_invalid` | The `oasdiff breaking` output is not a list of changes with an `id`, an integer `level` 1-3 and string locations; the `oasdiff diff` output is not an object; identical documents come with reported changes; or the imported classifier cannot process the output (for example a string bound compared with a number, or nesting beyond the recursion limit). |
| `compat.acknowledgement_invalid` | A named acknowledgement file is absent or too large, or `load_acknowledgement` refuses it. |

The input checks exist because the imported functions alone do not refuse malformed detector output:
`operation_findings` treats an output that is not a list, or a change without `level`, as no finding,
and `component_findings` raises on some malformed diffs. `check_breaking_changes.py` is not changed
here.

`python scripts/check_breaking_changes.py` without `--base` is vacuous while no `v*` tag exists: it
prints that there is no baseline and exits 0 without comparing anything (`--require-baseline` makes it
fail). That result is not compatibility evidence until the first version is published.

## Gate state

One committed file, `scripts/verification/compat-gate.json`, holds the gate. It is closed by
`scripts/verification/compat-gate.schema.v1.json` and committed as:

```json
{
  "schema": "pennilogic-compat-gate@1",
  "mode": "block",
  "warn_until": null
}
```

`block` blocks unacknowledged breaking changes. `warn` is the rollback of contracts#3, "downgrading the
gate to warning with a recorded expiry", and needs `warn_until`, the fixed 24-character UTC instant
`YYYY-MM-DDTHH:MM:SS.mmmZ`. The file changes only through a reviewed commit, so its history records
every downgrade. Every reader decides with `contract_gate.decide` and an injected clock, and nothing
defaults to warn:

| Decision | When | Effective gate |
| --- | --- | --- |
| `gate.block` | `mode` is `block`. | block |
| `gate.warn_active` | `mode` is `warn` and now is before `warn_until`. | warn |
| `gate.warn_expired` | `mode` is `warn` and now is at or after `warn_until`. | block |
| `gate.missing` | There is no state, or the file does not exist. | block |
| `gate.unreadable` | The path is not a regular file, cannot be read or exceeds 64 KiB. | block |
| `gate.malformed` | The state is not strict JSON or is outside the schema (unknown key or mode, other schema ID). | block |
| `gate.warn_until_missing` | `mode` is `warn` and `warn_until` is null or absent. | block |
| `gate.warn_until_invalid` | `warn_until` is not a real instant in the fixed form. | block |
| `gate.warn_until_unexpected` | `mode` is `block` and `warn_until` is not null. | block |

`compat` applies the resolved gate whatever the state's condition. `gate` reports a refused state with
exit 1 and a missing or unreadable one with exit 2. A report records the decision and the state's
SHA-256.

This change does not choose a warning window. Whether the rollout starts with additive detection
under a warning, and how long any warning may last, are owner decisions. How consumer pipelines in
other repositories pin the contracts commit they read the gate from is part of the held CI wiring.

## Compatibility report

`scripts/verification/compat-report.schema.v1.json` closes the report. `scripts/contract_report.py`
builds it (`build_report`) and validates it (`assess_report`, `verify_contract.py report`). A report
records:

- `generated_at` and `mode`: `published` for a loader-verified artifact, otherwise `synthetic-fixture`;
- `contract`: version, tag, tag commit, specification and release-manifest digests;
- `required_languages`: always `["kotlin", "typescript", "python"]`;
- `compatibility` and `gate`: the result above and the gate decision;
- `provider` and `consumers`: one row each, with name, language (consumers), the version verified, the
  contract version it was verified against, `mode` (`live`, `synthetic-fixture` or `not_run`) and
  `result` (`pass`, `fail` or `not_run`);
- `missing_languages`, `verdict` and `acceptance_eligible`, which must equal the recomputed values.

The verdict is `fail` when a required language has no consumer row, when any row is `not_run` or
`fail`, or when the compatibility result is `not_run` or `fail`. It is `warn` when only the
compatibility result is `warn`, and `pass` otherwise. A missing Python row is therefore a failure,
never an omission. `acceptance_eligible` is true exactly when the mode is `published`, the provider row
is `live` and every consumer row is `live`; a `synthetic-fixture` report is never acceptance-eligible.
The first published version has no baseline, so its compatibility result is `not_run` and its verdict
is `fail` until the owner decides how a first baseline is reported.

| Rule | The report is invalid when (exit 1) |
| --- | --- |
| `report.not_json` | The file is not strict JSON. |
| `report.schema` | The report is outside the closed schema, or `generated_at` is not a real instant. The subject is a JSON pointer and the failing keyword; values are never echoed. |
| `report.bounds` | The file exceeds 4 MiB, or a row count, finding or problem count, or text length exceeds its bound. |
| `report.contract_identity` | A tag is not `v<version>`, or a published report lacks the tag, tag commit or release-manifest digest. |
| `report.duplicate_consumer` | Two consumer rows share a language and a name. |
| `report.row_status_inconsistent` | `mode` and `result` disagree about `not_run`, or a `not_run` row names a version. |
| `report.synthetic_row_name` | A synthetic row is not named `synthetic-<language>` (`synthetic-provider`), or a live row is named `synthetic-*`. |
| `report.live_row_unidentified` | A live row does not name the version it verified. |
| `report.contract_version_mismatch` | A verified row names another contract version than the report. |
| `report.compat_inconsistent` | The class, findings, problems, result, baseline and gate of the compatibility section contradict each other or the contract version. |
| `report.gate_inconsistent` | The gate mode, reason and `warn_until` contradict each other or `generated_at`. |
| `report.missing_languages_inconsistent`, `report.verdict_inconsistent`, `report.acceptance_eligible_inconsistent` | The declared value differs from the recomputed one. |

| Reason | Verdict |
| --- | --- |
| `report.required_language_missing`, `report.row_not_run`, `report.row_failed`, `report.compat_not_run`, `report.compat_failed` | fail |
| `report.compat_warned` | warn |

## What is synthetic

- Every release in the tests is synthetic. The real `release.build_release_set` builds it from synthetic
  source in a temporary repository, and the tag is pushed to a temporary bare origin.
- Every compatibility input is a synthetic parsed document or oasdiff-shaped output built in memory.
- Every report in the tests is built in memory. This repository's runs produce `synthetic-fixture`
  reports with `synthetic-<language>` rows. One published-mode report with live rows exists only to give
  the acceptance-eligibility rule a passing control; its rows carry fixture names, not real
  repositories, and it is never written or published.
- The committed gate state is configuration, not a result: it blocks.

## Tests

```text
python -B -m unittest discover -s scripts/tests -p "test_contract_*.py" -v
python -B -m unittest discover -s scripts/tests -p "test_verify_contract.py" -v
```

- `test_contract_artifact.py`: the loader. `scripts/tests/verification_support.py` builds synthetic
  releases with the real `release.build_release_set`, with its module-level paths patched into a
  temporary directory. It uses hermetic git (`-c user.name`, `-c user.email`, signing off, no global or
  system configuration, fixed dates) and a temporary bare origin.
  `test_builder_and_loader_keys_agree` fails when the builder's output keys and the loader's expected
  keys diverge.
- `test_contract_compat.py`: the gate (expiry boundary at `warn_until` minus 1 ms and at `warn_until`,
  a warning without expiry, every unusable state blocking) and compatibility orchestration (classes,
  the planted removal of a response field, acknowledgement and version-bump boundary pairs, stale and
  blanket acknowledgements, refused detector output, and the drift guard against `compare`).
- `test_contract_report.py`: the report schema and rules, the strict JSON reader and the schema subset.
- `test_verify_contract.py`: subcommands and exit codes in process through `main(argv)`.
  `test_runner_writes_only_inside_temp` records every file-system write and every started process with
  an audit hook, and requires the writes to be inside the temporary directory and the processes to be
  read-only git commands.

No archive is committed and no network is used. Nothing is written inside the checkout. Every refusal
test runs a passing control, applies exactly one mutation and asserts the exact rule ID.

## Held criteria and open questions

- No published tag or Release exists. Acceptance criterion 5 (verification against the published
  artifact) is held until the owner publishes a version. Synthetic runs are never relabelled as
  product verification.
- The live provider (api, PenniLogic/api#3) is held, and provider verification is the second pull
  request. Criterion 1 is shown only by the synthetic compatibility tests, not on a provider pull
  request.
- The real consumers and their compile drift checks are held (criteria 2 and 6). The second pull
  request compiles or type-checks each consumer against the unpacked published archive, in CI only.
  Criterion 6 is unmet. Criteria 3, 4 and 7 are shown only with synthetic inputs and rows.
- contracts#1 integration is held.
- The contracts#13 owner (the coordinator) decides runner-lane registration of these test modules in
  PR #38's `run_source_tests.py`, the whole-run budget and the merge order relative to PR #38. Until
  then the modules are unregistered and would run in the serial lane.
- CI wiring (through the infra governance generator) and branch protection are held.
- The core attestation and the separate Core, QA, Contract, Security and Release reviews are pending.
- The PenniLogic/infra#22 hold remains.
- GitHub Release presence is not checked (`not_run`). Tags are not signed, so there is no signature
  verification. When `git ls-remote` times out, git itself is stopped; transport helpers that git
  started may outlive it.
- ADR-015 money vectors are not processed here.
- Open owner decisions: whether the rollout starts with a warning window, the longest warning
  allowed, and how a first published version without a baseline is reported.
