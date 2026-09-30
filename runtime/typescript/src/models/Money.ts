/* PenniLogic hand-written seam shipped with every generated TypeScript client (ADR-015 §1, §2.1).
 *
 * `Money` is the only representation of money in application code: `bigint` minor units plus the
 * currency code. It is constructed only from integer minor units (`Money.ofMinorUnits`), a validated
 * canonical string (`Money.parse`) or the wire object (`Money.fromWire`); `number` is never accepted.
 * The wire object `{ amount: "<canonical decimal string>", currency: "<ISO 4217>" }` is parsed and
 * rendered only here. The generated models call `MoneyFromJSON` / `MoneyToJSON`, so `bigint` never
 * reaches `JSON.stringify` and no raw primitive carries money. Rejections carry the reason and the
 * field name, never the offending value.
 */

import { currencyExponent } from './currencyRegistry.js';

export type MoneyReason = 'shape' | 'number_not_string' | 'grammar' | 'currency_unknown' | 'scale_mismatch' | 'out_of_range';

export const MONEY_REASONS: readonly MoneyReason[] = ['shape', 'number_not_string', 'grammar', 'currency_unknown', 'scale_mismatch', 'out_of_range'];
export const MAX_MINOR_UNITS = 9223372036854775807n;

const GRAMMAR = /^(0(\.[0-9]+)?|-?[1-9][0-9]*(\.[0-9]+)?|-0\.[0-9]*[1-9][0-9]*)$/;
const MEMBERS = ['amount', 'currency'] as const;

/** The wire object; internal to the client package and produced only by `Money.toWire`. */
export interface MoneyWire {
    readonly amount: string;
    readonly currency: string;
}

export class MoneyWireError extends Error {
    readonly reason: MoneyReason;
    readonly field: string;

    constructor(reason: MoneyReason, field: string) {
        super(`money rejected: ${reason} at ${field === '' ? '<value>' : field}`);
        this.name = 'MoneyWireError';
        this.reason = reason;
        this.field = field;
    }
}

function exponentOf(currency: string): number {
    const exponent = currencyExponent(currency);
    if (exponent === undefined) {
        throw new MoneyWireError('currency_unknown', 'currency');
    }
    return exponent;
}

function minorUnitsFromCanonical(amount: string, exponent: number): bigint {
    if (!GRAMMAR.test(amount)) {
        throw new MoneyWireError('grammar', 'amount');
    }
    const negative = amount.startsWith('-');
    const unsigned = negative ? amount.slice(1) : amount;
    const point = unsigned.indexOf('.');
    const integerPart = point === -1 ? unsigned : unsigned.slice(0, point);
    const fraction = point === -1 ? '' : unsigned.slice(point + 1);
    if (fraction.length !== exponent) {
        throw new MoneyWireError('scale_mismatch', 'amount');
    }
    const magnitude = BigInt(integerPart + fraction);
    if (magnitude > MAX_MINOR_UNITS) {
        throw new MoneyWireError('out_of_range', 'amount');
    }
    return negative ? -magnitude : magnitude;
}

function formatMinorUnits(minorUnits: bigint, exponent: number): string {
    const negative = minorUnits < 0n;
    const digits = (negative ? -minorUnits : minorUnits).toString().padStart(exponent + 1, '0');
    const integerPart = digits.slice(0, digits.length - exponent);
    const fraction = digits.slice(digits.length - exponent);
    const unsigned = exponent === 0 ? integerPart : `${integerPart}.${fraction}`;
    return negative ? `-${unsigned}` : unsigned;
}

export class Money {
    readonly #minorUnits: bigint;
    readonly #currency: string;

    private constructor(minorUnits: bigint, currency: string) {
        this.#minorUnits = minorUnits;
        this.#currency = currency;
    }

    /** Construct from integer minor units; `number` is refused even when integral. */
    static ofMinorUnits(minorUnits: bigint, currency: string): Money {
        if (typeof minorUnits !== 'bigint') {
            throw new TypeError('Money.ofMinorUnits takes bigint minor units; number is never money (ADR-015)');
        }
        if (typeof currency !== 'string') {
            throw new TypeError('currency must be a string ISO 4217 code');
        }
        exponentOf(currency);
        if (minorUnits > MAX_MINOR_UNITS || minorUnits < -MAX_MINOR_UNITS) {
            throw new MoneyWireError('out_of_range', 'amount');
        }
        return new Money(minorUnits, currency);
    }

    /** Construct from a canonical amount string; a non-string amount is a programming error. */
    static parse(amount: string, currency: string): Money {
        if (typeof amount !== 'string') {
            throw new TypeError('Money.parse takes the canonical decimal string; numbers are refused (ADR-015)');
        }
        if (typeof currency !== 'string') {
            throw new TypeError('currency must be a string ISO 4217 code');
        }
        const exponent = exponentOf(currency);
        return new Money(minorUnitsFromCanonical(amount, exponent), currency);
    }

    /** Construct from the wire object, checking in the fixed ADR-015 §1.5 order. */
    static fromWire(wire: unknown): Money {
        if (wire === null || typeof wire !== 'object' || Array.isArray(wire)) {
            throw new MoneyWireError('shape', '');
        }
        const record = wire as Record<string, unknown>;
        for (const member of MEMBERS) {
            if (!Object.prototype.hasOwnProperty.call(record, member)) {
                throw new MoneyWireError('shape', member);
            }
        }
        for (const key of Object.keys(record)) {
            if (!(MEMBERS as readonly string[]).includes(key)) {
                throw new MoneyWireError('shape', key);
            }
        }
        for (const member of MEMBERS) {
            const value = record[member];
            if (typeof value !== 'string' && typeof value !== 'number') {
                throw new MoneyWireError('shape', member);
            }
        }
        for (const member of MEMBERS) {
            if (typeof record[member] !== 'string') {
                throw new MoneyWireError('number_not_string', member);
            }
        }
        const amount = record.amount as string;
        const currency = record.currency as string;
        if (!GRAMMAR.test(amount)) {
            throw new MoneyWireError('grammar', 'amount');
        }
        const exponent = exponentOf(currency);
        return new Money(minorUnitsFromCanonical(amount, exponent), currency);
    }

    get minorUnits(): bigint {
        return this.#minorUnits;
    }

    get currency(): string {
        return this.#currency;
    }

    /** Render the wire object; the result is self-checked against the grammar and re-parsed. */
    toWire(): MoneyWire {
        const exponent = exponentOf(this.#currency);
        const amount = formatMinorUnits(this.#minorUnits, exponent);
        if (minorUnitsFromCanonical(amount, exponent) !== this.#minorUnits) {
            throw new Error('money destruction seam produced a non-canonical amount');
        }
        return { amount, currency: this.#currency };
    }

    /** `JSON.stringify` never meets the bigint. */
    toJSON(): MoneyWire {
        return this.toWire();
    }

    toString(): string {
        const wire = this.toWire();
        return `${wire.amount} ${wire.currency}`;
    }

    equals(other: unknown): boolean {
        return other instanceof Money && other.#currency === this.#currency && other.#minorUnits === this.#minorUnits;
    }

    private sameCurrency(other: Money): Money {
        if (!(other instanceof Money)) {
            throw new TypeError('Money operates only with Money');
        }
        if (other.#currency !== this.#currency) {
            throw new TypeError('Money operates only within one currency');
        }
        return other;
    }

    compare(other: Money): -1 | 0 | 1 {
        const rhs = this.sameCurrency(other).#minorUnits;
        return this.#minorUnits < rhs ? -1 : this.#minorUnits > rhs ? 1 : 0;
    }

    plus(other: Money): Money {
        return Money.ofMinorUnits(this.#minorUnits + this.sameCurrency(other).#minorUnits, this.#currency);
    }

    minus(other: Money): Money {
        return Money.ofMinorUnits(this.#minorUnits - this.sameCurrency(other).#minorUnits, this.#currency);
    }

    negate(): Money {
        return Money.ofMinorUnits(-this.#minorUnits, this.#currency);
    }
}

/* Functions the generated typescript-fetch models call for every Money-typed property. */

export function instanceOfMoney(value: unknown): value is Money {
    return value instanceof Money;
}

export function MoneyFromJSON(json: unknown): Money {
    return MoneyFromJSONTyped(json, false);
}

export function MoneyFromJSONTyped(json: unknown, _ignoreDiscriminator: boolean): Money {
    if (json instanceof Money) {
        return json;
    }
    return Money.fromWire(json);
}

export function MoneyToJSON(value?: Money | null): MoneyWire | null | undefined {
    return MoneyToJSONTyped(value, false);
}

export function MoneyToJSONTyped(value?: Money | null, _ignoreDiscriminator: boolean = false): MoneyWire | null | undefined {
    if (value == null) {
        return value;
    }
    if (!(value instanceof Money)) {
        throw new TypeError('only a Money instance can be rendered to the wire (ADR-015 §2.1)');
    }
    return value.toWire();
}
