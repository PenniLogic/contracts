/* PenniLogic hand-written seam shipped with every generated TypeScript client (ADR-015 §3.1).
 *
 * The wire form of an instant is exactly `YYYY-MM-DDTHH:MM:SS.sssZ` (24 characters, UTC,
 * milliseconds). `Instant` holds epoch milliseconds in a safe `number`; parsing validates the
 * grammar and the calendar and requires the re-formatted value to equal the input, so an offset,
 * another fraction length, lowercase letters or `2026-02-30` are rejected. The generated models call
 * `InstantFromJSON` / `InstantToJSON` for every `date-time` property.
 */

export type InstantReason = 'shape' | 'grammar' | 'calendar';

const GRAMMAR = /^(000[1-9]|00[1-9][0-9]|0[1-9][0-9]{2}|[1-9][0-9]{3})-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]\.[0-9]{3}Z$/;
const MIN_EPOCH_MILLIS = -62135596800000; // 0001-01-01T00:00:00.000Z
const MAX_EPOCH_MILLIS = 253402300799999; // 9999-12-31T23:59:59.999Z

export class InstantWireError extends Error {
    readonly reason: InstantReason;

    constructor(reason: InstantReason) {
        super(`instant rejected: ${reason}`);
        this.name = 'InstantWireError';
        this.reason = reason;
    }
}

function pad(value: number, width: number): string {
    return value.toString().padStart(width, '0');
}

export class Instant {
    readonly #epochMillis: number;

    private constructor(epochMillis: number) {
        this.#epochMillis = epochMillis;
    }

    static ofEpochMillis(epochMillis: number): Instant {
        if (typeof epochMillis !== 'number' || !Number.isInteger(epochMillis)) {
            throw new TypeError('Instant.ofEpochMillis takes an integer number of milliseconds');
        }
        if (epochMillis < MIN_EPOCH_MILLIS || epochMillis > MAX_EPOCH_MILLIS) {
            throw new InstantWireError('calendar');
        }
        return new Instant(epochMillis);
    }

    /** Construct from a `Date` already truncated to milliseconds (every `Date` is). */
    static fromDate(date: Date): Instant {
        if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
            throw new TypeError('Instant.fromDate takes a valid Date');
        }
        return Instant.ofEpochMillis(date.getTime());
    }

    static parse(text: string): Instant {
        if (typeof text !== 'string') {
            throw new TypeError('Instant.parse takes the 24-character wire string');
        }
        if (!GRAMMAR.test(text)) {
            throw new InstantWireError('grammar');
        }
        const year = Number(text.slice(0, 4));
        const month = Number(text.slice(5, 7));
        const day = Number(text.slice(8, 10));
        const utc = new Date(0);
        utc.setUTCFullYear(year, month - 1, day);
        utc.setUTCHours(Number(text.slice(11, 13)), Number(text.slice(14, 16)), Number(text.slice(17, 19)), Number(text.slice(20, 23)));
        if (utc.getUTCFullYear() !== year || utc.getUTCMonth() !== month - 1 || utc.getUTCDate() !== day) {
            throw new InstantWireError('calendar');
        }
        const instant = new Instant(utc.getTime());
        if (instant.toWire() !== text) {
            throw new InstantWireError('calendar');
        }
        return instant;
    }

    static fromWire(wire: unknown): Instant {
        if (typeof wire !== 'string') {
            throw new InstantWireError('shape');
        }
        return Instant.parse(wire);
    }

    get epochMillis(): number {
        return this.#epochMillis;
    }

    toDate(): Date {
        return new Date(this.#epochMillis);
    }

    toWire(): string {
        const date = new Date(this.#epochMillis);
        const text = `${pad(date.getUTCFullYear(), 4)}-${pad(date.getUTCMonth() + 1, 2)}-${pad(date.getUTCDate(), 2)}T${pad(date.getUTCHours(), 2)}:${pad(date.getUTCMinutes(), 2)}:${pad(date.getUTCSeconds(), 2)}.${pad(date.getUTCMilliseconds(), 3)}Z`;
        if (!GRAMMAR.test(text)) {
            throw new Error('instant destruction seam produced a non-canonical value');
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
        return other instanceof Instant && other.#epochMillis === this.#epochMillis;
    }

    compare(other: Instant): -1 | 0 | 1 {
        if (!(other instanceof Instant)) {
            throw new TypeError('Instant compares only with Instant');
        }
        return this.#epochMillis < other.#epochMillis ? -1 : this.#epochMillis > other.#epochMillis ? 1 : 0;
    }
}

/* Functions the generated typescript-fetch models call for every Instant-typed property. */

export function instanceOfInstant(value: unknown): value is Instant {
    return value instanceof Instant;
}

export function InstantFromJSON(json: unknown): Instant {
    return InstantFromJSONTyped(json, false);
}

export function InstantFromJSONTyped(json: unknown, _ignoreDiscriminator: boolean): Instant {
    if (json instanceof Instant) {
        return json;
    }
    return Instant.fromWire(json);
}

export function InstantToJSON(value?: Instant | null): string | null | undefined {
    return InstantToJSONTyped(value, false);
}

export function InstantToJSONTyped(value?: Instant | null, _ignoreDiscriminator: boolean = false): string | null | undefined {
    if (value == null) {
        return value;
    }
    if (!(value instanceof Instant)) {
        throw new TypeError('only an Instant instance can be rendered to the wire (ADR-015 §3.1)');
    }
    return value.toWire();
}
