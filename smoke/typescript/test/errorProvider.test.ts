import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';

import { ProblemCode, ProblemCodeFromJSON, ClientState, IdempotencyTreatment, AiRefusalFromJSON, AiRefusalToJSON } from '../../../build/generated/typescript/src/index.js';
import { AUTHENTICATION_POLICIES, ERROR_POLICIES, authenticationPolicy, errorPolicy, newCorrelationId } from '../../../build/generated/typescript/src/errorCatalogue.js';
import { ServiceProblemDetailFromJSON, ServiceProblemDetailToJSON } from '../../../build/generated/typescript/src/models/ServiceProblemDetail.js';
import { AllocationMismatchDirection, ValidationIssueFromJSON } from '../../../build/generated/typescript/src/index.js';
import { JSONApiResponse } from '../../../build/generated/typescript/src/runtime.js';
import { ROOT, loadFixture } from './fixtures.js';

interface ErrorFixture {
    correlation_id: string;
    examples: Array<Record<string, unknown> & { code: string }>;
    invalid: Array<{ name: string; base: string; set?: Record<string, unknown>; remove?: string[] }>;
    refusal: Record<string, unknown>;
}
interface Entry { code: string; title: string; detail: string; status: number }
const fixture = loadFixture<ErrorFixture>('error-provider.v1.json');
const catalogue: { codes: Entry[]; authentication_codes: Entry[] } = JSON.parse(readFileSync(join(ROOT, 'spec', 'error-catalogue.v1.json'), 'utf8'));
const examples = new Map<string, Record<string, unknown>>(fixture.examples.map((example) => {
    const entry = catalogue.codes.find((value) => value.code === example.code);
    assert.ok(entry);
    return [example.code, {
        type: `urn:pennilogic:problem:${example.code}`, title: entry.title, status: entry.status,
        detail: entry.detail, correlation_id: fixture.correlation_id, ...example,
    }];
}));

test('accepted allocation mismatch direction is required and both safe spellings round-trip', () => {
    const data = loadFixture<{ problem: Record<string, unknown>; directions: string[] }>('allocation-refusal.v1.json');
    assert.throws(() => ServiceProblemDetailFromJSON(data.problem));
    for (const direction of data.directions) {
        const model = ServiceProblemDetailFromJSON({ ...data.problem, direction });
        assert.equal(ServiceProblemDetailToJSON(model).direction, direction);
    }
});

test('allocation direction is typed and conditional across actual ordinary nested transport and write', async () => {
    const data = loadFixture<{ problem: Record<string, unknown>; directions: string[]; invalid_directions: unknown[] }>('allocation-refusal.v1.json');
    for (const direction of data.directions) {
        const issue = { field: 'allocation', reason: 'allocation_sum_mismatch', direction };
        const wire = { ...data.problem, direction, validation_errors: [issue] };
        const model = await new JSONApiResponse(new Response(JSON.stringify(wire)), ServiceProblemDetailFromJSON).value();
        assert.ok(Object.values(AllocationMismatchDirection).includes(model.direction!));
        assert.equal(ValidationIssueFromJSON(issue).direction, direction);
        assert.deepEqual(ServiceProblemDetailToJSON(model), wire);
        const missing = { ...wire, validation_errors: [{ field: 'allocation', reason: 'allocation_sum_mismatch' }] };
        await assert.rejects(new JSONApiResponse(new Response(JSON.stringify(missing)), ServiceProblemDetailFromJSON).value());
    }
    for (const direction of data.invalid_directions) {
        const wire = { ...data.problem, direction };
        await assert.rejects(new JSONApiResponse(new Response(JSON.stringify(wire)), ServiceProblemDetailFromJSON).value(), (error: unknown) => {
            assert.ok(error instanceof Error);
            assert.doesNotMatch(error.message, /PRIVATE_SYNTHETIC_CANARY|12\.34/);
            return true;
        });
    }
    assert.throws(() => ServiceProblemDetailFromJSON({ ...data.problem, direction: 'shortfall', reason: 'shape' }));
    assert.throws(() => ServiceProblemDetailFromJSON({ ...data.problem, direction: 'shortfall', amount: '12.34' }));
    const good = ServiceProblemDetailFromJSON({ ...data.problem, direction: 'shortfall' });
    assert.throws(() => Reflect.apply(ServiceProblemDetailToJSON, undefined, [{ ...good, direction: 'PRIVATE_SYNTHETIC_CANARY' }]));
    const missing = { ...good };
    delete missing.direction;
    assert.throws(() => ServiceProblemDetailToJSON(missing));
});

// This must remain a compile failure: the generated code is an enum, not a string alias.
// @ts-expect-error raw strings cannot stand in for ProblemCode
const untypedCode: ProblemCode = 'validation_rejected';
void untypedCode;

test('service policies retain typed state and retry keys while authentication is excluded', () => {
    assert.deepEqual([...Object.values(ProblemCode)].sort(), [...catalogue.codes, ...catalogue.authentication_codes].map((entry) => entry.code).sort());
    assert.equal(Object.keys(ERROR_POLICIES).length, catalogue.codes.length);
    assert.equal(Object.keys(AUTHENTICATION_POLICIES).length, catalogue.authentication_codes.length);
    for (const entry of catalogue.authentication_codes) {
        const code = ProblemCodeFromJSON(entry.code);
        assert.equal(authenticationPolicy(code).state, null);
        assert.equal(authenticationPolicy(code).flow, 'authentication_required');
        assert.throws(() => errorPolicy(code), { message: 'problem code rejected' });
    }
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
