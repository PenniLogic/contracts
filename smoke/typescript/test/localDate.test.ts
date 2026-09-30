// Date-only seam tests (ADR-015 §3.1) for the generated TypeScript client's LocalDate.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { LocalDate, LocalDateFromJSON, LocalDateToJSON, LocalDateWireError } from '../../../build/generated/typescript/src/models/LocalDate.js';

function reasonOf(fn: () => unknown): string {
    try {
        fn();
    } catch (error) {
        assert.ok(error instanceof LocalDateWireError, `expected LocalDateWireError, got ${String(error)}`);
        return error.reason;
    }
    assert.fail('expected a rejection');
}

test('valid dates round-trip through the seam functions', () => {
    for (const text of ['2026-09-30', '0001-01-01', '9999-12-31', '2024-02-29', '2000-02-29']) {
        const value = LocalDate.fromWire(text);
        assert.equal(value.toWire(), text);
        assert.equal(LocalDateToJSON(LocalDateFromJSON(text)), text);
        assert.ok(LocalDate.of(value.year, value.month, value.day).equals(value));
        assert.equal(JSON.stringify({ on: value }), `{"on":"${text}"}`);
    }
});

test('invalid dates are rejected with the fixed reasons and never coerced', () => {
    const cases: [string, string][] = [
        ['2026-9-30', 'grammar'], ['2026-09-30T00:00:00.000Z', 'grammar'], ['0000-01-01', 'grammar'], ['2026-13-01', 'grammar'],
        ['2026-09-30 ', 'grammar'], ['', 'grammar'],
        ['2026-02-30', 'calendar'], ['2023-02-29', 'calendar'], ['1900-02-29', 'calendar'], ['2026-04-31', 'calendar'],
    ];
    for (const [text, reason] of cases) {
        assert.equal(reasonOf(() => LocalDate.fromWire(text)), reason, text);
    }
    assert.equal(reasonOf(() => LocalDate.fromWire(20260930)), 'shape');
    assert.throws(() => LocalDate.of(2026.5, 9, 30), TypeError);
    assert.throws(() => LocalDateToJSON({ year: 2026 } as unknown as LocalDate), TypeError);
});

test('ordering compares the canonical form', () => {
    assert.equal(LocalDate.parse('2026-09-29').compare(LocalDate.parse('2026-09-30')), -1);
    assert.equal(LocalDate.parse('2026-09-30').compare(LocalDate.parse('2026-09-30')), 0);
    assert.equal(LocalDate.parse('2026-09-30').equals('2026-09-30'), false);
});
