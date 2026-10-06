import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
    AccountsApi, AuthApi, CategoriesApi, TransactionsApi, Configuration,
    CreateAccountRequestFromJSON, PostTransactionRequestFromJSON, PostTransactionRequestToJSON,
    CategorisationFromJSON, CategorisationToJSON, CategorisationViewFromJSON, CategorisationViewToJSON,
    AuthRecoveryProgressFromJSON, AuthRecoveryProgressToJSON,
    ApplicationProblemDetailFromJSON, ApplicationProblemDetailToJSON,
} from '../../../build/generated/typescript/src/index.js';
import { ResponseError } from '../../../build/generated/typescript/src/runtime.js';
import { Instant } from '../../../build/generated/typescript/src/models/Instant.js';
import { loadFixture } from './fixtures.js';

interface CoreFixture {
    payloads: Record<string, Record<string, unknown>>;
}
const core = loadFixture<CoreFixture>('core-endpoints.v1.json');
const auth = loadFixture<CoreFixture>('auth-endpoints.v1.json');
const dedup = loadFixture<{ screen: Record<string, unknown> }>('import-provider.v1.json');
const key = '00000000-0000-4000-8000-000000000010';

test('all eight authentication contexts use actual non-2xx transports and preserve explicit null safely', async () => {
    interface Catalogue { authentication_codes: Array<{ code: string; title: string; status: number; detail: string }> }
    interface AuthErrors { correlation_id: string; examples: Array<{ code: string; context: Record<string, unknown> }> }
    const catalogue = loadFixture<Catalogue>('../error-catalogue.v1.json');
    const errors = loadFixture<AuthErrors>('authentication-errors.v1.json');
    assert.equal(errors.examples.length, 8);
    for (const row of errors.examples) {
        const entry = catalogue.authentication_codes.find((value) => value.code === row.code);
        assert.ok(entry);
        const wire = { type: `urn:pennilogic:problem:${row.code}`, title: entry.title, status: entry.status,
            detail: entry.detail, code: row.code, correlation_id: errors.correlation_id, ...row.context };
        const api = new AuthApi(new Configuration({ apiKey: 'DPoP synthetic.access.signature',
            fetchApi: async () => new Response(JSON.stringify(wire), {
                status: entry.status, headers: { 'Content-Type': 'application/problem+json', 'Cache-Control': 'no-store' },
            }),
        }));
        let failure: unknown;
        try { await api.getProfile({ dPoP: 'synthetic.auth.signature' }); }
        catch (error: unknown) { failure = error; }
        assert.ok(failure instanceof ResponseError);
        const body: unknown = await failure.response.json();
        assert.deepEqual(ApplicationProblemDetailToJSON(ApplicationProblemDetailFromJSON(body)), wire);
        assert.throws(() => ApplicationProblemDetailFromJSON({ ...wire, provider: 'PRIVATE_SYNTHETIC_CANARY' }),
            (error: unknown) => error instanceof Error && !error.message.includes('PRIVATE_SYNTHETIC_CANARY'));
        assert.equal(failure.response.headers.get('Cache-Control'), 'no-store');
    }
});

test('required null, optional absence and nested ref arrays preserve exact declared wire', () => {
    const view = CategorisationViewFromJSON({ mode: 'CURRENT', as_of: null });
    assert.deepEqual(CategorisationViewToJSON(view), { mode: 'CURRENT', as_of: null });
    for (const wire of [{ mode: 'CURRENT' }, { mode: 'CURRENT', as_of: '2026-10-01T00:00:00.000Z' },
        { mode: 'AS_RECORDED', as_of: null }]) assert.throws(() => CategorisationViewFromJSON(wire));
    const pending = AuthRecoveryProgressFromJSON(auth.payloads.notification_pending);
    assert.deepEqual(AuthRecoveryProgressToJSON(pending), auth.payloads.notification_pending);
    assert.equal(pending.windowEndsAt, null);
    const wire = auth.payloads.notification_pending;
    assert.ok(wire);
    const missing = { ...wire };
    delete missing.window_ends_at;
    assert.throws(() => AuthRecoveryProgressFromJSON(missing));
    const nested = [CategorisationFromJSON(core.payloads.categorisation)];
    assert.deepEqual(nested.map(CategorisationToJSON), [core.payloads.categorisation]);
    assert.throws(() => CategorisationFromJSON({ ...core.payloads.categorisation, assigned_at: null }));
});

test('actual 201/202 public success union is status-discriminated and preserves exact wire bodies', async () => {
    const responses = [201, 202, 206];
    const api = new TransactionsApi(new Configuration({
        apiKey: 'DPoP synthetic.access.signature',
        fetchApi: async () => {
            const status = responses.shift();
            assert.ok(status);
            return new Response(JSON.stringify(status === 201 ? core.payloads.transaction : dedup.screen), {
                status, headers: { 'Content-Type': 'application/json' },
            });
        },
    }));
    const input = { dPoP: 'synthetic.fresh.signature', idempotencyKey: key,
        postTransactionRequest: PostTransactionRequestFromJSON(core.payloads.post_transaction) };
    const created = await api.postTransaction(input);
    if (created.status !== 201) assert.fail('expected declared creation case');
    const id: string = created.body.transactionId;
    assert.ok(id.startsWith('rec_'));
    const duplicate = await api.postTransaction(input);
    if (duplicate.status !== 202) assert.fail('expected declared decision case');
    assert.equal(duplicate.body.matchedRecordId, dedup.screen.matched_record_id);
    assert.equal(duplicate.body.outcome, 'duplicate_suspected');
    await assert.rejects(() => api.postTransaction(input));
    const emitted: unknown = PostTransactionRequestToJSON(input.postTransactionRequest);
    assert.ok(emitted && typeof emitted === 'object' && 'entries' in emitted && Array.isArray(emitted.entries));
    for (const entry of emitted.entries) {
        assert.ok(entry && typeof entry === 'object' && 'amount' in entry);
        assert.ok(entry.amount && typeof entry.amount === 'object' && 'amount' in entry.amount);
        assert.equal(typeof entry.amount.amount, 'string');
    }
    assert.doesNotThrow(() => JSON.stringify(emitted));
});

test('actual typed instant filter emits fixed UTC milliseconds and account DTO emits only declared fields', async () => {
    const captured: { url: string; body: unknown }[] = [];
    const configuration = new Configuration({
        apiKey: 'DPoP synthetic.access.signature',
        fetchApi: async (url, init) => {
            captured.push({ url: String(url), body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined });
            return new Response(JSON.stringify(init?.method === 'POST' ? core.payloads.account :
                { transactions: [], page: core.payloads.cursor_end }), {
                status: init?.method === 'POST' ? 201 : 200, headers: { 'Content-Type': 'application/json' },
            });
        },
    });
    await new TransactionsApi(configuration).listTransactions({
        dPoP: 'synthetic.query.signature', occurredFrom: Instant.parse('2026-10-01T00:00:00.000Z'),
    });
    await new AccountsApi(configuration).createAccount({
        dPoP: 'synthetic.create.signature', idempotencyKey: key,
        createAccountRequest: CreateAccountRequestFromJSON(core.payloads.create_account),
    });
    assert.equal(new URL(captured[0]!.url).searchParams.get('occurred_from'), '2026-10-01T00:00:00.000Z');
    assert.deepEqual(captured[1]!.body, core.payloads.create_account);
});

test('actual nullable categorisation transport and bootstrap per-call proof do not use static proof slots', async () => {
    const headers: Headers[] = [];
    const configuration = new Configuration({
        apiKey: 'SYNTHETIC_REUSED_PROOF',
        fetchApi: async (_url, init) => {
            headers.push(new Headers(init?.headers));
            return new Response(JSON.stringify(core.payloads.categorisation), { headers: { 'Content-Type': 'application/json' } });
        },
    });
    const value = await new CategoriesApi(configuration).getCategorisation({
        dPoP: 'synthetic.read.signature', transactionId: 'rec_00000000-0000-4000-8000-000000000004',
    });
    assert.deepEqual(CategorisationToJSON(value), core.payloads.categorisation);
    const bootstrap = new AuthApi(new Configuration({
        apiKey: 'SYNTHETIC_REUSED_PROOF',
        fetchApi: async (_url, init) => {
            headers.push(new Headers(init?.headers));
            return new Response(JSON.stringify({ enrollment_id: '00000000-0000-7000-8000-000000000021',
                expires_at: '2026-10-01T00:00:00.000Z' }), { status: 202, headers: { 'Content-Type': 'application/json' } });
        },
    }));
    const body = auth.payloads.enrollment;
    assert.ok(body);
    const { AuthEnrollmentRequestFromJSON } = await import('../../../build/generated/typescript/src/models/AuthEnrollmentRequest.js');
    await bootstrap.startEnrollment({ dPoP: 'synthetic.fresh.signature',
        authEnrollmentRequest: AuthEnrollmentRequestFromJSON(body) });
    assert.equal(headers.at(-1)!.get('DPoP'), 'synthetic.fresh.signature');
});
