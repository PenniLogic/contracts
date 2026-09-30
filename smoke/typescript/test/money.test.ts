// MoneyWireConformanceTest for the generated TypeScript client (ADR-015 §2.1, §7).
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { MAX_MINOR_UNITS, MONEY_REASONS, Money, MoneyFromJSON, MoneyToJSON, MoneyWireError } from '../../../build/generated/typescript/src/models/Money.js';
import { CURRENCY_REGISTRY } from '../../../build/generated/typescript/src/models/currencyRegistry.js';
import { loadFixture, type MoneyWireFixture } from './fixtures.js';

const fixture = loadFixture<MoneyWireFixture>('money-wire-fixtures.v1.json');

function reasonOf(fn: () => unknown): { reason: string; field: string } {
    try {
        fn();
    } catch (error) {
        assert.ok(error instanceof MoneyWireError, `expected MoneyWireError, got ${String(error)}`);
        return { reason: error.reason, field: error.field };
    }
    assert.fail('expected a rejection');
}

test('number is never money: ofMinorUnits and parse reject number input', () => {
    assert.throws(() => Money.ofMinorUnits(150 as unknown as bigint, 'INR'), TypeError);
    assert.throws(() => Money.parse(1.5 as unknown as string, 'INR'), TypeError);
    assert.throws(() => Money.parse(150 as unknown as string, 'INR'), TypeError);
});

test('a JSON number on the wire is rejected with number_not_string', () => {
    assert.deepEqual(reasonOf(() => Money.fromWire({ amount: 1234.56, currency: 'INR' })), { reason: 'number_not_string', field: 'amount' });
    assert.deepEqual(reasonOf(() => MoneyFromJSON({ amount: 123456, currency: 'INR' })), { reason: 'number_not_string', field: 'amount' });
});

test('minor units are bigint and never leave through JSON.stringify as a number', () => {
    const money = Money.parse('90071992547409.93', 'INR');
    assert.equal(typeof money.minorUnits, 'bigint');
    assert.equal(money.minorUnits, 9007199254740993n); // 2^53 + 1 survives
    assert.equal(JSON.stringify({ total: money }), '{"total":{"amount":"90071992547409.93","currency":"INR"}}');
    assert.deepEqual(MoneyToJSON(money), { amount: '90071992547409.93', currency: 'INR' });
});

test('reason order matches the fixture', () => {
    assert.deepEqual([...MONEY_REASONS], fixture.reason_order);
});

test('every valid vector round-trips byte-identically with the expected minor units', () => {
    for (const vector of fixture.valid) {
        const money = Money.fromWire(vector.wire);
        assert.equal(money.minorUnits, BigInt(vector.minor_units), vector.name);
        assert.deepEqual(money.toWire(), vector.wire, vector.name);
        assert.ok(Money.parse(vector.wire.amount, vector.wire.currency).equals(money), vector.name);
        assert.ok(Money.ofMinorUnits(BigInt(vector.minor_units), vector.wire.currency).equals(money), vector.name);
    }
});

test('every invalid vector is rejected with exactly the fixture reason and field', () => {
    for (const vector of fixture.invalid) {
        assert.deepEqual(reasonOf(() => Money.fromWire(vector.wire)), { reason: vector.reason, field: vector.field }, vector.name);
    }
});

test('parse checks in the shared order with the digit bound first', () => {
    assert.deepEqual([...fixture.parse_reason_order], ['grammar', 'currency_unknown', 'scale_mismatch', 'out_of_range']);
    for (const vector of fixture.parse_invalid) {
        const outcome = reasonOf(() => Money.parse(vector.amount, vector.currency));
        assert.equal(outcome.reason, vector.reason, vector.name);
    }
    assert.equal(reasonOf(() => Money.fromWire({ amount: '9'.repeat(5003), currency: 'JPY' })).reason, 'out_of_range');
});

test('rejections never echo the offending value', () => {
    try {
        Money.fromWire({ amount: '1234.567', currency: 'INR' });
        assert.fail('expected rejection');
    } catch (error) {
        assert.ok(error instanceof MoneyWireError);
        assert.ok(!error.message.includes('1234'));
    }
});

test('range boundaries exclude -2^63', () => {
    assert.equal(Money.ofMinorUnits(MAX_MINOR_UNITS, 'JPY').toWire().amount, '9223372036854775807');
    assert.equal(Money.ofMinorUnits(-MAX_MINOR_UNITS, 'JPY').toWire().amount, '-9223372036854775807');
    assert.equal(reasonOf(() => Money.ofMinorUnits(-9223372036854775808n, 'JPY')).reason, 'out_of_range');
    assert.equal(reasonOf(() => Money.ofMinorUnits(9223372036854775808n, 'JPY')).reason, 'out_of_range');
});

test('arithmetic is same-currency and range-checked; comparison never coerces', () => {
    const a = Money.parse('1.50', 'INR');
    const b = Money.parse('-0.75', 'INR');
    assert.equal(a.plus(b).toWire().amount, '0.75');
    assert.equal(a.minus(b).toWire().amount, '2.25');
    assert.equal(b.negate().toWire().amount, '0.75');
    assert.equal(a.compare(b), 1);
    assert.throws(() => a.plus(Money.parse('1', 'JPY')), TypeError);
    assert.throws(() => a.compare(Money.parse('1', 'JPY')), TypeError);
    assert.equal(reasonOf(() => Money.ofMinorUnits(MAX_MINOR_UNITS, 'JPY').plus(Money.ofMinorUnits(1n, 'JPY'))).reason, 'out_of_range');
    assert.equal(a.equals(1.5), false);
    assert.equal(a.equals(Money.parse('1.50', 'INR')), true);
    assert.equal(a.equals(Money.parse('150', 'JPY')), false);
});

test('formatting matches the registry exponent for every currency', () => {
    for (const [code, entry] of Object.entries(CURRENCY_REGISTRY)) {
        for (const minor of [0n, 1n, 10n, 100n, 1000000000000000000n, -1000000000000000000n]) {
            const amount = Money.ofMinorUnits(minor, code).toWire().amount;
            const fraction = amount.includes('.') ? amount.split('.')[1] ?? '' : '';
            assert.equal(fraction.length, entry.exponent, `${code} ${minor}`);
            assert.ok(!/[eE]/.test(amount));
        }
    }
});
