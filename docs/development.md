# Developer guide — PenniLogic contracts

Hand-maintained companion to the generated `AGENTS.md`, `README.md` and `CONTRIBUTING.md`
(those come from `PenniLogic/infra/governance`; edit the canonical source, not the copies).
This guide covers the specification pipeline added by PenniLogic/contracts#2 (T-SCA-CON-01).

## What this repository is

`spec/openapi.yaml` is the single source of truth for the PenniLogic API. Every change to it goes
through one pipeline: lint (Spectral + the committed ADR-015 rules), breaking-change detection
against the last published tag (oasdiff + a component guard), deterministic generation of the
Kotlin, TypeScript and Python clients (openapi-generator, one pinned version), and three smoke
consumers that compile, type-check and run the money/instant conformance vectors. Publication is a
versioned git tag plus a GitHub Release built by the owner outside CI (see
[publication.md](publication.md)). Business endpoints are **not** part of this scaffold; the
document carries the shared components only (`Money`, `CurrencyCode`, `Instant`, `LocalDate`,
`TimeZone`, `ProblemDetail`, the `IdempotencyKey` parameter and the `IdempotentReplayed` header).

The local `0.2.0` source adds proposed T-CON-12 error and T-CON-10 import/dedup providers
ahead of endpoint consumers. They are not an accepted release or current service adoption.
Their source authority, safe diagnostics, group version, replay/override rules, conformance
entry points and remaining rollout obligations are in [provider-contracts.md](provider-contracts.md).
The accepted scaffold components themselves are unchanged.

## Layout

| Path | Owner / purpose |
| --- | --- |
| `spec/openapi.yaml` | The OpenAPI 3.1 document. `info.version` is the published version (`vX.Y.Z` tag). |
| `spec/currency-registry.v1.json` | ISO 4217 codes and minor-unit exponents accepted by the codecs (ADR-015 §1.4); rendered into every client. |
| `spec/error-catalogue.v1.json`, `spec/client-state-bindings.v1.json`, `spec/import-group.v1.json` | Proposed provider diagnostics/retry/key policy, accepted taxonomy 1.1.0 identifier projection and new T-CON-10 import/dedup group policy. Copied/hash-bound into each client and prepared as standalone release assets. |
| `spec/fixtures/*.json` | Money and instant wire vectors (hand-written) and `money-roundtrip-generated.v1.json` (10 000 seeded values + boundaries, regenerated deterministically by `scripts/generate_money_fixtures.py`), consumed unchanged by all three smoke consumers (ADR-015 §7). Synthetic values only. |
| `spec/.spectral.yaml`, `spec/spectral-functions/` | Committed lint ruleset and its custom functions. |
| `spec/breaking-change-acknowledgement.json` | Present only while a deliberate breaking change is being published (see publication.md). |
| `toolchain/versions.json` | Exact versions and SHA-256 digests of every downloaded tool. |
| `generator/*.json`, `generator/openapi-generator-ignore`, `generator/templates/` | Generator configuration per target, the output ignore list and the drift-guarded template overrides (Python model; Kotlin `ApiClient` and `build.gradle`). |
| `generator/golden.json` | SHA-256 of each generated tree and of every file in it; CI fails when generation drifts. |
| `runtime/<language>/` | Hand-written seams copied into every generated client: money/time, strict service problems, pure import receipt/mapping verification and the Kotlin `PennilogicJson` converter configuration. |
| `smoke/<language>/` | Smoke consumers: they compile/type-check the generated client and run the conformance tests. |
| `scripts/*.py` | The pipeline (standard-library Python; see below). `scripts/setup.py` and `scripts/check_repository.py` are generated. |
| `scripts/tests/` | Tests of the pipeline itself (planted defects, determinism, release dry run). |
| `build/`, `.toolchain/`, `node_modules/` | Outputs and caches; never committed. |

## Toolchain

| Tool | Version | Pinned by |
| --- | --- | --- |
| Python | 3.14 | organization baseline (`actions/setup-python`) |
| Node.js / npm | 24.14.0 / bundled 11.x | `.nvmrc`, `package.json` engines, `.npmrc` `engine-strict` |
| JDK | 21 | `actions/setup-java` (Temurin) in CI; any JDK 21 locally |
| `@stoplight/spectral-cli` | 6.16.3 | `package-lock.json` (sha512 integrity) |
| `typescript` | 7.0.2 | `package-lock.json` |
| `openapi-generator-cli` | 7.25.0 | `toolchain/versions.json` (jar SHA-256, verified before every use) |
| `oasdiff` | 1.32.1 | `toolchain/versions.json` (tarball SHA-256 per platform) |
| Gradle | 9.8.0 | wrapper `distributionSha256Sum`; wrapper jar SHA-256 in `toolchain/versions.json` |
| Kotlin / kotlinx-serialization / Ktor | 2.4.20 / 1.11.0 / 3.6.0 | `smoke/kotlin/build.gradle.kts`; every artifact SHA-256 in `smoke/kotlin/gradle/verification-metadata.xml` |
| pydantic, mypy and friends | see `smoke/python/requirements.txt` | `pip --require-hashes` |

Why one generator: openapi-generator covers all three targets with type/import mappings, so the
hand-written `Money`/`Instant` seams replace the raw wire shapes in every language and a generator
bump is one reviewed change. `oasdiff` is a single static binary with stable change identifiers.
Spectral understands OpenAPI 3.1 and runs custom functions for the ADR-015 rules.

Updating a pin: change the version and digest in the pinning file, run the full command list, run
`python scripts/generate_clients.py --update-golden` if generated output changed, and record why in
the pull request. `scripts/toolchain.py install` refuses any download whose SHA-256 differs.

## Commands

Run from the repository root after `npm ci --no-audit --no-fund` and
`python scripts/toolchain.py install` (both also run in CI):

| Command | What it proves | Fails when |
| --- | --- | --- |
| `python scripts/lint_spec.py` | The document satisfies the committed ruleset. | Any Spectral finding of severity warn or higher (all rules are errors here); prints rule code, path, line and message. |
| `python scripts/check_breaking_changes.py` | No breaking change against the highest `v*` tag, or every one is acknowledged. | An operation-level break (oasdiff) or a component-level break (removed/narrowed component, property, required member, enum value) without an acknowledgement entry. |
| `python scripts/generate_clients.py --verify` | Generation is deterministic and matches `generator/golden.json`. | Two generations differ, or the tree hash differs from the golden (lists the differing files). |
| `python scripts/smoke.py python` | The Python client imports; `mypy --strict` passes over the smoke consumer, the seams and the generated models; conformance tests pass. | Type errors; a float accepted as money; a fixture vector mis-classified. |
| `python scripts/smoke.py typescript` | `tsc --strict` over the generated client and consumer; `node --test` conformance tests. | Same, for TypeScript (`bigint` minor units, `number` refused). |
| `python scripts/smoke.py kotlin` | Wrapper jar digest, then Gradle compiles the generated client and runs the JUnit tests. | Same, for Kotlin (`Long` minor units, overflow-checked arithmetic). |
| `python -m unittest discover -s scripts/tests -p "test_*.py"` | The pipeline itself: planted malformed specifications fail lint; a planted removal fails the breaking check unless acknowledged; determinism, golden consistency, manifest, template drift, seam wiring on a generated Money-bearing model, release preconditions and dry run, fixture consistency and regeneration, refused and tampered downloads. | Any regression in the scripts. |

Local Windows note: run the same commands in PowerShell; `smoke.py kotlin` uses `gradlew.bat`.
Cold runs download Gradle and the Kotlin toolchain (several minutes).

## Lint

`spec/.spectral.yaml` extends `spectral:oas` (recommended set, every rule raised to error) and adds:

| Rule | Enforces |
| --- | --- |
| `pl-info-version-semver` | `info.version` is `MAJOR.MINOR.PATCH`. |
| `pl-money-component-binding`, `pl-instant-component-binding`, `pl-timezone-component-binding`, `pl-idempotency-key-parameter-binding`, `pl-idempotent-replayed-header-binding` | The components carry exactly the ADR-015 binding shape (type, pattern, lengths, required members, parameter location). `description`, `title`, `summary`, `example(s)` and `externalDocs` may be added to a schema object; the keys of `properties` are member names and are compared exactly (`Money.properties.title` is an additional member and fails). Any other change needs a superseding ADR. |
| `pl-money-bearing-property-references-money` | A property named like money (`amount`, `balance`, `price`, `fee`, `total`, `cost`, `minor` as in `minor_units`, `amount_minor_units`, `balanceMinorUnits`, …) is `$ref: '#/components/schemas/Money'`. A non-money property with such a name declares `x-not-money: <reason>`; names containing `count`, `rate`, `percent`, `code`, `name`, … are not money (`quantity_units`, `unit_count` have no money word and pass). |
| `pl-no-json-number` | No `type: number` and no `format: float`/`double` anywhere (ADR-015 §1.6; constitution). |
| `pl-mutating-operation-idempotency` | Every POST/PUT/PATCH/DELETE references the `IdempotencyKey` parameter or declares `x-idempotency` ∈ {`not-applicable`, `per-item`, `provider-event`, `auth`}; a Money-bearing operation is never exempt (ADR-015 §4.1). Money is found through any chain of local `$ref`s (request body → schema → items/allOf/properties → `Money`), cycle-safe. |
| `pl-safe-method-no-idempotency-key` | The key is not declared on GET/HEAD/OPTIONS/TRACE, directly or via path-level parameters. |
| `pl-operation-security` | Every operation has a non-empty security requirement, at operation level or root level (ADR-019 §16; the root DPoP scheme is contracts#1's). |
| `pl-money-example-registry-scale` | Every Money example uses a registry currency with exactly the registry exponent of fraction digits. |
| `pl-problem-detail-no-money` | `ProblemDetail` never carries a monetary value. |

`oas3-unused-component` is off: the shared components are published before any operation
references them. The money-example rule reads `currency-registry.v1.json` from the linted
document's directory.

## Generation and the seams

`scripts/generate_clients.py` runs openapi-generator with `generator/<language>.json`, seeds the
committed ignore list into the output, copies `runtime/<language>/` in, renders the currency
registry as source, and writes `contracts-manifest.json` (specification version and SHA-256,
generator name/version/jar SHA-256, config/runtime/template hashes, per-file and tree SHA-256).
Outputs live in `build/generated/<language>/`.

| Language | Money | Instant / dates | Client wiring |
| --- | --- | --- | --- |
| Kotlin (`jvm-ktor`, kotlinx.serialization) | `com.pennilogic.contracts.money.Money` (`Long` minor units + currency, `@Serializable(with = MoneySerializer)`), mapped for every `$ref: Money`; the raw `Money.kt` model is not generated and the internal `MoneyWire` shape is not serializable — the only serializable money type is `Money`. | Generated models type instants as `@Contextual java.time.OffsetDateTime` and dates as `@Contextual java.time.LocalDate` (the Kotlin generator's `dateLibrary` overrides CLI type mappings); `InstantSerializer`/`LocalDateSerializer` enforce the grammar, calendar validity and offset Z; `InstantCodec` converts to and from `java.time.Instant`. | The generated `ApiClient` installs `json(PennilogicJson.json)` as its `ContentNegotiation` converter (template override) and the generated `build.gradle` carries `ktor-serialization-kotlinx-json`; `PennilogicJson.json` registers `PennilogicSerializers.module` and `ignoreUnknownKeys` (additive evolution). A consumer that builds its own `Json` must use `PennilogicJson.json` or its module; `smoke/kotlin/.../GeneratedClientWiringTest.kt` round-trips `Money` + instant + date through the generated client with a `MockEngine`. |
| TypeScript (`typescript-fetch`) | `src/models/Money.ts` (`bigint` minor units, `toJSON()` renders the wire object); the generated models call `MoneyFromJSON`/`MoneyToJSON`. | `date-time` → `src/models/Instant.ts`, `date` → `src/models/LocalDate.ts` (`*FromJSON`/`*ToJSON`, strict grammar and calendar). `models/index.ts` re-exports `Money`; import `Instant`/`LocalDate` from their files. | The generated runtime's `fetch` path serializes through `*ToJSON`; nothing to register. |
| Python (`python`, pydantic v2) | `pennilogic_contracts/models/money.py` (`Decimal` at registry scale + currency; `from_wire`/`to_wire`, Pydantic core schema; float construction raises `TypeError`). `Money.amount` is a display/fixture accessor: outside the seam the default decimal context applies, so keep money inside `Money` and use `minor_units`/`currency` or `to_wire()`. | `DateTime` → `pennilogic_contracts/models/instant.py`, `date` → `pennilogic_contracts/models/local_date.py`; the import lines are added by the template override below. | Every generated model has `model_config` with `strict=True` (ADR-015 §2.2) and `hide_input_in_errors=True`, so `str(ValidationError)` and `.json()` never echo an offending amount; a consumer that logs errors structurally must call `errors(include_input=False)` / `json(include_input=False)` (T-AI-01 inherits this rule). |

`Money.parse(amount, currency)` checks in the same order in all three languages — grammar, currency,
scale, range — with the 19-digit bound applied before any integer conversion; the `parse_invalid`
vectors of `money-wire-fixtures.v1.json` prove it per language. The wire seams add `shape` and
`number_not_string` in front (ADR-015 §1.5).

### Generator templates

Nine templates/partials are overridden; stock portions remain bound to the pinned generator:

- `generator/templates/python/model_generic.mustache`: the `Instant` and `LocalDate` imports (a
  type-mapped, non-model type gets no generated import), `Any -> Any` annotations on the generated
  validators so the models pass `mypy --strict`, and `strict=True, hide_input_in_errors=True` in
  `model_config`.
- `generator/templates/kotlin/libraries/jvm-ktor/infrastructure/ApiClient.kt.mustache`: a
  `{{#kotlinx_serialization}}` block that imports and installs `json(PennilogicJson.json)` (the stock
  template wires only gson and jackson).
- `generator/templates/kotlin/build.gradle.mustache`: the `ktor-serialization-kotlinx-json`
  dependency for `jvm-ktor` + `kotlinx_serialization`.
- `generator/templates/python/model_enum.mustache`: exact declared wire-type enum parsing under
  strict Pydantic models, enum-value serialization, and static no-input-echo rejection.
- `generator/templates/typescript/modelEnum.mustache`: typed guards and enum converters that
  reject unknown/coerced wire values instead of casting them into a known enum.
- `generator/templates/kotlin/data_class.mustache`: provider-only registered strict serializers,
  retained generated descriptors, mandatory source-schema binding, constructor checks and field constraints. Legacy model emission
  is unchanged; the global JSON configuration is not made strict.
- `generator/templates/python/model_provider.mustache`: provider-only Pydantic fields and safe
  regex validators; `model_generic.mustache` selects this partial for marked closed models only.
  Its schema-name binding selects the compiled recursive declarations on original ingress and
  normal nested/outbound serialization, including reference-array patterns absent from field annotations.
  Its payload serializer reuses the unchanged `Money.to_wire` Pydantic JSON seam, alongside the
  time and enum seams, rather than letting an untyped payload serializer lose the Money codec.
- `generator/templates/typescript/modelGeneric.mustache`: provider-only original-wire/native-model
  guards and original-wire/emitted-wire declaration checks around the generator conversion bodies.
  This rejects private Money member names before calling the unchanged dependency codec.
  Optional referenced properties are omitted
  before invoking a required child writer; required references and explicit null remain guarded.
- `generator/templates/typescript/providerField.mustache`: shared recursive field/item metadata
  for marked models, including model-valued array items. Array indices must be present and their
  values non-null/non-undefined before conversion; property omission is not array-item omission.

`scripts/tests/test_generate.py::TemplateOverrideDriftTest` extracts each stock template from the
pinned jar and asserts the override equals stock plus exactly those edits (and that no other
template is overridden), so a generator bump that changes a template fails loudly; re-apply the edits
on the new stock template and update the test.

The TypeScript target also uses its committed template directory. Manual source edits use LF
as required by `.gitattributes`; in particular templates must not acquire CRLF fragments that
would change generated bytes between Windows and Linux. The runtime copy already normalizes LF.

### Provider conformance

`pl-error-provider` binds the code enumeration to the machine catalogue, explicit retry/key
classification, one accepted state and exact safe diagnostic constants. It requires service
error responses to reference the strict subtype, while preserving the permissive scaffold.
`pl-import-provider` binds version, public identifiers/override, closed confidence, row/column
limits, source-pair windows and component references; it rejects inline/local duplicates and
raw-content/open-map escape hatches. Tagged import consumers declare separate preview/commit
roles and reference the shared shapes and key.

`scripts/tests/test_error_provider.py` and `test_import_provider.py` validate real OpenAPI 3.1
schemas with the already integrity-pinned Spectral AJV/parser dependencies, plant unsafe
catalogue/schema/inline-copy defects and verify exact thresholds. No dependency or framework
was added. The new `smoke/<language>` tests compile/import the actual generated components,
check enum typing, decode shared synthetic fixtures through the provider conformance seams,
and reject unsafe errors, invalid mappings/counts/decisions, changed replay receipts and
unknown confidence. JSON Schema checks and semantic-only checks are reported separately.
Neither proves a running service's ownership, atomic commit, metrics or no-duplicate effects.
The provider-transport inventory additionally binds all 19 new closed model paths and tests
ordinary conversion, native construction/serialization, nested models and actual generated
transport. Missing strict wiring fails lint. Runtime-only generator metadata is ignored by the
inline-equivalence fingerprint, so removing it does not let an equivalent local schema pass.
The array controls enumerate all seven declared provider array fields, including optional nested
validation issues. Actual TypeScript `BaseAPI` mock requests prove that undefined/sparse model
arrays fail before JSON emission, with normal optional-property omission retained.
`scripts/tests/test_provider_composition.py` extends the existing scratch-generation tests with
closed synthetic DTOs that reference Money, every accepted primitive seam, optional strict
problems, import preview and successful refusal. It runs strict mypy/TypeScript compilation,
ordinary/native/nested/generic serialization and actual generated transports in all three targets.
It uses the existing pinned venv/compiler/Gradle smoke dependencies; no product DTO, endpoint,
framework, dependency or pipeline command is added.

`scripts/provider_constraints.cjs` derives internal declarations directly from the owning source,
using the existing Spectral YAML parser and shared local-reference resolver. The actual generator
ships source-bound tables and `provider_constraints_sha256` in all three manifests. These are
internal runtime metadata, not a new wire group, taxonomy, product policy or release asset.
Kotlin and Python validate recursive scalar/item constraints before conversion and on ordinary
construction, generic/nested serialization and actual client writes. TypeScript preflights the
original body and validates projected output before transport, retaining dense-array prechecks,
optional-property omission and the registered static enum diagnostic.

The supported subset is explicit: objects/arrays/strings/integers/booleans; local acyclic schema
references; string enums and scalar constants; anchored portable ASCII regular expressions;
code-point string lengths; inclusive/exclusive safe-integer bounds; nested min/max/unique item
rules (including zero upper bounds); required/closed members and allOf/anyOf/oneOf/not/conditional
assertions. The accepted date/date-time codecs and integer formats remain registered; URI
references receive ASCII/escape/parser checks, not an arbitrary format or IRI certification.
Enum/constant predicates may constrain integer/boolean fields without introducing a new scalar
enum serializer. Referencing an unmarked legacy DTO does not make that DTO globally strict.

Generation fails explicitly for unknown keywords, untyped value/items, floating-number or nullable
union schemas, tuple/contains/map-schema features, external/unresolved/cyclic references,
nonportable patterns, unsupported formats/defaults/scalar-enum representations, malformed
metadata or contradictory/unrepresentable bounds. No unsupported declaration is silently treated
as a supported guard. Full producer JSON Schema validation and server obligations remain separate.
`test_provider_constraints.py` exercises each refusal on all three actual generation commands;
the generated composition matrix checks same-length public-ID failures, exact nested numeric/
cardinality boundaries, aliases, Unicode length, zero bounds and private member names.

The regular-expression subset is a checked grammar, not host-JavaScript parse acceptance:
whole-value outer anchors, printable ASCII literals and portable literal escapes, ordinary
nonempty groups with grouped alternatives, nonempty flat character classes/ranges (optional
negation), dot, and greedy `?`/`*`/`+`/bounded repetitions. Empty whole-value `^$` is supported.
Malformed/empty/nested/set-operation classes, ambiguous shared-endpoint ranges, ungrouped alternatives,
internal anchors, empty group alternatives, shorthand/property/backreference escapes,
lookarounds/inline flags, lazy/possessive modifiers and invalid/unrepresentable repetitions
are refused before any target output is removed or emitted. This intentionally conservative
subset does not promise universal regular-expression syntax.
Groups are limited to 64 levels so accepted syntax does not depend on a host parser's
recursion limit; repetition counts use canonical nonnegative decimals within signed 32-bit bounds.
That count range is a syntax limit, not a guarantee that the pinned generator can construct
every matching example. The Python generator always synthesizes examples with its bundled
RgxGen (seed 18, unbounded-repeat default 100); source-only/no-docs flags do not disable it.
The compiler therefore computes bounded constructive work across sequences, alternatives,
groups and repetitions, without expanding huge strings. Beyond 4,096 work units it supplies
a generation-only, at-most-4,096-character nonblank witness, independently validated against
the original schema with the existing AJV. An authored valid blank example may need that
nonblank witness because the pinned generator ignores blank annotations. No original
constraint, runtime declaration or source file is changed, and the generation manifest binds
the effective annotated input and budget. Supplied invalid examples are never substituted
into success.
Witness eligibility follows the actual generator's Java-whitespace and literal-`"null"`
rules, not JavaScript trimming. A valid bounded first enum value already bypasses RgxGen
and needs no new annotation, including an empty enum value.

If no validated bounded canonical/supplied witness exists, or a nullable child would require
more than 4,096 mandatory repetitions, preflight refuses unsupported example construction
before any target output mutation. Exact huge minima and aggregate over-budget concatenated/
nested minima therefore fail explicitly, while `0..2147483647`, the neighbouring upper bound
and alternatives with a short valid witness remain supported. This finite construction limit
is not a wire-length, heap allowance, universal-schema or runtime-regex performance claim.

Every marked string bound counts Unicode code points, including direct generated Kotlin
fields and native constructor/copy paths. Both TypeScript recursive and field-metadata guards
use Unicode-mode matching. Python and Kotlin translate only unescaped wildcard dots outside
classes to the ECMAScript exclusion set (LF, CR, LS and PS); NEL remains valid dot data.
Escaped dots and class members remain literal. Matching remains whole-value, never substring
or permissive final-newline matching. The original ASCII Money/time/public-ID source patterns
and primitive codecs are unchanged. Generated ordinary/native/nested/actual client tests cover
astral positives, combining-sequence negatives, class/escape/group/repetition boundaries and
the same newline controls in all three targets.

### Golden hashes

`generator/golden.json` records the tree SHA-256 and every file's SHA-256 per target. The per-file
map is not diagnostic only: `--verify` and `test_generate.py` re-fold it with the same
`"{sha}  {path}\n"` fold as `tree_hash` and refuse a record whose entries do not reproduce the
recorded tree digest, so the committed file cannot be edited inconsistently. After an intended change
to the specification, the generator configuration, the runtime seams, a template override or the
generator version, run `python scripts/generate_clients.py --update-golden` and commit the result;
the pull request explains why the output changed. `--verify` lists the differing files when a
mismatch is unintended.

Every generation builds in a fresh owned sibling staging directory, including runtime,
companions and the complete manifest. One requested target set is promoted only after every
target succeeds; downstream generator/companion failures leave previous caller output intact
and discard staging. Verification double-generates and compares before promoting any client;
golden updates are committed together with that verified client set, never on a failed run.
Promotion errors restore prior directories/files. If filesystem restoration itself fails or
receives a catchable `KeyboardInterrupt`, including during rollback cleanup,
the command fails explicitly and retains backups plus `recovery.json` in the named staging
directory; it does not delete recoverable prior bytes or advertise partial success.
Already-restored destinations and still-unresolved backups remain byte-verifiable against that
mapping. A promotion interrupt with a successful rollback is re-raised after prior output is
restored; an interrupted rollback follows the recovery-required path before staging cleanup.
These are recoverable process-level filesystem transactions, not a single multi-path atomic
rename, crash durability or protection against external concurrent writers. Consumers must not
read outputs while a generation command runs. Source/root directories and linked output
directories are refused; no persistent Git/global configuration is involved.

## Breaking-change check

`python scripts/check_breaking_changes.py` compares the working-tree document with
`spec/openapi.yaml` at the highest `v*` tag (`git tag --list 'v*'`, semantic order). Two detectors:
oasdiff `breaking` for everything reachable through operations, and a component guard over
oasdiff's structural diff for the shared components themselves (deleted schemas/parameters/headers,
removed properties, type/format/pattern changes, narrowed lengths or ranges, required members added
or removed, enum values removed, `additionalProperties` becoming false, parameter location or
required changes). Before the first tag exists the check reports that no baseline exists and passes;
the first publication defines the baseline. Acknowledging a break and the expand-and-contract
protocol are described in [publication.md](publication.md#breaking-changes).

Dereferenced `allOf` inheritance and response schemas can repeat a base component's optional
property addition. The guard recognises only proven optional-addition diffs there as additive;
removed/required/narrowed/conditional/unknown changes and response metadata changes still fail.
Adding an `allOf` constraint also fails. The composition and partial/stale/blanket acknowledgement
regressions prevent this source-provider inheritance fix from becoming a bypass.

The optional-inheritance proof is fail-closed at every record boundary: modified
records have exactly base/revision/diff, matching well-formed member identities,
no ambiguous duplicate indices, and a non-empty recursively proven optional
property addition. Unknown record/diff keys or malformed metadata never qualify.
Direct malformed-record cases and real pinned-oasdiff paired documents cover both
the positive inheritance seam and retained removal/required/narrowing/response
metadata failures. An absent published baseline is still explicitly not a release
compatibility result.

## Smoke consumers and conformance vectors

The three consumers under `smoke/` are the only consumers in this repository; product repositories
pin a published tag instead. Each one loads `spec/fixtures/money-wire-fixtures.v1.json` and
`spec/fixtures/instant-wire-fixtures.v1.json` and asserts, per language: every valid vector
round-trips byte-identically with the expected minor units / epoch milliseconds; every invalid
vector is rejected with exactly the fixture reason (in the ADR-015 §1.5 order) and never echoes the
value; a float or JSON number is rejected (`TypeError` on construction; `number_not_string` on the
wire); comparison across currencies or against a float never coerces. The Python consumer also
type-checks with `mypy --strict` (the generated transport modules `api_client`, `rest`,
`exceptions`, `configuration` and `api_response` use openapi-generator's own baseline through
per-module overrides in `smoke/python/mypy.ini`; every model and seam is strict).

### CrossLanguageMoneyRoundTripTest

`spec/fixtures/money-roundtrip-generated.v1.json` is the second ADR-015 §7 fixture: 30 boundary rows
(zero, ±1 minor unit, 2^53−1, 2^53+1, ±(2^63−1), ten, ±one major unit, per registry currency) plus
10 000 seeded values whose magnitude class (1–19 digits), sign and currency come from SplitMix64
with the seed recorded in the header. `python scripts/generate_money_fixtures.py` regenerates it
byte-identically and `--check` (run by `scripts/tests/test_fixtures.py`) fails when the committed
file differs. Each language's leg (`smoke/python/tests/test_roundtrip.py`,
`smoke/typescript/test/roundtrip.test.ts`, `smoke/kotlin/.../CrossLanguageMoneyRoundTripTest.kt`)
round-trips every row through `fromWire`/`toWire`, checks the minor units, and hashes its own
emitted `amount|currency|minor_units` lines; the digest must equal the header's
`round_trip_sha256`, so the three outputs are byte-identical to each other and to the input.

## Adding a component or an endpoint (for contract tickets)

1. Edit `spec/openapi.yaml`; reference `Money`, `Instant`, `LocalDate`, `CurrencyCode` and the
   `IdempotencyKey` parameter rather than redefining them. Synthetic examples only.
2. Run the command list. Fix lint findings; if the breaking check reports a change you intend,
   follow the protocol in publication.md instead of weakening the check.
3. `python scripts/generate_clients.py --update-golden` and commit `generator/golden.json`.
4. Bump `info.version` (additive change: MINOR; fix: PATCH) in the same pull request; the owner
   tags the merged commit with `scripts/release.py`.

## Safety notes

No secret is needed by any command; CI runs with `contents: read`. Every download is HTTPS and
SHA-256 verified, and the cached generator jar and oasdiff binary are re-hashed on every run
(`toolchain.py verify` refuses a tampered cache). Fixtures and examples are synthetic. Codec errors
carry a reason and a field name, never the value; generated Pydantic models hide the input in their
diagnostics, and structured error logging uses `errors(include_input=False)`. The generated clients
are outputs, never committed; consumers generate from a tag. `scripts/release.py` tags only the
commit `origin/main` carries.
