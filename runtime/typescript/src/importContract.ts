// Pure T-CON-10 receipt/mapping verification, not import, dedup or authorisation implementation.
import {
    ConfidenceBand, DedupDecidedBy, DedupMatchBasis, DedupOutcomeKind, DedupPrecedenceRule, DedupWindow, DuplicateSuppressionStatus,
    ImportColumnTarget, ImportCommitAction, ImportRowAction, ImportRowReason, ProblemCode,
    DedupOutcomeFromJSON, ImportPreviewFromJSON, ImportCommitRequestFromJSON, ImportCommitResultFromJSON, ImportCommitResultToJSON,
    type DedupOutcome, type ImportColumnMapping, type ImportCommitRequest, type ImportCommitResult, type ImportPreview,
} from './models/index.js';
import { IMPORT_GROUP_VERSION, MAX_IMPORT_COLUMNS, MAX_IMPORT_ROWS, SOURCE_PRECEDENCE, sourceWindow } from './importPolicy.js';
import { validateProblemWire } from './models/ServiceProblemDetail.js';

export class ImportContractError extends TypeError {
    constructor(readonly reason: string) { super(`import contract rejected: ${reason}`); }
}
function check(condition: boolean, reason: string): asserts condition {
    if (!condition) throw new ImportContractError(reason);
}
function identifier(value: unknown, prefix: string): void {
    check(typeof value === 'string' && value.length === prefix.length + 36 &&
        new RegExp(`^${prefix}[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`).test(value), 'reference');
}
function shape(value: unknown, required: string, optional: string = ''): Record<string, unknown> {
    check(value !== null && typeof value === 'object' && !Array.isArray(value), 'shape');
    const fields: Record<string, unknown> = Object.fromEntries(Object.entries(value));
    const needed = required.split(' ').filter(Boolean), allowed = new Set([...needed, ...optional.split(' ').filter(Boolean)]);
    check(needed.every((key) => Object.hasOwn(fields, key)) && Object.keys(fields).every((key) => allowed.has(key)) &&
        Object.values(fields).every((item) => item !== null && item !== undefined), 'shape');
    return fields;
}
function array(value: unknown): unknown[] {
    check(Array.isArray(value), 'shape');
    return value;
}
function closedDedup(value: unknown): void {
    const wire = shape(value, 'group_version outcome', 'matched_record_id match_basis precedence confidence_band window link enrichment');
    if (Object.hasOwn(wire, 'precedence')) shape(wire.precedence, 'rule user_confirmed_preserved', 'incoming_source compared_source surviving_source');
    if (Object.hasOwn(wire, 'link')) shape(wire.link, 'link_id suppressed_record_id status decided_by can_unmerge', 'reversed_at');
    if (Object.hasOwn(wire, 'enrichment')) for (const item of array(wire.enrichment)) shape(item, 'field source source_record_id');
}
function closedRow(value: unknown, commit: boolean): void {
    const wire = shape(value, 'source_row action reason', commit ? 'created_record_id dedup_outcome row_error' : 'confidence_band dedup_outcome row_error');
    if (Object.hasOwn(wire, 'dedup_outcome')) closedDedup(wire.dedup_outcome);
    if (Object.hasOwn(wire, 'row_error')) {
        const error = shape(wire.row_error, 'source_row problem', 'column_index');
        validateProblemWire(error.problem);
    }
}
function closedPreview(value: unknown): void {
    const wire = shape(value, 'group_version preview_ref created_at expires_at correlation_id mapping rows counts');
    const mapping = shape(wire.mapping, 'column_count detected_columns user_overrides unmapped_columns', 'institution_preset');
    for (const name of ['detected_columns', 'user_overrides']) for (const binding of array(mapping[name])) shape(binding, 'column_index target');
    const unmapped = array(mapping.unmapped_columns);
    check(unmapped.every((index, position) => {
        const previous = unmapped[position - 1];
        return typeof index === 'number' && Number.isInteger(index) &&
            (position === 0 || (typeof previous === 'number' && previous < index));
    }), 'mapping');
    shape(wire.counts, 'row_count create_count skip_count reject_count review_count');
    for (const row of array(wire.rows)) closedRow(row, false);
}
function closedResult(value: unknown): void {
    const wire = shape(value, 'group_version preview_ref completed_at correlation_id rows counts');
    shape(wire.counts, 'row_count created_count skipped_count rejected_count');
    for (const row of array(wire.rows)) closedRow(row, true);
}

export function verifyMapping(mapping: ImportColumnMapping, complete: boolean = false): void {
    check(Number.isInteger(mapping.columnCount) && mapping.columnCount >= 1 && mapping.columnCount <= MAX_IMPORT_COLUMNS, 'mapping');
    const effective = new Map<number, ImportColumnTarget>();
    for (const bindings of [mapping.detectedColumns, mapping.userOverrides]) {
        let previous = 0;
        for (const binding of bindings) {
            check(Number.isInteger(binding.columnIndex) && binding.columnIndex > previous && binding.columnIndex <= mapping.columnCount, 'mapping');
            previous = binding.columnIndex;
            effective.set(binding.columnIndex, binding.target);
        }
    }
    check(new Set(effective.values()).size === effective.size, 'mapping');
    const expected = Array.from({ length: mapping.columnCount }, (_, index) => index + 1).filter((index) => !effective.has(index));
    check(JSON.stringify([...mapping.unmappedColumns]) === JSON.stringify(expected), 'mapping');
    if (mapping.institutionPreset !== undefined) identifier(mapping.institutionPreset, 'pre_');
    if (complete) {
        const targets = new Set(effective.values());
        check(targets.has(ImportColumnTarget.Amount) && targets.has(ImportColumnTarget.Currency) &&
            (targets.has(ImportColumnTarget.OccurredAt) || targets.has(ImportColumnTarget.ValueDate)), 'mapping_incomplete');
    }
}

export function verifyDedup(outcome: DedupOutcome): void {
    check(outcome.groupVersion === IMPORT_GROUP_VERSION, 'version');
    if (outcome.outcome === DedupOutcomeKind.Clear) {
        check(outcome.matchedRecordId === undefined && outcome.matchBasis === undefined && outcome.precedence === undefined &&
            outcome.confidenceBand === undefined && outcome.window === undefined && outcome.link === undefined && outcome.enrichment === undefined, 'dedup');
        return;
    }
    identifier(outcome.matchedRecordId, 'rec_');
    const precedence = outcome.precedence;
    check(precedence !== undefined && outcome.matchBasis !== undefined && outcome.window !== undefined, 'dedup');
    check(precedence.userConfirmedPreserved === true, 'precedence');
    if (outcome.matchBasis === DedupMatchBasis.OperationScreen) {
        check(outcome.outcome === DedupOutcomeKind.DuplicateSuspected && precedence.rule === DedupPrecedenceRule.OperationScreen &&
            precedence.incomingSource === undefined && precedence.comparedSource === undefined && precedence.survivingSource === undefined &&
            [DedupWindow.SameLocalDay, DedupWindow.OperationDefined].includes(outcome.window) &&
            outcome.confidenceBand === undefined && outcome.link === undefined && outcome.enrichment === undefined, 'dedup');
        return;
    }
    const left = precedence.incomingSource, right = precedence.comparedSource, survivor = precedence.survivingSource;
    check(left !== undefined && right !== undefined && survivor !== undefined && outcome.confidenceBand !== undefined, 'precedence');
    check(precedence.rule !== DedupPrecedenceRule.OperationScreen && [left, right].includes(survivor), 'precedence');
    check(outcome.window === sourceWindow(left, right), 'window');
    if (precedence.rule === DedupPrecedenceRule.SourceOrder) check(SOURCE_PRECEDENCE.indexOf(survivor) ===
        Math.min(SOURCE_PRECEDENCE.indexOf(left), SOURCE_PRECEDENCE.indexOf(right)), 'precedence');
    if (precedence.rule === DedupPrecedenceRule.SameSourceExisting) check(left === right && right === survivor, 'precedence');
    if (outcome.outcome === DedupOutcomeKind.NeedsReview) check(outcome.confidenceBand === ConfidenceBand.Low, 'confidence');
    if (outcome.outcome !== DedupOutcomeKind.Linked && outcome.outcome !== DedupOutcomeKind.LinkReversed) {
        check(outcome.link === undefined && outcome.enrichment === undefined, 'dedup');
        return;
    }
    const link = outcome.link;
    check(link !== undefined && outcome.enrichment !== undefined, 'link');
    identifier(link.linkId, 'dln_');
    identifier(link.suppressedRecordId, 'rec_');
    check(link.suppressedRecordId !== outcome.matchedRecordId, 'link');
    check(outcome.matchBasis !== DedupMatchBasis.StructuredCandidate || link.decidedBy === DedupDecidedBy.User, 'basis');
    check(outcome.enrichment.length <= 7 && new Set(outcome.enrichment.map((item) => item.field)).size === outcome.enrichment.length, 'enrichment');
    for (const item of outcome.enrichment) identifier(item.sourceRecordId, 'rec_');
    if (outcome.outcome === DedupOutcomeKind.Linked) check(outcome.confidenceBand === ConfidenceBand.High &&
        link.status === DuplicateSuppressionStatus.SuppressedByLink && link.canUnmerge === true && link.reversedAt === undefined, 'link');
    else check(link.status === DuplicateSuppressionStatus.Restored && link.canUnmerge === false && link.reversedAt !== undefined, 'link');
}

function positions(values: number[]): void {
    check(values.length <= MAX_IMPORT_ROWS && values.every((index, position) => Number.isInteger(index) &&
        index >= 1 && index <= 2147483647 && (position === 0 || index > (values[position - 1] ?? 0))), 'rows');
}
export function verifyPreview(preview: ImportPreview): void {
    check(preview.groupVersion === IMPORT_GROUP_VERSION, 'version');
    identifier(preview.previewRef, 'prv_');
    identifier(preview.correlationId, 'cor_');
    check(preview.expiresAt.compare(preview.createdAt) > 0, 'expiry');
    verifyMapping(preview.mapping);
    positions(preview.rows.map((row) => row.sourceRow));
    const counts = { create: 0, skip: 0, reject: 0, review: 0 };
    for (const row of preview.rows) {
        counts[row.action] += 1;
        if (row.dedupOutcome !== undefined) verifyDedup(row.dedupOutcome);
        if (row.rowError !== undefined) {
            check(row.rowError.sourceRow === row.sourceRow &&
                [ProblemCode.ValidationRejected, ProblemCode.ImportMappingRequired].includes(row.rowError.problem.code), 'row_error');
            check(row.rowError.columnIndex === undefined || (Number.isInteger(row.rowError.columnIndex) &&
                row.rowError.columnIndex >= 1 && row.rowError.columnIndex <= preview.mapping.columnCount), 'row_error');
        }
        if (row.action === ImportRowAction.Create) check(row.reason === ImportRowReason.Ready && row.confidenceBand === ConfidenceBand.High &&
            row.rowError === undefined && (row.dedupOutcome === undefined || row.dedupOutcome.outcome === DedupOutcomeKind.Clear), 'row_action');
        else if (row.action === ImportRowAction.Skip) {
            check([ImportRowReason.Duplicate, ImportRowReason.NotSelected].includes(row.reason) && row.rowError === undefined, 'row_action');
            if (row.reason === ImportRowReason.Duplicate) check(row.dedupOutcome !== undefined &&
                [DedupOutcomeKind.DuplicateSuspected, DedupOutcomeKind.Linked].includes(row.dedupOutcome.outcome) &&
                row.dedupOutcome.confidenceBand === ConfidenceBand.High, 'row_action');
        } else if (row.action === ImportRowAction.Reject) check(row.reason === ImportRowReason.Invalid && row.rowError !== undefined, 'row_action');
        else check(row.reason === ImportRowReason.NeedsReview && row.confidenceBand === ConfidenceBand.Low &&
            row.rowError === undefined && (row.dedupOutcome === undefined || row.dedupOutcome.outcome === DedupOutcomeKind.NeedsReview), 'row_action');
    }
    const reported = preview.counts;
    check(reported.rowCount === preview.rows.length && reported.createCount === counts.create &&
        reported.skipCount === counts.skip && reported.rejectCount === counts.reject && reported.reviewCount === counts.review, 'counts');
}
export function verifyCommitResult(result: ImportCommitResult): void {
    check(result.groupVersion === IMPORT_GROUP_VERSION, 'version');
    identifier(result.previewRef, 'prv_');
    identifier(result.correlationId, 'cor_');
    positions(result.rows.map((row) => row.sourceRow));
    const counts = { created: 0, skipped: 0, rejected: 0 }, created = new Set<string>();
    for (const row of result.rows) {
        counts[row.action] += 1;
        if (row.dedupOutcome !== undefined) verifyDedup(row.dedupOutcome);
        if (row.rowError !== undefined) {
            check(row.rowError.sourceRow === row.sourceRow &&
                [ProblemCode.ValidationRejected, ProblemCode.ImportMappingRequired].includes(row.rowError.problem.code), 'row_error');
            check(row.rowError.columnIndex === undefined || (Number.isInteger(row.rowError.columnIndex) &&
                row.rowError.columnIndex >= 1 && row.rowError.columnIndex <= MAX_IMPORT_COLUMNS), 'row_error');
        }
        if (row.action === ImportCommitAction.Created) {
            identifier(row.createdRecordId, 'rec_');
            check(row.createdRecordId !== undefined && !created.has(row.createdRecordId) && row.reason === ImportRowReason.Ready &&
                row.rowError === undefined && row.dedupOutcome === undefined, 'row_action');
            created.add(row.createdRecordId);
        } else check(row.createdRecordId === undefined, 'row_action');
        if (row.action === ImportCommitAction.Skipped) {
            check([ImportRowReason.Duplicate, ImportRowReason.NotSelected].includes(row.reason) && row.rowError === undefined, 'row_action');
            if (row.reason === ImportRowReason.Duplicate) check(row.dedupOutcome !== undefined && row.dedupOutcome.outcome === DedupOutcomeKind.Linked, 'row_action');
        }
        if (row.action === ImportCommitAction.Rejected) check(row.reason === ImportRowReason.Invalid && row.rowError !== undefined && row.dedupOutcome === undefined, 'row_action');
    }
    const reported = result.counts;
    check(reported.rowCount === result.rows.length && reported.createdCount === counts.created &&
        reported.skippedCount === counts.skipped && reported.rejectedCount === counts.rejected, 'counts');
}
export function verifyCommit(preview: ImportPreview, result: ImportCommitResult): void {
    verifyPreview(preview);
    verifyCommitResult(result);
    verifyMapping(preview.mapping, true);
    check(preview.counts.reviewCount === 0 && preview.previewRef === result.previewRef && result.completedAt.compare(preview.createdAt) >= 0 &&
        JSON.stringify(preview.rows.map((row) => row.sourceRow)) === JSON.stringify(result.rows.map((row) => row.sourceRow)), 'preview_binding');
    preview.rows.forEach((planned, index) => {
        const actual = result.rows[index];
        check(actual !== undefined, 'preview_binding');
        if (planned.action === ImportRowAction.Skip) {
            check(actual.action === ImportCommitAction.Skipped && actual.reason === planned.reason, 'preview_decision');
            if (planned.reason === ImportRowReason.Duplicate) check(planned.dedupOutcome !== undefined && actual.dedupOutcome !== undefined &&
                planned.dedupOutcome.matchedRecordId === actual.dedupOutcome.matchedRecordId, 'preview_decision');
        }
        if (planned.action === ImportRowAction.Reject) check(actual.action === ImportCommitAction.Rejected, 'preview_decision');
    });
}
export function verifyReplay(original: ImportCommitResult, replay: ImportCommitResult): void {
    verifyCommitResult(original);
    verifyCommitResult(replay);
    check(JSON.stringify(ImportCommitResultToJSON(original)) === JSON.stringify(ImportCommitResultToJSON(replay)), 'replay_changed');
}
export function previewFromWire(wire: unknown): ImportPreview {
    closedPreview(wire);
    const value = ImportPreviewFromJSON(wire);
    verifyPreview(value);
    return value;
}
export function commitRequestFromWire(wire: unknown): ImportCommitRequest {
    shape(wire, 'group_version preview_ref');
    const value = ImportCommitRequestFromJSON(wire);
    identifier(value.previewRef, 'prv_');
    check(value.groupVersion === IMPORT_GROUP_VERSION, 'version');
    return value;
}
export function commitResultFromWire(wire: unknown): ImportCommitResult {
    closedResult(wire);
    const value = ImportCommitResultFromJSON(wire);
    verifyCommitResult(value);
    return value;
}
export function dedupFromWire(wire: unknown): DedupOutcome {
    closedDedup(wire);
    const value = DedupOutcomeFromJSON(wire);
    verifyDedup(value);
    return value;
}
