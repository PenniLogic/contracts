package com.pennilogic.contracts.smoke

import com.pennilogic.contracts.infrastructure.ApiClient
import com.pennilogic.contracts.infrastructure.RequestConfig
import com.pennilogic.contracts.infrastructure.RequestMethod
import com.pennilogic.contracts.infrastructure.wrap
import com.pennilogic.contracts.models.*
import com.pennilogic.contracts.money.Money
import com.pennilogic.contracts.serialization.PennilogicJson
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.client.statement.HttpResponse
import io.ktor.content.TextContent
import io.ktor.http.*
import java.io.File
import kotlin.test.*
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*

class ProviderCompositionTest {
    private val json = PennilogicJson.json
    private fun fixture(name: String): JsonObject = json.parseToJsonElement(
        File(System.getProperty("pennilogic.contracts.root"), "spec/fixtures/$name").readText(),
    ).jsonObject
    private fun control(): JsonObject = buildJsonObject {
        put("money", buildJsonObject { put("amount", "90071992547409.93"); put("currency", "INR") })
        put("recorded_at", "2026-09-30T04:52:08.439Z"); put("booked_on", "2026-09-30")
        put("currency_code", "INR"); put("zone", "Asia/Kolkata")
        put("public_id", "cor_00000000-0000-4000-8000-000000000001")
        put("values", JsonArray(listOf(JsonPrimitive(0), JsonPrimitive(10))))
        put("flags", JsonArray(listOf(JsonPrimitive(false), JsonPrimitive(true))))
        put("codes", JsonArray(listOf(JsonPrimitive("request_failed"))))
        put("preview", fixture("import-provider.v1.json").getValue("preview"))
        put("refusal", fixture("error-provider.v1.json").getValue("refusal"))
    }
    private class Probe(engine: MockEngine) : ApiClient(
        baseUrl = "https://api.pennilogic.example/v1", httpClientEngine = engine,
    ) {
        suspend fun get(): HttpResponse = jsonRequest(
            RequestConfig<Unit>(RequestMethod.GET, "/synthetic-composition", requiresAuthentication = false), null, listOf(),
        )
        suspend fun post(body: Any): HttpResponse = jsonRequest(
            RequestConfig<Unit>(RequestMethod.POST, "/synthetic-composition", requiresAuthentication = false), body, listOf(),
        )
    }
    private fun engine(wire: JsonElement, capture: (String) -> Unit = {}): MockEngine = MockEngine { request ->
        if (request.body is TextContent) capture((request.body as TextContent).text)
        respond(wire.toString(), HttpStatusCode.OK, headersOf("Content-Type", ContentType.Application.Json.toString()))
    }
    private fun safe(error: Throwable) {
        generateSequence(error) { it.cause }.forEach {
            assertFalse(it.message.orEmpty().contains("PRIVATE_SYNTHETIC_CANARY"))
            assertFalse(it.message.orEmpty().contains("90071992547409.93"))
        }
    }

    @Test fun acceptedMoneyTimeAndEnumRefsComposeInNormalNativeAndNestedSerialization() {
        val wire = control()
        val value = json.decodeFromJsonElement<SyntheticProviderRecord>(wire)
        assertEquals(9007199254740993L, value.money.minorUnits)
        assertEquals(ProblemCode.REQUEST_FAILED, value.codes.first())
        assertEquals(wire, json.encodeToJsonElement(value))
        assertEquals(wire, json.encodeToJsonElement(value.copy(money = Money.ofMinorUnits(9007199254740993L, "INR"))))
        val envelope = SyntheticProviderEnvelope(value, listOf(value))
        val expected = buildJsonObject { put("record", wire); put("records", JsonArray(listOf(wire))) }
        assertEquals(expected, json.parseToJsonElement(json.encodeToString(envelope)))
        assertEquals(expected, json.encodeToJsonElement(json.decodeFromJsonElement<SyntheticProviderEnvelope>(expected)))
        for ((amount, currency) in listOf("-0.001" to "KWD", "0" to "JPY", "-92233720368547758.07" to "INR")) {
            val source = JsonObject(wire + ("money" to buildJsonObject { put("amount", amount); put("currency", currency) }))
            assertEquals(source, json.encodeToJsonElement(json.decodeFromJsonElement<SyntheticProviderRecord>(source)))
        }
    }

    @Test fun optionalStrictProblemRefsUseActualGeneratedReadWriteTransport() = runBlocking<Unit> {
        val original = control()
        val problem = fixture("import-provider.v1.json").getValue("override_rejection")
        for (wire in listOf(original, JsonObject(original + ("problem" to problem)))) {
            val record = Probe(engine(wire)).get().wrap<SyntheticProviderRecord>().body()
            var sent: String? = null
            Probe(engine(wire) { sent = it }).post(record)
            assertEquals(wire, json.parseToJsonElement(assertNotNull(sent)))
            val envelope = buildJsonObject { put("record", wire); put("records", JsonArray(listOf(wire))) }
            val nested = Probe(engine(envelope)).get().wrap<SyntheticProviderEnvelope>().body()
            Probe(engine(envelope) { sent = it }).post(nested)
            assertEquals(envelope, json.parseToJsonElement(assertNotNull(sent)))
        }
        val nullProblem = JsonObject(original + ("problem" to JsonNull))
        safe(assertFails { Probe(engine(nullProblem)).get().wrap<SyntheticProviderRecord>().body() })
    }

    @Test fun primitiveModelArrayAndNestedPrivateFailuresStayStrictOnOrdinaryAndMockTransport() = runBlocking<Unit> {
        val wire = control()
        val privateRefusal = JsonObject(wire.getValue("refusal").jsonObject + ("message" to JsonPrimitive("PRIVATE_SYNTHETIC_CANARY")))
        val privateProblem = JsonObject(fixture("import-provider.v1.json").getValue("override_rejection").jsonObject +
            ("provider_detail" to JsonPrimitive("PRIVATE_SYNTHETIC_CANARY")))
        val invalid = listOf(
            JsonObject(wire + ("values" to JsonArray(listOf(JsonNull)))),
            JsonObject(wire + ("values" to JsonArray(listOf(JsonPrimitive("0"))))),
            JsonObject(wire + ("flags" to JsonArray(listOf(JsonPrimitive("false"))))),
            JsonObject(wire + ("codes" to JsonArray(listOf(JsonPrimitive("PRIVATE_SYNTHETIC_CANARY"))))),
            JsonObject(wire + ("refusal" to privateRefusal)),
            JsonObject(wire + ("problem" to privateProblem)),
            JsonObject(wire + ("provider_detail" to JsonPrimitive("PRIVATE_SYNTHETIC_CANARY"))),
        )
        invalid.forEach { source ->
            safe(assertFails { json.decodeFromJsonElement<SyntheticProviderRecord>(source) })
            safe(assertFails { Probe(engine(source)).get().wrap<SyntheticProviderRecord>().body() })
        }
        val value = json.decodeFromJsonElement<SyntheticProviderRecord>(wire)
        val rows = mutableListOf(value)
        val envelope = SyntheticProviderEnvelope(value, rows)
        java.util.List::class.java.getMethod("add", Any::class.java).invoke(rows, null)
        safe(assertFails { json.encodeToJsonElement(envelope) })
        var executed = false
        val transport = MockEngine {
            executed = true
            respond("{}", HttpStatusCode.OK, headersOf("Content-Type", ContentType.Application.Json.toString()))
        }
        safe(assertFails { Probe(transport).post(envelope) })
        assertFalse(executed)
    }

    @Test fun originalCodecLimitsMissingRequiredMembersAndLegacyAdditivePolicyAreRetained() {
        val wire = control()
        for (money in listOf(
            buildJsonObject { put("amount", 12.34); put("currency", "INR") },
            buildJsonObject { put("amount", "12.3"); put("currency", "INR") },
            buildJsonObject { put("amount", "-92233720368547758.08"); put("currency", "INR") },
            buildJsonObject { put("amount", "PRIVATE_SYNTHETIC_CANARY"); put("currency", "INR") },
        )) safe(assertFails { json.decodeFromJsonElement<SyntheticProviderRecord>(JsonObject(wire + ("money" to money))) })
        for (field in listOf("money", "recorded_at", "booked_on", "preview", "refusal")) {
            safe(assertFails { json.decodeFromJsonElement<SyntheticProviderRecord>(JsonObject(wire - field)) })
        }
        for ((field, invalid) in listOf(
            "currency_code" to "inr", "zone" to "1invalid",
            "public_id" to "rec_00000000-0000-4000-8000-000000000001",
        )) safe(assertFails { json.decodeFromJsonElement<SyntheticProviderRecord>(JsonObject(wire + (field to JsonPrimitive(invalid)))) })
        assertTrue(json.configuration.ignoreUnknownKeys)
        val legacy = buildJsonObject {
            put("type", "about:blank"); put("title", "Legacy"); put("status", 422)
            put("code", "legacy"); put("correlation_id", "legacy"); put("additive_member", true)
        }
        assertEquals("Legacy", json.decodeFromJsonElement<ProblemDetail>(legacy).title)
        assertNull(json.decodeFromJsonElement<SyntheticLegacyEnvelope>(buildJsonObject { put("future_member", true) }).problem)
        val methods = ProviderCompositionTest::class.java.declaredMethods.filter {
            it.isAnnotationPresent(org.junit.jupiter.api.Test::class.java)
        }
        assertEquals(4, methods.size)
        assertTrue(methods.all { it.returnType == Void.TYPE })
    }
}
