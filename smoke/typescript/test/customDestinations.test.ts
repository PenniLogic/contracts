import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';

import {
    Configuration, CredentialHeader, CustomDestinationFromJSON, CustomDestinationLifecycleRequestFromJSON,
    CustomDestinationRegistrationRequestFromJSON, CustomDestinationRegistrationRequestToJSON,
    CustomDestinationsApi, CustomDestinationState, DestinationClass, EgressDenialReason,
} from '../../../build/generated/typescript/src/index.js';
import { ROOT, loadFixture } from './fixtures.js';

interface CustomDestinationFixture {
    registration: Record<string, unknown>;
    lifecycle: { version: string };
    destination: Record<string, unknown>;
    invalid_registration: { name: string; add: Record<string, unknown> }[];
    invalid_lifecycle: { name: string; wire: Record<string, unknown> }[];
}

const fixture = loadFixture<CustomDestinationFixture>('custom-destination-wire.v1.json');

test('custom destination generated enums equal every canonical value', () => {
    const source = JSON.parse(readFileSync(join(ROOT, 'spec', 'adr022', 'ai-egress-consequences.json'), 'utf8')) as {
        enums: Record<string, string[]>;
    };
    for (const [name, values] of Object.entries({ DestinationClass, CredentialHeader, CustomDestinationState, EgressDenialReason })) {
        assert.deepEqual(Object.values(values), source.enums[name], name);
    }
});

test('six generated transports send raw DPoP auth once, correct proof and step-up headers, and core-only routes', async () => {
    const captured: { url: string; method: string; headers: Headers; body: unknown }[] = [];
    const authorization = 'DPoP synthetic.access.signature';
    const api = new CustomDestinationsApi(new Configuration({
        basePath: 'https://api.pennilogic.example/v1',
        apiKey: authorization,
        fetchApi: async (url, init) => {
            assert.equal(typeof url, 'string');
            const address = String(url);
            captured.push({
                url: address, method: init?.method ?? 'GET', headers: new Headers(init?.headers),
                body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
            });
            const response = address.includes('/validate') ? {
                destinationId: fixture.destination.destinationId, version: '1', validated: true,
                validatedAt: '2026-10-05T00:00:00.000Z',
            } : init?.method === 'GET' ? { destinations: [fixture.destination] } : fixture.destination;
            return new Response(JSON.stringify(response), {
                status: address.endsWith('/custom-destinations') && init?.method === 'POST' ? 201 : 200,
                headers: { 'Content-Type': 'application/json' },
            });
        },
    }));
    const registration = CustomDestinationRegistrationRequestFromJSON(fixture.registration);
    const id = fixture.destination.destinationId;
    assert.equal(typeof id, 'string');
    if (typeof id !== 'string') throw new Error('synthetic destination fixture must have an ID');
    const lifecycle = {
        idempotencyKey: '00000000-0000-4000-8000-000000000010',
        destinationId: id, customDestinationLifecycleRequest: fixture.lifecycle,
    };
    await api.registerCustomDestination({
        dPoP: 'header.register.signature', stepUpToken: 'synthetic-step-up',
        idempotencyKey: lifecycle.idempotencyKey, customDestinationRegistrationRequest: registration,
    });
    await api.listCustomDestinations({ dPoP: 'header.list.signature' });
    await api.validateCustomDestination({ ...lifecycle, dPoP: 'header.validate.signature' });
    await api.activateCustomDestination({ ...lifecycle, dPoP: 'header.activate.signature', stepUpToken: 'synthetic-step-up' });
    await api.suspendCustomDestination({ ...lifecycle, dPoP: 'header.suspend.signature' });
    await api.revokeCustomDestination({ ...lifecycle, dPoP: 'header.revoke.signature' });
    const expectedCalls = [
        ['POST', '', 'register'], ['GET', '', 'list'],
        ['POST', `/${id}/validate`, 'validate'], ['POST', `/${id}/activate`, 'activate'],
        ['POST', `/${id}/suspend`, 'suspend'], ['DELETE', `/${id}`, 'revoke'],
    ];
    assert.equal(captured.length, 6);
    for (const [index, request] of captured.entries()) {
        const expected = expectedCalls[index];
        assert.ok(expected);
        assert.equal(request.method, expected[0]);
        assert.equal(new URL(request.url).pathname, `/v1/ai/custom-destinations${expected[1]}`);
        assert.equal(request.headers.get('DPoP'), `header.${expected[2]}.signature`);
        assert.equal(request.headers.get('Authorization'), authorization);
        assert.ok(!request.headers.get('Authorization')?.startsWith('Bearer '));
        assert.ok(!request.headers.get('Authorization')?.startsWith('DPoP DPoP '));
        assert.equal(request.headers.has('Step-Up-Token'), index === 0 || index === 3);
        assert.equal(request.headers.has('Idempotency-Key'), index !== 1);
        assert.equal(request.headers.has('DPoP-Nonce'), false);
        assert.ok(request.url.startsWith('https://api.pennilogic.example/v1/ai/custom-destinations'));
    }
    assert.equal(new Set(captured.map((request) => request.headers.get('DPoP'))).size, 6);
    const [registrationCall, , , activationCall, , revocationCall] = captured;
    assert.ok(registrationCall && activationCall && revocationCall);
    assert.equal(revocationCall.method, 'DELETE');
    assert.equal(revocationCall.url, `https://api.pennilogic.example/v1/ai/custom-destinations/${id}`);
    assert.deepEqual(registrationCall.body, fixture.registration);
    assert.deepEqual(activationCall.body, fixture.lifecycle);
});

test('custom destination valid generated request round-trips without default insertion', () => {
    const minimal = { host: 'models.pennilogic.example', credentialHeader: 'authorization_bearer', models: ['fixture-model'] };
    assert.deepEqual(JSON.parse(JSON.stringify(CustomDestinationRegistrationRequestToJSON(
        CustomDestinationRegistrationRequestFromJSON(minimal),
    ))), minimal);
});

for (const vector of fixture.invalid_registration) {
    test(`T6 generated request decoder rejects ${vector.name}`, () => {
        CustomDestinationRegistrationRequestFromJSON(fixture.registration);
        assert.throws(() => CustomDestinationRegistrationRequestFromJSON({ ...fixture.registration, ...vector.add }));
    });
}

for (const vector of fixture.invalid_lifecycle) {
    test(`T6 generated lifecycle decoder rejects ${vector.name}`, () => {
        CustomDestinationLifecycleRequestFromJSON(fixture.lifecycle);
        assert.throws(() => CustomDestinationLifecycleRequestFromJSON(vector.wire));
    });
}

test('T6 generated nested model decoder rejects address fields in a closed response', () => {
    CustomDestinationFromJSON(fixture.destination);
    assert.throws(() => CustomDestinationFromJSON({
        ...fixture.destination, models: [{
            custom_model_id: '00000000-0000-4000-8000-000000000003', name: 'fixture-model', host: 'blocked.example',
        }],
    }));
});
