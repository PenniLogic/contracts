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

The expected asset set is computed from the tag's own tree: the specification, the registry, the
provider sources the tag itself names (the `PROVIDER_SOURCE_NAMES` literal of `scripts/pl_contracts.py`
at the tag, parsed and never run), `spec/fixtures/*.json`, the three client archives and
`release-manifest.json`. A runner therefore verifies a tag whose provider sources differ from its own.
The rest of the release format is this runner's: the three client languages (the tag's `LANGUAGES`
literal must name the same three) and the key sets of `release-manifest.json`, its client records,
`generator/golden.json` records and the embedded `contracts-manifest.json` (`release.py` and
`generate_clients.py` schema version 1). A tag in another format is refused with
`release.format_unsupported` or `manifest.malformed`, never verified against the wrong expectations.

| Rule | Refused when |
| --- | --- |
| `release.format_unsupported` | The tag commit has no `scripts/pl_contracts.py` with one literal `PROVIDER_SOURCE_NAMES` tuple of distinct plain file names and one literal `LANGUAGES` naming exactly Kotlin, TypeScript and Python. |
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
| `spec.version_mismatch` | The specification's `info.version` is missing or is not the tag version. It is read only after the specification asset equals its blob at the tag commit (`anchor.asset_blob_mismatch` below), so a swapped asset is never parsed: the no-YAML `info.version` expression backtracks exponentially on some inputs. |

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

Every reader of a client archive must see the tree that was verified, not only Python's tarfile.
tarfile is lenient where GNU tar and npm's node-tar are not: it applies a pax global `size` after
skipping by the header size, keeps a GNU long name over a following pax `path`, and stops quietly at
an unreadable header. Archives built that way verified as golden under tarfile while GNU tar and
node-tar unpacked other bytes. So an archive must be exactly one gzip member, decompressed with a
size bound, and a strict reader accepts only the tar subset `release.deterministic_tar_gz` writes:

- POSIX ustar headers (magic `ustar`, version `00`) with the checksum tarfile writes, plain octal
  numbers and an empty prefix field;
- regular files and directories only, and directories without data;
- at most one extended header before a member, holding exactly one `path` record, and only for a
  name longer than the 100-character ustar name field;
- NUL padding, and a two-block end-of-archive marker with nothing after it.

The members must also be in the builder's canonical form: mode 0755 for directories, `gradlew` and
`*.sh` files and 0644 for every other file; owner 0 without owner or link names; one modification
time; sorted names; and directories that are exactly the parents of the files, so an empty or extra
directory is refused. Member names are printable ASCII, at most 1024 characters and 64 components of
at most 255 characters, inside one prefix. tarfile must then read the same members at the same data
offsets. These rules are stricter than the tarfile `data` filter, which accepts `prefix/../x` while it
stays inside the destination. The archive is then unpacked with `filter="data"`. The tree digest is
recomputed with `pl_contracts.tree_hash`, excluding `contracts-manifest.json`, exactly as
`generate_clients.py` computes it.

The tar bytes are not compared with a rebuild of the tree. The builder's exact bytes depend on its
Python version (current tarfile versions leave the device fields of files empty, earlier ones wrote
zeros, and both forms are accepted), and `release.py` runs on the owner's machine, outside CI.

| Rule | Refused when |
| --- | --- |
| `archive.malformed` | The archive is not gzip or not a complete tar: a bad header checksum, data cut off, no two-block end-of-archive marker, data after that marker (a reader that skips zero blocks would unpack it), a length that is not a multiple of 512 bytes, or unpacking fails. |
| `archive.ambiguous` | Tar or gzip readers would read different members: a pax global header; a GNU long name or long link; a sparse, old-style or vendor type; an extended header record other than one `path` (for example `size`, `linkpath` or `hdrcharset`) or a malformed record; two extended headers for one member, or one that precedes no member; a GNU or V7 header; a numeric field that is not plain octal; a used prefix field; a directory that declares data; more than one gzip member or data after it; or tarfile reading other members than the strict reader. |
| `archive.noncanonical` | The tar is readable and unambiguous but not what the builder writes: another mode, owner, owner name or link name; device numbers or header padding; bytes after the end of a name; a directory name without its trailing slash; an extended header for a short name, or one whose `path` does not start with the ustar name; data in padding; members out of order; an empty, extra or missing directory; or more than one modification time. |
| `archive.too_large` / `archive.too_many_members` | The decompressed size, a member's size or the member count exceeds its bound. |
| `archive.unsafe_path` | A member name is not printable ASCII (a control or non-ASCII character, or bytes that are not UTF-8), is absolute, uses a backslash, exceeds the length or depth bounds, or has an empty, `.`, `..`, drive or stream component. |
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

The orchestration cannot tell whether the base document is the published baseline's document or
whether the detector output belongs to the two documents; it refuses only identical documents that come
with reported changes. Empty detector output for two different documents therefore classifies as
`additive`. Until the held parse path produces every input from the published artifact itself, a
`compat` result is only as good as the inputs its caller passes, and it is not compatibility evidence
on its own.

| Rule | Refused when (exit 2) |
| --- | --- |
| `compat.baseline_invalid` | The baseline does not fully match `check_breaking_changes.TAG`. |
| `compat.document_invalid` | A document is not an object with an `info` object. |
| `compat.version_invalid` | A document's `info.version` is not `MAJOR.MINOR.PATCH`. |
| `compat.baseline_version_mismatch` | The base document does not declare the baseline's version. |
| `compat.detector_output_invalid` | The `oasdiff breaking` output is not a list of changes with an `id` of at most 128 characters (the report's identifier bound), an integer `level` 1-3 and string locations; the `oasdiff diff` output is not an object; identical documents come with reported changes; or the imported classifier cannot process the output (for example a string bound compared with a number, or nesting beyond the recursion limit). |
| `compat.acknowledgement_invalid` | A named acknowledgement file is absent, unreadable, larger than 1 MiB or not strict JSON (a duplicate key, a byte order mark, a non-finite number or nesting beyond the parser), `load_acknowledgement` refuses it, or its reading differs from the strict one. |

The input checks exist because the imported functions alone do not refuse malformed detector output:
`operation_findings` treats an output that is not a list, or a change without `level`, as no finding,
and `component_findings` raises on some malformed diffs. Likewise `load_acknowledgement` parses with
`json.loads`, which keeps the last of duplicate keys (a reviewer may read the first) and raises
`RecursionError` on very deep nesting, so the acknowledgement is first read with the strict reader and
both readings must agree. `check_breaking_changes.py` is not changed here.

Detector text (finding locations, problems and warnings) comes from the compared documents. It is
printed and reported as printable ASCII: every other character, including C1 controls and
bidirectional or invisible format characters, is escaped as `\uXXXX` (`\UXXXXXXXX` outside the Basic
Multilingual Plane), so it cannot restyle or reorder a terminal, a log viewer or a report.

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
`YYYY-MM-DDTHH:MM:SS.mmmZ`. The file is meant to change only through a reviewed pull request, so that
its history records every downgrade; that relies on branch protection, which is held (CODEOWNERS names
the single owner account for every path). Every reader decides with `contract_gate.decide` and an injected clock, and nothing
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
exit 1 and a missing or unreadable one with exit 2. A valid state exits 0 in either mode, so a pipeline
reads the effective gate from `gate.mode` in the `--json` output, never from the exit code. A report
records the decision and the state's SHA-256.

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
never an omission. Rows are required per language: the validator does not know which consumer
repositories exist, so with two TypeScript consumers a report that names only one of them still passes.
Naming the required consumers belongs to the held consumer adapters. `acceptance_eligible` is true
exactly when the mode is `published`, the provider row is `live` and every consumer row is `live`; a
`synthetic-fixture` report is never acceptance-eligible.
The first published version has no baseline, so its compatibility result is `not_run` and its verdict
is `fail` until the owner decides how a first baseline is reported.

The validator checks that a report is closed and internally consistent and recomputes its verdict. It
cannot show that the rows reflect real runs, and it has no clock, so `generated_at` is taken as
declared. A report is evidence only when the runner itself produced it in CI, which is held. Report text
is printable ASCII only. `missing_languages` holds at most the three languages, so its uniqueness check
stays bounded; row, finding and problem counts and text lengths are checked before the consistency
rules.

| Rule | The report is invalid when (exit 1) |
| --- | --- |
| `report.not_json` | The file is not strict JSON. |
| `report.schema` | The report is outside the closed schema, or `generated_at` is not a real instant. The subject is a JSON pointer and the failing keyword; values are never echoed. |
| `report.bounds` | The file exceeds 4 MiB, or a row count, finding or problem count, or text length exceeds its bound. |
| `report.contract_identity` | A tag is not `v<version>`, or a published report lacks the tag, tag commit or release-manifest digest. |
| `report.duplicate_consumer` | Two consumer rows share a language and a name. |
| `report.row_status_inconsistent` | `mode` and `result` disagree about `not_run`, or a `not_run` row names a version. |
| `report.synthetic_row_name` | A `synthetic-fixture` row is not named `synthetic-<language>` (`synthetic-provider`), or a `live` row is named `synthetic-*`. A `not_run` row may carry either kind of name. |
| `report.live_row_unidentified` | A live row does not name the version it verified. |
| `report.contract_version_mismatch` | A row that ran names another contract version than the report. |
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

The modules are also part of the repository's complete test run. On main that run is
`python scripts/run_source_tests.py` (CI and `.github/agent-policy.json`, added by PR #38). It runs every
`test_*.py` module in lanes and gives each lane its own `TMPDIR` inside the checkout's gitignored
`build/source-tests/run-*/state/<lane>/temp`.

- `test_contract_artifact.py`: the loader. `scripts/tests/verification_support.py` builds synthetic
  releases with the real `release.build_release_set`, with its module-level paths patched into a
  temporary directory. It uses hermetic git (`-c user.name`, `-c user.email`, signing off, no global or
  system configuration, fixed dates) and a temporary bare origin.
  `test_builder_and_loader_keys_agree` fails when the builder's output keys and the loader's expected
  keys diverge. `ArchiveReaderDifferentialTest` replays the archives that verified as golden under
  tarfile while GNU tar or node-tar unpacked other bytes, and `ArchiveMemberRulesTest` mutates the
  builder's own archive one header field at a time.
- `test_contract_compat.py`: the gate (expiry boundary at `warn_until` minus 1 ms and at `warn_until`,
  a warning without expiry, every unusable state blocking) and compatibility orchestration (classes,
  the planted removal of a response field, acknowledgement and version-bump boundary pairs, stale and
  blanket acknowledgements, refused detector output and acknowledgement files (including a duplicate key
  and nesting beyond the parser), deeply nested documents, hostile detector text, and the drift guard
  against `compare`).
- `test_contract_report.py`: the report schema and rules, the strict JSON reader and the schema subset
  (including JSON equality of deeply nested values without recursion).
- `test_verify_contract.py`: subcommands and exit codes in process through `main(argv)`, and printable
  ASCII text output for hostile detector text.
  `test_runner_writes_only_inside_temp` records every file-system write and every started process with
  an audit hook, and requires the writes to be inside the temporary directory and the processes to be
  read-only git commands.

No archive is committed and no network is used. The tests write only inside their own temporary
directories (`verification_support.temporary_root`): under `TMPDIR` outside the checkout or, under the
complete test run, inside the lane's temporary directory in the gitignored `build/`. A temporary
directory anywhere else inside the checkout is refused;
`test_temporary_root_allows_the_runner_temp_area_but_no_other_checkout_path` checks both cases on a fake
checkout. Every refusal test runs a passing control, applies exactly one mutation and asserts the exact
rule ID.

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
  `scripts/run_source_tests.py` (added to main by PR #38) and the whole-run budget. Until then the
  modules are unregistered and run in its serial lane.
- CI wiring (through the infra governance generator) and branch protection are held.
- The core attestation and the separate Core, QA, Contract, Security and Release reviews are pending.
- The PenniLogic/infra#22 hold remains.
- GitHub Release presence is not checked (`not_run`). Tags are not signed, so there is no signature
  verification. When `git ls-remote` times out, git itself is stopped; transport helpers that git
  started may outlive it.
- ADR-015 money vectors are not processed here.
- Open owner decisions: whether the rollout starts with a warning window, the longest warning
  allowed, and how a first published version without a baseline is reported.
