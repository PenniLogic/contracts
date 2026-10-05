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
[publication.md](publication.md)). The accepted scaffold provides shared components (`Money`,
`CurrencyCode`, `Instant`, `LocalDate`, `TimeZone`, `ProblemDetail`, the `IdempotencyKey` parameter
and the `IdempotentReplayed` header). The additive [custom-destination source contract](custom-destinations.md)
is not runtime or release acceptance: generated-runtime and shared-refusal integration remain
blocked, and global/custom AI remain OFF.

## Layout

| Path | Owner / purpose |
| --- | --- |
| `spec/openapi.yaml` | The OpenAPI 3.1 document. `info.version` is a source candidate until the owner publishes the matching `vX.Y.Z` tag. |
| `spec/currency-registry.v1.json` | ISO 4217 codes and minor-unit exponents accepted by the codecs (ADR-015 §1.4); rendered into every client. |
| `spec/fixtures/*.json` | Money and instant wire vectors (hand-written) and `money-roundtrip-generated.v1.json` (10 000 seeded values + boundaries, regenerated deterministically by `scripts/generate_money_fixtures.py`), consumed unchanged by all three smoke consumers (ADR-015 §7). Synthetic values only. |
| `spec/.spectral.yaml`, `spec/spectral-functions/` | Committed lint ruleset and its custom functions. |
| `spec/adr022/` | Byte-identical accepted consequence and closed-schema inputs; provenance and immutable digests are documented in custom-destinations.md. |
| `spec/breaking-change-acknowledgement.json` | Present only while a deliberate breaking change is being published (see publication.md). |
| `toolchain/versions.json` | Exact versions and SHA-256 digests of every downloaded tool. |
| `generator/*.json`, `generator/openapi-generator-ignore`, `generator/templates/` | Generator configuration per target, the output ignore list and the drift-guarded template overrides (Python model; Kotlin `ApiClient` and `build.gradle`). |
| `generator/golden.json` | SHA-256 of each generated tree and of every file in it; CI fails when generation drifts. |
| `runtime/<language>/` | Hand-written seams copied into every generated client: `Money`, `Instant`, `LocalDate`, serializers and the Kotlin `PennilogicJson` converter configuration. |
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
| `pl-custom-destination-contract` | Direct accepted ADR-022 byte/schema/provenance validation, four exactly-once enum definitions, closed registration fields and canonical state-denial mapping. |
| `pl-no-inference-address` | Reference-recursive closed inference/tool requests; no address, header, open-map or encoded-argument escape. Only the exact named registration/lifecycle bodies are exempt, never their query/header parameters. |

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

### Kotlin Money value keys

`Money` remains `Comparable<Money>` with same-currency ordering and arithmetic only; equality
across currencies is false. Its hash partitions currencies into separate ranges and retains
16 bits of the minor-unit hash. Three-uppercase-ASCII-letter ISO codes have distinct Java
`String` hashes spanning less than 65536, so their low 16 bits remain distinct. JDK `HashMap`
spreading preserves distinct full hashes: a shared bucket can contain different currencies,
but an equal-hash tree comparison can only see one currency. Same-currency collisions remain
valid and use the existing minor-unit ordering; exact hashes and collision-free storage are
not contracts. `MoneyCollectionTest` covers both reported corpora, equal-key set/map operations,
same-currency hash collisions and mixed-currency bucket collisions. This changes neither the
wire format nor ledger admission: registry acceptance of JPY/KWD does not admit them to an INR ledger.

### Generator templates

Three templates are overridden, each the pinned generator's stock template plus documented edits:

- `generator/templates/python/model_generic.mustache`: the `Instant` and `LocalDate` imports (a
  type-mapped, non-model type gets no generated import), `Any -> Any` annotations on the generated
  validators so the models pass `mypy --strict`, and `strict=True, hide_input_in_errors=True` in
  `model_config`.
- `generator/templates/kotlin/libraries/jvm-ktor/infrastructure/ApiClient.kt.mustache`: a
  `{{#kotlinx_serialization}}` block that imports and installs `json(PennilogicJson.json)` (the stock
  template wires only gson and jackson).
- `generator/templates/kotlin/build.gradle.mustache`: the `ktor-serialization-kotlinx-json`
  dependency for `jvm-ktor` + `kotlinx_serialization`.

`scripts/tests/test_generate.py::TemplateOverrideDriftTest` extracts each stock template from the
pinned jar and asserts the override equals stock plus exactly those edits (and that no other
template is overridden), so a generator bump that changes a template fails loudly; re-apply the edits
on the new stock template and update the test.

### Golden hashes

`generator/golden.json` records the tree SHA-256 and every file's SHA-256 per target. The per-file
map is not diagnostic only: `--verify` and `test_generate.py` re-fold it with the same
`"{sha}  {path}\n"` fold as `tree_hash` and refuse a record whose entries do not reproduce the
recorded tree digest, so the committed file cannot be edited inconsistently. After an intended change
to the specification, the generator configuration, the runtime seams, a template override or the
generator version, run `python scripts/generate_clients.py --update-golden` and commit the result;
the pull request explains why the output changed. `--verify` lists the differing files when a
mismatch is unintended.

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

The namespaced custom-destination tests also use real generated transports and decoders.
Unlike schema-only controls, they currently expose shared generator defects; failed T6 controls
must remain failures, not be skipped or replaced with a separate strict decoder. See
[the source-only boundary and reproduction commands](custom-destinations.md#generated-runtime-gaps).

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
