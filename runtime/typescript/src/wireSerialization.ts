import { ProviderWireError } from './providerGuard.js';

export type WireOutput<Input, Wire, NullResult = null> =
    Input extends undefined ? undefined : Input extends null ? NullResult : Wire;

type OptionalWireKey<Value> = {
    [Key in keyof Value]-?: {} extends Pick<Value, Key> ? Key : never;
}[keyof Value];

export function requiredWireValue<Value>(value: Value | null | undefined): Value {
    if (value === null || value === undefined) throw new ProviderWireError();
    return value;
}

export function omitOptionalWire<Value extends object>(
    value: Value, keys: ReadonlyArray<OptionalWireKey<Value>>,
): Value {
    const result = { ...value };
    for (const key of keys) {
        if (result[key] === undefined) delete result[key];
    }
    return result;
}

export function mapWireRecord<Input, Output>(
    value: Readonly<Record<string, Input>>, convert: (member: Input) => Output,
): Record<string, Output> {
    return Object.fromEntries(Object.entries(value).map(
        ([key, member]): [string, Output] => [key, convert(member)],
    ));
}
