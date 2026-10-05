import assert from 'node:assert/strict';
import { test } from 'node:test';
import { loadFixture } from './fixtures.js';
import {
    ConfidenceBand, ConfidenceBandFromJSON, ImportColumnTarget, ImportPreviewToJSON, ImportCommitResultToJSON, DedupOutcomeToJSON,
} from '../../../build/generated/typescript/src/index.js';
import {
    commitRequestFromWire, commitResultFromWire, dedupFromWire, previewFromWire, verifyCommit, verifyMapping, verifyReplay,
} from '../../../build/generated/typescript/src/importContract.js';
import { MAX_IMPORT_ROWS, SOURCE_PRECEDENCE, sourceWindow } from '../../../build/generated/typescript/src/importPolicy.js';
import { ServiceProblemDetailFromJSON, ServiceProblemDetailToJSON } from '../../../build/generated/typescript/src/models/ServiceProblemDetail.js';

type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
type JsonObject = { [key: string]: JsonValue };
interface Change { name: string; target?: string; path: Array<string | number>; value?: JsonValue; remove?: boolean }
interface Fixture {
    request: JsonObject; preview: JsonObject; result: JsonObject; screen: JsonObject; suspected: JsonObject;
    linked: JsonObject; reversed: JsonObject; review: JsonObject; clear: JsonObject; override_rejection: JsonObject;
    invalid: Change[]; replay_changes: Change[];
    decision_changes: Array<{ name: string; preview_change?: Change; counts_change?: Change; result_change?: Change }>;
}
const data = loadFixture<Fixture>('import-provider.v1.json');
function member(value: JsonValue, key: string | number): JsonValue {
    if (Array.isArray(value)) {
        assert.equal(typeof key, 'number');
        const child = value[Number(key)];
        assert.notEqual(child, undefined);
        return child!;
    }
    assert.ok(value !== null && typeof value === 'object' && typeof key === 'string');
    const child = value[key];
    assert.notEqual(child, undefined);
    return child!;
}
function mutate(value: JsonObject, change: Change): JsonObject {
    const result = structuredClone(value);
    let target: JsonValue = result;
    for (const key of change.path.slice(0, -1)) target = member(target, key);
    const last = change.path.at(-1);
    if (Array.isArray(target)) {
        assert.ok(typeof last === 'number');
        if (change.remove) target.splice(last, 1);
        else {
            assert.notEqual(change.value, undefined);
            target[last] = change.value!;
        }
        return result;
    }
    assert.ok(typeof last === 'string' && target !== null && typeof target === 'object' && !Array.isArray(target));
    if (change.remove) delete target[last];
    else {
        assert.notEqual(change.value, undefined);
        target[last] = change.value!;
    }
    return result;
}
function original(target: string): JsonObject {
    switch (target) {
        case 'request': return data.request;
        case 'preview': return data.preview;
        case 'result': return data.result;
        case 'screen': return data.screen;
        case 'suspected': return data.suspected;
        case 'linked': return data.linked;
        case 'reversed': return data.reversed;
        default: assert.fail('unknown fixture target');
    }
}
function decode(target: string, wire: unknown): unknown {
    if (target === 'preview') return previewFromWire(wire);
    if (target === 'request') return commitRequestFromWire(wire);
    if (target === 'result') return commitResultFromWire(wire);
    return dedupFromWire(wire);
}

// @ts-expect-error confidence must remain a typed enum, never a locally invented string
const rawBand: ConfidenceBand = 'high';
void rawBand;

test('actual generated preview/commit models and common row problems round-trip', () => {
    const preview = previewFromWire(data.preview), request = commitRequestFromWire(data.request), result = commitResultFromWire(data.result);
    verifyCommit(preview, result);
    assert.equal(request.previewRef, preview.previewRef);
    assert.deepEqual(JSON.parse(JSON.stringify(ImportPreviewToJSON(preview))), data.preview);
    assert.deepEqual(JSON.parse(JSON.stringify(ImportCommitResultToJSON(result))), data.result);
    assert.equal(preview.rows[0]?.confidenceBand, ConfidenceBand.High);
    assert.equal(preview.mapping.userOverrides[0]?.target, ImportColumnTarget.ValueDate);
});
test('every dedup state decodes as typed content, with visible suppression and reversal', () => {
    for (const wire of [data.screen, data.suspected, data.linked, data.reversed, data.review, data.clear]) {
        const outcome = dedupFromWire(wire);
        assert.deepEqual(JSON.parse(JSON.stringify(DedupOutcomeToJSON(outcome))), wire);
    }
});
test('all schema and semantic negatives fail without echoing canaries', () => {
    for (const change of data.invalid) {
        assert.ok(change.target);
        const target = change.target;
        assert.throws(() => decode(target, mutate(original(target), change)), (error: unknown) => {
            assert.ok(error instanceof Error);
            assert.doesNotMatch(error.message, /SYNTHETIC_|12\.34|internal-record-42/);
            return true;
        }, change.name);
    }
});
test('same preview replay preserves the receipt and rejects a new fact or changed metadata', () => {
    const originalResult = commitResultFromWire(data.result);
    verifyReplay(originalResult, commitResultFromWire(structuredClone(data.result)));
    for (const change of data.replay_changes) {
        assert.throws(() => verifyReplay(originalResult, commitResultFromWire(mutate(data.result, change))), /replay_changed/, change.name);
    }
});
test('confidence is closed and all fifteen source pairs have symmetric published windows', () => {
    assert.deepEqual(Object.values(ConfidenceBand), ['high', 'low']);
    for (const left of SOURCE_PRECEDENCE) for (const right of SOURCE_PRECEDENCE) assert.equal(sourceWindow(left, right), sourceWindow(right, left));
    for (const bad of ['medium', 'HIGH', 85, null]) assert.throws(() => ConfidenceBandFromJSON(bad), /enum value rejected/);
});
test('exact row bound admits 10000 and refuses 10001', () => {
    const preview = structuredClone(data.preview);
    const rows = preview.rows;
    assert.ok(Array.isArray(rows));
    const row = rows[0];
    assert.ok(row !== null && typeof row === 'object' && !Array.isArray(row));
    preview.rows = Array.from({ length: MAX_IMPORT_ROWS }, (_, index) => ({ ...row, source_row: index + 1 }));
    preview.counts = { row_count: MAX_IMPORT_ROWS, create_count: MAX_IMPORT_ROWS, skip_count: 0, reject_count: 0, review_count: 0 };
    assert.equal(previewFromWire(preview).rows.length, 10000);
    preview.rows.push({ ...row, source_row: 10001 });
    assert.throws(() => previewFromWire(preview));
});
test('unresolved review and incomplete mapping cannot produce a bound commit receipt', () => {
    const preview = previewFromWire(data.preview);
    verifyMapping(preview.mapping, true);
    const partial = mutate(data.preview, { name: 'partial', path: ['mapping', 'detected_columns'], value: [
        { column_index: 1, target: 'amount' }, { column_index: 3, target: 'occurred_at' },
    ] });
    const unmapped = mutate(partial, { name: 'unmapped', path: ['mapping', 'unmapped_columns'], value: [2, 4] });
    assert.throws(() => verifyMapping(previewFromWire(unmapped).mapping, true), /mapping_incomplete/);
    const review = mutate(data.preview, { name: 'review', path: ['rows'], value: [
        { source_row: 2, action: 'review', reason: 'needs_review', confidence_band: 'low' },
    ] });
    const counted = mutate(review, { name: 'counts', path: ['counts'], value: { row_count: 1, create_count: 0, skip_count: 0, reject_count: 0, review_count: 1 } });
    assert.throws(() => verifyCommit(previewFromWire(counted), commitResultFromWire(data.result)), /preview_binding/);
});
test('unknown/foreign override shape is the same safe 400 and carries no record id', () => {
    const problem = ServiceProblemDetailFromJSON(data.override_rejection);
    assert.equal(problem.status, 400);
    assert.deepEqual(ServiceProblemDetailToJSON(problem), data.override_rejection);
});
test('commit cannot upgrade a skip or substitute the reviewed matching record', () => {
    for (const change of data.decision_changes) {
        let preview = structuredClone(data.preview), result = structuredClone(data.result);
        if (change.preview_change) preview = mutate(preview, change.preview_change);
        if (change.counts_change) preview = mutate(preview, change.counts_change);
        if (change.result_change) result = mutate(result, change.result_change);
        assert.throws(() => verifyCommit(previewFromWire(preview), commitResultFromWire(result)), /preview_decision/, change.name);
    }
});
