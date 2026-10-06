import assert from 'node:assert/strict';
import {
    Money, MoneyFromJSON, MoneyToJSON, LedgerEntryFromJSON, LedgerEntryToJSON, LedgerEntryToJSONTyped,
    OpeningBalanceRequestFromJSON, OpeningBalanceRequestToJSON, PostTransactionRequestFromJSON,
    PostTransactionRequestToJSON, PostTransactionRequestToJSONTyped, TransactionToJSON,
    CategorisationViewFromJSON, CategorisationViewToJSON, ProblemDetailToJSON,
    type LedgerEntry, type LedgerEntryWire, type OpeningBalanceRequestWire,
    type PostTransactionRequestWire, type MoneyWire, type TransactionWire,
} from './sdk/src/index.js';

const entry = LedgerEntryFromJSON({
    entry_id: '00000000-0000-7000-8000-000000000005',
    account_id: '00000000-0000-7000-8000-000000000001',
    amount: { amount: '-0.01', currency: 'INR' },
});
const ledgerWire: LedgerEntryWire = LedgerEntryToJSON(entry);
const ledgerTyped: LedgerEntryWire = LedgerEntryToJSONTyped(entry, false);
const money: MoneyWire = ledgerWire.amount;
const rawAmount: string = money.amount;
const entryId: string = ledgerWire.entry_id;
assert.equal(rawAmount, '-0.01');
assert.equal(entryId, entry.entryId);
assert.equal(money instanceof Money, false);
assert.deepEqual(ledgerTyped, ledgerWire);
assert.throws(() => Reflect.apply(MoneyToJSON, undefined, [money]), TypeError);
assert.equal(MoneyFromJSON(money).minorUnits, -1n);

const opening = OpeningBalanceRequestFromJSON({
    amount: { amount: '0.01', currency: 'INR' }, value_date: '2026-10-01',
});
const openingWire: OpeningBalanceRequestWire = OpeningBalanceRequestToJSON(opening);
const valueDate: string = openingWire.value_date;
assert.equal(valueDate, '2026-10-01');
const post = PostTransactionRequestFromJSON({
    source_event_id: '00000000-0000-4000-8000-000000000003',
    occurred_at: '2026-10-01T00:00:00.000Z', currency: 'INR',
    entries: [
        { account_id: '00000000-0000-7000-8000-000000000001', amount: { amount: '-0.01', currency: 'INR' } },
        { account_id: '00000000-0000-7000-8000-000000000002', amount: { amount: '0.01', currency: 'INR' } },
    ],
});
const postWire: PostTransactionRequestWire = PostTransactionRequestToJSON(post);
const postTyped: PostTransactionRequestWire = PostTransactionRequestToJSONTyped(post, true);
const first = postWire.entries[0];
assert.ok(first);
const nestedMoney: MoneyWire = first.amount;
assert.equal(nestedMoney.amount, '-0.01');
assert.deepEqual(postTyped, postWire);
assert.deepEqual(PostTransactionRequestToJSON(PostTransactionRequestFromJSON(postWire)), postWire);
const entries: LedgerEntryWire[] = [entry].map(LedgerEntryToJSON);
assert.deepEqual(entries, [ledgerWire]);
function optionalWriter(value: LedgerEntry | undefined): LedgerEntryWire | undefined {
    return LedgerEntryToJSON(value);
}
assert.deepEqual(optionalWriter(entry), ledgerWire);
assert.equal(optionalWriter(undefined), undefined);
const nullLegacy: null = ProblemDetailToJSON(null);
const omitted: undefined = LedgerEntryToJSON(undefined);
assert.equal(nullLegacy, null);
assert.equal(omitted, undefined);
const view = CategorisationViewFromJSON({ mode: 'CURRENT', as_of: null });
const asOf: string | null = CategorisationViewToJSON(view).as_of;
assert.equal(asOf, null);

type HasNoUnsafeMoney<Wire extends { entries: ReadonlyArray<{ amount: MoneyWire }> }> = Wire;
type TransactionPublicWire = HasNoUnsafeMoney<NonNullable<ReturnType<typeof TransactionToJSON>>>;
const shape: TransactionWire | TransactionPublicWire | undefined = undefined;
assert.equal(shape, undefined);
console.log('cast-free public MoneyWire/key/temporal/nested/collection/null/optional consumers PASS');
