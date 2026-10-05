"""Pure T-CON-10 receipt/mapping verification, not import, dedup or authorisation implementation."""

from __future__ import annotations

import re
from typing import Any, Mapping

from pennilogic_contracts.import_policy import (
    IMPORT_GROUP_VERSION, MAX_IMPORT_COLUMNS, MAX_IMPORT_ROWS, SOURCE_PRECEDENCE, source_window,
)
from pennilogic_contracts.models import (
    ConfidenceBand, DedupDecidedBy, DedupMatchBasis, DedupOutcome, DedupOutcomeKind, DedupPrecedenceRule,
    DedupWindow, DuplicateSuppressionStatus, ImportColumnMapping, ImportColumnTarget,
    ImportCommitAction, ImportCommitRequest, ImportCommitResult, ImportPreview,
    ImportRowAction, ImportRowReason, ProblemCode,
)
from pennilogic_contracts.models.service_problem_detail import validate_problem_wire

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"


class ImportContractError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"import contract rejected: {reason}")


def _check(condition: bool, reason: str) -> None:
    if not condition:
        raise ImportContractError(reason)


def _id(value: object, prefix: str) -> None:
    _check(isinstance(value, str) and re.fullmatch(prefix + _UUID, value) is not None, "reference")


def _shape(value: object, required: str, optional: str = "") -> Mapping[str, Any]:
    required_keys, optional_keys = set(required.split()), set(optional.split())
    _check(isinstance(value, dict), "shape")
    assert isinstance(value, dict)
    _check(required_keys <= value.keys() <= required_keys | optional_keys, "shape")
    _check(all(item is not None for item in value.values()), "shape")
    return value


def _array(value: object) -> list[Any]:
    _check(isinstance(value, list), "shape")
    assert isinstance(value, list)
    return value


def _closed_dedup(value: object) -> None:
    wire = _shape(value, "group_version outcome", "matched_record_id match_basis precedence confidence_band window link enrichment")
    if "precedence" in wire:
        _shape(wire["precedence"], "rule user_confirmed_preserved", "incoming_source compared_source surviving_source")
    if "link" in wire:
        _shape(wire["link"], "link_id suppressed_record_id status decided_by can_unmerge", "reversed_at")
    if "enrichment" in wire:
        for item in _array(wire["enrichment"]):
            _shape(item, "field source source_record_id")


def _closed_row(value: object, commit: bool) -> None:
    wire = _shape(value, "source_row action reason",
                  "created_record_id dedup_outcome row_error" if commit else "confidence_band dedup_outcome row_error")
    if "dedup_outcome" in wire:
        _closed_dedup(wire["dedup_outcome"])
    if "row_error" in wire:
        error = _shape(wire["row_error"], "source_row problem", "column_index")
        validate_problem_wire(error["problem"])


def _closed_preview(value: object) -> None:
    wire = _shape(value, "group_version preview_ref created_at expires_at correlation_id mapping rows counts")
    mapping = _shape(wire["mapping"], "column_count detected_columns user_overrides unmapped_columns", "institution_preset")
    for name in ("detected_columns", "user_overrides"):
        for binding in _array(mapping[name]):
            _shape(binding, "column_index target")
    _array(mapping["unmapped_columns"])
    _shape(wire["counts"], "row_count create_count skip_count reject_count review_count")
    for row in _array(wire["rows"]):
        _closed_row(row, False)


def _closed_result(value: object) -> None:
    wire = _shape(value, "group_version preview_ref completed_at correlation_id rows counts")
    _shape(wire["counts"], "row_count created_count skipped_count rejected_count")
    for row in _array(wire["rows"]):
        _closed_row(row, True)


def verify_mapping(mapping: ImportColumnMapping, *, complete: bool = False) -> None:
    _check(type(mapping.column_count) is int and 1 <= mapping.column_count <= MAX_IMPORT_COLUMNS, "mapping")
    effective: dict[int, ImportColumnTarget] = {}
    for bindings in (mapping.detected_columns, mapping.user_overrides):
        positions = [binding.column_index for binding in bindings]
        _check(positions == sorted(set(positions)) and all(1 <= index <= mapping.column_count for index in positions), "mapping")
        effective.update({binding.column_index: binding.target for binding in bindings})
    _check(len(set(effective.values())) == len(effective), "mapping")
    _check(mapping.unmapped_columns == sorted(set(range(1, mapping.column_count + 1)) - effective.keys()), "mapping")
    if mapping.institution_preset is not None:
        _id(mapping.institution_preset, "pre_")
    if complete:
        targets = set(effective.values())
        _check({ImportColumnTarget.AMOUNT, ImportColumnTarget.CURRENCY} <= targets and
               bool({ImportColumnTarget.OCCURRED_AT, ImportColumnTarget.VALUE_DATE} & targets), "mapping_incomplete")


def verify_dedup(outcome: DedupOutcome) -> None:
    _check(outcome.group_version.value == IMPORT_GROUP_VERSION, "version")
    if outcome.outcome == DedupOutcomeKind.CLEAR:
        _check(all(getattr(outcome, name) is None for name in (
            "matched_record_id", "match_basis", "precedence", "confidence_band", "window", "link", "enrichment")), "dedup")
        return
    _id(outcome.matched_record_id, "rec_")
    precedence = outcome.precedence
    _check(precedence is not None and outcome.match_basis is not None and outcome.window is not None, "dedup")
    assert precedence is not None
    _check(precedence.user_confirmed_preserved is True, "precedence")
    if outcome.match_basis == DedupMatchBasis.OPERATION_SCREEN:
        _check(outcome.outcome == DedupOutcomeKind.DUPLICATE_SUSPECTED and
               precedence.rule == DedupPrecedenceRule.OPERATION_SCREEN and
               all(source is None for source in (precedence.incoming_source, precedence.compared_source, precedence.surviving_source)) and
               outcome.window in (DedupWindow.SAME_LOCAL_DAY, DedupWindow.OPERATION_DEFINED) and
               outcome.confidence_band is None and outcome.link is None and outcome.enrichment is None, "dedup")
        return
    left, right, survivor = precedence.incoming_source, precedence.compared_source, precedence.surviving_source
    _check(left is not None and right is not None and survivor is not None and outcome.confidence_band is not None, "precedence")
    assert left is not None and right is not None and survivor is not None
    _check(precedence.rule != DedupPrecedenceRule.OPERATION_SCREEN and survivor in (left, right), "precedence")
    _check(outcome.window == source_window(left, right), "window")
    if precedence.rule == DedupPrecedenceRule.SOURCE_ORDER:
        _check(SOURCE_PRECEDENCE.index(survivor) == min(SOURCE_PRECEDENCE.index(left), SOURCE_PRECEDENCE.index(right)), "precedence")
    if precedence.rule == DedupPrecedenceRule.SAME_SOURCE_EXISTING:
        _check(left == right == survivor, "precedence")
    if outcome.outcome == DedupOutcomeKind.NEEDS_REVIEW:
        _check(outcome.confidence_band == ConfidenceBand.LOW, "confidence")
    if outcome.outcome not in (DedupOutcomeKind.LINKED, DedupOutcomeKind.LINK_REVERSED):
        _check(outcome.link is None and outcome.enrichment is None, "dedup")
        return
    link = outcome.link
    _check(link is not None and outcome.enrichment is not None, "link")
    assert link is not None and outcome.enrichment is not None
    _id(link.link_id, "dln_")
    _id(link.suppressed_record_id, "rec_")
    _check(link.suppressed_record_id != outcome.matched_record_id, "link")
    _check(outcome.match_basis != DedupMatchBasis.STRUCTURED_CANDIDATE or link.decided_by == DedupDecidedBy.USER, "basis")
    fields = [item.var_field for item in outcome.enrichment]
    _check(len(fields) <= 7 and len(set(fields)) == len(fields), "enrichment")
    for item in outcome.enrichment:
        _id(item.source_record_id, "rec_")
    if outcome.outcome == DedupOutcomeKind.LINKED:
        _check(outcome.confidence_band == ConfidenceBand.HIGH and
               link.status == DuplicateSuppressionStatus.SUPPRESSED_BY_LINK and link.can_unmerge and link.reversed_at is None, "link")
    else:
        _check(link.status == DuplicateSuppressionStatus.RESTORED and not link.can_unmerge and link.reversed_at is not None, "link")


def verify_preview(preview: ImportPreview) -> None:
    _check(preview.group_version.value == IMPORT_GROUP_VERSION, "version")
    _id(preview.preview_ref, "prv_")
    _id(preview.correlation_id, "cor_")
    _check(preview.expires_at > preview.created_at, "expiry")
    verify_mapping(preview.mapping)
    positions = [row.source_row for row in preview.rows]
    _check(len(positions) <= MAX_IMPORT_ROWS and positions == sorted(set(positions)) and
           all(type(index) is int and 1 <= index <= 2147483647 for index in positions), "rows")
    expected = {action: 0 for action in ImportRowAction}
    for row in preview.rows:
        expected[row.action] += 1
        if row.dedup_outcome is not None:
            verify_dedup(row.dedup_outcome)
        if row.row_error is not None:
            _check(row.row_error.source_row == row.source_row and row.row_error.problem.code in (
                ProblemCode.VALIDATION_REJECTED, ProblemCode.IMPORT_MAPPING_REQUIRED), "row_error")
            _check(row.row_error.column_index is None or 1 <= row.row_error.column_index <= preview.mapping.column_count, "row_error")
        if row.action == ImportRowAction.CREATE:
            _check(row.reason == ImportRowReason.READY and row.confidence_band == ConfidenceBand.HIGH and
                   row.row_error is None and (row.dedup_outcome is None or row.dedup_outcome.outcome == DedupOutcomeKind.CLEAR), "row_action")
        elif row.action == ImportRowAction.SKIP:
            _check(row.reason in (ImportRowReason.DUPLICATE, ImportRowReason.NOT_SELECTED) and row.row_error is None, "row_action")
            if row.reason == ImportRowReason.DUPLICATE:
                _check(row.dedup_outcome is not None and row.dedup_outcome.outcome in (
                    DedupOutcomeKind.DUPLICATE_SUSPECTED, DedupOutcomeKind.LINKED) and
                    row.dedup_outcome.confidence_band == ConfidenceBand.HIGH, "row_action")
        elif row.action == ImportRowAction.REJECT:
            _check(row.reason == ImportRowReason.INVALID and row.row_error is not None, "row_action")
        else:
            _check(row.reason == ImportRowReason.NEEDS_REVIEW and row.confidence_band == ConfidenceBand.LOW and
                   row.row_error is None and (row.dedup_outcome is None or row.dedup_outcome.outcome == DedupOutcomeKind.NEEDS_REVIEW), "row_action")
    counts = preview.counts
    _check(counts.row_count == len(preview.rows) and
           [counts.create_count, counts.skip_count, counts.reject_count, counts.review_count] ==
           [expected[ImportRowAction.CREATE], expected[ImportRowAction.SKIP], expected[ImportRowAction.REJECT], expected[ImportRowAction.REVIEW]], "counts")


def verify_commit_result(result: ImportCommitResult) -> None:
    _check(result.group_version.value == IMPORT_GROUP_VERSION, "version")
    _id(result.preview_ref, "prv_")
    _id(result.correlation_id, "cor_")
    positions = [row.source_row for row in result.rows]
    _check(len(positions) <= MAX_IMPORT_ROWS and positions == sorted(set(positions)) and
           all(type(index) is int and 1 <= index <= 2147483647 for index in positions), "rows")
    expected = {action: 0 for action in ImportCommitAction}
    created: set[str] = set()
    for row in result.rows:
        expected[row.action] += 1
        if row.dedup_outcome is not None:
            verify_dedup(row.dedup_outcome)
        if row.row_error is not None:
            _check(row.row_error.source_row == row.source_row and row.row_error.problem.code in (
                ProblemCode.VALIDATION_REJECTED, ProblemCode.IMPORT_MAPPING_REQUIRED), "row_error")
            _check(row.row_error.column_index is None or 1 <= row.row_error.column_index <= MAX_IMPORT_COLUMNS, "row_error")
        if row.action == ImportCommitAction.CREATED:
            _id(row.created_record_id, "rec_")
            _check(row.created_record_id is not None and row.created_record_id not in created and
                   row.reason == ImportRowReason.READY and row.row_error is None and row.dedup_outcome is None, "row_action")
            assert row.created_record_id is not None
            created.add(row.created_record_id)
        else:
            _check(row.created_record_id is None, "row_action")
        if row.action == ImportCommitAction.SKIPPED:
            _check(row.reason in (ImportRowReason.DUPLICATE, ImportRowReason.NOT_SELECTED) and row.row_error is None, "row_action")
            if row.reason == ImportRowReason.DUPLICATE:
                _check(row.dedup_outcome is not None and row.dedup_outcome.outcome == DedupOutcomeKind.LINKED, "row_action")
        if row.action == ImportCommitAction.REJECTED:
            _check(row.reason == ImportRowReason.INVALID and row.row_error is not None and row.dedup_outcome is None, "row_action")
    counts = result.counts
    _check(counts.row_count == len(result.rows) and [counts.created_count, counts.skipped_count, counts.rejected_count] ==
           [expected[ImportCommitAction.CREATED], expected[ImportCommitAction.SKIPPED], expected[ImportCommitAction.REJECTED]], "counts")


def verify_commit(preview: ImportPreview, result: ImportCommitResult) -> None:
    verify_preview(preview)
    verify_commit_result(result)
    verify_mapping(preview.mapping, complete=True)
    _check(preview.counts.review_count == 0 and result.preview_ref == preview.preview_ref and
           result.completed_at >= preview.created_at and
           [row.source_row for row in preview.rows] == [row.source_row for row in result.rows], "preview_binding")
    for planned, actual in zip(preview.rows, result.rows, strict=True):
        if planned.action == ImportRowAction.SKIP:
            _check(actual.action == ImportCommitAction.SKIPPED and actual.reason == planned.reason, "preview_decision")
            if planned.reason == ImportRowReason.DUPLICATE:
                _check(planned.dedup_outcome is not None and actual.dedup_outcome is not None and
                       planned.dedup_outcome.matched_record_id == actual.dedup_outcome.matched_record_id, "preview_decision")
        if planned.action == ImportRowAction.REJECT:
            _check(actual.action == ImportCommitAction.REJECTED, "preview_decision")


def verify_replay(original: ImportCommitResult, replay: ImportCommitResult) -> None:
    verify_commit_result(original)
    verify_commit_result(replay)
    _check(original.to_dict() == replay.to_dict(), "replay_changed")


def preview_from_wire(wire: dict[str, Any]) -> ImportPreview:
    _closed_preview(wire)
    value = ImportPreview.from_dict(wire)
    _check(value is not None, "shape")
    assert value is not None
    verify_preview(value)
    return value


def commit_request_from_wire(wire: dict[str, Any]) -> ImportCommitRequest:
    _shape(wire, "group_version preview_ref")
    value = ImportCommitRequest.from_dict(wire)
    _check(value is not None, "shape")
    assert value is not None
    _id(value.preview_ref, "prv_")
    _check(value.group_version.value == IMPORT_GROUP_VERSION, "version")
    return value


def commit_result_from_wire(wire: dict[str, Any]) -> ImportCommitResult:
    _closed_result(wire)
    value = ImportCommitResult.from_dict(wire)
    _check(value is not None, "shape")
    assert value is not None
    verify_commit_result(value)
    return value


def dedup_from_wire(wire: dict[str, Any]) -> DedupOutcome:
    _closed_dedup(wire)
    value = DedupOutcome.from_dict(wire)
    _check(value is not None, "shape")
    assert value is not None
    verify_dedup(value)
    return value
