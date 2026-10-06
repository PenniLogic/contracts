import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { join } from 'node:path';
import { test } from 'node:test';
import { loadFixture } from './fixtures.js';
import {
    AiRefusalFromJSON, AiRefusalToJSON, ImportPreviewFromJSON,
} from '../../../build/generated/typescript/src/index.js';
import { BaseAPI, Configuration, JSONApiResponse } from '../../../build/generated/typescript/src/runtime.js';
import * as models from '../../../build/generated/typescript/src/index.js';

interface Fixture { refusal: Record<string, unknown> }
const refusal = loadFixture<Fixture>('error-provider.v1.json').refusal;

interface Specimen {
    schema: string; fixture: string; path: Array<string | number>;
    set?: Record<string, unknown>; empty_allowed?: boolean;
}
interface TransportFixture {
    models: Specimen[];
    array_controls: Specimen[];
    refusal_negatives: Array<{ name: string; set?: Record<string, unknown>; remove?: string[] }>;
}
const transport = loadFixture<TransportFixture>('provider-transport.v1.json');
interface Codec {
    decode: (value: unknown) => unknown;
    encode: (value: unknown) => unknown;
}
function codec<T>(decode: (value: unknown) => T, encode: (value: T) => unknown): Codec {
    return { decode, encode: (value: unknown): unknown => Reflect.apply(encode, undefined, [value]) };
}
const codecs: Readonly<Record<string, Codec>> = {
    AiRefusal: codec(models.AiRefusalFromJSON, models.AiRefusalToJSON),
    ServiceProblemDetail: codec(models.ServiceProblemDetailFromJSON, models.ServiceProblemDetailToJSON),
    ValidationIssue: codec(models.ValidationIssueFromJSON, models.ValidationIssueToJSON),
    Allowance: codec(models.AllowanceFromJSON, models.AllowanceToJSON),
    EntitlementDenial: codec(models.EntitlementDenialFromJSON, models.EntitlementDenialToJSON),
    DedupPrecedence: codec(models.DedupPrecedenceFromJSON, models.DedupPrecedenceToJSON),
    DuplicateLink: codec(models.DuplicateLinkFromJSON, models.DuplicateLinkToJSON),
    DedupEnrichment: codec(models.DedupEnrichmentFromJSON, models.DedupEnrichmentToJSON),
    DedupOutcome: codec(models.DedupOutcomeFromJSON, models.DedupOutcomeToJSON),
    ImportColumnBinding: codec(models.ImportColumnBindingFromJSON, models.ImportColumnBindingToJSON),
    ImportColumnMapping: codec(models.ImportColumnMappingFromJSON, models.ImportColumnMappingToJSON),
    ImportRowError: codec(models.ImportRowErrorFromJSON, models.ImportRowErrorToJSON),
    ImportPreviewRow: codec(models.ImportPreviewRowFromJSON, models.ImportPreviewRowToJSON),
    ImportPreviewCounts: codec(models.ImportPreviewCountsFromJSON, models.ImportPreviewCountsToJSON),
    ImportPreview: codec(models.ImportPreviewFromJSON, models.ImportPreviewToJSON),
    ImportCommitRequest: codec(models.ImportCommitRequestFromJSON, models.ImportCommitRequestToJSON),
    ImportCommitRow: codec(models.ImportCommitRowFromJSON, models.ImportCommitRowToJSON),
    ImportCommitCounts: codec(models.ImportCommitCountsFromJSON, models.ImportCommitCountsToJSON),
    ImportCommitResult: codec(models.ImportCommitResultFromJSON, models.ImportCommitResultToJSON),
    CustomDestinationRegistrationRequest: codec(models.CustomDestinationRegistrationRequestFromJSON, models.CustomDestinationRegistrationRequestToJSON),
    CustomDestinationLifecycleRequest: codec(models.CustomDestinationLifecycleRequestFromJSON, models.CustomDestinationLifecycleRequestToJSON),
    CustomDestination: codec(models.CustomDestinationFromJSON, models.CustomDestinationToJSON),
    CustomDestinationValidationResult: codec(models.CustomDestinationValidationResultFromJSON, models.CustomDestinationValidationResultToJSON),
    CustomDestinationModel: codec(models.CustomDestinationModelFromJSON, models.CustomDestinationModelToJSON),
    CustomDestinationList: codec(models.CustomDestinationListFromJSON, models.CustomDestinationListToJSON),
    EgressDeniedProblemDetail: codec(models.EgressDeniedProblemDetailFromJSON, models.EgressDeniedProblemDetailToJSON),
    AuthenticationProblemDetail: codec(models.AuthenticationProblemDetailFromJSON, models.AuthenticationProblemDetailToJSON),
    AuthenticationRequiredProblemDetail: codec(models.AuthenticationRequiredProblemDetailFromJSON, models.AuthenticationRequiredProblemDetailToJSON),
    OperationProblemDetail: codec(models.OperationProblemDetailFromJSON, models.OperationProblemDetailToJSON),
    ApplicationProblemDetail: codec(models.ApplicationProblemDetailFromJSON, models.ApplicationProblemDetailToJSON),
    AuthenticationChallengeProblemDetail: codec(models.AuthenticationChallengeProblemDetailFromJSON, models.AuthenticationChallengeProblemDetailToJSON),
    AuthenticationContextProblemDetail: codec(models.AuthenticationContextProblemDetailFromJSON, models.AuthenticationContextProblemDetailToJSON),
    AuthPublicKey: codec(models.AuthPublicKeyFromJSON, models.AuthPublicKeyToJSON),
    AuthDeviceInput: codec(models.AuthDeviceInputFromJSON, models.AuthDeviceInputToJSON),
    AuthEnrollmentRequest: codec(models.AuthEnrollmentRequestFromJSON, models.AuthEnrollmentRequestToJSON),
    AuthEnrollmentAccepted: codec(models.AuthEnrollmentAcceptedFromJSON, models.AuthEnrollmentAcceptedToJSON),
    AuthChannelProofRequest: codec(models.AuthChannelProofRequestFromJSON, models.AuthChannelProofRequestToJSON),
    AuthEnrollmentVerified: codec(models.AuthEnrollmentVerifiedFromJSON, models.AuthEnrollmentVerifiedToJSON),
    AuthCredentialDescriptor: codec(models.AuthCredentialDescriptorFromJSON, models.AuthCredentialDescriptorToJSON),
    AuthCredentialAlgorithm: codec(models.AuthCredentialAlgorithmFromJSON, models.AuthCredentialAlgorithmToJSON),
    AuthRelyingParty: codec(models.AuthRelyingPartyFromJSON, models.AuthRelyingPartyToJSON),
    AuthPasskeyUser: codec(models.AuthPasskeyUserFromJSON, models.AuthPasskeyUserToJSON),
    AuthAuthenticatorSelection: codec(models.AuthAuthenticatorSelectionFromJSON, models.AuthAuthenticatorSelectionToJSON),
    AuthCreationExtensions: codec(models.AuthCreationExtensionsFromJSON, models.AuthCreationExtensionsToJSON),
    AuthCreationOptions: codec(models.AuthCreationOptionsFromJSON, models.AuthCreationOptionsToJSON),
    AuthRegistrationOptionsRequest: codec(models.AuthRegistrationOptionsRequestFromJSON, models.AuthRegistrationOptionsRequestToJSON),
    AuthAttestationResponse: codec(models.AuthAttestationResponseFromJSON, models.AuthAttestationResponseToJSON),
    AuthRegistrationResultRequest: codec(models.AuthRegistrationResultRequestFromJSON, models.AuthRegistrationResultRequestToJSON),
    AuthAuthenticationOptionsRequest: codec(models.AuthAuthenticationOptionsRequestFromJSON, models.AuthAuthenticationOptionsRequestToJSON),
    AuthAssertionOptions: codec(models.AuthAssertionOptionsFromJSON, models.AuthAssertionOptionsToJSON),
    AuthAssertionResponse: codec(models.AuthAssertionResponseFromJSON, models.AuthAssertionResponseToJSON),
    AuthAssertionResultRequest: codec(models.AuthAssertionResultRequestFromJSON, models.AuthAssertionResultRequestToJSON),
    AuthTokenSet: codec(models.AuthTokenSetFromJSON, models.AuthTokenSetToJSON),
    AuthBrowserTokenSet: codec(models.AuthBrowserTokenSetFromJSON, models.AuthBrowserTokenSetToJSON),
    AuthRefreshRequest: codec(models.AuthRefreshRequestFromJSON, models.AuthRefreshRequestToJSON),
    AuthBrowserRefreshRequest: codec(models.AuthBrowserRefreshRequestFromJSON, models.AuthBrowserRefreshRequestToJSON),
    AuthProfile: codec(models.AuthProfileFromJSON, models.AuthProfileToJSON),
    AuthCredential: codec(models.AuthCredentialFromJSON, models.AuthCredentialToJSON),
    AuthCredentialList: codec(models.AuthCredentialListFromJSON, models.AuthCredentialListToJSON),
    AuthCredentialNameRequest: codec(models.AuthCredentialNameRequestFromJSON, models.AuthCredentialNameRequestToJSON),
    AuthSession: codec(models.AuthSessionFromJSON, models.AuthSessionToJSON),
    AuthSessionList: codec(models.AuthSessionListFromJSON, models.AuthSessionListToJSON),
    AuthDevice: codec(models.AuthDeviceFromJSON, models.AuthDeviceToJSON),
    AuthDeviceList: codec(models.AuthDeviceListFromJSON, models.AuthDeviceListToJSON),
    AuthChannelInput: codec(models.AuthChannelInputFromJSON, models.AuthChannelInputToJSON),
    AuthChannel: codec(models.AuthChannelFromJSON, models.AuthChannelToJSON),
    AuthChannelList: codec(models.AuthChannelListFromJSON, models.AuthChannelListToJSON),
    AuthRecoveryCodeCount: codec(models.AuthRecoveryCodeCountFromJSON, models.AuthRecoveryCodeCountToJSON),
    AuthRecoveryCodeSet: codec(models.AuthRecoveryCodeSetFromJSON, models.AuthRecoveryCodeSetToJSON),
    AuthRecoveryStartRequest: codec(models.AuthRecoveryStartRequestFromJSON, models.AuthRecoveryStartRequestToJSON),
    AuthRecoveryAccepted: codec(models.AuthRecoveryAcceptedFromJSON, models.AuthRecoveryAcceptedToJSON),
    AuthRecoveryVerifyRequest: codec(models.AuthRecoveryVerifyRequestFromJSON, models.AuthRecoveryVerifyRequestToJSON),
    AuthRecoveryProgress: codec(models.AuthRecoveryProgressFromJSON, models.AuthRecoveryProgressToJSON),
    AuthRecoveryActionRequest: codec(models.AuthRecoveryActionRequestFromJSON, models.AuthRecoveryActionRequestToJSON),
    AuthRecoveryCredentialRequest: codec(models.AuthRecoveryCredentialRequestFromJSON, models.AuthRecoveryCredentialRequestToJSON),
    AuthRegistrationGrant: codec(models.AuthRegistrationGrantFromJSON, models.AuthRegistrationGrantToJSON),
    AuthDeviceChallenge: codec(models.AuthDeviceChallengeFromJSON, models.AuthDeviceChallengeToJSON),
    AuthDeviceRegistrationRequest: codec(models.AuthDeviceRegistrationRequestFromJSON, models.AuthDeviceRegistrationRequestToJSON),
    AuthRecoveryCompletion: codec(models.AuthRecoveryCompletionFromJSON, models.AuthRecoveryCompletionToJSON),
    AuthStepUpIntentTarget: codec(models.AuthStepUpIntentTargetFromJSON, models.AuthStepUpIntentTargetToJSON),
    AuthStepUpIntentBody: codec(models.AuthStepUpIntentBodyFromJSON, models.AuthStepUpIntentBodyToJSON),
    AuthStepUpIntent: codec(models.AuthStepUpIntentFromJSON, models.AuthStepUpIntentToJSON),
    AuthStepUpResultRequest: codec(models.AuthStepUpResultRequestFromJSON, models.AuthStepUpResultRequestToJSON),
    AuthStepUpGrant: codec(models.AuthStepUpGrantFromJSON, models.AuthStepUpGrantToJSON),
    SessionRevokedProblemDetail: codec(models.SessionRevokedProblemDetailFromJSON, models.SessionRevokedProblemDetailToJSON),
    CursorPage: codec(models.CursorPageFromJSON, models.CursorPageToJSON),
    CreateAccountRequest: codec(models.CreateAccountRequestFromJSON, models.CreateAccountRequestToJSON),
    UpdateAccountRequest: codec(models.UpdateAccountRequestFromJSON, models.UpdateAccountRequestToJSON),
    Account: codec(models.AccountFromJSON, models.AccountToJSON),
    AccountPage: codec(models.AccountPageFromJSON, models.AccountPageToJSON),
    OpeningBalanceRequest: codec(models.OpeningBalanceRequestFromJSON, models.OpeningBalanceRequestToJSON),
    LedgerEntryInput: codec(models.LedgerEntryInputFromJSON, models.LedgerEntryInputToJSON),
    LedgerEntry: codec(models.LedgerEntryFromJSON, models.LedgerEntryToJSON),
    PostTransactionRequest: codec(models.PostTransactionRequestFromJSON, models.PostTransactionRequestToJSON),
    Transaction: codec(models.TransactionFromJSON, models.TransactionToJSON),
    TransactionPage: codec(models.TransactionPageFromJSON, models.TransactionPageToJSON),
    ReverseTransactionRequest: codec(models.ReverseTransactionRequestFromJSON, models.ReverseTransactionRequestToJSON),
    CorrectTransactionRequest: codec(models.CorrectTransactionRequestFromJSON, models.CorrectTransactionRequestToJSON),
    TransactionCorrection: codec(models.TransactionCorrectionFromJSON, models.TransactionCorrectionToJSON),
    CategorisationView: codec(models.CategorisationViewFromJSON, models.CategorisationViewToJSON),
    CategoryAllocationLine: codec(models.CategoryAllocationLineFromJSON, models.CategoryAllocationLineToJSON),
    CategoryExactLineInput: codec(models.CategoryExactLineInputFromJSON, models.CategoryExactLineInputToJSON),
    CategoryWeightedLineInput: codec(models.CategoryWeightedLineInputFromJSON, models.CategoryWeightedLineInputToJSON),
    CategoryEntryAssignmentInput: codec(models.CategoryEntryAssignmentInputFromJSON, models.CategoryEntryAssignmentInputToJSON),
    CategoryAssignmentRequest: codec(models.CategoryAssignmentRequestFromJSON, models.CategoryAssignmentRequestToJSON),
    Categorisation: codec(models.CategorisationFromJSON, models.CategorisationToJSON),
    CreateCategoryRequest: codec(models.CreateCategoryRequestFromJSON, models.CreateCategoryRequestToJSON),
    UpdateCategoryRequest: codec(models.UpdateCategoryRequestFromJSON, models.UpdateCategoryRequestToJSON),
    Category: codec(models.CategoryFromJSON, models.CategoryToJSON),
    CategoryPage: codec(models.CategoryPageFromJSON, models.CategoryPageToJSON),
    RedirectCategoryRequest: codec(models.RedirectCategoryRequestFromJSON, models.RedirectCategoryRequestToJSON),
    CategoryRedirect: codec(models.CategoryRedirectFromJSON, models.CategoryRedirectToJSON),
};
function sample(entry: Specimen): Record<string, unknown> {
    let value: unknown = loadFixture<unknown>(entry.fixture);
    for (const key of entry.path) {
        if (typeof key === 'number') {
            assert.ok(Array.isArray(value)); value = value[key];
        } else {
            assert.ok(value !== null && typeof value === 'object');
            value = Object.fromEntries(Object.entries(value))[key];
        }
    }
    assert.ok(value !== null && typeof value === 'object' && !Array.isArray(value));
    return { ...Object.fromEntries(Object.entries(value)), ...entry.set };
}
function* quotedPrimitives(value: unknown): Generator<unknown> {
    if (Array.isArray(value)) {
        for (const [index, child] of value.entries()) for (const replacement of quotedPrimitives(child)) {
            const result = structuredClone(value); result[index] = replacement; yield result;
        }
    } else if (value !== null && typeof value === 'object') {
        for (const [key, child] of Object.entries(value)) for (const replacement of quotedPrimitives(child)) {
            yield { ...value, [key]: replacement };
        }
    } else if (typeof value === 'number' || typeof value === 'boolean') yield JSON.stringify(value);
}
function* quotedNativePrimitives(value: unknown): Generator<unknown> {
    if (value instanceof Set) {
        const entries = [...value];
        for (const [index, child] of entries.entries()) for (const replacement of quotedNativePrimitives(child)) {
            const result = [...entries]; result[index] = replacement; yield new Set(result);
        }
    } else if (Array.isArray(value)) {
        for (const [index, child] of value.entries()) for (const replacement of quotedNativePrimitives(child)) {
            const result = [...value]; result[index] = replacement; yield result;
        }
    } else if (value !== null && typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype) {
        for (const [key, child] of Object.entries(value)) for (const replacement of quotedNativePrimitives(child)) {
            yield { ...value, [key]: replacement };
        }
    } else if (typeof value === 'number' || typeof value === 'boolean') yield JSON.stringify(value);
}
function safe(error: unknown): boolean {
    assert.ok(error instanceof Error);
    assert.doesNotMatch(error.message, /PRIVATE_SYNTHETIC_CANARY/);
    return true;
}

class RequestProbe extends BaseAPI {
    send(body: unknown): Promise<Response> {
        return this.request({
            path: '/synthetic-provider', method: 'POST',
            headers: { 'Content-Type': 'application/json' }, body,
        });
    }
}

function* invalidArrays(value: unknown): Generator<unknown> {
    if (value instanceof Set) {
        yield new Set([...value, undefined]);
        yield new Set([...value, null]);
        yield new Set([...value, {}]);
        for (const [index, child] of [...value].entries()) for (const replacement of invalidArrays(child)) {
            const items = [...value]; items[index] = replacement; yield new Set(items);
        }
    } else if (Array.isArray(value)) {
        yield [...value, undefined];
        yield [...value, null];
        const sparse = value.slice(); sparse.length += 1; yield sparse;
        for (const [index, child] of value.entries()) for (const replacement of invalidArrays(child)) {
            const items = value.slice(); items[index] = replacement; yield items;
        }
    } else if (value !== null && typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype) {
        for (const [key, child] of Object.entries(value)) for (const replacement of invalidArrays(child)) {
            yield { ...value, [key]: replacement };
        }
    }
}

test('all eight undefined and sparse model arrays fail before actual generated BaseAPI request emission', async () => {
    const emitted: unknown[] = [];
    const schemaCases: Array<{ name: string; schema: string; wire: unknown; expected: boolean }> = [];
    const api = new RequestProbe(new Configuration({
        basePath: 'https://api.pennilogic.example/v1',
        fetchApi: async (_url, init) => {
            assert.equal(typeof init?.body, 'string');
            emitted.push(JSON.parse(String(init?.body)));
            return new Response(null, { status: 204 });
        },
    }));
    const outcomes: Array<{ schema: string; corruption: string; rejected: boolean; emitted: boolean }> = [];
    for (const [schema, field] of [
        ['ImportPreview', 'rows'], ['ImportColumnMapping', 'detectedColumns'],
        ['DedupOutcome', 'enrichment'], ['ImportCommitResult', 'rows'],
    ]) {
        const entry = transport.models.find((item) => item.schema === schema);
        assert.ok(entry && schema && field);
        const transform = codecs[schema]; assert.ok(transform);
        const wire = sample(entry), model = transform.decode(wire);
        assert.ok(model !== null && typeof model === 'object');
        const members: Record<string, unknown> = Object.fromEntries(Object.entries(model));
        const values = members[field]; assert.ok(Array.isArray(values));
        await api.send(transform.encode(model));
        const positive = emitted.pop();
        assert.deepEqual(positive, wire);
        schemaCases.push({ name: schema + ' control', schema, wire: positive, expected: true });
        for (const corruption of ['undefined', 'sparse']) {
            const items = values.slice();
            if (corruption === 'undefined') items.push(undefined);
            else items.length += 1;
            const before = emitted.length;
            let rejected = false;
            try {
                await api.send(transform.encode({ ...members, [field]: items }));
            } catch (error: unknown) {
                safe(error); rejected = true;
            }
            if (emitted.length !== before) {
                schemaCases.push({ name: schema + ' ' + corruption, schema, wire: emitted.at(-1), expected: false });
                assert.throws(() => transform.decode(emitted.at(-1)), safe);
            }
            outcomes.push({ schema, corruption, rejected, emitted: emitted.length !== before });
        }
    }
    const root = process.env.PL_CONTRACTS_ROOT; assert.ok(root);
    const checked = spawnSync(process.execPath, [
        join(root, 'scripts', 'tests', 'provider_schema.cjs'), join(root, 'spec', 'openapi.yaml'),
    ], { input: JSON.stringify(schemaCases), encoding: 'utf8' });
    assert.equal(checked.status, 0, checked.stderr);
    const results: Array<{ name: string; valid: boolean }> = JSON.parse(checked.stdout);
    assert.deepEqual(results.map((entry) => entry.valid), schemaCases.map((entry) => entry.expected));
    assert.equal(outcomes.length, 8);
    assert.deepEqual(outcomes, outcomes.map((outcome) => ({ ...outcome, rejected: true, emitted: false })));
    assert.equal(emitted.length, 0);
});

test('every nested marked-provider model and primitive array rejects absent and null entries before write', () => {
    let cases = 0;
    let legacyCases = 0;
    const legacy = new Set(transport.models.slice(0, 29).map((entry) => entry.schema));
    for (const entry of [...transport.models, ...transport.array_controls]) {
        const transform = codecs[entry.schema]; assert.ok(transform);
        const model = transform.decode(sample(entry));
        for (const invalid of invalidArrays(model)) {
            assert.throws(() => transform.encode(invalid), safe, entry.schema);
            cases += 1;
            if (legacy.has(entry.schema)) legacyCases += 1;
        }
    }
    assert.equal(legacyCases, 45);
    assert.equal(cases, 129);
});

test('all marked roots reject null in ordinary conversion and generated response transport', async () => {
    assert.equal(transport.models.length, 112);
    for (const entry of transport.models) {
        const transform = codecs[entry.schema]; assert.ok(transform);
        assert.throws(() => transform.decode(null), safe, entry.schema);
        assert.throws(() => transform.encode(null), safe, entry.schema);
        await assert.rejects(new JSONApiResponse(new Response('null'), transform.decode).value(), safe);
    }
    assert.equal(models.ProblemDetailFromJSON(null), null);
});

test('optional undefined properties still omit normally while null and unknown members stay strict', () => {
    const clear = loadFixture<{ clear: Record<string, unknown> }>('import-provider.v1.json').clear;
    const model = models.DedupOutcomeFromJSON(clear);
    assert.deepEqual(JSON.parse(JSON.stringify(models.DedupOutcomeToJSON({
        ...model, matchedRecordId: undefined, enrichment: undefined, precedence: undefined,
    }))), clear);
    assert.throws(() => models.DedupOutcomeToJSON({ ...model, enrichment: null }), safe);
    assert.throws(() => models.DedupOutcomeToJSON({ ...model, provider_detail: 'PRIVATE_SYNTHETIC_CANARY' }), safe);
    const legacy = models.ProblemDetailFromJSON({
        type: 'about:blank', title: 'Legacy', status: 422, code: 'legacy', correlation_id: 'legacy',
        additive_member: true,
    });
    assert.equal(legacy.title, 'Legacy');
});

test('successful refusal rejects original unknown members in generated conversion and transport', async () => {
    const invalid = { ...refusal, provider_detail: 'PRIVATE_SYNTHETIC_CANARY' };
    assert.throws(() => AiRefusalFromJSON(invalid));
    await assert.rejects(new JSONApiResponse(new Response(JSON.stringify(invalid)), AiRefusalFromJSON).value());
});

test('successful refusal rejects bad correlation and unsafe constants before conversion', () => {
    for (const invalid of [
        { ...refusal, correlation_id: 'PRIVATE_SYNTHETIC_CANARY' },
        { ...refusal, message: 'PRIVATE_SYNTHETIC_CANARY' },
        { ...refusal, code: 'PRIVATE_SYNTHETIC_CANARY' },
    ]) {
        assert.throws(() => AiRefusalFromJSON(invalid), (error: unknown) => {
            assert.ok(error instanceof Error);
            assert.doesNotMatch(error.message, /PRIVATE_SYNTHETIC_CANARY/);
            return true;
        });
    }
    const model = AiRefusalFromJSON(refusal);
    assert.throws(() => AiRefusalToJSON({ ...model, correlationId: 'PRIVATE_SYNTHETIC_CANARY' }));
});

test('ordinary generated import conversion rejects nested closed-provider extras', () => {
    const data = loadFixture<{ preview: { mapping: Record<string, unknown> } }>('import-provider.v1.json');
    const wire = structuredClone(data.preview);
    wire.mapping.provider_detail = 'PRIVATE_SYNTHETIC_CANARY';
    assert.throws(() => ImportPreviewFromJSON(wire));
});

test('every closed provider rejects original extras through ordinary conversion, write and transport', async () => {
    assert.equal(transport.models.slice(0, 29).length, 29);
    assert.equal(transport.models.length, 112);
    assert.deepEqual(Object.keys(codecs).sort(), transport.models.map((item) => item.schema).sort());
    for (const entry of transport.models) {
        const transform = codecs[entry.schema];
        assert.ok(transform);
        const wire = sample(entry), model = transform.decode(wire);
        assert.deepEqual(JSON.parse(JSON.stringify(transform.encode(model))), wire, entry.schema);
        await new JSONApiResponse(new Response(JSON.stringify(wire)), transform.decode).value();
        for (const key of ['provider_detail', 'PRIVATE_SYNTHETIC_CANARY']) {
            const invalid = { ...wire, [key]: 'PRIVATE_SYNTHETIC_CANARY' };
            assert.throws(() => transform.decode(invalid), safe, entry.schema);
            await assert.rejects(new JSONApiResponse(new Response(JSON.stringify(invalid)), transform.decode).value(), safe);
            assert.ok(model !== null && typeof model === 'object');
            assert.throws(() => transform.encode({ ...model, [key]: 'PRIVATE_SYNTHETIC_CANARY' }), safe, entry.schema);
        }
        const missing = { ...wire };
        delete missing[Object.keys(wire)[0]!];
        if (entry.empty_allowed) {
            assert.deepEqual(transform.encode(transform.decode(missing)), missing);
            await new JSONApiResponse(new Response(JSON.stringify(missing)), transform.decode).value();
        } else {
            assert.throws(() => transform.decode(missing), safe, entry.schema);
            await assert.rejects(new JSONApiResponse(new Response(JSON.stringify(missing)), transform.decode).value(), safe);
        }
        for (const invalid of quotedNativePrimitives(model)) assert.throws(() => transform.encode(invalid), safe, entry.schema);
    }
});

test('every imported integer and boolean leaf rejects quoted wire types in ordinary conversion and transport', async () => {
    let cases = 0;
    for (const entry of transport.models) {
        const transform = codecs[entry.schema];
        assert.ok(transform);
        for (const wire of quotedPrimitives(sample(entry))) {
            assert.throws(() => transform.decode(wire), safe, entry.schema);
            await assert.rejects(new JSONApiResponse(new Response(JSON.stringify(wire)), transform.decode).value(), safe);
            cases += 1;
        }
    }
    assert.ok(cases >= 50);
});

test('every successful-refusal negative is rejected on ordinary ingress and egress without diagnostic content', async () => {
    for (const negative of transport.refusal_negatives) {
        const invalid = { ...refusal, ...negative.set };
        for (const key of negative.remove ?? []) delete invalid[key];
        assert.throws(() => AiRefusalFromJSON(invalid), safe, negative.name);
        await assert.rejects(new JSONApiResponse(new Response(JSON.stringify(invalid)), AiRefusalFromJSON).value(), safe);
    }
    const normal = AiRefusalFromJSON(refusal);
    for (const value of [
        { ...normal, correlationId: 'PRIVATE_SYNTHETIC_CANARY' },
        { ...normal, provider_detail: 'PRIVATE_SYNTHETIC_CANARY' },
        { ...normal, message: 'PRIVATE_SYNTHETIC_CANARY' },
    ]) assert.throws(() => Reflect.apply(AiRefusalToJSON, undefined, [value]), safe);
    const hidden = { ...normal };
    Object.defineProperty(hidden, 'provider_detail', { value: 'PRIVATE_SYNTHETIC_CANARY', enumerable: false });
    assert.throws(() => AiRefusalToJSON(hidden), safe);
});

test('actual generated mapping bounds accept 256 columns and reject 257 without narrowing legacy DTOs', async () => {
    const value = loadFixture<{ preview: { mapping: Record<string, unknown> } }>('import-provider.v1.json');
    const wire = structuredClone(value.preview);
    wire.mapping.column_count = 256;
    wire.mapping.unmapped_columns = Array.from({ length: 253 }, (_, index) => index + 4);
    const model = models.ImportPreviewFromJSON(wire);
    assert.equal(model.mapping.columnCount, 256);
    assert.deepEqual(JSON.parse(JSON.stringify(models.ImportPreviewToJSON(model))), wire);
    wire.mapping.column_count = 257;
    assert.throws(() => models.ImportPreviewFromJSON(wire), safe);
    await assert.rejects(new JSONApiResponse(new Response(JSON.stringify(wire)), models.ImportPreviewFromJSON).value(), safe);
});
