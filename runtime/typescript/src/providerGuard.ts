import { Instant } from './models/Instant.js';
import { LocalDate } from './models/LocalDate.js';

export class ProviderWireError extends TypeError {
    constructor() { super('provider value rejected'); }
}

export interface ProviderField {
    readonly name: string;
    readonly required: boolean;
    readonly kind?: 'string' | 'integer' | 'boolean' | 'array' | 'object';
    readonly pattern?: RegExp;
    readonly minLength?: number;
    readonly maxLength?: number;
    readonly minimum?: number;
    readonly maximum?: number;
    readonly minItems?: number;
    readonly maxItems?: number;
    readonly uniqueItems?: boolean;
    readonly items?: ProviderField;
}

export function providerObject(value: unknown, fields: Readonly<Record<string, ProviderField>>, wire: boolean): Record<string, unknown> {
    if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new ProviderWireError();
    const names = new Set(Object.entries(fields).map(([key, field]) => wire ? key : field.name));
    if (Reflect.ownKeys(value).some((key) => typeof key !== 'string' || !names.has(key))) throw new ProviderWireError();
    const members: Record<string, unknown> = Object.fromEntries(Object.entries(value));
    for (const [key, field] of Object.entries(fields)) {
        const name = wire ? key : field.name;
        const member = members[name];
        if (member === undefined) {
            if (field.required) throw new ProviderWireError();
            continue;
        }
        if (member === null) throw new ProviderWireError();
        validateField(member, field, wire);
    }
    return members;
}

export function providerPattern(literal: string): RegExp {
    const end = literal.lastIndexOf('/');
    if (!literal.startsWith('/') || end < 1) throw new ProviderWireError();
    return new RegExp(literal.slice(1, end), literal.slice(end + 1));
}

function validateField(value: unknown, field: ProviderField, wire: boolean): void {
    if (value === null || value === undefined) throw new ProviderWireError();
    const scalar = !wire && (value instanceof Instant || value instanceof LocalDate) ? value.toWire() : value;
    if (field.kind === 'string' && typeof scalar !== 'string') throw new ProviderWireError();
    if (field.kind === 'boolean' && typeof value !== 'boolean') throw new ProviderWireError();
    if (field.kind === 'integer' && (typeof value !== 'number' || !Number.isSafeInteger(value))) throw new ProviderWireError();
    if (field.kind === 'object' && (value === null || typeof value !== 'object' || Array.isArray(value))) throw new ProviderWireError();
    if (field.pattern !== undefined && (typeof scalar !== 'string' || field.pattern.exec(scalar)?.[0] !== scalar)) {
        throw new ProviderWireError();
    }

    if ((field.minLength !== undefined && (typeof scalar !== 'string' || scalar.length < field.minLength)) ||
        (field.maxLength !== undefined && (typeof scalar !== 'string' || scalar.length > field.maxLength))) throw new ProviderWireError();
    if ((field.minimum !== undefined && (typeof value !== 'number' || value < field.minimum)) ||
        (field.maximum !== undefined && (typeof value !== 'number' || value > field.maximum))) throw new ProviderWireError();
    if (field.kind === 'array') {
        const items: unknown[] = !wire && field.uniqueItems && value instanceof Set ? [...value] :
            Array.isArray(value) ? value : (() => { throw new ProviderWireError(); })();
        if ((field.minItems !== undefined && items.length < field.minItems) ||
            (field.maxItems !== undefined && items.length > field.maxItems)) throw new ProviderWireError();
        const itemField = field.items ?? { name: '', required: true };
        for (let index = 0; index < items.length; index += 1) {
            if (!Object.hasOwn(items, index)) throw new ProviderWireError();
            validateField(items[index], itemField, wire);
        }
        if (field.uniqueItems && new Set(items.map((item) => JSON.stringify(item))).size !== items.length) throw new ProviderWireError();
    }
}
