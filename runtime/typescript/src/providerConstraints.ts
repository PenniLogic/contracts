import { PROVIDER_SCHEMAS } from './providerConstraintData.js';
import { Instant, InstantWireError } from './models/Instant.js';
import { LocalDate, LocalDateWireError } from './models/LocalDate.js';

type Scalar = string | number | boolean;
export interface ProviderSchema {
    readonly ref?: string;
    readonly type?: 'object' | 'array' | 'string' | 'integer' | 'boolean';
    readonly format?: string;
    readonly const?: Scalar;
    readonly enum?: readonly Scalar[];
    readonly pattern?: string;
    readonly minLength?: number;
    readonly maxLength?: number;
    readonly minimum?: number;
    readonly maximum?: number;
    readonly exclusiveMinimum?: number;
    readonly exclusiveMaximum?: number;
    readonly minItems?: number;
    readonly maxItems?: number;
    readonly uniqueItems?: boolean;
    readonly required?: readonly string[];
    readonly properties?: Readonly<Record<string, ProviderSchema>>;
    readonly additionalProperties?: boolean;
    readonly items?: ProviderSchema;
    readonly allOf?: readonly ProviderSchema[];
    readonly anyOf?: readonly ProviderSchema[];
    readonly oneOf?: readonly ProviderSchema[];
    readonly not?: ProviderSchema;
    readonly if?: ProviderSchema;
    readonly then?: ProviderSchema;
    readonly else?: ProviderSchema;
}

export class ProviderConstraintError extends TypeError {
    constructor() { super('provider value rejected'); }
}

export function validateProvider(name: string, value: unknown, omitUndefined: boolean): void {
    const schema = PROVIDER_SCHEMAS[name];
    if (schema === undefined) throw new ProviderConstraintError();
    if (!matches(value, schema, omitUndefined)) {
        if (enumMismatch(value, schema)) throw new TypeError('enum value rejected');
        throw new ProviderConstraintError();
    }
}

const patterns = new Map<string, RegExp>();
function enumMismatch(value: unknown, schema: ProviderSchema): boolean {
    if (schema.ref !== undefined) {
        const target = PROVIDER_SCHEMAS[schema.ref];
        if (target === undefined) throw new ProviderConstraintError();
        if (enumMismatch(value, target)) return true;
    }
    if (typeof value === 'string' && schema.enum !== undefined &&
        !schema.enum.some((allowed) => equal(value, allowed))) return true;
    const items = schema.items;
    if (Array.isArray(value) && items !== undefined &&
        value.some((item) => enumMismatch(item, items))) return true;
    if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
        const members: Record<string, unknown> = Object.fromEntries(Object.entries(value));
        if (Object.entries(schema.properties ?? {}).some(([key, declaration]) =>
            Object.hasOwn(members, key) && enumMismatch(members[key], declaration))) return true;
    }
    return schema.allOf?.some((child) => enumMismatch(value, child)) ?? false;
}
function equal(left: unknown, right: unknown): boolean {
    if (left === right) return true;
    if (Array.isArray(left) && Array.isArray(right)) {
        return left.length === right.length && left.every((value, index) => equal(value, right[index]));
    }
    if (left !== null && right !== null && typeof left === 'object' && typeof right === 'object' &&
        !Array.isArray(left) && !Array.isArray(right)) {
        const a = Object.fromEntries(Object.entries(left)), b = Object.fromEntries(Object.entries(right));
        return Object.keys(a).length === Object.keys(b).length &&
            Object.keys(a).every((key) => Object.hasOwn(b, key) && equal(a[key], b[key]));
    }
    return false;
}
function format(value: string, name: string): boolean {
    if (name === 'date-time' || name === 'date') {
        try {
            if (name === 'date-time') Instant.fromWire(value); else LocalDate.fromWire(value);
        } catch (error: unknown) {
            if (error instanceof InstantWireError || error instanceof LocalDateWireError) return false;
            throw error;
        }
        return true;
    }
    if (name === 'uri-reference') {
        if (/[^\x21-\x7e]|%(?![0-9a-fA-F]{2})/.test(value)) return false;
        try { new URL(value, 'https://pennilogic.example/'); return true; }
        catch (error: unknown) { if (error instanceof TypeError) return false; throw error; }
    }
    throw new ProviderConstraintError();
}
function matches(value: unknown, schema: ProviderSchema, omitUndefined: boolean): boolean {
    if (schema.ref !== undefined) {
        const target = PROVIDER_SCHEMAS[schema.ref];
        if (target === undefined) throw new ProviderConstraintError();
        if (!matches(value, target, omitUndefined)) return false;
    }
    if (value === null || value === undefined) return false;
    if ((schema.type === 'string' && typeof value !== 'string') ||
        (schema.type === 'integer' && (typeof value !== 'number' || !Number.isSafeInteger(value))) ||
        (schema.type === 'boolean' && typeof value !== 'boolean') ||
        (schema.type === 'array' && !Array.isArray(value)) ||
        (schema.type === 'object' && (typeof value !== 'object' || Array.isArray(value)))) return false;
    if (schema.const !== undefined && !equal(value, schema.const)) return false;
    if (schema.enum !== undefined && !schema.enum.some((item) => equal(value, item))) return false;
    if (typeof value === 'number') {
        if ((schema.format === 'int32' && (value < -2147483648 || value > 2147483647)) ||
            (schema.minimum !== undefined && value < schema.minimum) || (schema.maximum !== undefined && value > schema.maximum) ||
            (schema.exclusiveMinimum !== undefined && value <= schema.exclusiveMinimum) ||
            (schema.exclusiveMaximum !== undefined && value >= schema.exclusiveMaximum)) return false;
    }
    if (typeof value === 'string') {
        const length = [...value].length;
        if ((schema.minLength !== undefined && length < schema.minLength) ||
            (schema.maxLength !== undefined && length > schema.maxLength)) return false;
        if (schema.pattern !== undefined) {
            const pattern = patterns.get(schema.pattern) ?? new RegExp(schema.pattern);
            patterns.set(schema.pattern, pattern);
            if (pattern.exec(value)?.[0] !== value) return false;
        }
        if (schema.format !== undefined && !format(value, schema.format)) return false;
    }
    if (Array.isArray(value)) {
        if ((schema.minItems !== undefined && value.length < schema.minItems) ||
            (schema.maxItems !== undefined && value.length > schema.maxItems)) return false;
        for (let index = 0; index < value.length; index += 1) {
            if (!Object.hasOwn(value, index) || value[index] === null || value[index] === undefined ||
                (schema.items !== undefined && !matches(value[index], schema.items, omitUndefined))) return false;
            if (schema.uniqueItems && value.slice(0, index).some((other) => equal(value[index], other))) return false;
        }
    } else if (typeof value === 'object') {
        const members: Record<string, unknown> = Object.fromEntries(Object.entries(value));
        const present = (key: string): boolean => Object.hasOwn(members, key) && !(omitUndefined && members[key] === undefined);
        if (schema.required?.some((key) => !present(key))) return false;
        const properties = schema.properties ?? {};
        if (schema.additionalProperties === false &&
            Reflect.ownKeys(value).some((key) => typeof key !== 'string' || !Object.hasOwn(properties, key))) return false;
        if (Object.entries(properties).some(([key, declaration]) => present(key) && !matches(members[key], declaration, omitUndefined))) return false;
    }
    if (schema.allOf?.some((child) => !matches(value, child, omitUndefined))) return false;
    if (schema.anyOf !== undefined && !schema.anyOf.some((child) => matches(value, child, omitUndefined))) return false;
    if (schema.oneOf !== undefined && schema.oneOf.filter((child) => matches(value, child, omitUndefined)).length !== 1) return false;
    if (schema.not !== undefined && matches(value, schema.not, omitUndefined)) return false;
    if (schema.if !== undefined) {
        const branch = matches(value, schema.if, omitUndefined) ? schema.then : schema.else;
        if (branch !== undefined && !matches(value, branch, omitUndefined)) return false;
    }
    return true;
}
