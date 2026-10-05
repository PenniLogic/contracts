package com.pennilogic.contracts.smoke

import com.pennilogic.contracts.imports.ImportContract
import com.pennilogic.contracts.imports.ImportPolicy
import com.pennilogic.contracts.models.*
import com.pennilogic.contracts.serialization.PennilogicJson
import kotlin.test.*
import kotlinx.serialization.json.*

class ImportProviderTest {
    private val data = Fixtures.load("import-provider.v1.json")

    private fun mutate(value: JsonElement, change: JsonObject): JsonElement {
        val path = change.getValue("path").jsonArray
        fun edit(node: JsonElement, depth: Int): JsonElement {
            val key = path[depth].jsonPrimitive
            if (node is JsonArray) {
                val rows = node.toMutableList()
                if (depth == path.lastIndex) {
                    if (change["remove"]?.jsonPrimitive?.booleanOrNull == true) rows.removeAt(key.int)
                    else rows[key.int] = change.getValue("value")
                } else rows[key.int] = edit(rows[key.int], depth + 1)
                return JsonArray(rows)
            }
            val fields = node.jsonObject.toMutableMap()
            if (depth == path.lastIndex) {
                if (change["remove"]?.jsonPrimitive?.booleanOrNull == true) fields.remove(key.content)
                else fields[key.content] = change.getValue("value")
            } else fields[key.content] = edit(fields.getValue(key.content), depth + 1)
            return JsonObject(fields)
        }
        return edit(value, 0)
    }
    private fun decode(target: String, wire: JsonElement): Any = when (target) {
        "preview" -> ImportContract.previewFromJson(wire.toString())
        "request" -> ImportContract.commitRequestFromJson(wire.toString())
        "result" -> ImportContract.commitResultFromJson(wire.toString())
        else -> ImportContract.dedupFromJson(wire.toString())
    }

    @Test fun generatedPreviewCommitAndSharedProblemsRoundTrip() {
        val preview = ImportContract.previewFromJson(data.getValue("preview").toString())
        val request = ImportContract.commitRequestFromJson(data.getValue("request").toString())
        val result = ImportContract.commitResultFromJson(data.getValue("result").toString())
        ImportContract.verifyCommit(preview, result)
        assertEquals(preview.previewRef, request.previewRef)
        assertEquals(data.getValue("preview"), PennilogicJson.json.encodeToJsonElement(preview))
        assertEquals(data.getValue("result"), PennilogicJson.json.encodeToJsonElement(result))
        assertEquals(ConfidenceBand.HIGH, preview.rows.first().confidenceBand)
        assertEquals(ImportColumnTarget.VALUE_DATE, preview.mapping.userOverrides.first().target)
    }

    @Test fun everyDedupStateIsTypedVisibleAndReversible() {
        listOf("screen", "suspected", "linked", "reversed", "review", "clear").forEach { name ->
            val outcome = ImportContract.dedupFromJson(data.getValue(name).toString())
            val typed: DedupOutcomeKind = outcome.outcome
            assertEquals(data.getValue(name).jsonObject.getValue("outcome").jsonPrimitive.content, typed.value)
            assertEquals(data.getValue(name), PennilogicJson.json.encodeToJsonElement(outcome))
        }
    }

    @Test fun allSchemaAndSemanticNegativesFailWithoutDisclosure() {
        data.getValue("invalid").jsonArray.forEach { value ->
            val change = value.jsonObject
            val target = change.getValue("target").jsonPrimitive.content
            val error = assertFails(change.getValue("name").jsonPrimitive.content) { decode(target, mutate(data.getValue(target), change)) }
            assertFalse(error.message.orEmpty().contains("SYNTHETIC_"))
            assertFalse(error.message.orEmpty().contains("12.34"))
            assertFalse(error.message.orEmpty().contains("internal-record-42"))
        }
    }

    @Test fun samePreviewReplayPreservesReceiptAndRejectsNewFactOrMetadata() {
        val original = ImportContract.commitResultFromJson(data.getValue("result").toString())
        ImportContract.verifyReplay(original, ImportContract.commitResultFromJson(data.getValue("result").toString()))
        data.getValue("replay_changes").jsonArray.forEach { value ->
            val replay = ImportContract.commitResultFromJson(mutate(data.getValue("result"), value.jsonObject).toString())
            assertFailsWith<IllegalArgumentException> { ImportContract.verifyReplay(original, replay) }
        }
    }

    @Test fun confidenceAndAllSourcePairWindowsAreClosedTypedAndSymmetric() {
        assertEquals(setOf("high", "low"), ConfidenceBand.entries.map { it.value }.toSet())
        ImportPolicy.sourcePrecedence.forEach { left ->
            ImportPolicy.sourcePrecedence.forEach { right ->
                val typed: DedupWindow = ImportPolicy.sourceWindow(left, right)
                assertEquals(typed, ImportPolicy.sourceWindow(right, left))
            }
        }
        listOf("\"medium\"", "\"HIGH\"", "85", "null").forEach { wire ->
            assertFails { PennilogicJson.json.decodeFromString<ConfidenceBand>(wire) }
        }
    }

    @Test fun exactRowBoundAdmits10000AndRejects10001() {
        val original = data.getValue("preview").jsonObject
        val row = original.getValue("rows").jsonArray.first().jsonObject
        val rows = (1..ImportPolicy.MAX_ROWS).map { JsonObject(row + ("source_row" to JsonPrimitive(it))) }
        val counts = buildJsonObject {
            put("row_count", ImportPolicy.MAX_ROWS); put("create_count", ImportPolicy.MAX_ROWS)
            put("skip_count", 0); put("reject_count", 0); put("review_count", 0)
        }
        val bound = JsonObject(original + mapOf("rows" to JsonArray(rows), "counts" to counts))
        assertEquals(10000, ImportContract.previewFromJson(bound.toString()).rows.size)
        val overflow = JsonObject(bound + ("rows" to JsonArray(rows + JsonObject(row + ("source_row" to JsonPrimitive(10001))))))
        assertFails { ImportContract.previewFromJson(overflow.toString()) }
    }

    @Test fun unresolvedReviewAndIncompleteMappingBlockCommit() {
        val original = data.getValue("preview").jsonObject
        val mapping = original.getValue("mapping").jsonObject
        val partial = JsonObject(mapping + mapOf(
            "detected_columns" to JsonArray(mapping.getValue("detected_columns").jsonArray.filter { it.jsonObject["column_index"] != JsonPrimitive(2) }),
            "unmapped_columns" to JsonArray(listOf(JsonPrimitive(2), JsonPrimitive(4))),
        ))
        val preview = ImportContract.previewFromJson(JsonObject(original + ("mapping" to partial)).toString())
        assertFails { ImportContract.verifyMapping(preview.mapping, true) }
        val first = original.getValue("rows").jsonArray.first().jsonObject
        val review = JsonObject(first + mapOf("action" to JsonPrimitive("review"), "reason" to JsonPrimitive("needs_review"), "confidence_band" to JsonPrimitive("low")))
        val rows = JsonArray(listOf(review) + original.getValue("rows").jsonArray.drop(1))
        val counts = JsonObject(original.getValue("counts").jsonObject + mapOf("create_count" to JsonPrimitive(0), "review_count" to JsonPrimitive(1)))
        val unresolved = ImportContract.previewFromJson(JsonObject(original + mapOf("rows" to rows, "counts" to counts)).toString())
        assertFails { ImportContract.verifyCommit(unresolved, ImportContract.commitResultFromJson(data.getValue("result").toString())) }
    }

    @Test fun overrideDenialHasTheSameSafe400WithoutARecordId() {
        val problem = ServiceProblemContract.fromJson(data.getValue("override_rejection").toString())
        assertEquals(400, problem.status)
        assertEquals(data.getValue("override_rejection"), PennilogicJson.json.parseToJsonElement(ServiceProblemContract.toJson(problem)))
    }

    @Test fun commitCannotUpgradeASkipOrSubstituteTheReviewedMatch() {
        data.getValue("decision_changes").jsonArray.forEach { item ->
            val change = item.jsonObject
            var preview: JsonElement = data.getValue("preview")
            var result: JsonElement = data.getValue("result")
            listOf("preview_change", "counts_change").forEach { key -> change[key]?.let { preview = mutate(preview, it.jsonObject) } }
            change["result_change"]?.let { result = mutate(result, it.jsonObject) }
            val failure = assertFails { ImportContract.verifyCommit(
                ImportContract.previewFromJson(preview.toString()), ImportContract.commitResultFromJson(result.toString())) }
            assertTrue(failure.message.orEmpty().contains("preview_decision"))
        }
    }
}
