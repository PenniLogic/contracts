import assert from 'node:assert/strict';
import {
    NullableProbeFromJSON, NullableProbeToJSON, ProbeApi, Configuration, type NullableChoice,
} from './generated/typescript/src/index.js';

const wire = { only_null: null, number: null, label: null, values: [null, 1], nested: [null, [null, 'ok']],
    choice: null, choice_alias: null, choices: [null, 'alpha'],
    choices_nested: [null, [null, 'beta']], nonnull_choice: 'alpha' };
const value = NullableProbeFromJSON(wire);
const shared: NullableChoice | null = value.choice;
const sharedItems: ReadonlyArray<NullableChoice | null> = value.choices;
const nestedSharedItems: ReadonlyArray<ReadonlySet<NullableChoice | null> | null> = value.choicesNested;
assert.equal(shared, null);
assert.deepEqual(sharedItems, [null, 'alpha']);
assert.deepEqual(nestedSharedItems, [null, new Set([null, 'beta'])]);
assert.deepEqual(NullableProbeToJSON(value), wire);
assert.deepEqual(NullableProbeToJSON(NullableProbeFromJSON({ ...wire, choice: 'beta', choice_alias: 'alpha' })),
    { ...wire, choice: 'beta', choice_alias: 'alpha' });
assert.deepEqual(NullableProbeToJSON(NullableProbeFromJSON({ ...wire, optional_flag: null })),
    { ...wire, optional_flag: null });
assert.deepEqual(NullableProbeToJSON(NullableProbeFromJSON({ ...wire, optional_flag: true })),
    { ...wire, optional_flag: true });
assert.deepEqual(NullableProbeToJSON(NullableProbeFromJSON({ ...wire, optional_choice: null })),
    { ...wire, optional_choice: null });
assert.deepEqual(NullableProbeToJSON(NullableProbeFromJSON({ ...wire, optional_choice: 'beta' })),
    { ...wire, optional_choice: 'beta' });
for (const field of Object.keys(wire)) {
    const missing: Record<string, unknown> = { ...wire }; delete missing[field];
    assert.throws(() => NullableProbeFromJSON(missing));
}
for (const change of [{ only_null: 'wrong' }, { number: 1.5 }, { number: true }, { label: '' },
    { values: ['1'] }, { nested: [[1]] }, { optional_flag: 'true' }, { choice: 'unknown' },
    { choice_alias: 1 }, { choices: ['unknown'] }, { optional_choice: 'unknown' },
    { choices_nested: [[null, null]] }, { choices_nested: [['unknown']] }, { nonnull_choice: null },
    { private: 'CANARY' }]) {
    assert.throws(() => NullableProbeFromJSON({ ...wire, ...change }), (error: unknown) =>
        error instanceof Error && !error.message.includes('CANARY'));
}
const responses = [
    new Response(JSON.stringify(wire), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    new Response(null, { status: 204 }),
    new Response('', { status: 200, headers: { 'Content-Type': 'application/json' } }),
    new Response(JSON.stringify(wire), { status: 200, headers: { 'Content-Type': 'text/plain' } }),
    new Response(JSON.stringify(wire), { status: 206, headers: { 'Content-Type': 'application/json' } }),
];
const api = new ProbeApi(new Configuration({ fetchApi: async () => {
    const response = responses.shift();
    assert.ok(response instanceof Response);
    return response;
} }));
const body = await api.probeNullable();
if (body.status !== 200) assert.fail('expected typed body');
assert.deepEqual(NullableProbeToJSON(body.body), wire);
const empty = await api.probeNullable();
assert.equal(empty.status, 204);
assert.throws(() => new Response('null', { status: 204 }), TypeError);
for (let index = 0; index < 3; index += 1) await assert.rejects(api.probeNullable());
console.log('actual TypeScript null/presence/ref-alias/enum/nested-array and typed-empty transport controls');
