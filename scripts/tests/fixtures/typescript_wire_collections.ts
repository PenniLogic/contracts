import assert from 'node:assert/strict';
import {
    Money, MoneyFromJSON, MoneyToJSON, WireProbeFromJSON, WireProbeToJSON, WireProbeToJSONTyped,
    WireLeafFromJSON, WireLeafToJSON, WireLegacyToJSON,
    WireReceiptFromJSON, WireReceiptToJSON, type WireReceiptWire,
    type MoneyWire, type WireLeafWire, type WireProbeWire, type WireLegacyWire,
} from './sdk/src/index.js';

const source = {
    total: { amount: '92233720368547758.07', currency: 'INR' },
    money_items: [{ amount: '-92233720368547758.07', currency: 'INR' }],
    money_set: [{ amount: '0', currency: 'JPY' }, { amount: '-0.001', currency: 'KWD' }],
    leaf: { ledger_amount: { amount: '90071992547409.93', currency: 'INR' } },
    leaves: [{ ledger_amount: { amount: '-0.01', currency: 'INR' } }],
    nullable_items: null,
    number_or_null: null,
};
const value = WireProbeFromJSON(source);
const ordinary: WireProbeWire = WireProbeToJSON(value);
const typed: WireProbeWire = WireProbeToJSONTyped(value, true);
const money: MoneyWire = ordinary.total;
const items: MoneyWire[] = ordinary.money_items;
const members: MoneyWire[] = ordinary.money_set;
const leaf: WireLeafWire = ordinary.leaf;
const leaves: WireLeafWire[] = ordinary.leaves;
const nullableItems: MoneyWire[] | null = ordinary.nullable_items;
const count: number | null = ordinary.number_or_null;
const optional: MoneyWire | undefined = ordinary.optional_money;
assert.equal(money.amount, '92233720368547758.07');
assert.equal(MoneyFromJSON(money).minorUnits, 9223372036854775807n);
assert.equal(MoneyFromJSON(items[0]).minorUnits, -9223372036854775807n);
assert.deepEqual(members, source.money_set);
assert.deepEqual(leaves, source.leaves);
assert.deepEqual(leaf, source.leaf);
assert.equal(nullableItems, null);
assert.equal(count, null);
assert.equal(optional, undefined);
assert.equal(Object.hasOwn(ordinary, 'optional_money'), false);
assert.ok(value.moneySet instanceof Set);
assert.deepEqual(typed, ordinary);
assert.deepEqual(ordinary, source);
assert.deepEqual(WireProbeToJSON(WireProbeFromJSON(ordinary)), ordinary);
assert.deepEqual(JSON.parse(JSON.stringify(ordinary)), source);

const supplied = WireProbeFromJSON({
    ...source, optional_money: { amount: '0.00', currency: 'INR' },
    optional_items: [{ amount: '-0.01', currency: 'INR' }],
    nullable_items: [{ amount: '0.00', currency: 'INR' }], number_or_null: 1,
});
const suppliedWire = WireProbeToJSON(supplied);
const suppliedMoney: MoneyWire | undefined = suppliedWire.optional_money;
assert.deepEqual(suppliedMoney, { amount: '0.00', currency: 'INR' });
assert.deepEqual(suppliedWire.optional_items, [{ amount: '-0.01', currency: 'INR' }]);
assert.deepEqual(suppliedWire.nullable_items, [{ amount: '0.00', currency: 'INR' }]);
assert.equal(suppliedWire.number_or_null, 1);
assert.deepEqual(WireProbeToJSON({ ...value, optionalMoney: undefined, optionalItems: undefined }), ordinary);
assert.throws(() => Reflect.apply(WireProbeToJSON, undefined, [{ ...value, optionalMoney: null }]), TypeError);
for (const changes of [
    { total: null }, { total: { amount: 1, currency: 'INR' } },
    { total: { amount: '0.00', currency: 'INR', private: 'PRIVATE_SYNTHETIC_CANARY' } },
    { money_items: [null] }, { money_set: [source.money_set[0], source.money_set[0]] },
]) {
    assert.throws(() => WireProbeFromJSON({ ...source, ...changes }),
        (error: unknown) => error instanceof Error && !error.message.includes('PRIVATE_SYNTHETIC_CANARY'));
}
assert.throws(() => Reflect.apply(MoneyToJSON, undefined, [ordinary.total]), TypeError);
assert.equal(value.total instanceof Money, true);
const callback: WireLeafWire[] = [WireLeafFromJSON(source.leaf)].map(WireLeafToJSON);
assert.deepEqual(callback, [source.leaf]);
const legacy: WireLegacyWire = WireLegacyToJSON({ total: Money.parse('0.00', 'INR') });
const legacyNull: null = WireLegacyToJSON(null);
assert.deepEqual(legacy, { total: { amount: '0.00', currency: 'INR' } });
assert.equal(legacyNull, null);
const receipt = WireReceiptFromJSON({ result_total: { amount: '0.00', currency: 'INR' } });
const receiptWire: WireReceiptWire = WireReceiptToJSON(receipt);
const readonlyMoney: MoneyWire = receiptWire.result_total;
assert.deepEqual(readonlyMoney, { amount: '0.00', currency: 'INR' });
assert.deepEqual(WireReceiptToJSON(WireReceiptFromJSON(receiptWire)), receiptWire);
console.log('fresh generated typed Money arrays/sets/nested refs/nullable arrays/optional omissions PASS');
