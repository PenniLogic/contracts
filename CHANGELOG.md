# Changelog

## Unreleased source candidate 0.2.0

- Add the isolated `CustomDestinations` registration/list/validate/activate/suspend/revoke
  operation group for original [contracts#27](https://github.com/PenniLogic/contracts/issues/27),
  preserving ADR-015 money, currency, time and idempotency components.
- Consume exact accepted ADR-022 consequence/schema bytes from
  [PenniLogic/docs#42](https://github.com/PenniLogic/docs/issues/42), with pinned source
  commit/digests, four canonical enum definitions, closed enrollment schemas and recursive
  inference/tool no-address lint. No replacement policy or redundant Docs edit is introduced.
- Add coordinated DPoP raw-header mapping, operation-scoped step-up contract, finite
  `step-up-request@1` vectors, real lint controls and genuine three-language generated
  transport/decoder tests. Regenerate golden hashes through the managed generator only.

**Not accepted or released:** generated-runtime T6 and Python typing/UUID/enum conversion
gaps, shared typed-refusal integration and shared pipeline-test integration remain blocking.
See [the source-only guide](docs/custom-destinations.md). This change does not complete
contracts#27, enable AI, approve destinations/providers, deploy anything or publish a tag.
