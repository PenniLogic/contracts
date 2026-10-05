package com.pennilogic.contracts.smoke

import com.pennilogic.contracts.imports.ImportContract
import com.pennilogic.contracts.infrastructure.ApiClient
import com.pennilogic.contracts.infrastructure.RequestConfig
import com.pennilogic.contracts.infrastructure.RequestMethod
import com.pennilogic.contracts.infrastructure.wrap
import com.pennilogic.contracts.models.*
import com.pennilogic.contracts.serialization.PennilogicJson
import io.ktor.client.engine.HttpClientEngine
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.client.statement.HttpResponse
import io.ktor.http.ContentType
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import io.ktor.content.TextContent
import kotlin.test.*
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import kotlinx.serialization.Serializable

class ProviderTransportTest {
    private val json = PennilogicJson.json
    private val data = Fixtures.load("import-provider.v1.json")
    private val refusal = Fixtures.load("error-provider.v1.json").getValue("refusal").jsonObject

    private class Probe(engine: HttpClientEngine) : ApiClient(
        baseUrl = "https://api.pennilogic.example/v1", httpClientEngine = engine,
    ) {
        suspend fun get(): HttpResponse = jsonRequest(
            RequestConfig<Unit>(RequestMethod.GET, "/synthetic-provider", requiresAuthentication = false), null, listOf(),
        )
        suspend fun post(body: Any): HttpResponse = jsonRequest(
            RequestConfig<Unit>(RequestMethod.POST, "/synthetic-provider", requiresAuthentication = false), body, listOf(),
        )
    }
    private fun engine(wire: JsonElement, capture: (String) -> Unit = {}): MockEngine = MockEngine { request ->
        val body = request.body
        if (body is TextContent) capture(body.text)
        respond(wire.toString(), HttpStatusCode.OK, headersOf("Content-Type", ContentType.Application.Json.toString()))
    }
    private fun assertSafe(error: Throwable) {
        generateSequence(error) { it.cause }.forEach {
            assertFalse(it.message.orEmpty().contains("PRIVATE_SYNTHETIC_CANARY"), "diagnostic echoed rejected content")
        }
    }
    private data class Codec(
        val decode: (JsonElement) -> JsonElement,
        val transport: suspend (JsonElement) -> JsonElement,
    )
    private inline fun <reified T> codec(): Codec = Codec(
        decode = { wire -> json.encodeToJsonElement(json.decodeFromJsonElement<T>(wire)) },
        transport = { wire -> json.encodeToJsonElement(Probe(engine(wire)).get().wrap<T>().body()) },
    )
    private fun codecs(): Map<String, Codec> = mapOf(
        "AiRefusal" to codec<AiRefusal>(),
        "ServiceProblemDetail" to codec<ServiceProblemDetail>(),
        "ValidationIssue" to codec<ValidationIssue>(),
        "Allowance" to codec<Allowance>(),
        "EntitlementDenial" to codec<EntitlementDenial>(),
        "DedupPrecedence" to codec<DedupPrecedence>(),
        "DuplicateLink" to codec<DuplicateLink>(),
        "DedupEnrichment" to codec<DedupEnrichment>(),
        "DedupOutcome" to codec<DedupOutcome>(),
        "ImportColumnBinding" to codec<ImportColumnBinding>(),
        "ImportColumnMapping" to codec<ImportColumnMapping>(),
        "ImportRowError" to codec<ImportRowError>(),
        "ImportPreviewRow" to codec<ImportPreviewRow>(),
        "ImportPreviewCounts" to codec<ImportPreviewCounts>(),
        "ImportPreview" to codec<ImportPreview>(),
        "ImportCommitRequest" to codec<ImportCommitRequest>(),
        "ImportCommitRow" to codec<ImportCommitRow>(),
        "ImportCommitCounts" to codec<ImportCommitCounts>(),
        "ImportCommitResult" to codec<ImportCommitResult>(),
    )
    private fun sample(entry: JsonObject): JsonElement {
        var value: JsonElement = Fixtures.load(entry.getValue("fixture").jsonPrimitive.content)
        entry.getValue("path").jsonArray.forEach { key ->
            value = if (key.jsonPrimitive.isString) value.jsonObject.getValue(key.jsonPrimitive.content) else value.jsonArray[key.jsonPrimitive.int]
        }
        return entry["set"]?.jsonObject?.let { JsonObject(value.jsonObject + it) } ?: value
    }
    private fun quotedPrimitives(value: JsonElement): List<JsonElement> = when (value) {
        is JsonObject -> value.flatMap { (key, child) -> quotedPrimitives(child).map { JsonObject(value + (key to it)) } }
        is JsonArray -> value.indices.flatMap { index -> quotedPrimitives(value[index]).map {
            JsonArray(value.toMutableList().apply { this[index] = it })
        } }
        is JsonPrimitive -> if (!value.isString && value != JsonNull) listOf(JsonPrimitive(value.content)) else emptyList()
    }

    private fun nullArrayEntries(value: JsonElement): List<JsonElement> = when (value) {
        is JsonObject -> value.flatMap { (key, child) -> nullArrayEntries(child).map { JsonObject(value + (key to it)) } }
        is JsonArray -> listOf(JsonArray(value + JsonNull)) + value.indices.flatMap { index ->
            nullArrayEntries(value[index]).map { JsonArray(value.toMutableList().apply { this[index] = it }) }
        }
        else -> emptyList()
    }

    @Test fun everyNestedProviderModelAndPrimitiveArrayRejectsNullBeforeActualTransport() = runBlocking<Unit> {
        val transforms = codecs()
        var cases = 0
        val inventory = Fixtures.load("provider-transport.v1.json")
        (inventory.getValue("models").jsonArray + inventory.getValue("array_controls").jsonArray).forEach { entry ->
            val transform = transforms.getValue(entry.jsonObject.getValue("schema").jsonPrimitive.content)
            nullArrayEntries(sample(entry.jsonObject)).forEach { wire ->
                assertSafe(assertFails { transform.decode(wire) })
                assertSafe(assertFails { transform.transport(wire) })
                cases += 1
            }
        }
        assertEquals(11, cases)
    }

    @Test fun refusalIsStrictInOrdinaryDecodeAndActualGeneratedTransport() = runBlocking {
        for (wire in listOf(
            JsonObject(refusal + ("provider_detail" to JsonPrimitive("PRIVATE_SYNTHETIC_CANARY"))),
            JsonObject(refusal + ("correlation_id" to JsonPrimitive("PRIVATE_SYNTHETIC_CANARY"))),
            JsonObject(refusal + ("message" to JsonPrimitive("PRIVATE_SYNTHETIC_CANARY"))),
        )) {
            assertSafe(assertFails { json.decodeFromString<AiRefusal>(wire.toString()) })
            assertSafe(assertFails { Probe(engine(wire)).get().wrap<AiRefusal>().body() })
        }
    }

    @Test fun serviceProblemIsStrictInOrdinaryDecodeAndActualGeneratedTransport() = runBlocking {
        val valid = data.getValue("override_rejection").jsonObject
        val wire = JsonObject(valid + ("provider_detail" to JsonPrimitive("PRIVATE_SYNTHETIC_CANARY")))
        assertSafe(assertFails { json.decodeFromString<ServiceProblemDetail>(wire.toString()) })
        assertSafe(assertFails { Probe(engine(wire)).get().wrap<ServiceProblemDetail>().body() })
    }

    @Test fun officialAndOrdinaryImportReadersRejectEveryQuotedPrimitiveLeaf() {
        for (wire in quotedPrimitives(data.getValue("preview"))) {
            assertFails { ImportContract.previewFromJson(wire.toString()) }
            assertFails { json.decodeFromString<ImportPreview>(wire.toString()) }
        }
        for (wire in quotedPrimitives(data.getValue("linked"))) {
            assertFails { ImportContract.dedupFromJson(wire.toString()) }
            assertFails { json.decodeFromString<DedupOutcome>(wire.toString()) }
        }
        for (wire in quotedPrimitives(data.getValue("result"))) {
            assertFails { ImportContract.commitResultFromJson(wire.toString()) }
            assertFails { json.decodeFromString<ImportCommitResult>(wire.toString()) }
        }
        for (wire in quotedPrimitives(data.getValue("reversed"))) {
            assertFails { ImportContract.dedupFromJson(wire.toString()) }
            assertFails { json.decodeFromString<DedupOutcome>(wire.toString()) }
        }
    }

    @Test fun everyClosedProviderUsesStrictOrdinarySerializationAndActualGeneratedTransport() = runBlocking {
        val declared = Fixtures.load("provider-transport.v1.json").getValue("models").jsonArray
        val transforms = codecs()
        assertEquals(19, declared.size)
        assertEquals(transforms.keys, declared.map { it.jsonObject.getValue("schema").jsonPrimitive.content }.toSet())
        declared.forEach { entry ->
            val name = entry.jsonObject.getValue("schema").jsonPrimitive.content
            val transform = transforms.getValue(name)
            val wire = sample(entry.jsonObject)
            assertEquals(wire, transform.decode(wire), name)
            assertEquals(wire, transform.transport(wire), name)
            listOf("provider_detail", "PRIVATE_SYNTHETIC_CANARY").forEach { key ->
                val invalid = JsonObject(wire.jsonObject + (key to JsonPrimitive("PRIVATE_SYNTHETIC_CANARY")))
                assertSafe(assertFails(name) { transform.decode(invalid) })
                assertSafe(assertFails(name) { transform.transport(invalid) })
            }
            val missing = JsonObject(wire.jsonObject - wire.jsonObject.keys.first())
            assertSafe(assertFails(name) { transform.decode(missing) })
            assertSafe(assertFails(name) { transform.transport(missing) })
        }
    }

    @Test fun everyIntegerAndBooleanLeafIsCheckedBeforeActualTransportConversion() = runBlocking {
        val declared = Fixtures.load("provider-transport.v1.json").getValue("models").jsonArray
        val transforms = codecs()
        var cases = 0
        declared.forEach { entry ->
            val transform = transforms.getValue(entry.jsonObject.getValue("schema").jsonPrimitive.content)
            quotedPrimitives(sample(entry.jsonObject)).forEach { wire ->
                assertSafe(assertFails { transform.decode(wire) })
                assertSafe(assertFails { transform.transport(wire) })
                cases += 1
            }
        }
        assertTrue(cases >= 50)
    }

    @Test fun everyRefusalFailureHasSafeDiagnosticsAndInvalidConstructionCannotEscape() = runBlocking {
        Fixtures.load("provider-transport.v1.json").getValue("refusal_negatives").jsonArray.forEach { item ->
            val negative = item.jsonObject
            val fields = refusal.toMutableMap()
            negative["set"]?.jsonObject?.let { fields.putAll(it) }
            negative["remove"]?.jsonArray?.forEach { fields.remove(it.jsonPrimitive.content) }
            val wire = JsonObject(fields)
            assertSafe(assertFails { json.decodeFromJsonElement<AiRefusal>(wire) })
            assertSafe(assertFails { Probe(engine(wire)).get().wrap<AiRefusal>().body() })
        }
        assertSafe(assertFails {
            AiRefusal(AiRefusalCode.AI_REFUSAL, AiRefusalMessage.entries.single(), "PRIVATE_SYNTHETIC_CANARY")
        })
        val problem = json.decodeFromJsonElement<ServiceProblemDetail>(data.getValue("override_rejection"))
        assertSafe(assertFails { problem.copy(detail = "PRIVATE_SYNTHETIC_CANARY") })
    }

    @Serializable
    private data class Envelope(val refusal: AiRefusal, val problem: ServiceProblemDetail)

    @Test fun nestedProvidersStayStrictWhileLegacyAdditiveDtoPolicyIsUnchanged() = runBlocking<Unit> {
        val original = buildJsonObject {
            put("refusal", refusal); put("problem", data.getValue("override_rejection")); put("legacy_additive_member", true)
        }
        val envelope = json.decodeFromJsonElement<Envelope>(original)
        assertEquals(refusal, json.encodeToJsonElement(envelope.refusal))
        for (field in listOf("refusal", "problem")) {
            val invalid = JsonObject(original + (field to JsonObject(original.getValue(field).jsonObject +
                ("provider_detail" to JsonPrimitive("PRIVATE_SYNTHETIC_CANARY")))))
            assertSafe(assertFails { json.decodeFromJsonElement<Envelope>(invalid) })
            assertSafe(assertFails { Probe(engine(invalid)).get().wrap<Envelope>().body() })
        }
        assertTrue(json.configuration.ignoreUnknownKeys)
        val preview = json.decodeFromJsonElement<ImportPreview>(data.getValue("preview"))
        val mutableRows = preview.rows.toMutableList()
        val copy = preview.copy(rows = mutableRows)
        mutableRows.add(preview.rows.first())
        assertFails { json.encodeToJsonElement(copy) }
    }

    @Test fun actualGeneratedRequestSerializationUsesProviderSeamsAndRejectsInvalidOutboundState() = runBlocking {
        val refusalModel = json.decodeFromJsonElement<AiRefusal>(refusal)
        val problem = json.decodeFromJsonElement<ServiceProblemDetail>(data.getValue("override_rejection"))
        for ((value, expected) in listOf(
            refusalModel to refusal,
            problem to data.getValue("override_rejection"),
            Envelope(refusalModel, problem) to buildJsonObject { put("refusal", refusal); put("problem", data.getValue("override_rejection")) },
        )) {
            var sent: String? = null
            Probe(engine(refusal) { sent = it }).post(value)
            assertEquals(expected, json.parseToJsonElement(assertNotNull(sent)))
        }
        val preview = json.decodeFromJsonElement<ImportPreview>(data.getValue("preview"))
        val rows = preview.rows.toMutableList()
        val model = preview.copy(rows = rows)
        rows.add(preview.rows.first())
        var requestExecuted = false
        val transport = MockEngine {
            requestExecuted = true
            respond(refusal.toString(), HttpStatusCode.OK, headersOf("Content-Type", ContentType.Application.Json.toString()))
        }
        assertSafe(assertFails { Probe(transport).post(model) })
        assertFalse(requestExecuted)
    }

    @Test fun everyDeclaredProviderTransportTestHasAJunitCompatibleVoidReturn() {
        val methods = ProviderTransportTest::class.java.declaredMethods.filter {
            it.isAnnotationPresent(org.junit.jupiter.api.Test::class.java)
        }
        assertTrue(methods.isNotEmpty())
        assertTrue(methods.all { it.returnType == Void.TYPE }, "Every declared provider transport test must be discovered by JUnit")
    }

    @Test fun actualGeneratedColumnBoundsAccept256AndReject257() = runBlocking<Unit> {
        val original = data.getValue("preview").jsonObject
        val mapping = JsonObject(original.getValue("mapping").jsonObject + mapOf(
            "column_count" to JsonPrimitive(256),
            "unmapped_columns" to JsonArray((4..256).map { JsonPrimitive(it) }),
        ))
        val wire = JsonObject(original + ("mapping" to mapping))
        assertEquals(256, json.decodeFromJsonElement<ImportPreview>(wire).mapping.columnCount)
        assertEquals(wire, codecs().getValue("ImportPreview").transport(wire))
        val invalid = JsonObject(wire + ("mapping" to JsonObject(mapping + ("column_count" to JsonPrimitive(257)))))
        assertSafe(assertFails { json.decodeFromJsonElement<ImportPreview>(invalid) })
        assertSafe(assertFails { codecs().getValue("ImportPreview").transport(invalid) })
    }

    @Test fun acceptedAllocationDirectionIsConditionalTypedAndSafeOnActualNestedReadAndWrite() = runBlocking<Unit> {
        val fixture = Fixtures.load("allocation-refusal.v1.json")
        val base = fixture.getValue("problem").jsonObject
        assertFails { Probe(engine(base)).get().wrap<ServiceProblemDetail>().body() }
        fixture.getValue("directions").jsonArray.forEach { direction ->
            val issue = buildJsonObject { put("field", "allocation"); put("reason", "allocation_sum_mismatch"); put("direction", direction) }
            val wire = JsonObject(base + mapOf("direction" to direction, "validation_errors" to JsonArray(listOf(issue))))
            val problem = Probe(engine(wire)).get().wrap<ServiceProblemDetail>().body()
            val typed: AllocationMismatchDirection = assertNotNull(problem.direction)
            assertEquals(direction.jsonPrimitive.content, typed.value)
            assertEquals(wire, json.encodeToJsonElement(problem))
            var sent: String? = null
            Probe(engine(wire) { sent = it }).post(problem)
            assertEquals(wire, json.parseToJsonElement(assertNotNull(sent)))
            val missing = JsonObject(wire + ("validation_errors" to JsonArray(listOf(JsonObject(issue - "direction")))))
            assertSafe(assertFails { Probe(engine(missing)).get().wrap<ServiceProblemDetail>().body() })
            assertFails { problem.copy(direction = null) }
        }
        fixture.getValue("invalid_directions").jsonArray.forEach { direction ->
            val wire = JsonObject(base + ("direction" to direction))
            assertSafe(assertFails { Probe(engine(wire)).get().wrap<ServiceProblemDetail>().body() })
        }
        val amount = JsonObject(base + mapOf("direction" to JsonPrimitive("shortfall"), "amount" to JsonPrimitive("12.34")))
        assertSafe(assertFails { Probe(engine(amount)).get().wrap<ServiceProblemDetail>().body() })
        val unrelated = JsonObject(base + mapOf("reason" to JsonPrimitive("shape"), "direction" to JsonPrimitive("shortfall")))
        assertFails { json.decodeFromJsonElement<ServiceProblemDetail>(unrelated) }
    }
}
