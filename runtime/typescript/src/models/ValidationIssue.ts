import { providerObject, type ProviderField } from '../providerGuard.js';
import { AllocationMismatchDirection, AllocationMismatchDirectionFromJSON } from './AllocationMismatchDirection.js';
import { ProblemField, ProblemFieldFromJSON } from './ProblemField.js';
import { ValidationReason, ValidationReasonFromJSON } from './ValidationReason.js';

export interface ValidationIssue {
    readonly field: ProblemField;
    readonly reason: ValidationReason;
    readonly direction?: AllocationMismatchDirection;
}

const FIELDS: Readonly<Record<string, ProviderField>> = {
    field: { name: 'field', required: true, kind: 'string' },
    reason: { name: 'reason', required: true, kind: 'string' },
    direction: { name: 'direction', required: false, kind: 'string' },
};

export function validateDirection(reason: ValidationReason, value: unknown, present: boolean): void {
    if (reason === ValidationReason.AllocationSumMismatch) {
        if (!present) throw new TypeError('validation direction rejected');
        AllocationMismatchDirectionFromJSON(value);
    } else if (present) throw new TypeError('validation direction rejected');
}

export function ValidationIssueFromJSON(value: unknown): ValidationIssue {
    const wire = providerObject(value, FIELDS, true);
    const reason = ValidationReasonFromJSON(wire.reason);
    validateDirection(reason, wire.direction, Object.hasOwn(wire, 'direction'));
    return { field: ProblemFieldFromJSON(wire.field), reason,
        ...(Object.hasOwn(wire, 'direction') ? { direction: AllocationMismatchDirectionFromJSON(wire.direction) } : {}) };
}

export function ValidationIssueToJSON(value?: ValidationIssue | null): Record<string, unknown> | undefined {
    if (value === undefined) return undefined;
    const model = providerObject(value, FIELDS, false);
    const reason = ValidationReasonFromJSON(model.reason);
    validateDirection(reason, model.direction, model.direction !== undefined);
    return { field: ProblemFieldFromJSON(model.field), reason,
        ...(model.direction !== undefined ? { direction: AllocationMismatchDirectionFromJSON(model.direction) } : {}) };
}

export function ValidationIssueFromJSONTyped(value: unknown, _ignoreDiscriminator: boolean): ValidationIssue {
    return ValidationIssueFromJSON(value);
}
export function ValidationIssueToJSONTyped(value?: ValidationIssue | null, _ignoreDiscriminator: boolean = false): Record<string, unknown> | undefined {
    return ValidationIssueToJSON(value);
}
export function instanceOfValidationIssue(value: object): value is ValidationIssue {
    return 'field' in value && 'reason' in value;
}
