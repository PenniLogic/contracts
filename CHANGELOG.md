# Contract source changes

## 0.2.0 - local provider preparation, not released

Source corrections after the frozen `fd7ef0a` draft: wire strict ingress/egress
for successful AI refusal, recursively reject quoted integer/boolean primitives
before Kotlin model conversion, and register the service-problem guard on actual
ordinary/nested/generated Kotlin transport serialization. Apply closure guards
to all 19 new object providers without narrowing legacy DTOs or the accepted
global JSON configuration. Add schema and actual three-target transport matrices;
the earlier source FAIL remains immutable and fresh review is still required.

The same remediation also restores accepted ADR-016 allocation-mismatch
`shortfall`/`excess` direction on problems and nested validation issues, with no
monetary diagnostic or arithmetic. Optional-inheritance breaking-change proof now
rejects unknown/ambiguous/malformed change-record metadata while retaining the
actual pinned optional-addition positive and all existing constraint negatives.
The intermediate local `1af164a` commit/evidence remain history, not a published
or reviewed provider pin.

Original owning issues: [#16 (T-CON-12)](https://github.com/PenniLogic/contracts/issues/16)
and [#13 (T-CON-10)](https://github.com/PenniLogic/contracts/issues/13).

- Add strict `ServiceProblemDetail` alongside the unchanged scaffold `ProblemDetail`,
  typed error codes, accepted taxonomy 1.1.0 bindings, safe fixed diagnostics, validation
  tokens, public correlation IDs, allowance/entitlement shapes and successful `AiRefusal`.
- Add import group 1.0.0: immutable preview/commit references, ordinal column mapping,
  row errors referencing the error provider, closed high/low threshold-policy bands,
  counts, matched surviving-record identifiers, source precedence, visible reversible
  duplicate links, enrichment provenance and the shared `DuplicateOverride` binding.
- Generate typed policy tables and strict enum seams with the existing pinned generator
  for Kotlin, TypeScript and Python. Add synthetic schema/client conformance and
  negative tests; preserve the money/time/idempotency components and conformance vectors.
- Prepare source/manifest/asset metadata for the existing immutable-tag publication
  pipeline. No tag, registry package, release, consumer adoption or service behaviour
  is asserted.

Native dependencies, full issue acceptance, separate review, native CI, endpoint
adoption, publication and the [PenniLogic/infra#22](https://github.com/PenniLogic/infra/issues/22)
integration hold remain pending. See [provider-contracts.md](docs/provider-contracts.md).
