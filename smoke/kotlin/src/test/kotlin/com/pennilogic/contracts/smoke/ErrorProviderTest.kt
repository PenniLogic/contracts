package com.pennilogic.contracts.smoke

import com.pennilogic.contracts.errors.ErrorCatalogue
import com.pennilogic.contracts.models.*
import com.pennilogic.contracts.serialization.PennilogicJson
import java.io.File
import kotlin.test.*
import kotlinx.serialization.json.*

class ErrorProviderTest {
    private val fixture = Fixtures.load("error-provider.v1.json")
    private val entries = PennilogicJson.json.parseToJsonElement(
        File(Fixtures.root, "spec/error-catalogue.v1.json").readText()).jsonObject["codes"]!!.jsonArray
        .associate { it.jsonObject["code"]!!.jsonPrimitive.content to it.jsonObject }

    private fun examples(): Map<String, JsonObject> =
        fixture["examples"]!!.jsonArray.associate { value ->
            val example = value.jsonObject
            val code = example["code"]!!.jsonPrimitive.content
            val entry = entries.getValue(code)
            code to JsonObject(mapOf(
                "type" to JsonPrimitive("urn:pennilogic:problem:$code"),
                "title" to entry.getValue("title"), "status" to entry.getValue("status"),
                "detail" to entry.getValue("detail"), "correlation_id" to fixture.getValue("correlation_id"),
            ) + example)
        }

    @Test fun codesExposeTypedStateAndSafeRetryPolicy() {
        assertEquals(entries.keys, ProblemCode.entries.map { it.value }.toSet())
        val state: ClientState = ErrorCatalogue.policy(ProblemCode.DEPENDENCY_UNAVAILABLE).state
        assertEquals(ClientState.ERROR, state)
        val mismatch = ErrorCatalogue.policy(ProblemCode.IDEMPOTENCY_PAYLOAD_MISMATCH)
        assertFalse(mismatch.retryable)
        assertEquals(IdempotencyTreatment.NEVER_REPLACE_TO_ESCAPE_MISMATCH, mismatch.idempotency)
    }

    @Test fun everyExampleUsesTypedCodesAndRoundTrips() {
        examples().forEach { (code, wire) ->
            val problem = ServiceProblemContract.fromJson(wire.toString())
            val typed: ProblemCode = problem.code
            assertEquals(code, typed.value)
            assertEquals(wire, PennilogicJson.json.parseToJsonElement(ServiceProblemContract.toJson(problem)))
        }
    }

    @Test fun disclosureAndRetryNegativesAreRejected() {
        val originals = examples()
        fixture["invalid"]!!.jsonArray.forEach { value ->
            val negative = value.jsonObject
            val wire = originals.getValue(negative.getValue("base").jsonPrimitive.content).toMutableMap()
            negative["set"]?.jsonObject?.let { wire.putAll(it) }
            negative["remove"]?.jsonArray?.forEach { wire.remove(it.jsonPrimitive.content) }
            val failure = assertFails(negative.getValue("name").jsonPrimitive.content) {
                ServiceProblemContract.fromJson(JsonObject(wire).toString())
            }
            assertFalse(failure.message.orEmpty().contains("SYNTHETIC_"))
            assertFalse(failure.message.orEmpty().contains("12.34"))
            assertFalse(failure.message.orEmpty().contains("internal-42"))
        }
    }

    @Test fun generatedEnumSerializerRejectsUnknownAndCaseCoercion() {
        listOf("\"VALIDATION_REJECTED\"", "\"unknown\"", "422", "true", "null").forEach { value ->
            assertFails { PennilogicJson.json.decodeFromString<ProblemCode>(value) }
        }
    }

    @Test fun refusalIsSuccessfulContentNotAProblem() {
        val wire = fixture.getValue("refusal")
        val refusal = PennilogicJson.json.decodeFromJsonElement<AiRefusal>(wire)
        assertEquals(AiRefusalCode.AI_REFUSAL, refusal.code)
        assertEquals(wire, PennilogicJson.json.encodeToJsonElement(refusal))
        assertFalse(ProblemCode.entries.any { it.value == refusal.code.value })
    }

    @Test fun correlationFactoryDoesNotAcceptInternalRecordInputs() {
        val values = (1..100).map { ErrorCatalogue.newCorrelationId() }.toSet()
        assertEquals(100, values.size)
        values.forEach { assertTrue(Regex("cor_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}").matches(it)) }
    }
}
