// Strict T-CON-12 seam; the scaffold ProblemDetail remains unchanged.
import { errorPolicy, errorStatus } from '../errorCatalogue.js';
import { providerObject, type ProviderField } from '../providerGuard.js';
import { Instant } from './Instant.js';
import { ProblemCode, ProblemCodeFromJSON } from './ProblemCode.js';
import { ProblemField, ProblemFieldFromJSON } from './ProblemField.js';
import { ValidationReason, ValidationReasonFromJSON } from './ValidationReason.js';
import { AllowanceUnit } from './AllowanceUnit.js';
import { AllowanceWindow } from './AllowanceWindow.js';
import { AllowanceFromJSON, AllowanceToJSON, type Allowance } from './Allowance.js';
import { EntitlementDenialFromJSON, EntitlementDenialToJSON, type EntitlementDenial } from './EntitlementDenial.js';
import { ValidationIssueFromJSON, ValidationIssueToJSON, type ValidationIssue } from './ValidationIssue.js';

export interface ServiceProblemDetail {
    readonly type: string;
    readonly title: string;
    readonly status: number;
    readonly detail: string;
    readonly code: ProblemCode;
    readonly correlationId: string;
    readonly instance?: string;
    readonly field?: ProblemField;
    readonly reason?: ValidationReason;
    readonly validationErrors?: Set<ValidationIssue>;
    readonly idempotencyKey?: string;
    readonly retryAfterSeconds?: number;
    readonly allowance?: Allowance;
    readonly entitlement?: EntitlementDenial;
}

export class ProblemWireError extends TypeError {
    constructor(readonly reason: string) { super(`problem rejected: ${reason}`); }
}

const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}';
const REQUIRED = ['type', 'title', 'status', 'detail', 'code', 'correlation_id'];
const OPTIONAL = ['instance', 'field', 'reason', 'validation_errors', 'idempotency_key', 'retry_after_seconds', 'allowance', 'entitlement'];
const VALIDATION = new Set([ProblemCode.ValidationRejected, ProblemCode.ImportMappingRequired, ProblemCode.IdempotencyKeyInvalid]);
const DELAY = new Set([ProblemCode.DependencyUnavailable, ProblemCode.IdempotencyInProgress, ProblemCode.RateLimited, ProblemCode.RequestFailed]);
const MODEL_FIELDS: Readonly<Record<string, ProviderField>> = {
    type: { name: 'type', required: true, kind: 'string' },
    title: { name: 'title', required: true, kind: 'string' },
    status: { name: 'status', required: true, kind: 'integer' },
    detail: { name: 'detail', required: true, kind: 'string' },
    code: { name: 'code', required: true, kind: 'string' },
    correlation_id: { name: 'correlationId', required: true, kind: 'string' },
    instance: { name: 'instance', required: false, kind: 'string' },
    field: { name: 'field', required: false, kind: 'string' },
    reason: { name: 'reason', required: false, kind: 'string' },
    validation_errors: { name: 'validationErrors', required: false, kind: 'array', uniqueItems: true },
    idempotency_key: { name: 'idempotencyKey', required: false, kind: 'string' },
    retry_after_seconds: { name: 'retryAfterSeconds', required: false, kind: 'integer' },
    allowance: { name: 'allowance', required: false, kind: 'object' },
    entitlement: { name: 'entitlement', required: false, kind: 'object' },
};

function object(value: unknown, required: readonly string[], optional: readonly string[] = []): Record<string, unknown> {
    if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new ProblemWireError('shape');
    const fields: Record<string, unknown> = Object.fromEntries(Object.entries(value));
    if (required.some((key) => !Object.hasOwn(fields, key)) ||
        Object.keys(fields).some((key) => !required.includes(key) && !optional.includes(key)) ||
        Object.values(fields).some((item) => item === null || item === undefined)) throw new ProblemWireError('shape');
    return fields;
}

function exactUuid(value: unknown, prefix: string): value is string {
    return typeof value === 'string' && value.length === prefix.length + 36 && new RegExp(`^${prefix}${UUID}$`).test(value);
}

export function validateProblemWire(value: unknown): void {
    const wire = object(value, REQUIRED, OPTIONAL);
    const code = ProblemCodeFromJSON(wire.code);
    const policy = errorPolicy(code);
    if (wire.type !== `urn:pennilogic:problem:${code}` || wire.title !== policy.title ||
        wire.status !== errorStatus(code, Object.hasOwn(wire, 'field') ? ProblemFieldFromJSON(wire.field) : undefined) ||
        wire.detail !== policy.detail) throw new ProblemWireError('catalogue');
    if (!exactUuid(wire.correlation_id, 'cor_')) throw new ProblemWireError('correlation');
    if (Object.hasOwn(wire, 'instance') && !exactUuid(wire.instance, 'urn:pennilogic:problem-instance:')) throw new ProblemWireError('instance');
    if (VALIDATION.has(code)) {
        const field = ProblemFieldFromJSON(wire.field), reason = ValidationReasonFromJSON(wire.reason);
        if (code === ProblemCode.IdempotencyKeyInvalid &&
            (field !== ProblemField.IdempotencyKey || ![ValidationReason.Required, ValidationReason.Malformed].includes(reason))) throw new ProblemWireError('validation');
        if (code === ProblemCode.ImportMappingRequired &&
            (field !== ProblemField.ColumnMapping || ![ValidationReason.MappingUnmapped, ValidationReason.MappingConflict].includes(reason))) throw new ProblemWireError('validation');
        if (code === ProblemCode.ValidationRejected && field === ProblemField.DuplicateOverride &&
            ![ValidationReason.Malformed, ValidationReason.NotAvailable].includes(reason)) throw new ProblemWireError('validation');
        if (Object.hasOwn(wire, 'validation_errors')) {
            if (!Array.isArray(wire.validation_errors) || wire.validation_errors.length < 1 || wire.validation_errors.length > 20) throw new ProblemWireError('validation');
            const seen = new Set<string>();
            for (const value of wire.validation_errors) {
                const issue = object(value, ['field', 'reason']);
                const pair = `${ProblemFieldFromJSON(issue.field)}|${ValidationReasonFromJSON(issue.reason)}`;
                if (seen.has(pair)) throw new ProblemWireError('validation');
                seen.add(pair);
            }
        }
    } else if (['field', 'reason', 'validation_errors'].some((key) => Object.hasOwn(wire, key))) throw new ProblemWireError('validation');
    if (code === ProblemCode.IdempotencyPayloadMismatch) {
        if (!exactUuid(wire.idempotency_key, '')) throw new ProblemWireError('key');
    } else if (Object.hasOwn(wire, 'idempotency_key')) throw new ProblemWireError('key');
    if (Object.hasOwn(wire, 'retry_after_seconds')) {
        const delay = wire.retry_after_seconds;
        if (!DELAY.has(code) || typeof delay !== 'number' || !Number.isInteger(delay) || delay < 1 || delay > 86400) throw new ProblemWireError('retry');
    } else if (code === ProblemCode.IdempotencyInProgress || code === ProblemCode.RateLimited) throw new ProblemWireError('retry');
    if (code === ProblemCode.QuotaExhausted || code === ProblemCode.RateLimited) {
        const allowance = object(wire.allowance, ['limit', 'unit', 'window'], ['resets_at']);
        if (typeof allowance.limit !== 'number' || !Number.isInteger(allowance.limit) || allowance.limit < 0 ||
            allowance.limit > 2147483647 || typeof allowance.unit !== 'string' ||
            !['requests', 'tokens'].includes(allowance.unit) || typeof allowance.window !== 'string' ||
            !['minute', 'hour', 'day', 'month', 'rolling_7_days', 'lifetime'].includes(allowance.window)) throw new ProblemWireError('allowance');
        if (Object.hasOwn(allowance, 'resets_at')) {
            if (allowance.limit === 0 || allowance.window === 'lifetime') throw new ProblemWireError('allowance');
            Instant.fromWire(allowance.resets_at);
        }
    } else if (Object.hasOwn(wire, 'allowance')) throw new ProblemWireError('allowance');
    if (code === ProblemCode.EntitlementDenied) {
        const entitlement = object(wire.entitlement, ['upgrade_available']);
        if (typeof entitlement.upgrade_available !== 'boolean') throw new ProblemWireError('entitlement');
    } else if (Object.hasOwn(wire, 'entitlement')) throw new ProblemWireError('entitlement');
}

export function ServiceProblemDetailFromJSON(value: unknown): ServiceProblemDetail {
    validateProblemWire(value);
    const wire = object(value, REQUIRED, OPTIONAL);
    // Catalogue equality and UUID checks above are the type guards for these four scalar members.
    const policy = errorPolicy(ProblemCodeFromJSON(wire.code));
    const correlation = wire.correlation_id;
    if (!exactUuid(correlation, 'cor_')) throw new ProblemWireError('correlation');
    return {
        type: `urn:pennilogic:problem:${ProblemCodeFromJSON(wire.code)}`, title: policy.title,
        status: errorStatus(ProblemCodeFromJSON(wire.code), Object.hasOwn(wire, 'field') ? ProblemFieldFromJSON(wire.field) : undefined),
        detail: policy.detail, code: ProblemCodeFromJSON(wire.code), correlationId: correlation,
        ...(typeof wire.instance === 'string' ? { instance: wire.instance } : {}),
        ...(Object.hasOwn(wire, 'field') ? { field: ProblemFieldFromJSON(wire.field) } : {}),
        ...(Object.hasOwn(wire, 'reason') ? { reason: ValidationReasonFromJSON(wire.reason) } : {}),
        ...(Array.isArray(wire.validation_errors) ? { validationErrors: new Set(wire.validation_errors.map(ValidationIssueFromJSON)) } : {}),
        ...(typeof wire.idempotency_key === 'string' ? { idempotencyKey: wire.idempotency_key } : {}),
        ...(typeof wire.retry_after_seconds === 'number' ? { retryAfterSeconds: wire.retry_after_seconds } : {}),
        ...(Object.hasOwn(wire, 'allowance') ? { allowance: AllowanceFromJSON(wire.allowance) } : {}),
        ...(Object.hasOwn(wire, 'entitlement') ? { entitlement: EntitlementDenialFromJSON(wire.entitlement) } : {}),
    };
}

export function ServiceProblemDetailToJSON(value: ServiceProblemDetail): Record<string, unknown> {
    providerObject(value, MODEL_FIELDS, false);
    const wire: Record<string, unknown> = {
        type: value.type, title: value.title, status: value.status, detail: value.detail,
        code: value.code, correlation_id: value.correlationId,
    };
    if (value.instance !== undefined) wire.instance = value.instance;
    if (value.field !== undefined) wire.field = value.field;
    if (value.reason !== undefined) wire.reason = value.reason;
    if (value.validationErrors !== undefined) wire.validation_errors = [...value.validationErrors].map(ValidationIssueToJSON);
    if (value.idempotencyKey !== undefined) wire.idempotency_key = value.idempotencyKey;
    if (value.retryAfterSeconds !== undefined) wire.retry_after_seconds = value.retryAfterSeconds;
    if (value.allowance !== undefined) {
        const rendered: unknown = AllowanceToJSON(value.allowance);
        if (rendered === null || typeof rendered !== 'object' || Array.isArray(rendered)) throw new ProblemWireError('allowance');
        wire.allowance = Object.fromEntries(Object.entries(rendered).filter(([, member]) => member !== undefined));
    }
    if (value.entitlement !== undefined) wire.entitlement = EntitlementDenialToJSON(value.entitlement);
    validateProblemWire(wire);
    return wire;
}

export function ServiceProblemDetailFromJSONTyped(value: unknown, _ignoreDiscriminator: boolean): ServiceProblemDetail {
    return ServiceProblemDetailFromJSON(value);
}

export function ServiceProblemDetailToJSONTyped(value: ServiceProblemDetail, _ignoreDiscriminator: boolean = false): Record<string, unknown> {
    return ServiceProblemDetailToJSON(value);
}

export function instanceOfServiceProblemDetail(value: unknown): value is ServiceProblemDetail {
    if (value === null || typeof value !== 'object' || Array.isArray(value)) return false;
    const fields: Record<string, unknown> = Object.fromEntries(Object.entries(value));
    if (typeof fields.type !== 'string' || typeof fields.title !== 'string' || typeof fields.status !== 'number' ||
        typeof fields.detail !== 'string' || typeof fields.correlationId !== 'string' ||
        !Object.values(ProblemCode).some((member) => member === fields.code)) return false;
    if (fields.instance !== undefined && typeof fields.instance !== 'string') return false;
    if (fields.field !== undefined && !Object.values(ProblemField).some((member) => member === fields.field)) return false;
    if (fields.reason !== undefined && !Object.values(ValidationReason).some((member) => member === fields.reason)) return false;
    if (fields.idempotencyKey !== undefined && typeof fields.idempotencyKey !== 'string') return false;
    if (fields.retryAfterSeconds !== undefined && typeof fields.retryAfterSeconds !== 'number') return false;
    if (fields.validationErrors !== undefined) {
        if (!(fields.validationErrors instanceof Set)) return false;
        for (const item of fields.validationErrors) {
            if (item === null || typeof item !== 'object') return false;
            const issue: Record<string, unknown> = Object.fromEntries(Object.entries(item));
            if (!Object.values(ProblemField).some((member) => member === issue.field) ||
                !Object.values(ValidationReason).some((member) => member === issue.reason)) return false;
        }
    }
    if (fields.allowance !== undefined) {
        if (fields.allowance === null || typeof fields.allowance !== 'object') return false;
        const allowance: Record<string, unknown> = Object.fromEntries(Object.entries(fields.allowance));
        if (typeof allowance.limit !== 'number' || !Object.values(AllowanceUnit).some((member) => member === allowance.unit) ||
            !Object.values(AllowanceWindow).some((member) => member === allowance.window) ||
            (allowance.resetsAt !== undefined && !(allowance.resetsAt instanceof Instant))) return false;
    }
    if (fields.entitlement !== undefined) {
        if (fields.entitlement === null || typeof fields.entitlement !== 'object') return false;
        const entitlement: Record<string, unknown> = Object.fromEntries(Object.entries(fields.entitlement));
        if (typeof entitlement.upgradeAvailable !== 'boolean') return false;
    }
    return true;
}
