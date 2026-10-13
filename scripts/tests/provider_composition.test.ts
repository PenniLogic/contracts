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
    SyntheticConstraintBundleFromJSON, SyntheticConstraintBundleToJSON,
    SyntheticConstraintEnvelopeFromJSON, SyntheticConstraintEnvelopeToJSON,
    SyntheticUnicodeRecordFromJSON, SyntheticUnicodeRecordToJSON,
    SyntheticUnicodeEnvelopeFromJSON, SyntheticUnicodeEnvelopeToJSON,
} from './typescript/src/index.js';
import { BaseAPI, Configuration, JSONApiResponse } from './typescript/src/runtime.js';
import { Instant } from './typescript/src/models/Instant.js';
import { LocalDate } from './typescript/src/models/LocalDate.js';

const root = process.env.PL_CONTRACTS_ROOT;
assert.ok(root);
function fixture<T = Record<string, unknown>>(name: string): T {
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
    assert.doesNotMatch(error.message, /PRIVATE_SYNTHETIC_CANARY|PRIVATE_SYNTHETIC_MEMBER|90071992547409\.93/);
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
    assert.throws(() => Reflect.apply(SyntheticProviderRecordToJSON, undefined, [{ ...value, problem: null }]), safe);
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
        await assert.rejects(async () => api.send(Reflect.apply(SyntheticProviderEnvelopeToJSON, undefined, [invalid])), safe);
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
    ]) assert.throws(() => Reflect.apply(SyntheticProviderRecordToJSON, undefined, [invalid]), safe);
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

test('recursive declared constraints reject every negative before actual generated fetch', async () => {
    interface RecursiveCases {
        control: Record<string, unknown>;
        negatives: Array<{ name: string; field: string; value: unknown }>;
    }
    const data = fixture<RecursiveCases>('provider-recursive.v1.json');
    const sent: unknown[] = [];
    const api = new RequestProbe(new Configuration({
        basePath: 'https://api.pennilogic.example/v1',
        fetchApi: async (_url, init) => {
            sent.push(JSON.parse(String(init?.body))); return new Response(null, { status: 204 });
        },
    }));
    const valid = SyntheticConstraintBundleFromJSON(data.control);
    await api.send(SyntheticConstraintBundleToJSON(valid));
    assert.deepEqual(sent.pop(), data.control);
    for (const negative of data.negatives) {
        const invalid = { ...data.control, [negative.field]: negative.value };
        assert.throws(() => SyntheticConstraintBundleFromJSON(invalid), safe, negative.name);
        await assert.rejects(new JSONApiResponse(new Response(JSON.stringify(invalid)), SyntheticConstraintBundleFromJSON).value(), safe);
        const nested = { bundle: invalid };
        assert.throws(() => SyntheticConstraintEnvelopeFromJSON(nested), safe);
        await assert.rejects(new JSONApiResponse(new Response(JSON.stringify(nested)), SyntheticConstraintEnvelopeFromJSON).value(), safe);
        const nativeField = negative.field.replace(/_([a-z])/g, (_match, letter: string) => letter.toUpperCase());
        assert.ok(Object.hasOwn(valid, nativeField));
        await assert.rejects(async () => api.send(SyntheticConstraintBundleToJSON({ ...valid, [nativeField]: negative.value })), safe);
        await assert.rejects(async () => api.send(SyntheticConstraintEnvelopeToJSON({ bundle: { ...valid, [nativeField]: negative.value } })), safe);
        assert.equal(sent.length, 0, negative.name);
    }
    for (const values of [[-1], [11]]) {
        const record = control();
        assert.throws(() => SyntheticProviderRecordFromJSON({ ...record, values }), safe);
    }
});

test('private Money member names stay outside marked ordinary nested and JSONApiResponse diagnostics', async () => {
    const source = control();
    const money = source.money;
    assert.ok(money !== null && typeof money === 'object');
    const invalid = { ...source, money: { ...money, PRIVATE_SYNTHETIC_MEMBER: 'PRIVATE_SYNTHETIC_CANARY' } };
    const envelope = { record: invalid, records: [invalid] };
    const routes: Record<string, () => unknown | Promise<unknown>> = {
        ordinary: () => SyntheticProviderRecordFromJSON(invalid),
        actual_response: () => new JSONApiResponse(new Response(JSON.stringify(invalid)), SyntheticProviderRecordFromJSON).value(),
        nested: () => SyntheticProviderEnvelopeFromJSON(envelope),
        generic_response: () => new JSONApiResponse(new Response(JSON.stringify(envelope)), SyntheticProviderEnvelopeFromJSON).value(),
    };
    const outcomes: Record<string, boolean> = {};
    for (const [route, read] of Object.entries(routes)) {
        try { await read(); outcomes[route] = false; }
        catch (error: unknown) {
            assert.ok(error instanceof Error);
            const diagnostic = Object.fromEntries(Object.entries(error));
            outcomes[route] = !error.message.includes('PRIVATE_SYNTHETIC') &&
                !JSON.stringify(diagnostic).includes('PRIVATE_SYNTHETIC');
        }
    }
    assert.deepEqual(outcomes, Object.fromEntries(Object.keys(routes).map((route) => [route, true])));
});

test('code-point lengths and Unicode wildcard semantics hold on ordinary native nested and actual transport', async () => {
    interface UnicodeFixture {
        positive: Array<Record<string, unknown> & { name: string }>;
        negative: Array<{ name: string; field: string; value: unknown }>;
    }
    const data = fixture<UnicodeFixture>('provider-unicode.v1.json');
    const emitted: unknown[] = [];
    const api = new RequestProbe(new Configuration({
        basePath: 'https://api.pennilogic.example/v1',
        fetchApi: async (_url, init) => {
            emitted.push(JSON.parse(String(init?.body))); return new Response(null, { status: 204 });
        },
    }));
    const outcomes: Record<string, boolean> = {};
    for (const entry of data.positive) {
        const { name, ...source } = entry;
        try {
            const model = SyntheticUnicodeRecordFromJSON(source);
            await api.send(SyntheticUnicodeRecordToJSON(model));
            outcomes[name + '/ordinary_write'] = equalWire(emitted.pop(), source);
        } catch (error: unknown) { safe(error); outcomes[name + '/ordinary_write'] = false; }
        try {
            const model = await new JSONApiResponse(new Response(JSON.stringify(source)), SyntheticUnicodeRecordFromJSON).value();
            outcomes[name + '/actual_response'] = equalWire(wire(SyntheticUnicodeRecordToJSON(model)), source);
        } catch (error: unknown) { safe(error); outcomes[name + '/actual_response'] = false; }
        try {
            const envelope = SyntheticUnicodeEnvelopeFromJSON({ record: source });
            await api.send(SyntheticUnicodeEnvelopeToJSON(envelope));
            outcomes[name + '/nested_request'] = equalWire(emitted.pop(), { record: source });
        } catch (error: unknown) { safe(error); outcomes[name + '/nested_request'] = false; }
    }
    const entry = data.positive[0]; assert.ok(entry);
    const { name: _name, ...base } = entry;
    const valid = SyntheticUnicodeRecordFromJSON(base);
    const astral = '\u{1f9ea}';
    const native = { ...valid, symbol: astral, unit: astral, wild: [astral], notA: [astral] };
    await api.send(SyntheticUnicodeRecordToJSON(native));
    const actualNative: unknown = emitted.pop();
    const expectedNative = { ...base, symbol: astral, unit: astral, wild: [astral], not_a: [astral] };
    assert.deepEqual(actualNative, expectedNative);
    await api.send(SyntheticUnicodeEnvelopeToJSON({ record: native }));
    assert.deepEqual(emitted.pop(), { record: expectedNative });
    for (const negative of data.negative) {
        const invalid = { ...base, [negative.field]: negative.value };
        const nativeField = negative.field.replace(/_([a-z])/g, (_match, letter: string) => letter.toUpperCase());
        for (const [route, read] of Object.entries({
            ordinary: async (): Promise<unknown> => SyntheticUnicodeRecordFromJSON(invalid),
            response: (): Promise<unknown> => new JSONApiResponse(new Response(JSON.stringify(invalid)), SyntheticUnicodeRecordFromJSON).value(),
            native: (): Promise<unknown> => api.send(SyntheticUnicodeRecordToJSON({ ...valid, [nativeField]: negative.value })),
            nested: async (): Promise<unknown> => SyntheticUnicodeEnvelopeFromJSON({ record: invalid }),
        })) {
            try { await read(); outcomes[negative.name + '/' + route] = false; }
            catch (error: unknown) { safe(error); outcomes[negative.name + '/' + route] = true; }
        }
        assert.equal(emitted.length, 0);
    }
    assert.deepEqual(Object.entries(outcomes).filter(([, accepted]) => !accepted), []);
});

function equalWire(left: unknown, right: unknown): boolean {
    try { assert.deepEqual(left, right); return true; } catch (error: unknown) {
        if (error instanceof assert.AssertionError) return false;
        throw error;
    }
}
