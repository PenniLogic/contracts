import {
    Money, MoneyToJSON,
    LedgerEntryToJSON, LedgerEntryToJSONTyped,
    OpeningBalanceRequestToJSON, OpeningBalanceRequestToJSONTyped,
    PostTransactionRequestToJSON, PostTransactionRequestToJSONTyped,
    ProblemDetailToJSON,
    type LedgerEntry, type LedgerEntryWire, type OpeningBalanceRequest, type PostTransactionRequest,
    type ProblemDetail, type ProblemDetailWire, type TransactionsApi,
    type Transaction, type Categorisation, type TransactionWire, type CategorisationWire,
} from './sdk/src/index.js';

declare const entry: LedgerEntry;
declare const opening: OpeningBalanceRequest;
declare const post: PostTransactionRequest;
declare const maybeEntry: LedgerEntry | undefined;
declare const nullableEntries: Array<LedgerEntry | undefined>;
declare const nullableLegacy: Array<ProblemDetail | null>;
declare const api: TransactionsApi;
declare const transaction: Transaction;
declare const categorisation: Categorisation;
declare const transactionWire: TransactionWire;
declare const categorisationWire: CategorisationWire;

const normalLedgerMoney: Money = LedgerEntryToJSON(entry).amount;
const typedLedgerMoney: Money = LedgerEntryToJSONTyped(entry, false).amount;
const normalOpeningMoney: Money = OpeningBalanceRequestToJSON(opening).amount;
const typedOpeningMoney: Money = OpeningBalanceRequestToJSONTyped(opening, true).amount;
const nestedMoney: Money | undefined = PostTransactionRequestToJSON(post).entries[0]?.amount;
const nestedTypedMoney: Money | undefined = PostTransactionRequestToJSONTyped(post, true).entries[0]?.amount;
const domainAfterWrite: LedgerEntry = LedgerEntryToJSON(entry);
const typedDomainAfterWrite: OpeningBalanceRequest = OpeningBalanceRequestToJSONTyped(opening, false);
const camelWireId: string = LedgerEntryToJSON(entry).entryId;
const wireWrapperMethod = LedgerEntryToJSON(entry).amount.toWire();
const wireMinorUnits: bigint = LedgerEntryToJSON(entry).amount.minorUnits;
const recycledWireMoney = MoneyToJSON(LedgerEntryToJSON(entry).amount);
const maybeOutput: LedgerEntryWire = LedgerEntryToJSON(maybeEntry);
const optionalCallbacks: LedgerEntryWire[] = nullableEntries.map(LedgerEntryToJSON);
const nullableCallbacks: ProblemDetailWire[] = nullableLegacy.map(ProblemDetailToJSON);
const plainWireArgument = LedgerEntryToJSON(LedgerEntryToJSON(entry));
const plainWireApiArgument = api.postTransaction({
    dPoP: 'synthetic.proof.signature', idempotencyKey: '00000000-0000-4000-8000-000000000010',
    postTransactionRequest: PostTransactionRequestToJSON(post),
});
transaction.categoryId = null;
categorisation.categoryId = '00000000-0000-7000-8000-000000000008';
transactionWire.category_id = null;
categorisationWire.category_id = null;
