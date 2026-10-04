import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import {
    Money, ProblemCode,
    ServiceProblemDetailToJSON,
    SyntheticProviderRecordFromJSON, SyntheticProviderRecordToJSON,
    SyntheticProviderEnvelopeFromJSON, SyntheticProviderEnvelopeToJSON,
    SyntheticLegacyEnvelopeFromJSON, SyntheticLegacyEnvelopeToJSON,
} from './typescript/src/index.js';
import { BaseAPI, Configuration, JSONApiResponse } from './typescript/src/runtime.js';
import { Instant } from './typescript/src/models/Instant.js';
import { LocalDate } from './typescript/src/models/LocalDate.js';

const root = process.env.PL_CONTRACTS_ROOT;
assert.ok(root);
function fixture(name: string): Record<string, unknown> {
    return JSON.parse(readFileSync(join(root!, 'spec', 'fixtures', name), 'utf8'));
}
function control(): Record<string, unknown> {
    return {
        money: { amount: '90071992547409.93', currency: 'INR' },
        recorded_at: '2026-09-30T04:52:08.439Z', booked_on: '2026-09-30',
        currency_code: 'INR', zone: 'Asia/Kolkata',
        public_id: 'cor_00000000-0000-4000-8000-000000000001',
        values: [0, 10], flags: [false, true], codes: ['request_failed'],
        preview: fixture('import-provider.v1.json').preview,
        refusal: fixture('error-provider.v1.json').refusal,
    };
}
function wire(value: unknown): unknown {
    return JSON.parse(JSON.stringify(value));
}
function safe(error: unknown): boolean {
    assert.ok(error instanceof Error);
    assert.doesNotMatch(error.message, /PRIVATE_SYNTHETIC_CANARY|90071992547409\.93/);
    return true;
}
class RequestProbe extends BaseAPI {
    send(body: unknown): Promise<Response> {
        return this.request({ path: '/synthetic-composition', method: 'POST',
            headers: { 'Content-Type': 'application/json' }, body });
    }
}

test('freshly generated record reuses Money time enum and string-ref codecs', () => {
    const source = control(), value = SyntheticProviderRecordFromJSON(source);
    assert.ok(value.money instanceof Money);
    assert.ok(value.recordedAt instanceof Instant);
    assert.ok(value.bookedOn instanceof LocalDate);
    assert.equal(value.money.minorUnits, 9007199254740993n);
    assert.equal(value.codes[0], ProblemCode.RequestFailed);
    assert.deepEqual(wire(SyntheticProviderRecordToJSON(value)), source);
    assert.deepEqual(wire(SyntheticProviderRecordToJSON({ ...value, money: Money.ofMinorUnits(9007199254740993n, 'INR') })), source);
    for (const money of [
        { amount: '-0.001', currency: 'KWD' }, { amount: '0', currency: 'JPY' },
        { amount: '-92233720368547758.07', currency: 'INR' },
    ]) {
        const record = { ...source, money };
        assert.deepEqual(wire(SyntheticProviderRecordToJSON(SyntheticProviderRecordFromJSON(record))), record);
    }
});

test('optional strict child refs compile and omit undefined without permitting null or weakening required writers', () => {
    const source = control(), value = SyntheticProviderRecordFromJSON(source);
    assert.deepEqual(wire(SyntheticProviderRecordToJSON({ ...value, problem: undefined })), source);
    const problem = fixture('import-provider.v1.json').override_rejection;
    const included = { ...source, problem };
    assert.deepEqual(wire(SyntheticProviderRecordToJSON(SyntheticProviderRecordFromJSON(included))), included);
    assert.throws(() => SyntheticProviderRecordFromJSON({ ...source, problem: null }), safe);
    assert.throws(() => SyntheticProviderRecordToJSON({ ...value, problem: null }), safe);
    assert.throws(() => Reflect.apply(ServiceProblemDetailToJSON, undefined, [undefined]), safe);
    const legacy = SyntheticLegacyEnvelopeFromJSON({ future_member: true });
    const legacyWire = wire(SyntheticLegacyEnvelopeToJSON({ ...legacy, problem: undefined }));
    assert.ok(legacyWire !== null && typeof legacyWire === 'object');
    assert.equal(Object.hasOwn(legacyWire, 'problem'), false);
});

test('actual generated BaseAPI and generic response preserve ordinary and nested composition controls', async () => {
    const source = control(), envelope = { record: source, records: [source] };
    const sent: unknown[] = [];
    const api = new RequestProbe(new Configuration({
        basePath: 'https://api.pennilogic.example/v1',
        fetchApi: async (_url, init) => {
            assert.equal(typeof init?.body, 'string');
            sent.push(JSON.parse(String(init?.body)));
            return new Response(null, { status: 204 });
        },
    }));
    const record = await new JSONApiResponse(new Response(JSON.stringify(source)), SyntheticProviderRecordFromJSON).value();
    await api.send(SyntheticProviderRecordToJSON(record));
    assert.deepEqual(sent.pop(), source);
    const value = await new JSONApiResponse(new Response(JSON.stringify(envelope)), SyntheticProviderEnvelopeFromJSON).value();
    await api.send(SyntheticProviderEnvelopeToJSON(value));
    assert.deepEqual(sent.pop(), envelope);
    const invalids: unknown[] = [
        { ...value, records: [undefined] }, { ...value, records: new Array(1) },
        { ...value, records: [null] },
        { ...value, record: { ...record, values: [undefined] } },
        { ...value, record: { ...record, values: new Array(1) } },
        { ...value, record: { ...record, flags: [null] } },
        { ...value, record: { ...record, codes: [undefined] } },
        { ...value, record: { ...record, preview: { ...record.preview, rows: [undefined] } } },
        { ...value, record: { ...record, preview: { ...record.preview, rows: new Array(1) } } },
    ];
    for (const invalid of invalids) {
        await assert.rejects(async () => api.send(SyntheticProviderEnvelopeToJSON(invalid)), safe);
        assert.equal(sent.length, 0);
    }
    const inherited = new Array(1);
    const prototype = Object.create(Array.prototype);
    Object.defineProperty(prototype, '0', { value: record });
    Object.setPrototypeOf(inherited, prototype);
    await assert.rejects(async () => api.send(SyntheticProviderEnvelopeToJSON({ ...value, records: inherited })), safe);
    assert.equal(sent.length, 0);
});

test('array item kinds enums and nested private content are rejected before projection', () => {
    const source = control(), value = SyntheticProviderRecordFromJSON(source);
    for (const invalid of [
        { ...source, values: ['0'] }, { ...source, flags: ['false'] }, { ...source, codes: ['PRIVATE_SYNTHETIC_CANARY'] },
        { ...source, provider_detail: 'PRIVATE_SYNTHETIC_CANARY' },
        { ...source, refusal: { code: 'ai_refusal', message: 'PRIVATE_SYNTHETIC_CANARY', correlation_id: source.public_id } },
        { ...source, money: { amount: '0.00', currency: 'INR', provider_detail: 'PRIVATE_SYNTHETIC_CANARY' } },
    ]) assert.throws(() => SyntheticProviderRecordFromJSON(invalid), safe);
    for (const invalid of [
        { ...value, values: ['0'] }, { ...value, flags: ['false'] }, { ...value, codes: ['PRIVATE_SYNTHETIC_CANARY'] },
        { ...value, provider_detail: 'PRIVATE_SYNTHETIC_CANARY' },
        { ...value, refusal: { ...value.refusal, message: 'PRIVATE_SYNTHETIC_CANARY' } },
    ]) assert.throws(() => SyntheticProviderRecordToJSON(invalid), safe);
});

test('accepted money and time invalid inputs remain refused without any numeric fallback', () => {
    for (const money of [
        { amount: 12.34, currency: 'INR' }, { amount: '12.3', currency: 'INR' },
        { amount: '-92233720368547758.08', currency: 'INR' },
        { amount: 'PRIVATE_SYNTHETIC_CANARY', currency: 'INR' },
    ]) assert.throws(() => SyntheticProviderRecordFromJSON({ ...control(), money }), safe);
    assert.throws(() => SyntheticProviderRecordFromJSON({ ...control(), recorded_at: '2026-02-30T00:00:00.000Z' }), safe);
    assert.throws(() => SyntheticProviderRecordFromJSON({ ...control(), booked_on: '2026-02-30' }), safe);
    for (const [field, invalid] of [
        ['currency_code', 'inr'], ['zone', '1invalid'],
        ['public_id', 'rec_00000000-0000-4000-8000-000000000001'],
    ]) assert.throws(() => SyntheticProviderRecordFromJSON({ ...control(), [field!]: invalid }), safe);
});
