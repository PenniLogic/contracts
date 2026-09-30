/* PenniLogic hand-written seam shipped with every generated TypeScript client (ADR-015 §3.1).
 *
 * A date-only fact travels as exactly `YYYY-MM-DD` (years 0001–9999, valid proleptic Gregorian date)
 * and never carries a time of day. `LocalDate` holds year, month and day; parsing validates the
 * grammar and the calendar and requires the re-formatted value to equal the input. The generated
 * models call `LocalDateFromJSON` / `LocalDateToJSON` for every `date` property.
 */

export type LocalDateReason = 'shape' | 'grammar' | 'calendar';

const GRAMMAR = /^(000[1-9]|00[1-9][0-9]|0[1-9][0-9]{2}|[1-9][0-9]{3})-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$/;

export class LocalDateWireError extends Error {
    readonly reason: LocalDateReason;

    constructor(reason: LocalDateReason) {
        super(`date rejected: ${reason}`);
        this.name = 'LocalDateWireError';
        this.reason = reason;
    }
}

function pad(value: number, width: number): string {
    return value.toString().padStart(width, '0');
}

export class LocalDate {
    readonly #year: number;
    readonly #month: number;
    readonly #day: number;

    private constructor(year: number, month: number, day: number) {
        this.#year = year;
        this.#month = month;
        this.#day = day;
    }

    static of(year: number, month: number, day: number): LocalDate {
        if (![year, month, day].every((part) => typeof part === 'number' && Number.isInteger(part))) {
            throw new TypeError('LocalDate.of takes integer year, month and day');
        }
        if (year < 1 || year > 9999) {
            throw new LocalDateWireError('calendar');
        }
        const probe = new Date(0);
        probe.setUTCFullYear(year, month - 1, day);
        probe.setUTCHours(0, 0, 0, 0);
        if (probe.getUTCFullYear() !== year || probe.getUTCMonth() !== month - 1 || probe.getUTCDate() !== day) {
            throw new LocalDateWireError('calendar');
        }
        return new LocalDate(year, month, day);
    }

    static parse(text: string): LocalDate {
        if (typeof text !== 'string') {
            throw new TypeError('LocalDate.parse takes the 10-character wire string');
        }
        if (!GRAMMAR.test(text)) {
            throw new LocalDateWireError('grammar');
        }
        const value = LocalDate.of(Number(text.slice(0, 4)), Number(text.slice(5, 7)), Number(text.slice(8, 10)));
        if (value.toWire() !== text) {
            throw new LocalDateWireError('calendar');
        }
        return value;
    }

    static fromWire(wire: unknown): LocalDate {
        if (typeof wire !== 'string') {
            throw new LocalDateWireError('shape');
        }
        return LocalDate.parse(wire);
    }

    get year(): number {
        return this.#year;
    }

    get month(): number {
        return this.#month;
    }

    get day(): number {
        return this.#day;
    }

    toWire(): string {
        const text = `${pad(this.#year, 4)}-${pad(this.#month, 2)}-${pad(this.#day, 2)}`;
        if (!GRAMMAR.test(text)) {
            throw new Error('date destruction seam produced a non-canonical value');
        }
        return text;
    }

    toJSON(): string {
        return this.toWire();
    }

    toString(): string {
        return this.toWire();
    }

    equals(other: unknown): boolean {
        return other instanceof LocalDate && other.#year === this.#year && other.#month === this.#month && other.#day === this.#day;
    }

    compare(other: LocalDate): -1 | 0 | 1 {
        if (!(other instanceof LocalDate)) {
            throw new TypeError('LocalDate compares only with LocalDate');
        }
        const lhs = this.toWire();
        const rhs = other.toWire();
        return lhs < rhs ? -1 : lhs > rhs ? 1 : 0;
    }
}

/* Functions the generated typescript-fetch models call for every LocalDate-typed property. */

export function instanceOfLocalDate(value: unknown): value is LocalDate {
    return value instanceof LocalDate;
}

export function LocalDateFromJSON(json: unknown): LocalDate {
    return LocalDateFromJSONTyped(json, false);
}

export function LocalDateFromJSONTyped(json: unknown, _ignoreDiscriminator: boolean): LocalDate {
    if (json instanceof LocalDate) {
        return json;
    }
    return LocalDate.fromWire(json);
}

export function LocalDateToJSON(value?: LocalDate | null): string | null | undefined {
    return LocalDateToJSONTyped(value, false);
}

export function LocalDateToJSONTyped(value?: LocalDate | null, _ignoreDiscriminator: boolean = false): string | null | undefined {
    if (value == null) {
        return value;
    }
    if (!(value instanceof LocalDate)) {
        throw new TypeError('only a LocalDate instance can be rendered to the wire (ADR-015 §3.1)');
    }
    return value.toWire();
}
