# Publication, versioning and compatibility

How a version of the PenniLogic API contract is published, how consumers pin it, how it is rolled
back and how a breaking change is carried out. Decided by the Sprint 01 coordinator for the MVP
(PenniLogic/contracts#2) and binding until superseded by a reviewed decision.

## Publication scheme: versioned git tags

A contract version is an **immutable annotated git tag** `vMAJOR.MINOR.PATCH` on this repository
whose commit contains, at that tag:

- `spec/openapi.yaml` with `info.version` equal to the tag version;
- `spec/currency-registry.v1.json` and `spec/fixtures/*.json` (the hand-written money and instant
  vectors and the seeded `money-roundtrip-generated.v1.json`, ADR-015 §7);
- `spec/error-catalogue.v1.json`, `spec/client-state-bindings.v1.json` and
  `spec/import-group.v1.json`, the error/import provider source companions;
- the committed generator configuration (`generator/*.json`, `generator/openapi-generator-ignore`,
  `generator/templates/`), the runtime seams (`runtime/`) and the generator lock
  (`toolchain/versions.json`: generator version and jar SHA-256; `generator/golden.json`: SHA-256 of
  every generated tree and file).

A GitHub Release with the same tag carries, for convenience and audit: the specification
(`openapi-vX.Y.Z.yaml`), the registry and fixtures, one archive per generated client
(`pennilogic-contracts-<kotlin|typescript|python>-vX.Y.Z.tar.gz`), `release-manifest.json`
(specification version and SHA-256, commit, provider-source SHA-256 bindings, generator name/version/jar SHA-256, per-archive SHA-256
and generated-tree SHA-256) and `SHA256SUMS`.

The accepted `0.2.0` provider source and local combined `0.3.0` preparation do not authorise
publication and are not released clients. Original consumer adoption, separate review, native CI/dependency conditions and
the PenniLogic/infra#22 hold remain pending; see [provider-contracts.md](provider-contracts.md).
The provider companions ship beside the standalone specification and inside each client
archive, so a pinned consumer can run the same catalogue/import lint and conformance checks.

A tag or Release is never moved, deleted or re-uploaded with different bytes. A mistake is fixed by
publishing the next version. Until the owner adds a repository ruleset for `refs/tags/v*` (creation
allowed; update and deletion blocked), this immutability is procedural.

### How a version is published

By the repository owner, locally, from a clean checkout of `main` after the pull request that bumped
`info.version` merged, in a token-removed process authenticated with `gh` as the owner:

```text
python scripts/release.py --version X.Y.Z --dry-run   # builds build/dist/vX.Y.Z and prints SHA256SUMS
python scripts/release.py --version X.Y.Z             # re-runs every CI command, tags, pushes the tag, creates the Release
```

The script refuses a dirty tree, a non-`main` HEAD, a HEAD that is not the commit `origin/main`
carries after a fetch (an unpushed local commit is never tagged), a version that differs from
`info.version` or a tag that already exists, and re-runs the full CI command list before building. Archives are
deterministic (sorted entries, commit time as mtime, numeric owner 0), so a second run from the
same commit reproduces every digest in `SHA256SUMS`. Record the tag, commit and digests on the
ticket that published the version.

Publication runs outside CI on purpose: the public workflow token is `contents: read`, carries no
secrets and uses GitHub-owned SHA-pinned actions only.

### Deferred: registry publication with federated credentials

Publishing the generated clients to a package registry (Maven, npm, PyPI or GitHub Packages) with
OIDC/short-lived federated credentials is **not** part of this version. It needs an owner decision
on the destination (GitHub Packages under this organization is the no-new-spend default) and a
separately reviewed CI/policy change adding an `id-token: write` publish job through the canonical
generator. Until then no registry coordinates exist, and this repository claims no OIDC publish
path. Follow-up ticket: to be opened by the coordinator.

## Versioning

`info.version` follows semantic versioning:

| Change | Bump | Examples |
| --- | --- | --- |
| Additive, compatible | MINOR | new optional property, new endpoint, new registry currency, new reasoned `x-idempotency` exemption |
| Fix without contract change | PATCH | description, example, documentation, tooling |
| Breaking (see below) | MAJOR (MINOR while `0.y.z`) | removed or renamed property, narrowed constraint, removed endpoint or enum value, new required member |

The version is bumped in the pull request that makes the change; the lint rule
`pl-info-version-semver` guards the form and `check_breaking_changes.py` enforces the bump rule when a
break is acknowledged.

## Consumer pinning

Consumers (`api`, `android`, `web`, `admin`, `ai-service`) pin a tag and generate their client
from it with the contracts-provided script, one invocation per language:

```text
git clone --branch vX.Y.Z --depth 1 https://github.com/PenniLogic/contracts.git
cd contracts
npm ci --no-audit --no-fund          # only needed for lint; generation needs Python 3.14 and a JDK 21
python scripts/toolchain.py install  # downloads and verifies the pinned generator
python scripts/generate_clients.py --language kotlin      # or typescript / python
```

The output in `build/generated/<language>/` is byte-identical to the archive on the Release for the
same tag (compare `contracts-manifest.json` `tree_sha256` with `release-manifest.json`). Consumers
never generate from a branch and never edit generated output; the hand-written `Money` and `Instant`
seams ship inside the generated client and are the only place money or instants are parsed or
rendered (ADR-015 §2).

### Unreleased combined source adoption

AA8 (`aa8d90cb`) and accepted provider (`5b41d458`) source consumers remain pinned unchanged.
The local `0.3.0` source/error-group `1.1.0` preparation is an expand candidate, not a migrated
consumer, published tag, release or deployment. Future adoption requires actual regeneration
and correct authentication-versus-service routing before new codes are emitted. Older consumer
pins must not receive those new codes. No old wire shape has been removed, so no contract-stage
acknowledgement is authorized or claimed.

The genuine accepted-provider comparison still holds the new fixed `egress_denied` allOf
branch, its nested import uses and shared response for review. The scalar-enum/description-only
response proof does not waive conditional or composition changes. Until that exact finding is
resolved through the owning review/integration process, source and native conformance passes
are not full compatibility or publication acceptance.

## Rollback

Rollback is **re-pinning the previous tag**: a consumer moves its pinned version back and
regenerates. Nothing is republished and no tag changes. If a published version is defective for
every consumer, the fix is the next version (`X.Y.Z+1`), which may restore the previous shape.

## Breaking changes

A breaking change is any change `check_breaking_changes.py` reports: an operation-level break from
oasdiff (removed or changed operation, parameter, request or response semantics) or a
component-level break from the component guard (removed schema/parameter/header, removed property,
type/format/pattern change, narrowed length or range, required member added or removed, enum value
removed, `additionalProperties` becoming false, parameter location or required change).

### Expand and contract

Breaking changes are published in two steps so that no consumer is ever pinned to a version its
peers cannot talk to:

1. **Expand** (additive MINOR): add the new shape alongside the old one (new property, new endpoint,
   new enum value). Consumers migrate to the new shape at their own pace by bumping their pin.
2. **Contract** (MAJOR, or MINOR while `0.y.z`): remove the old shape once every consumer has
   migrated. This is the step the check flags, and it is allowed only with an acknowledgement.

### Acknowledging a break

Add `spec/breaking-change-acknowledgement.json` in the same pull request:

```json
{
  "baseline": "v1.4.2",
  "target_version": "2.0.0",
  "acknowledged": [
    {
      "id": "component-schema-property-removed",
      "location": "components/schemas/Account/properties/legacy_balance",
      "reason": "expand-and-contract: `balance` shipped in v1.3.0; api, android, web, admin and ai-service migrated (links to their pins); removal agreed on contracts#N."
    }
  ]
}
```

Rules enforced by the check: `baseline` must equal the tag being compared against (a stale file
never carries over); every reported finding must be listed by `id` and `location` with a non-empty
`reason`; an entry that matches no finding fails (blanket acknowledgements are refused); a file with
no detected break fails (remove it after publishing); `info.version` must carry the required bump
and equal `target_version` when present. The file is removed in the first pull request after the
version is tagged. The `id`/`location` pairs are printed by the check; `--format json` gives them
machine-readably.

### Not breaking

Adding optional properties, endpoints, enum values, registry currencies, examples or descriptions;
relaxing a constraint; adding a response. These pass the check and need a MINOR bump.
The response-schema proof recognizes complete, nonempty, duplicate-free scalar enum additions
and inert description changes through otherwise unchanged properties/items and identified allOf
members. Deleted/replaced values, new enum constraints, partial/malformed/unknown records,
requiredness, type/ref/pattern changes, conditionals/`not`, changed compositions and response
metadata remain unproved or breaking. Structured enum-value changes are also kept for review.

## Compatibility of the check itself

Before the first tag exists there is no baseline and the check passes with an explicit notice; the
first owner-authorised publication defines the baseline and every later change is compared with the
highest tag. CI fetches all history and tags (`fetch-depth: 0`) so the baseline is always present.
