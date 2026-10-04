import assert from 'node:assert/strict';
import { test } from 'node:test';
import { loadFixture } from './fixtures.js';
import {
    AiRefusalFromJSON, AiRefusalToJSON, ImportPreviewFromJSON,
} from '../../../build/generated/typescript/src/index.js';
import { JSONApiResponse } from '../../../build/generated/typescript/src/runtime.js';
import * as models from '../../../build/generated/typescript/src/index.js';

interface Fixture { refusal: Record<string, unknown> }
const refusal = loadFixture<Fixture>('error-provider.v1.json').refusal;

interface Specimen { schema: string; fixture: string; path: Array<string | number> }
interface TransportFixture {
    models: Specimen[];
    refusal_negatives: Array<{ name: string; set?: Record<string, unknown>; remove?: string[] }>;
}
const transport = loadFixture<TransportFixture>('provider-transport.v1.json');
interface Codec {
    decode: (value: unknown) => unknown;
    encode: (value: unknown) => unknown;
}
function codec<T>(decode: (value: unknown) => T, encode: (value: T) => unknown): Codec {
    return { decode, encode: (value: unknown): unknown => Reflect.apply(encode, undefined, [value]) };
}
const codecs: Readonly<Record<string, Codec>> = {
    AiRefusal: codec(models.AiRefusalFromJSON, models.AiRefusalToJSON),
    ServiceProblemDetail: codec(models.ServiceProblemDetailFromJSON, models.ServiceProblemDetailToJSON),
    ValidationIssue: codec(models.ValidationIssueFromJSON, models.ValidationIssueToJSON),
    Allowance: codec(models.AllowanceFromJSON, models.AllowanceToJSON),
    EntitlementDenial: codec(models.EntitlementDenialFromJSON, models.EntitlementDenialToJSON),
    DedupPrecedence: codec(models.DedupPrecedenceFromJSON, models.DedupPrecedenceToJSON),
    DuplicateLink: codec(models.DuplicateLinkFromJSON, models.DuplicateLinkToJSON),
    DedupEnrichment: codec(models.DedupEnrichmentFromJSON, models.DedupEnrichmentToJSON),
    DedupOutcome: codec(models.DedupOutcomeFromJSON, models.DedupOutcomeToJSON),
    ImportColumnBinding: codec(models.ImportColumnBindingFromJSON, models.ImportColumnBindingToJSON),
    ImportColumnMapping: codec(models.ImportColumnMappingFromJSON, models.ImportColumnMappingToJSON),
    ImportRowError: codec(models.ImportRowErrorFromJSON, models.ImportRowErrorToJSON),
    ImportPreviewRow: codec(models.ImportPreviewRowFromJSON, models.ImportPreviewRowToJSON),
    ImportPreviewCounts: codec(models.ImportPreviewCountsFromJSON, models.ImportPreviewCountsToJSON),
    ImportPreview: codec(models.ImportPreviewFromJSON, models.ImportPreviewToJSON),
    ImportCommitRequest: codec(models.ImportCommitRequestFromJSON, models.ImportCommitRequestToJSON),
    ImportCommitRow: codec(models.ImportCommitRowFromJSON, models.ImportCommitRowToJSON),
    ImportCommitCounts: codec(models.ImportCommitCountsFromJSON, models.ImportCommitCountsToJSON),
    ImportCommitResult: codec(models.ImportCommitResultFromJSON, models.ImportCommitResultToJSON),
};
function sample(entry: Specimen): Record<string, unknown> {
    let value: unknown = loadFixture<unknown>(entry.fixture);
    for (const key of entry.path) {
        if (typeof key === 'number') {
            assert.ok(Array.isArray(value)); value = value[key];
        } else {
            assert.ok(value !== null && typeof value === 'object');
            value = Object.fromEntries(Object.entries(value))[key];
        }
    }
    assert.ok(value !== null && typeof value === 'object' && !Array.isArray(value));
    return Object.fromEntries(Object.entries(value));
}
function* quotedPrimitives(value: unknown): Generator<unknown> {
    if (Array.isArray(value)) {
        for (const [index, child] of value.entries()) for (const replacement of quotedPrimitives(child)) {
            const result = structuredClone(value); result[index] = replacement; yield result;
        }
    } else if (value !== null && typeof value === 'object') {
        for (const [key, child] of Object.entries(value)) for (const replacement of quotedPrimitives(child)) {
            yield { ...value, [key]: replacement };
        }
    } else if (typeof value === 'number' || typeof value === 'boolean') yield JSON.stringify(value);
}
function* quotedNativePrimitives(value: unknown): Generator<unknown> {
    if (value instanceof Set) {
        const entries = [...value];
        for (const [index, child] of entries.entries()) for (const replacement of quotedNativePrimitives(child)) {
            const result = [...entries]; result[index] = replacement; yield new Set(result);
        }
    } else if (Array.isArray(value)) {
        for (const [index, child] of value.entries()) for (const replacement of quotedNativePrimitives(child)) {
            const result = [...value]; result[index] = replacement; yield result;
        }
    } else if (value !== null && typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype) {
        for (const [key, child] of Object.entries(value)) for (const replacement of quotedNativePrimitives(child)) {
            yield { ...value, [key]: replacement };
        }
    } else if (typeof value === 'number' || typeof value === 'boolean') yield JSON.stringify(value);
}
function safe(error: unknown): boolean {
    assert.ok(error instanceof Error);
    assert.doesNotMatch(error.message, /PRIVATE_SYNTHETIC_CANARY/);
    return true;
}

test('successful refusal rejects original unknown members in generated conversion and transport', async () => {
    const invalid = { ...refusal, provider_detail: 'PRIVATE_SYNTHETIC_CANARY' };
    assert.throws(() => AiRefusalFromJSON(invalid));
    await assert.rejects(new JSONApiResponse(new Response(JSON.stringify(invalid)), AiRefusalFromJSON).value());
});

test('successful refusal rejects bad correlation and unsafe constants before conversion', () => {
    for (const invalid of [
        { ...refusal, correlation_id: 'PRIVATE_SYNTHETIC_CANARY' },
        { ...refusal, message: 'PRIVATE_SYNTHETIC_CANARY' },
        { ...refusal, code: 'PRIVATE_SYNTHETIC_CANARY' },
    ]) {
        assert.throws(() => AiRefusalFromJSON(invalid), (error: unknown) => {
            assert.ok(error instanceof Error);
            assert.doesNotMatch(error.message, /PRIVATE_SYNTHETIC_CANARY/);
            return true;
        });
    }
    const model = AiRefusalFromJSON(refusal);
    assert.throws(() => AiRefusalToJSON({ ...model, correlationId: 'PRIVATE_SYNTHETIC_CANARY' }));
});

test('ordinary generated import conversion rejects nested closed-provider extras', () => {
    const data = loadFixture<{ preview: { mapping: Record<string, unknown> } }>('import-provider.v1.json');
    const wire = structuredClone(data.preview);
    wire.mapping.provider_detail = 'PRIVATE_SYNTHETIC_CANARY';
    assert.throws(() => ImportPreviewFromJSON(wire));
});

test('every closed provider rejects original extras through ordinary conversion, write and transport', async () => {
    assert.equal(transport.models.length, 19);
    assert.deepEqual(Object.keys(codecs).sort(), transport.models.map((item) => item.schema).sort());
    for (const entry of transport.models) {
        const transform = codecs[entry.schema];
        assert.ok(transform);
        const wire = sample(entry), model = transform.decode(wire);
        assert.deepEqual(JSON.parse(JSON.stringify(transform.encode(model))), wire, entry.schema);
        await new JSONApiResponse(new Response(JSON.stringify(wire)), transform.decode).value();
        for (const key of ['provider_detail', 'PRIVATE_SYNTHETIC_CANARY']) {
            const invalid = { ...wire, [key]: 'PRIVATE_SYNTHETIC_CANARY' };
            assert.throws(() => transform.decode(invalid), safe, entry.schema);
            await assert.rejects(new JSONApiResponse(new Response(JSON.stringify(invalid)), transform.decode).value(), safe);
            assert.ok(model !== null && typeof model === 'object');
            assert.throws(() => transform.encode({ ...model, [key]: 'PRIVATE_SYNTHETIC_CANARY' }), safe, entry.schema);
        }
        const missing = { ...wire };
        delete missing[Object.keys(wire)[0]!];
        assert.throws(() => transform.decode(missing), safe, entry.schema);
        await assert.rejects(new JSONApiResponse(new Response(JSON.stringify(missing)), transform.decode).value(), safe);
        for (const invalid of quotedNativePrimitives(model)) assert.throws(() => transform.encode(invalid), safe, entry.schema);
    }
});

test('every imported integer and boolean leaf rejects quoted wire types in ordinary conversion and transport', async () => {
    let cases = 0;
    for (const entry of transport.models) {
        const transform = codecs[entry.schema];
        assert.ok(transform);
        for (const wire of quotedPrimitives(sample(entry))) {
            assert.throws(() => transform.decode(wire), safe, entry.schema);
            await assert.rejects(new JSONApiResponse(new Response(JSON.stringify(wire)), transform.decode).value(), safe);
            cases += 1;
        }
    }
    assert.ok(cases >= 50);
});

test('every successful-refusal negative is rejected on ordinary ingress and egress without diagnostic content', async () => {
    for (const negative of transport.refusal_negatives) {
        const invalid = { ...refusal, ...negative.set };
        for (const key of negative.remove ?? []) delete invalid[key];
        assert.throws(() => AiRefusalFromJSON(invalid), safe, negative.name);
        await assert.rejects(new JSONApiResponse(new Response(JSON.stringify(invalid)), AiRefusalFromJSON).value(), safe);
    }
    const normal = AiRefusalFromJSON(refusal);
    for (const value of [
        { ...normal, correlationId: 'PRIVATE_SYNTHETIC_CANARY' },
        { ...normal, provider_detail: 'PRIVATE_SYNTHETIC_CANARY' },
        { ...normal, message: 'PRIVATE_SYNTHETIC_CANARY' },
    ]) assert.throws(() => Reflect.apply(AiRefusalToJSON, undefined, [value]), safe);
    const hidden = { ...normal };
    Object.defineProperty(hidden, 'provider_detail', { value: 'PRIVATE_SYNTHETIC_CANARY', enumerable: false });
    assert.throws(() => AiRefusalToJSON(hidden), safe);
});

test('actual generated mapping bounds accept 256 columns and reject 257 without narrowing legacy DTOs', async () => {
    const value = loadFixture<{ preview: { mapping: Record<string, unknown> } }>('import-provider.v1.json');
    const wire = structuredClone(value.preview);
    wire.mapping.column_count = 256;
    wire.mapping.unmapped_columns = Array.from({ length: 253 }, (_, index) => index + 4);
    const model = models.ImportPreviewFromJSON(wire);
    assert.equal(model.mapping.columnCount, 256);
    assert.deepEqual(JSON.parse(JSON.stringify(models.ImportPreviewToJSON(model))), wire);
    wire.mapping.column_count = 257;
    assert.throws(() => models.ImportPreviewFromJSON(wire), safe);
    await assert.rejects(new JSONApiResponse(new Response(JSON.stringify(wire)), models.ImportPreviewFromJSON).value(), safe);
});
