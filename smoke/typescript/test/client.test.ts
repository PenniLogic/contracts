// InstantWireConformanceTest and client import test for the generated TypeScript client (ADR-015 §3.1).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';

import { Configuration, ProblemDetailFromJSON, ProblemDetailToJSON, instanceOfProblemDetail } from '../../../build/generated/typescript/src/index.js';
import { Instant, InstantFromJSON, InstantToJSON, InstantWireError } from '../../../build/generated/typescript/src/models/Instant.js';
import { Money } from '../../../build/generated/typescript/src/models/Money.js';
import { ROOT, loadFixture, type InstantWireFixture } from './fixtures.js';

const fixture = loadFixture<InstantWireFixture>('instant-wire-fixtures.v1.json');

function reasonOf(fn: () => unknown): string {
    try {
        fn();
    } catch (error) {
        assert.ok(error instanceof InstantWireError, `expected InstantWireError, got ${String(error)}`);
        return error.reason;
    }
    assert.fail('expected a rejection');
}

test('every valid instant round-trips with the expected epoch milliseconds', () => {
    for (const vector of fixture.valid) {
        const instant = Instant.fromWire(vector.wire);
        assert.equal(instant.epochMillis, Number(vector.epoch_millis), vector.name);
        assert.equal(instant.toWire(), vector.wire, vector.name);
        assert.equal(InstantToJSON(InstantFromJSON(vector.wire)), vector.wire, vector.name);
        assert.ok(Instant.fromDate(instant.toDate()).equals(instant), vector.name);
    }
});

test('every invalid instant is rejected with exactly the fixture reason', () => {
    for (const vector of fixture.invalid) {
        assert.equal(reasonOf(() => Instant.fromWire(vector.wire)), vector.reason, vector.name);
    }
});

test('JSON.stringify renders the canonical form and never a number', () => {
    const instant = Instant.ofEpochMillis(1790743928439);
    assert.equal(JSON.stringify({ at: instant }), '{"at":"2026-09-30T04:52:08.439Z"}');
    assert.throws(() => Instant.ofEpochMillis(1.5), TypeError);
    assert.equal(instant.compare(Instant.ofEpochMillis(0)), 1);
});

test('the generated client imports and the ProblemDetail model round-trips', () => {
    const configuration = new Configuration({ basePath: 'https://api.pennilogic.example/v1' });
    assert.equal(configuration.basePath, 'https://api.pennilogic.example/v1');
    const wire = {
        type: 'about:blank', title: 'Validation rejected', status: 422, code: 'validation_rejected',
        correlation_id: 'req-01HZY0000000000000000000', field: 'amount', reason: 'scale_mismatch',
    };
    const problem = ProblemDetailFromJSON(wire);
    assert.ok(instanceOfProblemDetail(problem));
    assert.equal(problem.status, 422);
    assert.equal(problem.correlationId, 'req-01HZY0000000000000000000');
    // ToJSON carries `undefined` for absent optional members; JSON.stringify drops them like the wire does.
    assert.deepEqual(JSON.parse(JSON.stringify(ProblemDetailToJSON(problem))), wire);
});

test('the manifest records the specification and generator versions', () => {
    const manifest = JSON.parse(readFileSync(join(ROOT, 'build', 'generated', 'typescript', 'contracts-manifest.json'), 'utf8')) as {
        target: string; spec_version: string; generator: { version: string }; tree_sha256: string;
    };
    assert.equal(manifest.target, 'typescript');
    assert.match(manifest.spec_version, /^\d+\.\d+\.\d+$/);
    assert.equal(manifest.generator.version, '7.25.0');
    assert.equal(manifest.tree_sha256.length, 64);
});

test('a synthetic envelope typed with the wrappers serializes through the seams only', () => {
    interface Envelope { readonly total: Money; readonly recordedAt: Instant }
    const envelope: Envelope = { total: Money.parse('-1234.56', 'INR'), recordedAt: Instant.parse('2026-09-30T04:52:08.439Z') };
    assert.equal(JSON.stringify(envelope), '{"total":{"amount":"-1234.56","currency":"INR"},"recordedAt":"2026-09-30T04:52:08.439Z"}');
});
