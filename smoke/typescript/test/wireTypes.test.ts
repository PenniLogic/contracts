import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
    Money, MoneyFromJSON, MoneyToJSON, LedgerEntryFromJSON, LedgerEntryToJSON, LedgerEntryToJSONTyped,
    OpeningBalanceRequestFromJSON, OpeningBalanceRequestToJSON, OpeningBalanceRequestToJSONTyped,
    PostTransactionRequestFromJSON, PostTransactionRequestToJSON, PostTransactionRequestToJSONTyped,
    TransactionFromJSON, TransactionToJSON, TransactionPageFromJSON, TransactionPageToJSON, TransactionsApi, Configuration,
    CategorisationFromJSON, CategorisationToJSON,
    ProblemDetailToJSON, ProblemDetailToJSONTyped,
    type LedgerEntryWire, type OpeningBalanceRequestWire, type PostTransactionRequestWire,
    type TransactionWire, type TransactionPageWire, type CategorisationWire, type MoneyWire,
} from '../../../build/generated/typescript/src/index.js';
import { Instant } from '../../../build/generated/typescript/src/models/Instant.js';
import { LocalDate } from '../../../build/generated/typescript/src/models/LocalDate.js';
import { loadFixture } from './fixtures.js';
import { mapWireRecord } from '../../../build/generated/typescript/src/wireSerialization.js';

const core = loadFixture<{ payloads: Record<string, unknown> }>('core-endpoints.v1.json').payloads;

test('typed wire record conversion preserves own prototype-spelled keys without mutating prototypes', () => {
    const input = Object.fromEntries<Money>([
        ['ordinary', Money.parse('0.01', 'INR')],
        ['__proto__', Money.parse('-0.01', 'INR')],
    ]);
    const converted: Record<string, MoneyWire> = mapWireRecord(input, member => member.toWire());
    const special = converted['__proto__'];
    assert.ok(special);
    assert.equal(Object.hasOwn(converted, '__proto__'), true);
    assert.deepEqual(special, { amount: '-0.01', currency: 'INR' });
    assert.equal(Object.getPrototypeOf(converted), Object.prototype);
    assert.equal(Object.getPrototypeOf(input), Object.prototype);
    assert.equal(Object.keys(converted).length, 2);
});

function portable(value: unknown): void {
    assert.notEqual(typeof value, 'bigint');
    assert.equal(value instanceof Money || value instanceof Instant || value instanceof LocalDate || value instanceof Set, false);
    if (Array.isArray(value)) {
        for (const member of value) portable(member);
    } else if (value !== null && typeof value === 'object') {
        for (const member of Object.values(value)) portable(member);
    }
}

test('public normal and typed financial writers return real MoneyWire and wire member names', () => {
    const transaction = TransactionFromJSON(core.transaction);
    const entry = transaction.entries[0];
    assert.ok(entry);
    const ordinary: LedgerEntryWire = LedgerEntryToJSON(entry);
    const typed: LedgerEntryWire = LedgerEntryToJSONTyped(entry, false);
    const amount: MoneyWire = ordinary.amount;
    const entryId: string = ordinary.entry_id;
    assert.equal(entryId, entry.entryId);
    assert.deepEqual(typed, ordinary);
    assert.equal(amount.amount, '-0.01');
    assert.equal(typeof amount.amount, 'string');
    assert.equal(amount instanceof Money, false);
    assert.throws(() => Reflect.apply(MoneyToJSON, undefined, [amount]), TypeError);
    assert.equal(MoneyFromJSON(amount).minorUnits, -1n);
    assert.deepEqual(LedgerEntryToJSON(LedgerEntryFromJSON(ordinary)), ordinary);
    portable(ordinary);

    const opening = OpeningBalanceRequestFromJSON(core.opening_balance);
    const openingWire: OpeningBalanceRequestWire = OpeningBalanceRequestToJSON(opening);
    const openingTyped: OpeningBalanceRequestWire = OpeningBalanceRequestToJSONTyped(opening, false);
    const date: string = openingWire.value_date;
    assert.equal(date, '2026-10-01');
    assert.deepEqual(openingTyped, openingWire);
    assert.deepEqual(OpeningBalanceRequestToJSON(OpeningBalanceRequestFromJSON(openingWire)), openingWire);
    portable(openingWire);
});

test('public nested and callback collection writers keep their wire types without a domain assertion', () => {
    const post = PostTransactionRequestFromJSON(core.post_transaction);
    const ordinary: PostTransactionRequestWire = PostTransactionRequestToJSON(post);
    const typed: PostTransactionRequestWire = PostTransactionRequestToJSONTyped(post, false);
    const first = ordinary.entries[0];
    assert.ok(first);
    const amount: MoneyWire = first.amount;
    const occurredAt: string = ordinary.occurred_at;
    assert.equal(amount.amount, '-0.01');
    assert.equal(occurredAt, '2026-10-01T00:00:00.000Z');
    assert.deepEqual(ordinary, typed);
    assert.deepEqual(ordinary, core.post_transaction);
    assert.deepEqual(PostTransactionRequestToJSON(PostTransactionRequestFromJSON(ordinary)), ordinary);
    portable(ordinary);

    const transaction = TransactionFromJSON(core.transaction);
    const entries: LedgerEntryWire[] = transaction.entries.map(LedgerEntryToJSON);
    const transactionWire: TransactionWire = TransactionToJSON(transaction);
    const page = TransactionPageFromJSON({ transactions: [core.transaction], page: core.cursor_end });
    const pageWire: TransactionPageWire = TransactionPageToJSON(page);
    assert.deepEqual(entries, transactionWire.entries);
    assert.deepEqual(pageWire.transactions[0], transactionWire);
    portable(pageWire);
});

test('public wire output keeps declared null and optional omission without broadening Money null', () => {
    const source = CategorisationFromJSON(core.categorisation);
    const wire: CategorisationWire = CategorisationToJSON(source);
    const selected: string | null = wire.view.as_of;
    const category: string | null = wire.category_id;
    const first = wire.allocations[0];
    assert.ok(first);
    const amount: MoneyWire = first.amount;
    assert.equal(selected, null);
    assert.equal(category, null);
    assert.equal(amount.amount, '0.01');
    assert.deepEqual(wire, core.categorisation);
    portable(wire);

    const omitted: undefined = LedgerEntryToJSON(undefined);
    const typedOmitted: undefined = LedgerEntryToJSONTyped(undefined, true);
    const legacyNull: null = ProblemDetailToJSON(null);
    const legacyTypedNull: null = ProblemDetailToJSONTyped(null, false);
    assert.equal(omitted, undefined);
    assert.equal(typedOmitted, undefined);
    assert.equal(legacyNull, null);
    assert.equal(legacyTypedNull, null);
    assert.throws(() => LedgerEntryToJSON(null), TypeError);
});

test('readonly response projection preserves omitted, explicit-null and non-null cases through real transport', async () => {
    const category = '00000000-0000-7000-8000-000000000008';
    const source = core.transaction;
    assert.ok(source !== null && typeof source === 'object');
    for (const extra of [{}, { category_id: null }, { category_id: category }]) {
        const wire: Record<string, unknown> = { ...source, ...extra };
        const api = new TransactionsApi(new Configuration({
            apiKey: 'DPoP synthetic.access.signature',
            fetchApi: async () => new Response(JSON.stringify(wire), { headers: { 'Content-Type': 'application/json' } }),
        }));
        const value = await api.getTransaction({
            dPoP: 'synthetic.projection.signature', transactionId: 'rec_00000000-0000-4000-8000-000000000004',
        });
        const projection: string | null | undefined = value.categoryId;
        assert.equal(projection, 'category_id' in extra ? extra.category_id : undefined);
        assert.deepEqual(TransactionToJSON(value), wire);
        assert.equal(Object.hasOwn(TransactionToJSON(value), 'category_id'), 'category_id' in extra);
        const page = TransactionPageFromJSON({ transactions: [wire], page: core.cursor_end });
        assert.deepEqual(TransactionPageToJSON(page).transactions[0], wire);
    }
    const posting = core.post_transaction;
    assert.ok(posting !== null && typeof posting === 'object');
    for (const categoryId of [null, category]) {
        assert.throws(() => PostTransactionRequestFromJSON({
            ...posting, category_id: categoryId,
        }), TypeError);
    }
});
