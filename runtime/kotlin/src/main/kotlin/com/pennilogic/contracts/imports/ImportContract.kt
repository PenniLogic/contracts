// Pure T-CON-10 receipt/mapping verification, not import, dedup or authorisation implementation.
package com.pennilogic.contracts.imports

import com.pennilogic.contracts.models.*
import com.pennilogic.contracts.serialization.PennilogicJson
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.*

class ImportContractException(val reason: String) : IllegalArgumentException("import contract rejected: $reason")

object ImportContract {
    private const val UUID = "[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
    private fun check(condition: Boolean, reason: String) {
        if (!condition) throw ImportContractException(reason)
    }
    private fun identifier(value: String?, prefix: String) {
        check(value != null && Regex("$prefix$UUID").matches(value), "reference")
    }
    private fun shape(value: JsonElement?, required: String, optional: String = ""): JsonObject {
        val needed = required.split(" ").filter { it.isNotEmpty() }.toSet()
        val allowed = needed + optional.split(" ").filter { it.isNotEmpty() }
        check(value is JsonObject, "shape")
        require(value is JsonObject)
        check(value.keys.containsAll(needed) && allowed.containsAll(value.keys) && value.values.none { it == JsonNull }, "shape")
        return value
    }
    private fun array(value: JsonElement?): JsonArray {
        check(value is JsonArray, "shape")
        require(value is JsonArray)
        return value
    }
    private fun closedDedup(value: JsonElement?) {
        val wire = shape(value, "group_version outcome", "matched_record_id match_basis precedence confidence_band window link enrichment")
        wire["precedence"]?.let { shape(it, "rule user_confirmed_preserved", "incoming_source compared_source surviving_source") }
        wire["link"]?.let { shape(it, "link_id suppressed_record_id status decided_by can_unmerge", "reversed_at") }
        wire["enrichment"]?.let { items -> array(items).forEach { shape(it, "field source source_record_id") } }
    }
    private fun closedRow(value: JsonElement, commit: Boolean) {
        val wire = shape(value, "source_row action reason",
            if (commit) "created_record_id dedup_outcome row_error" else "confidence_band dedup_outcome row_error")
        wire["dedup_outcome"]?.let { closedDedup(it) }
        wire["row_error"]?.let {
            val error = shape(it, "source_row problem", "column_index")
            ServiceProblemContract.fromJson(error.getValue("problem").toString())
        }
    }
    private fun closedPreview(value: JsonElement) {
        val wire = shape(value, "group_version preview_ref created_at expires_at correlation_id mapping rows counts")
        val mapping = shape(wire["mapping"], "column_count detected_columns user_overrides unmapped_columns", "institution_preset")
        listOf("detected_columns", "user_overrides").forEach { name ->
            array(mapping[name]).forEach { shape(it, "column_index target") }
        }
        val unmapped = array(mapping["unmapped_columns"]).map {
            check(it is JsonPrimitive && !it.isString && it.intOrNull != null, "mapping")
            it.jsonPrimitive.int
        }
        check(unmapped == unmapped.distinct().sorted(), "mapping")
        shape(wire["counts"], "row_count create_count skip_count reject_count review_count")
        array(wire["rows"]).forEach { closedRow(it, false) }
    }
    private fun closedResult(value: JsonElement) {
        val wire = shape(value, "group_version preview_ref completed_at correlation_id rows counts")
        shape(wire["counts"], "row_count created_count skipped_count rejected_count")
        array(wire["rows"]).forEach { closedRow(it, true) }
    }
    private fun parse(text: String): JsonElement = try { PennilogicJson.json.parseToJsonElement(text) }
    catch (_: SerializationException) { throw ImportContractException("json") }

    fun verifyMapping(mapping: ImportColumnMapping, complete: Boolean = false) {
        check(mapping.columnCount in 1..ImportPolicy.MAX_COLUMNS, "mapping")
        val effective = mutableMapOf<Int, ImportColumnTarget>()
        listOf(mapping.detectedColumns, mapping.userOverrides).forEach { bindings ->
            val positions = bindings.map { it.columnIndex }
            check(positions == positions.distinct().sorted() && positions.all { it in 1..mapping.columnCount }, "mapping")
            bindings.forEach { effective[it.columnIndex] = it.target }
        }
        check(effective.values.distinct().size == effective.size, "mapping")
        check(mapping.unmappedColumns.toList() == (1..mapping.columnCount).filter { it !in effective }, "mapping")
        mapping.institutionPreset?.let { identifier(it, "pre_") }
        if (complete) check(ImportColumnTarget.AMOUNT in effective.values && ImportColumnTarget.CURRENCY in effective.values &&
            (ImportColumnTarget.OCCURRED_AT in effective.values || ImportColumnTarget.VALUE_DATE in effective.values), "mapping_incomplete")
    }

    fun verifyDedup(outcome: DedupOutcome) {
        check(outcome.groupVersion.value == ImportPolicy.VERSION, "version")
        if (outcome.outcome == DedupOutcomeKind.CLEAR) {
            check(outcome.matchedRecordId == null && outcome.matchBasis == null && outcome.precedence == null &&
                outcome.confidenceBand == null && outcome.window == null && outcome.link == null && outcome.enrichment == null, "dedup")
            return
        }
        identifier(outcome.matchedRecordId, "rec_")
        val precedence = outcome.precedence
        check(precedence != null && outcome.matchBasis != null && outcome.window != null, "dedup")
        require(precedence != null)
        check(precedence.userConfirmedPreserved, "precedence")
        if (outcome.matchBasis == DedupMatchBasis.OPERATION_SCREEN) {
            check(outcome.outcome == DedupOutcomeKind.DUPLICATE_SUSPECTED && precedence.rule == DedupPrecedenceRule.OPERATION_SCREEN &&
                precedence.incomingSource == null && precedence.comparedSource == null && precedence.survivingSource == null &&
                outcome.window in setOf(DedupWindow.SAME_LOCAL_DAY, DedupWindow.OPERATION_DEFINED) &&
                outcome.confidenceBand == null && outcome.link == null && outcome.enrichment == null, "dedup")
            return
        }
        val left = precedence.incomingSource
        val right = precedence.comparedSource
        val survivor = precedence.survivingSource
        check(left != null && right != null && survivor != null && outcome.confidenceBand != null, "precedence")
        require(left != null && right != null && survivor != null)
        check(precedence.rule != DedupPrecedenceRule.OPERATION_SCREEN && survivor in setOf(left, right), "precedence")
        check(outcome.window == ImportPolicy.sourceWindow(left, right), "window")
        if (precedence.rule == DedupPrecedenceRule.SOURCE_ORDER) check(ImportPolicy.sourcePrecedence.indexOf(survivor) ==
            minOf(ImportPolicy.sourcePrecedence.indexOf(left), ImportPolicy.sourcePrecedence.indexOf(right)), "precedence")
        if (precedence.rule == DedupPrecedenceRule.SAME_SOURCE_EXISTING) check(left == right && right == survivor, "precedence")
        if (outcome.outcome == DedupOutcomeKind.NEEDS_REVIEW) check(outcome.confidenceBand == ConfidenceBand.LOW, "confidence")
        if (outcome.outcome !in setOf(DedupOutcomeKind.LINKED, DedupOutcomeKind.LINK_REVERSED)) {
            check(outcome.link == null && outcome.enrichment == null, "dedup")
            return
        }
        val link = outcome.link
        val enrichment = outcome.enrichment
        check(link != null && enrichment != null, "link")
        require(link != null && enrichment != null)
        identifier(link.linkId, "dln_")
        identifier(link.suppressedRecordId, "rec_")
        check(link.suppressedRecordId != outcome.matchedRecordId, "link")
        check(outcome.matchBasis != DedupMatchBasis.STRUCTURED_CANDIDATE || link.decidedBy == DedupDecidedBy.USER, "basis")
        check(enrichment.size <= 7 && enrichment.map { it.field }.distinct().size == enrichment.size, "enrichment")
        enrichment.forEach { identifier(it.sourceRecordId, "rec_") }
        if (outcome.outcome == DedupOutcomeKind.LINKED) check(outcome.confidenceBand == ConfidenceBand.HIGH &&
            link.status == DuplicateSuppressionStatus.SUPPRESSED_BY_LINK && link.canUnmerge && link.reversedAt == null, "link")
        else check(link.status == DuplicateSuppressionStatus.RESTORED && !link.canUnmerge && link.reversedAt != null, "link")
    }

    private fun positions(values: List<Int>) {
        check(values.size <= ImportPolicy.MAX_ROWS && values == values.distinct().sorted() && values.all { it >= 1 }, "rows")
    }
    fun verifyPreview(preview: ImportPreview) {
        check(preview.groupVersion.value == ImportPolicy.VERSION, "version")
        identifier(preview.previewRef, "prv_")
        identifier(preview.correlationId, "cor_")
        check(preview.expiresAt > preview.createdAt, "expiry")
        verifyMapping(preview.mapping)
        positions(preview.rows.map { it.sourceRow })
        preview.rows.forEach { row ->
            row.dedupOutcome?.let { verifyDedup(it) }
            row.rowError?.let {
                check(it.sourceRow == row.sourceRow &&
                    it.problem.code in setOf(ProblemCode.VALIDATION_REJECTED, ProblemCode.IMPORT_MAPPING_REQUIRED), "row_error")
                check(it.columnIndex == null || it.columnIndex in 1..preview.mapping.columnCount, "row_error")
            }
            when (row.action) {
                ImportRowAction.CREATE -> check(row.reason == ImportRowReason.READY && row.confidenceBand == ConfidenceBand.HIGH &&
                    row.rowError == null && (row.dedupOutcome == null || row.dedupOutcome.outcome == DedupOutcomeKind.CLEAR), "row_action")
                ImportRowAction.SKIP -> {
                    check(row.reason in setOf(ImportRowReason.DUPLICATE, ImportRowReason.NOT_SELECTED) && row.rowError == null, "row_action")
                    if (row.reason == ImportRowReason.DUPLICATE) check(row.dedupOutcome != null &&
                        row.dedupOutcome.outcome in setOf(DedupOutcomeKind.DUPLICATE_SUSPECTED, DedupOutcomeKind.LINKED) &&
                        row.dedupOutcome.confidenceBand == ConfidenceBand.HIGH, "row_action")
                }
                ImportRowAction.REJECT -> check(row.reason == ImportRowReason.INVALID && row.rowError != null, "row_action")
                ImportRowAction.REVIEW -> check(row.reason == ImportRowReason.NEEDS_REVIEW && row.confidenceBand == ConfidenceBand.LOW &&
                    row.rowError == null && (row.dedupOutcome == null || row.dedupOutcome.outcome == DedupOutcomeKind.NEEDS_REVIEW), "row_action")
            }
        }
        val counts = preview.counts
        check(counts.rowCount == preview.rows.size &&
            counts.createCount == preview.rows.count { it.action == ImportRowAction.CREATE } &&
            counts.skipCount == preview.rows.count { it.action == ImportRowAction.SKIP } &&
            counts.rejectCount == preview.rows.count { it.action == ImportRowAction.REJECT } &&
            counts.reviewCount == preview.rows.count { it.action == ImportRowAction.REVIEW }, "counts")
    }
    fun verifyCommitResult(result: ImportCommitResult) {
        check(result.groupVersion.value == ImportPolicy.VERSION, "version")
        identifier(result.previewRef, "prv_")
        identifier(result.correlationId, "cor_")
        positions(result.rows.map { it.sourceRow })
        val created = mutableSetOf<String>()
        result.rows.forEach { row ->
            row.dedupOutcome?.let { verifyDedup(it) }
            row.rowError?.let {
                check(it.sourceRow == row.sourceRow &&
                    it.problem.code in setOf(ProblemCode.VALIDATION_REJECTED, ProblemCode.IMPORT_MAPPING_REQUIRED), "row_error")
                check(it.columnIndex == null || it.columnIndex in 1..ImportPolicy.MAX_COLUMNS, "row_error")
            }
            if (row.action == ImportCommitAction.CREATED) {
                identifier(row.createdRecordId, "rec_")
                check(row.createdRecordId != null && created.add(row.createdRecordId) && row.reason == ImportRowReason.READY &&
                    row.rowError == null && row.dedupOutcome == null, "row_action")
            } else check(row.createdRecordId == null, "row_action")
            if (row.action == ImportCommitAction.SKIPPED) {
                check(row.reason in setOf(ImportRowReason.DUPLICATE, ImportRowReason.NOT_SELECTED) && row.rowError == null, "row_action")
                if (row.reason == ImportRowReason.DUPLICATE) check(row.dedupOutcome?.outcome == DedupOutcomeKind.LINKED, "row_action")
            }
            if (row.action == ImportCommitAction.REJECTED) check(row.reason == ImportRowReason.INVALID &&
                row.rowError != null && row.dedupOutcome == null, "row_action")
        }
        val counts = result.counts
        check(counts.rowCount == result.rows.size &&
            counts.createdCount == result.rows.count { it.action == ImportCommitAction.CREATED } &&
            counts.skippedCount == result.rows.count { it.action == ImportCommitAction.SKIPPED } &&
            counts.rejectedCount == result.rows.count { it.action == ImportCommitAction.REJECTED }, "counts")
    }
    fun verifyCommit(preview: ImportPreview, result: ImportCommitResult) {
        verifyPreview(preview)
        verifyCommitResult(result)
        verifyMapping(preview.mapping, true)
        check(preview.counts.reviewCount == 0 && preview.previewRef == result.previewRef && result.completedAt >= preview.createdAt &&
            preview.rows.map { it.sourceRow } == result.rows.map { it.sourceRow }, "preview_binding")
        preview.rows.zip(result.rows).forEach { (planned, actual) ->
            if (planned.action == ImportRowAction.SKIP) {
                check(actual.action == ImportCommitAction.SKIPPED && actual.reason == planned.reason, "preview_decision")
                if (planned.reason == ImportRowReason.DUPLICATE) check(planned.dedupOutcome != null && actual.dedupOutcome != null &&
                    planned.dedupOutcome.matchedRecordId == actual.dedupOutcome.matchedRecordId, "preview_decision")
            }
            if (planned.action == ImportRowAction.REJECT) check(actual.action == ImportCommitAction.REJECTED, "preview_decision")
        }
    }
    fun verifyReplay(original: ImportCommitResult, replay: ImportCommitResult) {
        verifyCommitResult(original)
        verifyCommitResult(replay)
        check(PennilogicJson.json.encodeToJsonElement(original) == PennilogicJson.json.encodeToJsonElement(replay), "replay_changed")
    }

    fun previewFromJson(text: String): ImportPreview {
        val wire = parse(text)
        closedPreview(wire)
        val value = try { PennilogicJson.json.decodeFromJsonElement<ImportPreview>(wire) }
        catch (_: SerializationException) { throw ImportContractException("shape") }
        verifyPreview(value)
        return value
    }
    fun commitRequestFromJson(text: String): ImportCommitRequest {
        val wire = parse(text)
        shape(wire, "group_version preview_ref")
        val value = try { PennilogicJson.json.decodeFromJsonElement<ImportCommitRequest>(wire) }
        catch (_: SerializationException) { throw ImportContractException("shape") }
        identifier(value.previewRef, "prv_")
        check(value.groupVersion.value == ImportPolicy.VERSION, "version")
        return value
    }
    fun commitResultFromJson(text: String): ImportCommitResult {
        val wire = parse(text)
        closedResult(wire)
        val value = try { PennilogicJson.json.decodeFromJsonElement<ImportCommitResult>(wire) }
        catch (_: SerializationException) { throw ImportContractException("shape") }
        verifyCommitResult(value)
        return value
    }
    fun dedupFromJson(text: String): DedupOutcome {
        val wire = parse(text)
        closedDedup(wire)
        val value = try { PennilogicJson.json.decodeFromJsonElement<DedupOutcome>(wire) }
        catch (_: SerializationException) { throw ImportContractException("shape") }
        verifyDedup(value)
        return value
    }
}
