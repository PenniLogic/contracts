# Custom destinations: source contract and integration boundary

This is the local, unreleased source implementation of
[contracts#27 / T-CON-EGRESS-01](https://github.com/PenniLogic/contracts/issues/27).
It does **not** complete that issue, publish a client version, implement a gateway or authorize
AI exposure. The `0.3.0` source version composes the accepted provider source at
`5b41d4580c85be3cc1617074c0f3052b1f7b02cd`; it is not a tag or release.
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
The copy guard rejects another enum containing the complete canonical set even when reordered,
widened or padded with repeated members. Ref-only aliases and unrelated or partial-overlap enums
remain allowed; the actual Spectral controls distinguish these from complete copies.
The custom guards distinguish OpenAPI/schema containers from annotation data: schema-map names such
as `example`, `examples` and `default` remain inspectable through nested objects, arrays and
compositions. Genuine example/default/const/enum payloads are data, not extra schema declarations.
This is not a guarantee about the whole Spectral engine: its unchanged stock
`oas3-valid-schema-example`/resolution path can reject an AJV-valid example payload that combines
`enum`, `example`/`default` member names and a literal `$ref`. That known stock limitation has no
`pl-*` finding, is not fixed or waived here, and must not be reported as a passing full-source
positive. Existing passing CLI positives remain required; the stock rule and dependencies remain
unchanged.
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
Stock API-key setup labels or prefix examples are not DPoP protocol guidance.

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
string-encoded tool arguments. The canonical registration address roles (`host` and `pathPrefix`)
also identify their declared schemas and source-proved equivalent aliases: an innocuous inference
field cannot reuse those address scalars through references, compositions or nested arrays.
The rule and strict compiler share the compiler's existing non-constraining annotation vocabulary,
including `x-not-money` and the supported provider metadata, so those annotations cannot hide a
registration role on a direct reference or intermediate alias. This does not admit new compiler
keywords or change default handling; the existing lint-only `summary` annotation remains separate.
The bounded proof follows local references and positive `allOf` conjunctions, retaining scalar
type, effective length bounds, exact pattern/format identities and finite string constraints.
Matching types, equal/looser bounds and equivalent intermediate or renamed wrappers cannot erase
the role. It does not blindly propagate provenance into every referenced base: a genuinely narrowed
general-text use remains separate when a concrete value satisfies the base but not the role.
That proper-narrowing witness is limited to empty/single-character strings; it is not a
regex-equivalence guess. Unproved narrowing, unsupported/ambiguous composition, cycles and exhausted
bounds fail explicitly rather than certify role absence. Traversal is limited to depth 64, 16,384
nodes, 256-member conjunctions/finite sets and 4,096-character pattern sources. Component-name
resemblance or regex similarity alone is not address provenance. Compiler/generator inputs and
their supported vocabulary are unchanged.

Explicit `uri`, `uri-reference`, `uri-template`, `iri`, `iri-reference`, `url`, `hostname`,
`idn-hostname`, `ipv4` and `ipv6` format declarations are address-bearing in inspected requests.
This finite source check does not expand the generated validators' supported format vocabulary.
Ordinary typed prompt strings, including URL text as content, and owner-bound destination/model
identifiers remain allowed. The rule does not classify arbitrary strings or regex languages,
authorize routing or prove operational SSRF resistance.

Negative `not` compositions beyond plain scalar exclusions are explicitly unsupported in inspected
request schemas, even under an outer scalar type. Scalar type/const/enum/pattern/bound exclusions
remain allowed, including the existing DPoP newline exclusion. References, formats, structured
values and nested composition inside `not` are refused. The rule does not attempt Boolean
equivalence or polarity analysis; this conservative source boundary prevents double-negation from
hiding an enrollment scalar or format.
It applies through request bodies, parameters, references and nested arrays/compositions, not to
ordinary field names or schema-shaped example data. It does not change the compiler vocabulary or
the existing response-schema negative constraints.

Only the exact six operation names and canonical enrollment body references receive an address
exception. A substituted body, reused tag/path or address query/header does not. The planted
test-only inference overlay is **not** the production tool group owned by contracts#10.
Real Spectral CLI tests plant `base_url`, `endpoint` and `host` through requestBody references,
arrays/compositions and nested objects; all must fail with the rule name and precise path.
Semantic scalar aliases, address formats and fake enrollment/parameter exceptions have named
actual-CLI controls as well. The same overlay with the address removed must pass.

## Shared error composition

The single owning `error-catalogue.v1.json` advances to group `1.1.0`; import group `1.0.0`,
all 14 accepted service policies and the exact eight-state taxonomy projection remain intact.
Root's additional source decision binds the canonical reasons as follows; these code/status
choices are not claimed to have already appeared in ADR-022.

| Canonical reasons | Shared code / HTTP | Classification |
| --- | --- | --- |
| `destination_busy`, `unreachable` | `dependency_unavailable` / 503 | Existing service policy and retry/key treatment |
| `registration_rate_limited` | `rate_limited` / 429 | Existing service policy; mandatory delay and allowance |
| `step_up_required` | `step_up_required` / 403 | Authentication-owned, NONE/null service state |
| Other ten canonical reasons | `egress_denied` / 403 | `request_failed` / `error`; never automatic retry, fallback or repinning |

The new denial uses only the fixed diagnostics "Destination not available" and
"The destination cannot be used for this request." Explicit resubmission after independent
resolution keeps the same intent/key/body within existing bounds; edited input is new intent.
Authorization and indistinguishable unknown/foreign resource handling precede this classification.

`EgressDeniedProblemDetail` requires the shared typed `egress_denial_reason`, not a
`ValidationReason`. `AuthenticationProblemDetail` has the two authentication-owned global codes;
`authentication_required` is 401 and `step_up_required` is 403. Both route to the already excluded
authentication flow, not a new service state. Unrelated authentication needs no egress member.
The egress-aware `OperationProblemDetail` union used by these six operations additionally requires
that member for `egress_denied` and `step_up_required`. `ServiceProblemDetail.code` intersects the
one global `ProblemCode` reference with the positive, catalogue-derived service subset: the old
14 codes plus `egress_denied`, never the two authentication codes. Its new code still has fixed
safe diagnostics, but no egress member; it cannot substitute for the reason-bearing operation
refusal. Exactly one operation family must validate before conversion. Actual Kotlin, TypeScript
and Python constructors/converters retain the public `ProblemCode` type, not a second enum.

Every 401 uses `AuthenticationRequiredProblemDetail`, required `WWW-Authenticate` and no-store.
A nonce challenge is exactly `DPoP error="use_dpop_nonce"` with fresh `DPoP-Nonce`; this is protocol
metadata, not a new ProblemCode. A present `retry_after_seconds` requires equal decimal
`Retry-After`. Body schemas do not verify HTTP framing: consumers must check status equality,
problem media and required/conditional headers, without turning a delay into retry permission.

Python's normal generated API raises `ApiException` with the strict generated model in `.data`.
Validation errors now propagate instead of being masked by a `finally` block that copied an
invalid raw body into an API exception. TypeScript still raises `ResponseError`; the caller
explicitly decodes its response with the generated shared converter. Kotlin exposes its
`HttpResponse` and `typedBody<AuthenticationRequiredProblemDetail>` / `typedBody<OperationProblemDetail>`.
The transport tests exercise those real paths with synthetic responses, not a second decoder
or a claim of automatic framing, authorization or retry enforcement.

## Generated consumers and preserved failure history

All six closed destination object models select the accepted marked-model serializer path.
It checks nested unknown members, wire kinds, source patterns, bounds and duplicate array entries
before projection or `Set` conversion, and checks construction and outbound serialization.
The shared UUID bridge preserves hyphenated UUID wire strings; source constraints further require
the declared lowercase v4 identifiers. The generated Python API has precise transport/return
typing without relaxed mypy settings. Legacy DTO behaviour and Kotlin's global JSON configuration
are unchanged.

The finite compiler retains complete source compositions and constants for runtime validation.
Its generation-only layout uses the marked closed object's declared fields and required list,
without allowing the pinned generator to flatten union branches into mandatory fields or
manufacture boolean/ref enums. The manifest binds both the layout input and retained constraints.
An enum constraint next to a reference remains in runtime validation; the generation-only layout
retains the referenced public type instead of manufacturing a competing field enum.
TypeScript omits only declared undefined optional output members after native-field checks,
then validates the actual emitted object; unknown names, including undefined names, still fail.
This is not a raw duplicate-JSON-name parser or a step-up/JCS implementation.

Deterministic generation and schema validation are not T6 proof. The source tests deliberately
exercise the actual generated models/APIs, with passing positive controls before negatives.
The original source-only freeze `c296ebbc4330ab636b8f20b0de8bc3e96b7f7b39`
(tree `e0bbedbad85be0823ad3024ac5b25abf4e498229`) remains unchanged in history.
Its AA8-only failures, and the uncorrected ordinary composition with accepted `5b41d458`,
are retained as failed evidence rather than rewritten as passes:

| Shared interface | Historical failure and current treatment |
| --- | --- |
| TypeScript generated `*FromJSON` | AA8 dropped unknown fields and deduplicated before validation. Accepted marked-model machinery now guards this consumer; closed union output omission is repaired at the canonical template. |
| Kotlin generated model / `PennilogicJson` | AA8 accepted unknown members/collapsed duplicates and lacked UUID serialization. Marked serializers now cover this group and the shared UUID serializer is registered without changing Money/Instant or additive defaults. |
| Python generated API/model | The combined baseline retained 32 API typing errors plus enum/UUID/strict-wire failures. Canonical templates, typed UUID conversion and marked validation repair those paths; the raw-error masking failure is separately preserved. |
| Shared problem detail | The open AA8 scaffold was insufficient. Accepted provider source plus Root's explicit group-1.1.0 decision now supplies the shared auth/service-egress families above, one global code enum and unchanged validation reasons. |
| Shared pipeline tests | The old empty-path/version/text-fragment assumptions failed on the real composition. Named additive helpers now preserve genuine operations, security, responses and version, and breaking probes retain valid references. |
| Generated public APIs at `1ce4acd5` | The identical old TypeScript service-policy lookup compiled against accepted 5b but failed with TS2322/TS2532 against 1ce4. The old positional Kotlin import consumer also failed native compilation. Canonical service-key typing and generic layout ordering now preserve those APIs, all 22 accepted model primary signatures and old model/helper exports; no emitted-file patch or caller migration is used. |
| Proof / step-up runtime | Still not supplied: per-send signer, raw duplicate-name/IJSON parser, JCS implementation, issuer, freshness verifier or atomic single use. These are future runtime obligations, not gateway/admin/tool-group additions to issue27's source acceptance criteria. |

The shared transport inventory retains all 19 accepted provider entries and adds these six
destination and four error models. All 29 are covered; eleven declared arrays yield fifteen
recursive null controls and 45 TypeScript malformed-array controls, without dropping old cases.
The three native suites also reject null at each marked model root. Python rejects null before
its ordinary deserializer can bypass the model, including actual 401/403 error responses;
explicit Optional and legacy/no-content controls remain. Actual API argument-type failures
hide input in normal diagnostics. These are not proof-signature or server-admission checks.

**Local compatibility correction, integration still held:** a bounded source-backed proof now
passes the genuine accepted-`5b41d458` comparison without an acknowledgement. It binds the exact
oasdiff records and complete reference closures, proves the required finite discriminator's old
domain is excluded from each new guard, preserves the old constraints/prefix and separately
checks response metadata. Generic paired controls retain refusals for unsupported constraints,
malformed/partial records, changed reference identities and enum changes in old negative
positions. The original negative-partition finding, all seven `1ce4acd5` positive-partition RED
findings and both public-API failures remain failed historical evidence. The diff-only detector
still reports those seven findings; no allOf blanket waiver, consumer migration or binary ABI
certification is claimed. See [the bounded proof](publication.md#bounded-source-backed-conditional-expansion).

AA8/5b source consumers remain pinned unchanged. Future adoption of `0.3.0` requires actual
consumer regeneration and correct auth-versus-service routing; new codes must not be emitted
under older consumer pins. No old shape is removed and no consumer migration is claimed.
No contract-stage acknowledgement, published tag, release or deployment is authorized here.

Namespaced tests use real generated TS fetch, Kotlin Ktor and Python urllib3 interception with
no sockets. They verify raw authorization, six proof arguments, step-up/idempotency scopes and
DELETE routing. Python's standalone runtime command is additional diagnostic evidence only;
it does not turn a failing official mypy-first smoke command into a pass.

Reproduce from the repository root with the existing managed dependencies:

```text
python scripts\lint_spec.py
node --test scripts\tests\custom_destinations.test.cjs
python scripts\generate_clients.py --verify
git show 5b41d4580c85be3cc1617074c0f3052b1f7b02cd:spec/openapi.yaml > build\accepted-provider-openapi.yaml
python scripts\check_breaking_changes.py --base build\accepted-provider-openapi.yaml
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
the real generator. Root serializes review and integration of this combined owning source
before any publication. Separate non-author Core/Contract/Security and QA review,
current-head native CI, and hosted job **and** whole-workflow durations strictly below 600 seconds
remain required; local source timing is not hosted evidence. Future H1/H2 and applicable T1-T20,
provider evidence, consent, server authorization and operational approvals remain unmet here.
Gateway runtime, admin UI and the production tool group remain explicit issue27 non-goals.
