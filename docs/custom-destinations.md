# Custom destinations: source contract and integration boundary

This is the local, unreleased source implementation of
[contracts#27 / T-CON-EGRESS-01](https://github.com/PenniLogic/contracts/issues/27).
It does **not** complete that issue, publish a client version, implement a gateway or authorize
AI exposure. The `0.2.0` source version is an additive candidate, not a tag or release.
Global/custom AI remain OFF; approved providers, approved custom destinations and deployment
inventory remain empty.

## Accepted input, not a replacement policy

The direct input is [ADR-022's accepted consequence](https://github.com/PenniLogic/docs/blob/9e394961032b50eafb68b37ff3f7c209243127cf/adr/ai-egress-consequences.json),
`adr-022-ai-egress-consequences@1.1.0`, policy `2026-10-05.2`.
The two files under `spec/adr022/` are exact copies of the accepted bytes, not a forked
`egress-policy.json`, extracted enum-only substitute or deployable egress configuration.

| Input at Docs commit `9e394961032b50eafb68b37ff3f7c209243127cf` | Bytes | Git blob | SHA-256 |
| --- | ---: | --- | --- |
| `adr/ai-egress-consequences.json` | 27141 | `6e1d9fbfe279e0e9013a9a9b48d4e577148aaaaf` | `44c3b40b8caa01c26c46a6b8103667501bc9e1be2a2cf714dcb84d8a358f476f` |
| `adr/ai-egress-consequences.schema.json` | 40271 | `d213ba66b4e77b8a144cac73f4c486fc19b30e93` | `61f625d370840e9a40e1d7a05355c5504cc8a9d98aafac4814d641d21a021d73` |

These blobs are unchanged at Docs `29741b431124f27751cb2bd4bd0f0dd4c94d6f3d`.
The accepted ADR already references `T-CON-EGRESS-01` / contracts#27 once in section 5
and once in section 11, with no `T-CON-NEW-EGRESS` placeholder. No redundant Docs edit is made.
The ADR bytes are bound by SHA-256
`923069193103d6c65f31a1bb0662cb073b0c3ebd7d1557ddb0d07fceb7762047`.

`_customDestinationSource.js` checks each complete file's digest **before** JSON parsing.
`pl-custom-destination-contract` validates the entire consequence against its published
closed schema, including deny rules, bounds, lifecycle, default-OFF state, empty approvals,
inventory and source owners. Duplicate JSON names, whitespace drift or policy edits fail the
byte guard; unknown or contradictory members also fail the schema controls.

The four OpenAPI definitions are `DestinationClass`, `CredentialHeader`,
`CustomDestinationState` and `EgressDenialReason`. They contain exactly the canonical ordered
values and are referenced rather than copied into another enumeration. "Exactly once" means
one OpenAPI enum definition, not removal of canonical values from the pinned source or fixtures.
The canonical state-denial map is preserved as `CustomDestination.x-state-denials`; that
metadata is not a generated runtime conditional validator.

## Enrollment operations

All paths are relative to the configured **core API**, never a custom provider.
Their sole tag is `CustomDestinations`. UUID possession is not authorization: the server must
derive the live owner/session/device from verified ADR-019 context.

| Method and path | Operation | Step-up |
| --- | --- | --- |
| `GET /ai/custom-destinations` | `listCustomDestinations` | No |
| `POST /ai/custom-destinations` | `registerCustomDestination` | Required |
| `POST /ai/custom-destinations/{destinationId}/validate` | `validateCustomDestination` | No; strictly TLS probe-only |
| `POST /ai/custom-destinations/{destinationId}/activate` | `activateCustomDestination` | Required |
| `POST /ai/custom-destinations/{destinationId}/suspend` | `suspendCustomDestination` | No; protective action |
| `DELETE /ai/custom-destinations/{destinationId}` | `revokeCustomDestination` | No; protective action |

Every non-GET operation retains the accepted `IdempotencyKey` parameter and replay header.
No Money/import/dedup prerequisite is introduced. Personal settings and challenges are no-store.
List is owner-only and bounded (limit 1-100, default 20); its cursor is opaque.

Registration accepts only `host`, optional `pathPrefix`, `credentialHeader`, optional
`providerKeyRef`, and `models`. It does not accept arbitrary URLs, raw keys, header maps,
owner IDs, approval flags or caller-selected destination class/state. A key reference addresses
an already enrolled, authorized credential; this group neither enrolls nor reveals one.
Models follow ADR-023's grammar and 1-16 unique-name bound, not a speculative model whitelist.
The response publishes `destinationId` and each server-issued `custom_model_id`.

Lifecycle bodies are closed objects containing `version`: a positive canonical decimal string,
at most 19 digits. This source contract's monotonic server revision and exact-match precondition
prevent a delayed action from silently targeting a replaced registration; it is not an ADR
claim that a database, lock, receipt or concurrency protocol is already implemented.
Validate cannot mutate active pins, models, keys, state or approval. Reconfirmation that
mutates state uses activate with step-up; waiting out a hold never activates automatically.

Host/prefix patterns are only lexical wire controls. Actual IDNA normalization, pre/post-IDNA
literal denial, complete DNS answer classification, byte/label bounds, self/metadata refusal,
TLS identity, single decode, prefix/segment rules, pinned dialing and redirect/lease enforcement
remain ADR-022's runtime owners' duties. Reserved `.example` fixtures are deliberately
ineligible for real egress and never prove network validation or activation.

## DPoP and step-up binding

The shared names coordinated with original contracts#1 are `securitySchemes.DPoP`,
root `security: [{DPoP: []}]`, `DPoPProofValue`, `StepUpTokenValue`,
parameters `DPoPProof` / `StepUpToken`, and challenge headers `DPoPNonce` / `DPoPAuthenticate`.

Pinned generator 7.25 has no HTTP-DPoP template. `DPoP` therefore uses an OpenAPI `apiKey`
**raw-header code-generation mapping** for `Authorization`; the protocol remains RFC9449,
not API-key or Bearer authentication. Supply the complete `DPoP <at+jwt>` value and leave
the generated API-key prefix unset. Do not add a second Authorization parameter.
All six calls require a `DPoP` proof argument; no proof/token default is embedded.

The generated parameter is not a signer. A fresh proof is required for **each physical send**,
including retries and nonce-challenge resends, with actual method/target, `ath`, `cnf.jkt`,
`iat` within 60 seconds and fresh `jti` under the 300-second replay cache. Reading
`DPoP-Nonce` / `WWW-Authenticate` is not verification. No mandatory request nonce header is
invented. Automatic retry/redirect adoption stays blocked without a per-send proof hook;
one successful mock transport call does not prove retry, signature or server admission.

The coordinator's explicit source choices are the opaque, nonempty `Step-Up-Token` header
and `step-up-request@1`; these spellings are not presented as literal ADR-019 quotations.
The grant's `op` is the unique operationId. Its `rq` is unpadded base64url SHA-256 of the
UTF-8 RFC8785 canonical JSON envelope with exactly:

| Member | Bound value |
| --- | --- |
| `version` | `step-up-request@1` |
| `operation` | Actual unique OpenAPI operationId |
| `method` | Uppercase actual HTTP method |
| `target` | Every validated path parameter in a closed object; `{}` for register |
| `body` | Exact strict validated JSON request object; preserve omissions, insert no defaults |

Reject unknown fields, duplicate JSON names, non-IJSON values and unknown query parameters.
Do not normalize host, deduplicate/reorder models, remove fields or insert a prefix before the
digest. Every supplied registration field, destinationId and activation revision participates.
The issuer must validate/bind the same intent; the resource verifier must recompute it from
the actual request and require exact session/key/operation/target/body binding, passkey UV
freshness at most 300 seconds, expiry and atomic single use. Verified context alone supplies
sid/jkt authority. No body, host, credential reference, token or request digest belongs in audit
or telemetry.

`custom-destination-step-up.v1.json` contains 11 fixed synthetic ASCII-only canonical byte
vectors and their digest/base64url expectations, including omissions, field changes,
model ordering, target and revision changes. Their test is a finite conformance check,
**not** a JCS implementation, duplicate-name parser, issuer, cryptographic verifier or
single-use runtime. No such runtime is added here.

## Lint and negative controls

`pl-no-inference-address` inspects AI/inference/tool operations and named request components,
follows local requestBody/path/schema references and reference siblings, and walks arrays,
compositions and cycles. It refuses address/header aliases, encoded names, open nested maps,
external/dynamic references, untyped array/media shapes, object-valued constant escapes and
string-encoded tool arguments. Ordinary typed prompt strings remain allowed; the rule is not
a prompt-content classifier or a substitute for server-side routing authority.

Only the exact six operation names and canonical enrollment body references receive an address
exception. A substituted body, reused tag/path or address query/header does not. The planted
test-only inference overlay is **not** the production tool group owned by contracts#10.
Real Spectral CLI tests plant `base_url`, `endpoint` and `host` through requestBody references,
arrays/compositions and nested objects; all must fail with the rule name and precise path.
The same overlay with the address removed must pass.

## Generated-runtime gaps

Deterministic generation and schema validation are not T6 proof. The source tests deliberately
exercise the actual generated models/APIs, with passing positive controls before negatives.
At this source boundary the following failures block adoption:

| Shared interface | Actual gap and required integration |
| --- | --- |
| TypeScript generated `*FromJSON` | Selects known fields without enforcing closure, enums or wire constraints; nested addresses and invalid lifecycle values are accepted. Registration's `Set` conversion deduplicates before any step-up binding. Require the actual shared generator's strict request/nested model path, not a local parallel decoder. |
| Kotlin generated model / `PennilogicJson` | Existing additive `ignoreUnknownKeys` semantics accept unknown request members; `Set` decoding collapses duplicates. Valid UUID-containing registration/response decoding needs the missing shared UUID serializer. Preserve accepted Money/Instant and additive-response behavior while the original generator owner integrates strict request handling. |
| Python generated API/model | The API template has strict-mypy errors; canonical enum/UUID strings fail the generated `from_json` helper, while actual `model_validate_json` accepts unknown members and duplicate names. Fix the shared generation/conversion seam without weakening types or hiding T6 failures. |
| Shared problem detail | Accepted `ProblemDetail` is open and its `reason` is an untyped string. Namespaced success schemas are closed, but error closure/typing remains unmet. Original contracts#16 must supply a real closed shared refusal shape referencing `EgressDenialReason` without changing the global problem-code vocabulary or leaking input. A namespaced parallel catalogue or an unaccepted draft is not this binding. |
| Proof / step-up runtime | No per-send signer hook, JCS parser/runtime, issuer, freshness verifier or atomic single-use implementation is supplied. Finite source vectors and mock transport headers cannot authorize enrollment. |
| Shared pipeline tests | Legacy probe helpers assume literal `paths: {}` and tests assume `0.1.0`. The original provider/pipeline owner must integrate these real additive paths/version; this source unit does not patch their helpers. |

Namespaced tests use real generated TS fetch, Kotlin Ktor and Python urllib3 interception with
no sockets. They verify raw authorization, six proof arguments, step-up/idempotency scopes and
DELETE routing. Python's standalone runtime command is additional diagnostic evidence only;
it does not turn a failing official mypy-first smoke command into a pass.

Reproduce from the repository root with the existing managed dependencies:

```text
python scripts\lint_spec.py
node --test scripts\tests\custom_destinations.test.cjs
python scripts\generate_clients.py --verify
git show aa8d90cb98cec9b6dd08c91b3a4d869e47362662:spec/openapi.yaml > build\accepted-aa8-openapi.yaml
python scripts\check_breaking_changes.py --base build\accepted-aa8-openapi.yaml
python scripts\smoke.py typescript
python scripts\smoke.py kotlin
python scripts\smoke.py python
python -m unittest discover -s scripts\tests -p "test_*.py"
```

The export above requires byte-preserving native redirection (PowerShell 7.4+ on Windows).
`--base` accepts a published tag or an existing file, not an arbitrary commit ref; do not
replace the accepted comparison with a newly invented baseline or tag.

These commands must report their real outcomes, including failures. Do not skip expected
failures, relax type-checking, hand-edit generated clients, flip shared unknown-key defaults
or create a competing decoder to obtain green output. Golden/index changes must come from
the real generator. Root serializes auth, typed-refusal, common generator and metadata
integration before any publication. Separate non-author Core/Contract/Security and QA review,
current-head native CI, and hosted job **and** whole-workflow durations strictly below 600 seconds
remain required; local source timing is not hosted evidence. H1/H2 and applicable T1-T20,
provider evidence, consent, server authorization and operational approvals remain unmet here.
