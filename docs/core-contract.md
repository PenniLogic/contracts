# Core contract: authentication, accounts, transactions and categories

Original [contracts#1](https://github.com/PenniLogic/contracts/issues/1) adds the four core groups
to accepted `ffdd507206990cb880baea150ffae2f6a7bb3043`, preserving the accepted Money source,
T-CON-10 import/dedup, T-CON-12 error and custom-destination definitions. The source version is
`0.4.0`; it is not a published tag or deployment. Original issues #1/#16 and
[PenniLogic/infra#22](https://github.com/PenniLogic/infra/issues/22) are not completed by this source.

The existing custom-destination group and exact ADR-022 files remain intact, with global/custom
AI OFF, empty approvals and empty deployment inventory. No debt, goals, groups or inference
endpoint is introduced here.

## Shared authority

ADR-015 fixes the sole Money object, symmetric minor-unit range, currency exponents, fixed UTC
millisecond instants and scoped P30D/P14D idempotency. ADR-016 fixes reporting categories and
whole-set append-only assignments; ADR-017 fixes balanced append-only ledger corrections.
ADR-018 excludes raw messages and raw-derived digests. ADR-019 fixes passkeys, DPoP, device
binding, sessions, step-up and recovery. These accepted bytes remain authoritative at Docs
`a700e639585c61a4610e7b99dbd02b2dab28bdcc` and are unchanged in current Docs
`47986ef6bef986a3ba9214ccf652609347d73c66`.

`AuthenticationParameters` identifies the complete section-15
`adr-019-auth-parameters@1` mirror in its `x-adr-019-auth-parameters` source metadata. A contract
test compares the full sorted-JSON digest against the accepted block and binds the generated
enums and wire constants to it. Null/false owner inputs and domain/signing placeholders remain
unbound, not production RP/provider configuration. This is an artifact comparison, not the
step-up RFC8785 request digest.

Public transaction aliases are exactly `DedupRecordId`, the same component used by
`DedupOutcome.matched_record_id` and `DuplicateOverride`. There is no second dedup shape,
currency registry, error catalogue or amount parser. Other resource IDs use server UUIDv7;
they are never authorization claims or echoed diagnostic identifiers.

## Endpoint groups

| Group | Available source operations |
| --- | --- |
| Auth | Consent/eligibility-receipt enrollment and email proof; WebAuthn registration/assertion options/results; Android/browser refresh; private profile; credential/device/channel list and management; protective sign-out; recovery and one-shot codes; intent-bound step-up options/result. |
| Accounts | Own list/create/read/name/archive; opening balances are separate balanced postings. Only ASSET/LIABILITY are client-creatable and the MVP ledger admits INR only. |
| Transactions | Own posted list/read/manual structured posting; exact reversal and atomic reversal/re-post correction. No PATCH/DELETE ledger facts. |
| Categories | Own list/create/read/label/archive, append/retract redirects, and exact/weighted whole-set categorisation. No hard deletion or category-as-account model. |

Every request object and its nested objects reject unknown members. Raw SMS/email/file bodies,
raw hashes/digests, arbitrary metadata, caller owners, client dedupe keys, posting clocks,
trust verdicts and writable category projections are absent. Bounded user-authored labels/notes
are personal descriptive fields, not a captured-message transport or log attribute.

A posted transaction carries both `occurred_at` and application-assigned `booked_at`. The server
validates ownership, admitted currency, nonzero entries, exact zero sum and sealed composition.
Opening balances carry a date-only `value_date`; corrections never mutate an old entry/date.
Calendar reasoning is server-owned in the profile's tzdb-validated IANA zone.

Financial writes declare principal/method/path-template/key scope and P30D/P14D bounds, publish
their structured duplicate-screen field set/window and reference the shared `202` decision.
Reversal uniqueness and correction-link constraints are independent duplicate backstops.
Same-key/different-payload is the existing safe mismatch, never a reason to mint another key.

## Authentication is not public-by-default

The accepted shared names are root `DPoP`, per-physical-send `DPoPProof`, `StepUpToken`,
`DPoPNonce` and `DPoPAuthenticate`. Root authorization remains the raw-header code-generation
mapping for the complete `DPoP <at+jwt>` value with prefix unset, never Bearer.

Before an access token exists, `DPoPBootstrap` explicitly overrides the resource-token requirement
for bounded authentication ceremonies. It maps the proof header, but its
`x-pennilogic-per-call-proof` designation prevents generated auth settings from overwriting the
required call argument with a configured static proof. This is a new source transport choice,
not an assertion that a preauthentication verifier or signer exists. The server must verify
the actual method/target/key/iat/jti and each channel/ceremony/recovery/refresh instrument.
Resource calls additionally verify the access-token hash and session/key binding.

Bootstrap, refresh-cookie and opaque capability spellings are local source choices where the ADR
did not prescribe literals. Browser refresh requires the browser-carried
`pennilogic_refresh` HttpOnly/Secure/SameSite=Strict cookie, not a script-readable operation
argument; use browser credentials inclusion and server origin/CSRF controls. The browser JSON
refresh envelope structurally excludes a refresh token. Android receives the rotating value.
The first-party token request uses explicit JSON, not a claim of a public OAuth authorization server.

Only justified ADR-015 `x-idempotency: auth` operations use ceremony/session/recovery lifecycles.
Refresh retains the inclusive 30.000-second grace, 30-day sliding and 90-day absolute family
lifetime; no credential response is put in a P30D financial replay store. Profile metadata is
keyed normally. Protective sign-out/cancellation does not require new enrollment step-up.

`Step-Up-Token` and `step-up-request@1` are Root's explicit technical choices. The issuer and
target must validate the same exact registered operationId, uppercase method, every path
parameter and strict wire body, preserve omissions, insert no defaults, reject unknown query/
JSON fields, duplicates and non-IJSON, then compute the prescribed UTF-8 RFC8785 hash envelope.
Verified context alone supplies sid/jkt; UV freshness is at most 300 seconds, expiry and
atomic single use are required. These schemas and finite vectors are not JCS, signature,
nonce, retry, issuer, verifier, database locking or live-session implementations.

Recovery is proof-gated and anti-enumerating. Anonymous account/channel/code/limiter differences
are accepted-and-dropped identically. Only a proven own key-bound flow receives window, lock,
pending-route or restriction context. Notification acceptance precedes cooling-off;
`notification_pending.window_ends_at` is explicitly null. A timer is not authorization.
The recovering-device one-shot add, 24-hour credential maturity, protective not-me, suspect
channel rules and restoration nonresurrection remain server duties.

`addCredential` always requires its mature-passkey step-up. The separate
`addRecoveryCredential` operation obtains the accepted one-shot grant with a proven recovering
instrument on the same restricted device/session/key; it does not require an impossible new
mature passkey or authorize another step-up action. Its allowance, window and single consumption
must be enforced atomically by the real server. Unproven receipts structurally exclude window/
proof/account context, and proven flows return the instrument needed for their next action.

## Shared authentication errors and nullable values

The canonical original #16 catalogue is extended to group `1.2.0`, preserving every old row,
service-state mapping and egress binding. Its six new ADR-019 authentication codes remain
outside the accepted eight service states, in the already excluded authentication flow:
`session_revoked`, `step_up_credential_too_new`, `restricted_after_recovery`,
`recovery_notification_pending`, `recovery_locked`, `recovery_pending_elsewhere`.

Their context is closed, code-specific and safe: reason class; maturity/restriction/not-me/lock
instants; explicit null notification window and delay; or pending route/window and the single
`cancel_and_restart` action. No credential/channel value, user/account existence, amount, provider
text or arbitrary diagnostic can be carried. Recovery limiting never selects one of these
codes for an anonymous caller. Existing `AuthenticationPolicy` constructors are unchanged;
separate typed authentication retry/lifecycle tables add classification without a service policy.

New context codes use `AuthenticationContextProblemDetail`. The accepted two-code
`AuthenticationProblemDetail`, narrow `AuthenticationRequiredProblemDetail` and legacy
`OperationProblemDetail` response domains are preserved; old operations cannot emit new codes/
contexts that old enum clients reject. New core responses use the shared
`ApplicationProblemDetail`/`AuthenticationChallengeProblemDetail` composition. A request expansion
alone is never proof of legacy response compatibility.

The strict compiler now supports explicitly declared `null`, scalar-plus-null type pairs,
nullable scalar references and nullable arrays/items. Nested/reference/array reads and writes
use those declarations. Untyped values, multiple non-null type unions, nullable object unions,
ambiguous/cyclic/unsupported references and other unsupported families still fail closed.
Null remains rejected for Money, non-null fields and marked object roots.

Missing, explicit null and non-null are distinct. Required nullable constructor arguments cannot
be omitted. Python retains explicit `model_fields_set` nulls; TypeScript preserves null while
omitting optional undefined. New optional-nullable Kotlin fields use
`ProviderPresence.Absent` or `ProviderPresence.Present(value)` rather than overloading null as
absence. This is generic declaration-driven generation, not a recovery-model exception.
The accepted global JSON configuration, old optional-non-null fields and Money codecs are unchanged.
Nullable enum references retain their shared named enum, never a copied inline vocabulary.
Source-derived generator metadata repairs the pinned generator's lost enum-reference
nullability, including required/optional fields, scalar aliases and nested array/set items.
Full source guards still reject null for non-null enums, unknown values and duplicate items.

## Typed success statuses, not new wire envelopes

The pinned generator previously selected the first success body and wrongly rejected a valid
shared `202` decision. The canonical generator now derives status-discriminated success carriers
from declared distinct successes. The actual `201` resource and `202` bare `DedupOutcome`
wire schemas/statuses are unchanged.

TypeScript exposes a literal-status union; Python/Kotlin expose nominal status cases with a
typed `body`. These are SDK HTTP results, never serialized API envelopes. Existing single-success
method signatures and model constructors remain intact. Exact response-schema restrictions,
media, empty bodies and unexpected success statuses are checked before conversion. Error
responses still use the shared error families; Kotlin retains raw status/headers and explicit
typed-error access rather than pretending an error is a success.

This finite adapter supports named closed marked-object bodies (and their supported positive
reference refinements) plus empty branches. Ambiguous/nonlocal/untyped or unsupported success
bindings fail generation before replacing prior valid clients; there is no Any/blob fallback.
Temporal query arguments use the canonical Instant/LocalDate seams, not raw strings, object
enumeration, incidental `toString` forms or relaxed type checks.

The nested `AuthStepUpIntentTarget` and `AuthStepUpIntentBody` are named strict schemas with the
same wire fields, not permissive inline DTOs. Their normal converters preserve exact omissions
before the outer operation-specific guard. Scratch-generated consumers also exercise literal
null, scalar pairs, multi-hop aliases, nullable/nested arrays and body/empty success branches in
all three real targets.

## Cursor, filter, sort, version and immutable rollback

The shared `CursorPage` envelope defines `page_size` 1-100 and `has_more`; next `cursor` exists
iff there is another page. Default size is 20. Cursors are opaque and bound to verified principal,
operation, complete filters/sort and a stable snapshot with PT24H lifetime. The service rejects
expired/foreign/changed-scope cursors, repeated/unknown selectors, noninteger/oversized pages and
undeclared unstable sorts. Every declared sort includes a unique-id tie breaker.

Transaction time filters are `[occurred_from, occurred_before)` canonical UTC; an empty/reversed
interval fails. `as_of` is not applicable with CURRENT; AS_RECORDED echoes the server-used/clamped
instant. JSON Schema/fixtures prove lexical/bound/closed-query behavior, not cursor authenticity,
storage snapshots or server authorization.

`API-Version` selects the configured major `/v1` interface; omission preserves it. A retired/
unknown major gets stable `validation_rejected`, field `request`, reason `not_available`, not
silent write rerouting. The contract semantic version is separate. Additive changes are MINOR;
breaking changes use expand/migrate/contract and the existing complete acknowledgement check.
This source chooses at least P90D deprecation notice; services must emit the published notice/
sunset metadata before retirement. No deployed compatibility window is claimed.

`CoreDeprecation`/`CoreSunset` declare the RFC9745 structured date and RFC8594 HTTP-date headers;
source lint requires both on every response if an operation is marked deprecated. No current
operation is deprecated. `x-contract-changelog` version 1 records the exact schema, parameter,
header, response and security-component delta from the accepted base, separately from general prose.

Release/tag/artifact metadata and immutable rollback use the existing pipeline. Rollback re-pins
the prior immutable version; never move a tag or replace existing bytes. No version tags,
prerelease channel, package credentials/publication or producer runtime adoption are created here.

## Reusable fixtures and real validation

`core-endpoints.v1.json` and `auth-endpoints.v1.json` contain one named valid request and one
deterministic invalid mutation per actual operation, sharing synthetic payloads in the same files.
The API contract harness can materialize these directly; it must not author consumer copies or
interpret a fixture pass as a deployed response test. `authentication-errors.v1.json` is the
canonical error-context corpus. Existing monetary/time/property, old provider/constructor/privacy,
custom-destination and source-negative corpora remain required.

Run the actual documented lint, generation/golden verification, three smoke commands and pipeline
suite. Generated outputs/goldens come only from the real pinned generator, never hand edits.
Mock transport tests exercise real generated signatures and converters but never contact a
provider, device, deployed API or verifier.

## Exact accepted category source

`spec/category-seed.v1.json` is the byte-identical protected-accepted
[PenniLogic/api](https://github.com/PenniLogic/api) provider from commit
`fd58da679672a4de7aabee2562644bed3799e614`, path `data/categories/category-seed.v1.json`:
14,237 bytes, SHA256 `3fcf568abcfc7da7409e463ea6bbda9d952af739cfa9ef42a8e9fe90b993390c`,
Git blob `16b70af7b527bfd617539d9bd413ba78738f8f05`. The OpenAPI source pin, lint,
generator and publication companions bind those exact bytes, not a proposal or API branch.

Its ordered 59 SYSTEM keys (16 roots, 43 children), parent/nature/lifecycle defaults,
16 icon code points and eight integer-sRGB colours derive the one shared typed vocabulary
and immutable default registry in Kotlin, TypeScript and Python. Locale selection uses
the explicit en-IN fallback; no Hindi translation is claimed. Run
`node scripts\category_seed.cjs --write-enums spec\openapi.yaml` to derive the enums;
normal pinned generation derives every client/default registry and companion hash.
Byte, pin or vocabulary drift fails closed.

Category UUIDs and owner-bound runtime materialisation remain distinct from SYSTEM keys.
API9's seed execution/result/provider adoption and API7's runtime categorisation are not
implemented by this source, and optional AI remains OFF.

`CHANGELOG.md`, `docs/development.md` and the 50-case custom-destination source test
were reserved until Root transferred the accepted timing continuation. This branch ordinarily
fast-forwards to `2c4a1f1545599949f1a6ac1e061c7219fb841c8e`; its aggregate raw-byte boundary,
concurrency two, error/close/cleanup behavior and every source assertion remain preserved.
`CHANGELOG.md` now records the initial core contract without replacing historical failures.

Final native hosted CI/current-base timing, separate non-author review, release/publication,
server/consumer acceptance and Infra #22 qualification remain Root's uncompleted integration work.
