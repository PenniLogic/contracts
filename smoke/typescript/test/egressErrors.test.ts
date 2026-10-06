import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';

import {
    AuthenticationProblemDetailFromJSON, AuthenticationProblemDetailToJSON,
    AuthenticationRequiredProblemDetailFromJSON, AuthenticationRequiredProblemDetailToJSON,
    EgressDeniedProblemDetailFromJSON, EgressDeniedProblemDetailToJSON,
    OperationProblemDetailFromJSON, OperationProblemDetailToJSON, ServiceProblemDetailFromJSON, ServiceProblemDetailToJSON,
    EgressDenialReasonFromJSON, ProblemCode, ProblemCodeFromJSON, Configuration, CustomDestinationsApi,
    CustomDestinationRegistrationRequestFromJSON,
} from '../../../build/generated/typescript/src/index.js';
import { authenticationPolicy, egressProblemCode, errorPolicy } from '../../../build/generated/typescript/src/errorCatalogue.js';
import { JSONApiResponse, ResponseError } from '../../../build/generated/typescript/src/runtime.js';
import { ROOT, loadFixture } from './fixtures.js';

interface Entry { code: string; status: number; title: string; detail: string }
interface Case { code: string; status: number; reason?: string; extra?: Record<string, unknown> }
interface Fixture {
    correlation_id: string;
    cases: Case[];
    authentication: Case[];
    safe_headers: Record<string, string>;
    nonce_headers: Record<string, string>;
    invalid: { name: string; base: string; set?: Record<string, unknown>; remove?: string[] }[];
    unknown_foreign_control: { code: string; contexts: string[]; forbidden: string[] };
}
const fixture = loadFixture<Fixture>('egress-errors.v1.json');
const catalogue: { codes: Entry[]; authentication_codes: Entry[] } =
    JSON.parse(readFileSync(join(ROOT, 'spec', 'error-catalogue.v1.json'), 'utf8'));
const entries = new Map([...catalogue.codes, ...catalogue.authentication_codes].map((entry) => [entry.code, entry]));
function wire(input: { code: string; reason?: string; extra?: Record<string, unknown> }): Record<string, unknown> {
    const entry = entries.get(input.code);
    assert.ok(entry);
    return {
        type: `urn:pennilogic:problem:${entry.code}`, title: entry.title, status: entry.status, detail: entry.detail,
        code: entry.code, correlation_id: fixture.correlation_id, ...input.extra,
        ...(input.reason === undefined ? {} : { egress_denial_reason: input.reason }),
    };
}

test('every egress reason uses the actual closed family and one global code through generated conversion', () => {
    for (const input of fixture.cases) {
        const value = wire(input);
        const reason = EgressDenialReasonFromJSON(input.reason);
        assert.equal(egressProblemCode(reason), ProblemCodeFromJSON(input.code));
        assert.equal(value.status, input.status);
        const specific = input.reason === 'step_up_required'
            ? AuthenticationProblemDetailToJSON(AuthenticationProblemDetailFromJSON(value))
            : EgressDeniedProblemDetailToJSON(EgressDeniedProblemDetailFromJSON(value));
        assert.deepEqual(specific, value);
        assert.deepEqual(OperationProblemDetailToJSON(OperationProblemDetailFromJSON(value)), value);
        assert.throws(() => ServiceProblemDetailFromJSON(value));
    }
    const original = loadFixture<{ examples: Array<{ code: string }> }>('error-provider.v1.json');
    for (const input of original.examples) {
        const value = { ...wire({ code: input.code }), ...input };
        assert.deepEqual(OperationProblemDetailToJSON(OperationProblemDetailFromJSON(value)), value);
    }
});

test('authentication remains outside service states and unrelated confirmation has no invented egress reason', () => {
    for (const input of fixture.authentication) {
        const value = wire(input);
        assert.deepEqual(AuthenticationProblemDetailToJSON(AuthenticationProblemDetailFromJSON(value)), value);
        const code = ProblemCodeFromJSON(input.code);
        assert.equal(authenticationPolicy(code).state, null);
        assert.throws(() => errorPolicy(code));
        if (input.status === 401) {
            assert.deepEqual(AuthenticationRequiredProblemDetailToJSON(AuthenticationRequiredProblemDetailFromJSON(value)), value);
        } else {
            assert.throws(() => AuthenticationRequiredProblemDetailFromJSON(value));
            assert.throws(() => OperationProblemDetailFromJSON(value));
        }
    }
});

test('positive service subset retains the actual global ProblemCode type and enforces family and fixed diagnostics', () => {
    const value = wire({ code: 'egress_denied' });
    const model = ServiceProblemDetailFromJSON(value);
    const code: ProblemCode = model.code;
    const excluded: typeof model.code = ProblemCode.AuthenticationRequired;
    assert.equal(code, ProblemCode.EgressDenied);
    assert.deepEqual(ServiceProblemDetailToJSON(model), value);
    assert.throws(() => ServiceProblemDetailToJSON({ ...model, code: excluded }));
    assert.throws(() => ServiceProblemDetailToJSON({ ...model, status: 401 }));
    assert.throws(() => ServiceProblemDetailToJSON({ ...model, detail: 'PRIVATE_SYNTHETIC_CANARY' }));
    assert.throws(() => OperationProblemDetailFromJSON(value));
    assert.throws(() => EgressDeniedProblemDetailFromJSON(value));
    for (const input of fixture.authentication) assert.throws(() => ServiceProblemDetailFromJSON(wire(input)));
});

test('shared error negatives and mutations fail before projection or serialization without echoing data', async () => {
    const originals = new Map(fixture.cases.map((input) => [input.reason, wire(input)]));
    for (const negative of fixture.invalid) {
        const original = originals.get(negative.base);
        assert.ok(original);
        const value = { ...structuredClone(original), ...negative.set };
        for (const member of negative.remove ?? []) delete value[member];
        await assert.rejects(new JSONApiResponse(new Response(JSON.stringify(value)), OperationProblemDetailFromJSON).value(),
            (error: unknown) => {
                assert.ok(error instanceof Error);
                assert.doesNotMatch(error.message, /PRIVATE_SYNTHETIC_CANARY/);
                return true;
            }, negative.name);
    }
    const model = OperationProblemDetailFromJSON(originals.get('destination_denied'));
    assert.throws(() => OperationProblemDetailToJSON({ ...model, detail: 'PRIVATE_SYNTHETIC_CANARY' }));
    assert.throws(() => OperationProblemDetailToJSON({ ...model, status: 401 }));
    assert.throws(() => OperationProblemDetailToJSON({ ...model, PRIVATE_SYNTHETIC_CANARY: undefined }));
});

async function actualFailure(value: Record<string, unknown>, status: number, headers: Record<string, string>): Promise<ResponseError> {
    let sends = 0;
    const api = new CustomDestinationsApi(new Configuration({
        basePath: 'https://api.pennilogic.example/v1', apiKey: 'DPoP synthetic.access.signature',
        fetchApi: async () => {
            sends += 1;
            return new Response(JSON.stringify(value), { status, headers });
        },
    }));
    const registration = loadFixture<{ registration: Record<string, unknown> }>('custom-destination-wire.v1.json').registration;
    try {
        await api.registerCustomDestination({
            dPoP: 'header.register.signature', stepUpToken: 'synthetic-step-up',
            idempotencyKey: '00000000-0000-4000-8000-000000000010',
            customDestinationRegistrationRequest: CustomDestinationRegistrationRequestFromJSON(registration),
        });
    } catch (error: unknown) {
        assert.ok(error instanceof ResponseError);
        assert.equal(sends, 1);
        return error;
    }
    throw new Error('synthetic non-2xx transport must throw ResponseError');
}

test('actual non-2xx transport exposes the response for explicit generated shared error decoding', async () => {
    for (const input of [...fixture.cases, ...fixture.authentication.filter((value) => value.status === 401)]) {
        const value = wire(input);
        const headers = { ...fixture.safe_headers, ...(input.status === 401 ? fixture.nonce_headers : {}) };
        if (value.retry_after_seconds !== undefined) headers['Retry-After'] = String(value.retry_after_seconds);
        const failure = await actualFailure(value, input.status, headers);
        const received: unknown = await failure.response.json();
        const model = input.status === 401
            ? AuthenticationRequiredProblemDetailFromJSON(received) : OperationProblemDetailFromJSON(received);
        assert.equal(model.status, failure.response.status);
        const serialized = input.status === 401
            ? AuthenticationRequiredProblemDetailToJSON(model) : OperationProblemDetailToJSON(model);
        assert.deepEqual(serialized, value);
        assert.equal(failure.response.headers.get('Content-Type'), 'application/problem+json');
        assert.equal(failure.response.headers.get('Cache-Control'), 'no-store');
        if (input.status === 401) {
            assert.equal(failure.response.headers.get('WWW-Authenticate'), 'DPoP error="use_dpop_nonce"');
            assert.equal(failure.response.headers.get('DPoP-Nonce'), 'synthetic-response-nonce');
        }
        if (value.retry_after_seconds !== undefined) {
            assert.equal(failure.response.headers.get('Retry-After'), String(value.retry_after_seconds));
        }
    }
});

test('actual wrong-family and private error bodies fail explicit generated error decoding safely', async () => {
    const first = fixture.cases[0];
    assert.ok(first);
    for (const [status, value] of [
        [403, { ...wire(first), host: 'PRIVATE_SYNTHETIC_CANARY' }],
        [401, wire(first)],
    ] satisfies Array<[number, Record<string, unknown>]>) {
        const failure = await actualFailure(value, status, fixture.safe_headers);
        const received: unknown = await failure.response.json();
        assert.throws(() => status === 401
            ? AuthenticationRequiredProblemDetailFromJSON(received) : OperationProblemDetailFromJSON(received),
        (error: unknown) => {
            assert.ok(error instanceof Error);
            assert.doesNotMatch(error.message, /PRIVATE_SYNTHETIC_CANARY/);
            return true;
        });
    }
});

test('unknown and foreign synthetic denial fixtures cannot disclose resource existence', () => {
    const value = wire(fixture.unknown_foreign_control);
    for (const _context of fixture.unknown_foreign_control.contexts) {
        assert.deepEqual(OperationProblemDetailToJSON(OperationProblemDetailFromJSON(value)), value);
    }
    for (const member of fixture.unknown_foreign_control.forbidden) {
        assert.throws(() => OperationProblemDetailFromJSON({ ...value, [member]: 'PRIVATE_SYNTHETIC_CANARY' }));
    }
});
