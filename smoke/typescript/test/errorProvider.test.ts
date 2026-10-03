import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';

import { ProblemCode, ProblemCodeFromJSON, ClientState, IdempotencyTreatment, AiRefusalFromJSON, AiRefusalToJSON } from '../../../build/generated/typescript/src/index.js';
import { ERROR_POLICIES, errorPolicy, newCorrelationId } from '../../../build/generated/typescript/src/errorCatalogue.js';
import { ServiceProblemDetailFromJSON, ServiceProblemDetailToJSON } from '../../../build/generated/typescript/src/models/ServiceProblemDetail.js';
import { ROOT, loadFixture } from './fixtures.js';

interface ErrorFixture {
    correlation_id: string;
    examples: Array<Record<string, unknown> & { code: string }>;
    invalid: Array<{ name: string; base: string; set?: Record<string, unknown>; remove?: string[] }>;
    refusal: Record<string, unknown>;
}
interface Entry { code: string; title: string; detail: string; status: number }
const fixture = loadFixture<ErrorFixture>('error-provider.v1.json');
const catalogue: { codes: Entry[] } = JSON.parse(readFileSync(join(ROOT, 'spec', 'error-catalogue.v1.json'), 'utf8'));
const examples = new Map<string, Record<string, unknown>>(fixture.examples.map((example) => {
    const entry = catalogue.codes.find((value) => value.code === example.code);
    assert.ok(entry);
    return [example.code, {
        type: `urn:pennilogic:problem:${example.code}`, title: entry.title, status: entry.status,
        detail: entry.detail, correlation_id: fixture.correlation_id, ...example,
    }];
}));

// This must remain a compile failure: the generated code is an enum, not a string alias.
// @ts-expect-error raw strings cannot stand in for ProblemCode
const untypedCode: ProblemCode = 'validation_rejected';
void untypedCode;

test('all catalogue codes have a typed state and immutable retry/key policy', () => {
    assert.deepEqual([...Object.values(ProblemCode)].sort(), catalogue.codes.map((entry) => entry.code).sort());
    assert.equal(Object.keys(ERROR_POLICIES).length, Object.values(ProblemCode).length);
    assert.equal(errorPolicy(ProblemCode.DependencyUnavailable).state, ClientState.Error);
    assert.equal(errorPolicy(ProblemCode.IdempotencyPayloadMismatch).retryable, false);
    assert.equal(errorPolicy(ProblemCode.IdempotencyPayloadMismatch).idempotency, IdempotencyTreatment.NeverReplaceToEscapeMismatch);
});

test('every error example decodes typed codes and round-trips through the actual seam', () => {
    for (const [code, wire] of examples) {
        const problem = ServiceProblemDetailFromJSON(wire);
        const typed: ProblemCode = problem.code;
        assert.equal(typed, code);
        assert.deepEqual(ServiceProblemDetailToJSON(problem), wire);
    }
});

test('all disclosure and retry negatives fail with safe diagnostics', () => {
    for (const negative of fixture.invalid) {
        const original = examples.get(negative.base);
        assert.ok(original);
        const wire = { ...structuredClone(original), ...negative.set };
        for (const key of negative.remove ?? []) delete wire[key];
        assert.throws(() => ServiceProblemDetailFromJSON(wire), (error: unknown) => {
            assert.ok(error instanceof Error);
            assert.doesNotMatch(error.message, /SYNTHETIC_|12\.34|internal-42/);
            return true;
        }, negative.name);
    }
});

test('enum decoder rejects unrecognised values, numbers and case coercion', () => {
    for (const value of ['VALIDATION_REJECTED', 'unknown', 422, true, null]) {
        assert.throws(() => ProblemCodeFromJSON(value), { message: 'enum value rejected' });
    }
});

test('AI refusal is distinct safe successful content', () => {
    const refusal = AiRefusalFromJSON(fixture.refusal);
    assert.deepEqual(AiRefusalToJSON(refusal), fixture.refusal);
    assert.throws(() => AiRefusalFromJSON({ ...fixture.refusal, message: 'SYNTHETIC_PROVIDER_TEXT' }), { message: 'enum value rejected' });
});

test('correlation factory creates independent public UUIDv4 values', () => {
    const values = new Set(Array.from({ length: 100 }, () => newCorrelationId()));
    assert.equal(values.size, 100);
    for (const value of values) assert.match(value, /^cor_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});
