# Shared error and import providers

**Authority and status:** local source preparation through original
[#16 (T-CON-12)](https://github.com/PenniLogic/contracts/issues/16) and
[#13 (T-CON-10)](https://github.com/PenniLogic/contracts/issues/13), not an accepted
release or implemented service. Repository source version is `0.2.0`; both new
component groups start at `1.0.0`. `paths` remains empty. No replacement issue,
endpoint, backend import/dedup algorithm or client screen is supplied.

The coordinator re-sequenced only this provider slice against accepted contracts
`ea56c63d5c9b679537bd9205b04626049c20c572` and accepted Docs
`a700e639585c61a4610e7b99dbd02b2dab28bdcc`. The original native dependencies and full
definitions of done remain unchanged. Final endpoint adoption is original
[#1](https://github.com/PenniLogic/contracts/issues/1) and API consumer work.
[PenniLogic/infra#22](https://github.com/PenniLogic/infra/issues/22) remains an
integration hold. A source pin is not a published client pin.

## Sources and machine catalogues

`spec/openapi.yaml` owns wire shapes. `error-catalogue.v1.json` owns error diagnostics
and retry/key classifications; `client-state-bindings.v1.json` is the exact identifier
and service-condition projection of accepted taxonomy **1.1.0**, with its source pin
and SHA-256. It does not copy or implement UI wording, state composition or accessibility.
`import-group.v1.json` owns the new import version, source-pair windows, limits, public
identifier binding, replay/count/decision rules and accepted ADR source bindings.

Accepted inputs are ADR-015 sections 1.5, 4.2-4.8, 7 and 8; ADR-016 sections 4.2,
4.3, 4.5 and 10/14; ADR-017's rejection reasons and INR-only ledger admission;
ADR-018 sections 7.3/7.4 and 10; and ADR-019 section 16's safe error consequences.
ADR-019 authentication-owned flows/codes remain T-AUTH-01 work: this catalogue
does not invent their taxonomy states or apply 30-day financial response replay
to token rotation. Money codecs still admit INR/JPY/KWD at exponents 2/0/3;
that is not admission of JPY/KWD to the MVP ledger.

The identifier namespaces, code names, two confidence wire names, source-pair
window values, row/column limits and 202 decision status introduced here are
**new provider source chosen by these owning tickets**, not previously accepted
contract bytes or runtime-verified production policy.

## Error provider

The existing `ProblemDetail` component is unchanged for scaffold compatibility.
New service responses reference **`ServiceProblemDetail`**, its additive strict
subtype: `type`, `title`, `status`, `detail`, typed `code` and `correlation_id` are
required, and every object is closed. Code-specific constants bind diagnostic
text to the machine catalogue; an unconstrained string is not a safety guarantee.
An operation inlining a problem or referencing only the permissive base fails lint.

| Code | HTTP status | Accepted condition / state | Retry and key treatment |
| --- | --- | --- | --- |
| `dependency_unavailable` | 503 | `dependency_unavailable` / `error` | Same persisted key/bytes; honour delay and P14D. |
| `request_failed` | 503 | `request_failed` / `error` | Same request; unknown financial outcome is never a second effect. |
| `idempotency_in_progress` | 409 | `request_failed` / `error` | Required delay, same key/bytes; never concurrent second execution. |
| `idempotency_key_invalid` | 400 | `request_failed` / `error` | No automatic retry; repair/restore a valid persisted random key before an explicit corrected send. |
| `request_malformed` | 400 | `request_failed` / `error` | No automatic retry; edited bytes are a new intended effect. |
| `idempotency_payload_mismatch` | 422 | `request_failed` / `error` | Reconcile; never mint a key to escape a mismatch for the same bytes. |
| `validation_rejected` | 422; 400 for `duplicate_override` | `validation_rejected` / `error` | Body edit gets a new key; header-only override correction retains the same key/bytes. |
| `import_mapping_required` | 422 | `validation_rejected` / `error` | Correct mapping makes a new immutable preview/intended effect. |
| `import_preview_unavailable` | 409 | `request_failed` / `error` | Reconcile before an explicit new preview; no automatic re-import. |
| `entitlement_denied` | 403 | `entitlement_denied` / `permission_denied` | No automatic retry; server explicitly states `upgrade_available`. |
| `grant_not_active` | 403 | `grant_not_active` / `permission_denied` | Absent/foreign/revoked/expired access receives the same denial. |
| `role_capability_denied` | 403 | `role_capability_denied` / `permission_denied` | No probing or automatic retry. |
| `quota_exhausted` | 429 | `quota_exhausted` / `quota_exceeded` | Conditional retry only after a stated reset, same key/bytes and P14D. |
| `rate_limited` | 429 | `rate_limited` / `quota_exceeded` | Required delay and allowance/window, same request. Not ADR-019's accepted-and-dropped recovery limiters. |

Every error code binds to **one** accepted state. A supplementary region's `error`
can compose to a `degraded` surface under the taxonomy, but this is not a second
code mapping. Offline is a platform observation, not an invented server code.
Missing retry/key/state bindings or an unclassified enum addition fail lint/build.

**AI refusal is successful content**, as the accepted taxonomy explicitly says.
`AiRefusal.code = ai_refusal` has its own typed enum and fixed safe message, never
a `ProblemCode` or an `error`/`degraded` mapping. Code alone distinguishes it from
provider outage and quota exhaustion. Advice refusal is not a retry trigger.

### Safe diagnostics and correlation

Never echo an amount, balance, offending input, full account identifier, denied
resource, raw provider text, internal ID or stack. Protocol diagnostics are not
UI copy and are not rendered/localised by these schemas. `ProblemField` contains
registered tokens, not arbitrary JSON pointers; unknown input names map to `request`.
`ValidationReason` contains the accepted money/category/ledger reasons and safe
mapping reasons. Row errors use that same provider, not another code vocabulary.

Accepted ADR-016 section 3.2 additionally binds `allocation_sum_mismatch` to a
required field and typed `direction`, exactly `shortfall` or `excess`, never an
amount or a computed share. The shared problem and each nested `ValidationIssue`
require that direction only for this reason and reject it for unrelated reasons.
All three ordinary construction/read/write/nested transport seams enforce it;
this is a missing accepted provider representation, not a future endpoint or new
allocation algorithm.

`correlation_id` is `cor_` plus an independently random public UUIDv4; it is not an
internal record ID, idempotency key or raw-derived digest. The generated catalogue
offers the platform's CSPRNG correlation factory. The service must attach this
same public value to its log event, and support reports carry it outside UI copy.
Optional `instance` is a bounded public occurrence URN, never a resource URL.

Allowances state an integer `limit`, typed unit/window and an optional server UTC
reset. No usage, prices or hidden-resource counts travel there. Zero and lifetime
allowances have no reset; an absent reset permits no automatic quota retry. Own-key
token usage is not a quota exhaustion. Services do not compute reset boundaries
on a client.

All 19 new closed object providers select `x-pennilogic-strict-provider`, including
the successful refusal and the nested allowance, validation, mapping, row and link
types. This is generator wiring, not a new wire policy. Ordinary generated conversion
and serialization enforce closure and primitive kinds; callers do not have to opt
into a separate helper. Legacy `ProblemDetail`, ordinary additive DTOs and the shared
Kotlin `ignoreUnknownKeys` configuration keep their accepted behaviour.

Python provider models inherit a closed, frozen Pydantic seam with no additional-
property collector. Constructors and ordinary `from_dict`/`model_validate` paths
reject extras before conversion; ordinary model dumps and the generated client
serializer revalidate outbound state, including nested models. TypeScript provider
converters guard the original wire object and native model before projecting fields,
so an unknown member is rejected rather than dropped. Declared absent optional
members still serialize normally: an optional referenced property is omitted before
calling its required child writer, while a required reference or explicit null still
fails. Every marked array checks its own indices and item kinds before projection;
sparse slots and undefined/null items never become emitted JSON nulls. Recursive
item metadata covers nested model and primitive arrays, including unique-item sets.
Python's ordinary and generic provider payload writers reuse the original
`Money.to_wire` JSON seam, just as they reuse the time and enum seams. The Money
wrapper, currency registry, exponent/range rules and float refusal are unchanged.

Kotlin provider types register their own serializer with `KeepGeneratedSerializer`
on the pinned compiler/runtime. The strict wrapper checks the original JSON tree
against the retained generated descriptor before any tree decoding: every integer
and boolean leaf is checked by kind, not by six field names or leniency flags.
It also checks closed members, enum spellings, patterns and bounds, and validates
construction and serialization. `ServiceProblemDetail` invokes its existing wire
guard through the registered serializer on ordinary, nested and actual generated
`ApiClient` converter paths. Refusal failures use static diagnostics rather than
stock enum diagnostics that can echo a rejected message.

These seams are not a replacement for full producer JSON Schema validation or
server authorisation/atomicity. Python diagnostic strings hide inputs; structured
diagnostic logging must still use `errors(include_input=False)` /
`json(include_input=False)`. A generated `instanceOf` helper is not conformance proof.

## Import, dedup and override provider

### Public references and decisions

`ImportPreviewRef` is `prv_` plus a server-issued public CSPRNG UUIDv4, immutable
and owner-scoped. Mapping/row/decision edits make a new reference. There is no
client owner claim, raw upload, row text or account number in this group.
Authentication and ownership checks happen before lookup; unknown, foreign and
expired uncommitted previews receive the same safe unavailable problem.

**`DedupRecordId` is the normative matched surviving-record identifier**, `rec_`
plus an independent stable public UUIDv4 alias of an owner's financial record.
`DedupOutcome.matched_record_id` and `DuplicateOverride.schema` reference exactly
that one component. It is not a database primary key, account number, source
event ID, idempotency key, device raw digest or server dedupe fingerprint.

`duplicate_suspected` is content awaiting a decision: the shared response is **202**,
distinct from a committed resource's 200/201/204, with no effect, created record
or queue acknowledgement. Same fact acknowledges as duplicate; different fact
retains the same persisted key/bytes and names the current match in
`Duplicate-Override`. Unknown/foreign overrides are identical **400**
`validation_rejected`, field `duplicate_override`, reason `not_available`;
no record ID is echoed. Every request re-screens ownership/current matches.
A changed/additional match needs another decision; no match executes as new
with no override. Endpoint screen field sets/windows and queue implementation
remain their owning consumers' work.

Ingestion matches name the basis and source precedence without compared values.
Authority order is `AA > IMPORT > SMS > NOTIFICATION > MANUAL_GUESS`; user-confirmed
values always survive. Published windows are symmetric: `P3D` for AA/import pairs,
`PT5M` for SMS/notification pairs and `P1D` for manual-guess pairs without AA/import.
These are new T-CON-10 source policy, not executed match logic. A structured-key
collision alone cannot create a **system** link.

Links carry public `dln_` IDs, the suppressed owner's public record ID,
`suppressed_by_link`, `can_unmerge = true` and who decided. Neither record is
deleted. Already-posted suppression uses a reversing fact, not a ledger edit.
An unmerge is a new fact with `restored`, `can_unmerge = false` and UTC `reversed_at`.
Enrichment exposes field/source/public-record provenance only, never the value;
ledger fields are excluded and category assignment precedence remains ADR-016's.

### Preview, mapping, commit and counts

Preview and commit are separate shapes/operations. A commit requires only its
group version and preview reference plus the existing `IdempotencyKey` header.
Both operations require that header. Repeating a preview request with its same
key/bytes returns the original snapshot. Repeating a commit of the same owner's
preview, even under a new request key, returns its **first immutable receipt**:
same IDs, rows, counts, completion, public correlation and status, no new fact/link.
`Import-Commit-Replayed` is a header, not a changed body member. Ordinary request
replay additionally uses `Idempotent-Replayed` when served from the key store.

Consumption, effects and receipt must commit atomically, and concurrent commits
serialize on principal/preview. A committed receipt is checked before uncommitted
expiry and retained at least P30D from first commit. Same key with another preview
payload is the existing mismatch rule. These are service obligations; pure receipt
comparisons do **not** prove database atomicity, authorisation or no-duplicate execution.

Column mapping carries only one-based ordinals, typed roles, a public preset
reference, detected bindings, user overrides and the exact sorted unmapped
complement. Overrides replace that column; effective roles are unique. Commit-ready
mapping has amount/currency and occurred-at or date-only value-date. Category
values remain API-side resolution under ADR-016, with explicit confirmation before
creating an unmapped user category. No preset or parsing engine is implemented.

`high` meets the owning categorisation/source-pair policy threshold; `low` is below
it or unestablished and needs clarification. These two shared wire names are chosen
here from the accepted applied-versus-clarification policy; no invented middle
threshold or universal model score is published. They qualify suggestions, not
immutable category assignments.

Preview actions are `create`, `skip`, `reject`, `review`. Reasons are content
dispositions; rejection includes a safe T-CON-12 row problem at the same source
row/valid column. Unresolved review blocks commit. A previewed skip/reject never
becomes a new fact; a changed reviewed match needs a fresh preview/decision.
Revalidation may create, visibly link/skip, or reject a planned create.

Rows are unique/ascending, at most **10,000**, with at most **256** mapped columns.
Preview counts are the exact row/action counts; commit counts are the exact first
receipt's created/skipped/rejected counts. Server response and metrics must derive
from the same result collection. Replay increments no imported-row metric.
Metrics/logs use group version, public correlation and counts/codes, not row
content, mapping values, record IDs or money.

## Three-language conformance and rollout

The existing generator publishes typed policy tables, enum seams and the source
JSON files with SHA-256 bindings in each `contracts-manifest.json`. Nothing in
`build/generated` is hand-edited or committed. Python/TypeScript named enums reject
unknown/coerced values rather than plain-string comparisons; Kotlin uses typed
generated enum serializers.

Use `pennilogic_contracts.import_contract` (Python), `src/importContract`
(TypeScript) or `com.pennilogic.contracts.imports.ImportContract` (Kotlin).
Their preview/request/result/dedup entry points enforce closed wire boundaries;
pure verification checks counts, mapping, version/reference/decision binding,
source precedence/windows, visible links and immutable replay receipts. They
contain no backend matching, parser, authorisation, retry or ledger implementation.
Synthetic fixtures are shared unchanged by all three smoke consumers. The
`provider-transport.v1.json` inventory covers every closed provider with ordinary
conversion/read/write tests, all integer/boolean leaves, missing/unknown members,
refusal correlation/constant failures and private-diagnostic checks. Kotlin tests
use the actual generated client with `MockEngine`; Python and TypeScript use the
generated transport's normal model conversion and serialization. No real endpoint
or network/provider call occurs. The tests also distinguish schema constraints
from semantic-only checks. Source failures on an earlier head and native CI success
are retained history, not approval of a correction head.
Scratch-generation regression tests additionally compile closed synthetic DTOs
composing required Money and primitive references, optional strict problems and
nested import/refusal content. Ordinary/native/nested/generic reads and writes,
all three actual generated transports, optional-property omission and dense array
requirements are exercised. Those test-only definitions are not new product
components or endpoints and do not claim provider or consumer adoption.

After independent current-head Core/QA and affected contract/money/privacy/security
review, native CI, dependency/integration conditions and owner approval, the
existing publication pipeline can publish an immutable tag. Every service/API/client
consumer then pins that release and references these components. No current
service, API #10/#18 consumer or rendered review screen is claimed to have adopted it.
Any new code/group change updates its catalogue, enum bindings, examples, three
clients/golden hashes and appropriate version in one owning change.

Before publication/consumer pins, withdraw a defective **local proposed source**
unit without a release. After a tag exists, never delete/unpublish/rewrite its
bytes: re-pin a prior immutable tag or issue a corrective release, then use
expand-migrate-contract. The permissive scaffold shape is removed only after
real migration evidence, not by narrowing it in this provider slice.
