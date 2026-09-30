// CrossLanguageMoneyRoundTripTest (ADR-015 §7) — TypeScript leg over spec/fixtures/money-roundtrip-generated.v1.json.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { test } from 'node:test';

import { MAX_MINOR_UNITS, Money } from '../../../build/generated/typescript/src/models/Money.js';
import { loadFixture } from './fixtures.js';

interface RoundTripFixture {
    readonly count: number;
    readonly boundary_count: number;
    readonly generated_count: number;
    readonly seed: string;
    readonly round_trip_sha256: string;
    readonly values: readonly { readonly name?: string; readonly wire: { readonly amount: string; readonly currency: string }; readonly minor_units: string }[];
}

const fixture = loadFixture<RoundTripFixture>('money-roundtrip-generated.v1.json');

test('the header is consistent', () => {
    assert.equal(fixture.values.length, fixture.count);
    assert.equal(fixture.boundary_count + fixture.generated_count, fixture.count);
    assert.ok(fixture.generated_count >= 10_000);
    assert.match(fixture.seed, /^0x[0-9A-F]{16}$/);
    assert.deepEqual(new Set(fixture.values.map((row) => row.wire.currency)), new Set(['INR', 'JPY', 'KWD']));
});

test('every value round-trips and the emitted lines hash to the shared digest', () => {
    const digest = createHash('sha256');
    for (const row of fixture.values) {
        const money = Money.fromWire(row.wire);
        const wire = money.toWire();
        assert.deepEqual(wire, row.wire, row.name ?? row.wire.amount);
        assert.equal(money.minorUnits.toString(), row.minor_units, row.name ?? row.wire.amount);
        assert.ok(Money.ofMinorUnits(money.minorUnits, money.currency).equals(money));
        digest.update(`${wire.amount}|${wire.currency}|${money.minorUnits.toString()}\n`, 'ascii');
    }
    assert.equal(digest.digest('hex'), fixture.round_trip_sha256);
});

test('boundary rows are present for every currency', () => {
    const named = new Map(fixture.values.filter((row) => row.name !== undefined).map((row) => [row.name as string, row]));
    for (const code of ['INR', 'JPY', 'KWD']) {
        assert.equal(BigInt(named.get(`maximum ${code}`)?.minor_units ?? '0'), MAX_MINOR_UNITS);
        assert.equal(BigInt(named.get(`minimum ${code}`)?.minor_units ?? '0'), -MAX_MINOR_UNITS);
        assert.equal(BigInt(named.get(`2^53+1 ${code}`)?.minor_units ?? '0'), 9007199254740993n);
        assert.equal(named.get(`zero ${code}`)?.minor_units, '0');
    }
});
