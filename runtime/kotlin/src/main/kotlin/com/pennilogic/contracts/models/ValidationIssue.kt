package com.pennilogic.contracts.models

import com.pennilogic.contracts.serialization.StrictProviderSerializer
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.*

@OptIn(kotlinx.serialization.ExperimentalSerializationApi::class)
@kotlinx.serialization.KeepGeneratedSerializer
@Serializable(with = ValidationIssueSerializer::class)
data class ValidationIssue(
    val field: ProblemField,
    val reason: ValidationReason,
    val direction: AllocationMismatchDirection? = null,
) {
    init {
        require((reason == ValidationReason.ALLOCATION_SUM_MISMATCH) == (direction != null)) { "validation direction rejected" }
    }
}

@OptIn(kotlinx.serialization.ExperimentalSerializationApi::class)
object ValidationIssueSerializer : StrictProviderSerializer<ValidationIssue>(ValidationIssue.generatedSerializer(), "ValidationIssue") {
    override fun validateContent(value: JsonElement) = ValidationIssueContract.validateWire(value)
}

object ValidationIssueContract {
    fun validateDirection(reason: ValidationReason?, value: JsonElement?, present: Boolean) {
        if (reason == ValidationReason.ALLOCATION_SUM_MISMATCH) {
            require(present && value is JsonPrimitive && value.isString &&
                AllocationMismatchDirection.entries.any { it.value == value.content }) { "validation direction rejected" }
        } else require(!present) { "validation direction rejected" }
    }

    fun validateWire(value: JsonElement) {
        require(value is JsonObject && value.keys.containsAll(setOf("field", "reason")) &&
            setOf("field", "reason", "direction").containsAll(value.keys)) { "validation issue rejected" }
        require(ProblemField.entries.any { JsonPrimitive(it.value) == value["field"] }) { "validation issue rejected" }
        val reason = ValidationReason.entries.firstOrNull { JsonPrimitive(it.value) == value["reason"] }
        require(reason != null) { "validation issue rejected" }
        validateDirection(reason, value["direction"], "direction" in value)
    }
}
