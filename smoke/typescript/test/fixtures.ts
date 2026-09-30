// Fixture loading shared by the TypeScript smoke tests. PL_CONTRACTS_ROOT is set by scripts/smoke.py.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const root = process.env['PL_CONTRACTS_ROOT'];
if (root === undefined) {
    throw new Error('PL_CONTRACTS_ROOT must point at the repository root (set by scripts/smoke.py)');
}
export const ROOT: string = root;

export interface MoneyWireFixture {
    readonly reason_order: readonly string[];
    readonly parse_reason_order: readonly string[];
    readonly parse_invalid: readonly { readonly name: string; readonly amount: string; readonly currency: string; readonly reason: string }[];
    readonly valid: readonly { readonly name: string; readonly wire: { readonly amount: string; readonly currency: string }; readonly minor_units: string }[];
    readonly invalid: readonly { readonly name: string; readonly wire: unknown; readonly reason: string; readonly field: string }[];
}

export interface InstantWireFixture {
    readonly reason_order: readonly string[];
    readonly valid: readonly { readonly name: string; readonly wire: string; readonly epoch_millis: string }[];
    readonly invalid: readonly { readonly name: string; readonly wire: unknown; readonly reason: string }[];
}

export function loadFixture<T>(name: string): T {
    return JSON.parse(readFileSync(join(ROOT, 'spec', 'fixtures', name), 'utf8')) as T;
}
