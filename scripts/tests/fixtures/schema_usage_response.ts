import assert from 'node:assert/strict';
import {
    Configuration, RequestUsageApi, UsageReceiptToJSON, UsageReceiptToJSONTyped,
    type UsageReceiptWire,
} from './generated/typescript/src/index.js';

const requests: unknown[] = [];
const api = new RequestUsageApi(new Configuration({
    apiKey: 'DPoP synthetic.access.signature',
    fetchApi: async (_url, init) => {
        if (init?.method === 'POST') {
            assert.equal(typeof init.body, 'string');
            requests.push(JSON.parse(String(init.body)));
            return new Response(null, { status: 204 });
        }
        return new Response(JSON.stringify({ result: 'synthetic-response-owned' }), {
            headers: { 'Content-Type': 'application/json' },
        });
    },
}));
const received = await api.readUsage();
const wire: UsageReceiptWire = UsageReceiptToJSON(received);
const typed: UsageReceiptWire = UsageReceiptToJSONTyped(received, false);
const result: string = wire.result;
assert.equal(result, 'synthetic-response-owned');
assert.deepEqual(typed, wire);
await api.postUsage({
    idempotencyKey: '00000000-0000-4000-8000-000000000010',
    requestBody: { caption: received.result, examples: 'ordinary map key', additionalProperties: 'ordinary map key' },
});
assert.deepEqual(requests, [{
    caption: 'synthetic-response-owned', examples: 'ordinary map key', additionalProperties: 'ordinary map key',
}]);
console.log('genuine response-only readonly receipt and unrelated typed request map compile/transport PASS');
