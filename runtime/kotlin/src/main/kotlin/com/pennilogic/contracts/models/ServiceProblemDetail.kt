// Strict T-CON-12 seam; the scaffold ProblemDetail remains unchanged.
package com.pennilogic.contracts.models

import com.pennilogic.contracts.errors.ErrorCatalogue
import com.pennilogic.contracts.serialization.PennilogicJson
import com.pennilogic.contracts.time.InstantCodec
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.*

@Serializable
data class ServiceProblemDetail(
    val type: String,
    val title: String,
    val status: Int,
    val detail: String,
    val code: ProblemCode,
    @SerialName("correlation_id") val correlationId: String,
    val instance: String? = null,
    val field: ProblemField? = null,
    val reason: ValidationReason? = null,
    @SerialName("validation_errors") val validationErrors: Set<ValidationIssue>? = null,
    @SerialName("idempotency_key") val idempotencyKey: String? = null,
    @SerialName("retry_after_seconds") val retryAfterSeconds: Int? = null,
    val allowance: Allowance? = null,
    val entitlement: EntitlementDenial? = null,
) {
    init {
        val policy = ErrorCatalogue.policy(code)
        require(type == "urn:pennilogic:problem:${code.value}" && title == policy.title &&
            status == ErrorCatalogue.status(code, field) && detail == policy.detail) { "problem rejected: catalogue" }
        require(ServiceProblemContract.publicCorrelation.matches(correlationId)) { "problem rejected: correlation" }
        require(instance == null || ServiceProblemContract.publicInstance.matches(instance)) { "problem rejected: instance" }
        val validation = code in setOf(ProblemCode.VALIDATION_REJECTED, ProblemCode.IMPORT_MAPPING_REQUIRED, ProblemCode.IDEMPOTENCY_KEY_INVALID)
        require(if (validation) field != null && reason != null else field == null && reason == null && validationErrors == null) { "problem rejected: validation" }
        require(validationErrors == null || validationErrors.size in 1..20) { "problem rejected: validation" }
        require(code != ProblemCode.IDEMPOTENCY_KEY_INVALID || (field == ProblemField.IDEMPOTENCY_KEY &&
            reason in setOf(ValidationReason.REQUIRED, ValidationReason.MALFORMED))) { "problem rejected: validation" }
        require(code != ProblemCode.IMPORT_MAPPING_REQUIRED || (field == ProblemField.COLUMN_MAPPING &&
            reason in setOf(ValidationReason.MAPPING_UNMAPPED, ValidationReason.MAPPING_CONFLICT))) { "problem rejected: validation" }
        require(code != ProblemCode.VALIDATION_REJECTED || field != ProblemField.DUPLICATE_OVERRIDE ||
            reason in setOf(ValidationReason.MALFORMED, ValidationReason.NOT_AVAILABLE)) { "problem rejected: validation" }
        require(if (code == ProblemCode.IDEMPOTENCY_PAYLOAD_MISMATCH)
            idempotencyKey != null && ServiceProblemContract.uuid.matches(idempotencyKey) else idempotencyKey == null) { "problem rejected: key" }
        val delays = setOf(ProblemCode.DEPENDENCY_UNAVAILABLE, ProblemCode.IDEMPOTENCY_IN_PROGRESS, ProblemCode.RATE_LIMITED, ProblemCode.REQUEST_FAILED)
        require(retryAfterSeconds == null || (code in delays && retryAfterSeconds in 1..86400)) { "problem rejected: retry" }
        require(code !in setOf(ProblemCode.IDEMPOTENCY_IN_PROGRESS, ProblemCode.RATE_LIMITED) || retryAfterSeconds != null) { "problem rejected: retry" }
        require((code in setOf(ProblemCode.QUOTA_EXHAUSTED, ProblemCode.RATE_LIMITED)) == (allowance != null)) { "problem rejected: allowance" }
        require(allowance == null || (allowance.limit >= 0 &&
            ((allowance.limit != 0 && allowance.window != AllowanceWindow.LIFETIME) || allowance.resetsAt == null))) { "problem rejected: allowance" }
        require((code == ProblemCode.ENTITLEMENT_DENIED) == (entitlement != null)) { "problem rejected: entitlement" }
    }
}

object ServiceProblemContract {
    private const val UUID = "[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
    internal val uuid = Regex(UUID)
    internal val publicCorrelation = Regex("cor_$UUID")
    internal val publicInstance = Regex("urn:pennilogic:problem-instance:$UUID")
    private val required = setOf("type", "title", "status", "detail", "code", "correlation_id")
    private val optional = setOf("instance", "field", "reason", "validation_errors", "idempotency_key", "retry_after_seconds", "allowance", "entitlement")

    private fun shape(value: JsonElement?, required: Set<String>, optional: Set<String> = emptySet()): JsonObject {
        require(value is JsonObject && value.keys.containsAll(required) &&
            (required + optional).containsAll(value.keys) && value.values.none { it == JsonNull }) { "problem rejected: shape" }
        return value
    }

    fun fromJson(text: String): ServiceProblemDetail {
        val value = try { PennilogicJson.json.parseToJsonElement(text) }
        catch (_: SerializationException) { throw IllegalArgumentException("problem rejected: json") }
        validateWire(value)
        return try { PennilogicJson.json.decodeFromJsonElement<ServiceProblemDetail>(value) }
        catch (_: SerializationException) { throw IllegalArgumentException("problem rejected: shape") }
    }

    fun toJson(value: ServiceProblemDetail): String {
        val wire = PennilogicJson.json.encodeToJsonElement(value)
        validateWire(wire)
        return wire.toString()
    }

    fun validateWire(value: JsonElement) {
        val wire = shape(value, required, optional)
        fun string(key: String): String {
            val member = wire[key]
            require(member is JsonPrimitive && member.isString) { "problem rejected: shape" }
            return member.content
        }
        val code = ProblemCode.entries.firstOrNull { it.value == string("code") }
            ?: throw IllegalArgumentException("problem rejected: code")
        val policy = ErrorCatalogue.policy(code)
        require(string("type") == "urn:pennilogic:problem:${code.value}" &&
            string("title") == policy.title && string("detail") == policy.detail) { "problem rejected: catalogue" }
        val status = wire["status"]
        val field = ProblemField.entries.firstOrNull { JsonPrimitive(it.value) == wire["field"] }
        require(status is JsonPrimitive && !status.isString && status.intOrNull == ErrorCatalogue.status(code, field)) { "problem rejected: catalogue" }
        require(publicCorrelation.matches(string("correlation_id"))) { "problem rejected: correlation" }
        if ("instance" in wire) require(publicInstance.matches(string("instance"))) { "problem rejected: instance" }
        if ("field" in wire) require(ProblemField.entries.any { it.value == string("field") }) { "problem rejected: validation" }
        if ("reason" in wire) require(ValidationReason.entries.any { it.value == string("reason") }) { "problem rejected: validation" }
        if ("idempotency_key" in wire) require(uuid.matches(string("idempotency_key"))) { "problem rejected: key" }
        if ("retry_after_seconds" in wire) {
            val delay = wire["retry_after_seconds"]
            require(delay is JsonPrimitive && !delay.isString && delay.intOrNull in 1..86400) { "problem rejected: retry" }
        }
        wire["validation_errors"]?.let { issues ->
            require(issues is JsonArray && issues.size in 1..20 && issues.distinct().size == issues.size) { "problem rejected: validation" }
            issues.forEach { item ->
                val issue = shape(item, setOf("field", "reason"))
                require(ProblemField.entries.any { JsonPrimitive(it.value) == issue["field"] } &&
                    ValidationReason.entries.any { JsonPrimitive(it.value) == issue["reason"] }) { "problem rejected: validation" }
            }
        }
        wire["allowance"]?.let {
            val allowance = shape(it, setOf("limit", "unit", "window"), setOf("resets_at"))
            val limit = allowance.getValue("limit")
            require(limit is JsonPrimitive && !limit.isString && limit.intOrNull != null && limit.int >= 0) { "problem rejected: allowance" }
            require(AllowanceUnit.entries.any { JsonPrimitive(it.value) == allowance["unit"] } &&
                AllowanceWindow.entries.any { JsonPrimitive(it.value) == allowance["window"] }) { "problem rejected: allowance" }
            allowance["resets_at"]?.let { reset ->
                require(limit.int != 0 && allowance["window"] != JsonPrimitive("lifetime") &&
                    reset is JsonPrimitive && reset.isString) { "problem rejected: allowance" }
                InstantCodec.parse(reset.content)
            }
        }
        wire["entitlement"]?.let {
            val upgrade = shape(it, setOf("upgrade_available")).getValue("upgrade_available")
            require(upgrade is JsonPrimitive && !upgrade.isString && upgrade.booleanOrNull != null) { "problem rejected: entitlement" }
        }
    }
}
