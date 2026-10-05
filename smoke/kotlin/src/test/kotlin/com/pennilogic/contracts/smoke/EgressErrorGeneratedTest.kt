package com.pennilogic.contracts.smoke

import com.pennilogic.contracts.apis.CustomDestinationsApi
import com.pennilogic.contracts.errors.ErrorCatalogue
import com.pennilogic.contracts.models.*
import com.pennilogic.contracts.serialization.PennilogicJson
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.http.Headers
import io.ktor.http.HttpStatusCode
import io.ktor.util.reflect.typeInfo
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.*
import kotlin.test.*

class EgressErrorGeneratedTest {
    private val fixture = Fixtures.load("egress-errors.v1.json")
    private val catalogue = PennilogicJson.json.parseToJsonElement(
        Fixtures.root.resolve("spec/error-catalogue.v1.json").readText(Charsets.UTF_8)).jsonObject
    private val entries = (catalogue.getValue("codes").jsonArray + catalogue.getValue("authentication_codes").jsonArray)
        .associate { it.jsonObject.getValue("code").jsonPrimitive.content to it.jsonObject }
    private val cases = fixture.getValue("cases").jsonArray.map { it.jsonObject }
    private val authentication = fixture.getValue("authentication").jsonArray.map { it.jsonObject }

    private fun wire(case: JsonObject): JsonObject {
        val code = case.getValue("code").jsonPrimitive.content
        val entry = entries.getValue(code)
        val value = mutableMapOf(
            "type" to JsonPrimitive("urn:pennilogic:problem:$code"),
            "title" to entry.getValue("title"), "status" to entry.getValue("status"),
            "detail" to entry.getValue("detail"), "code" to JsonPrimitive(code),
            "correlation_id" to fixture.getValue("correlation_id"),
        )
        case["extra"]?.jsonObject?.let { value.putAll(it) }
        case["reason"]?.let { value["egress_denial_reason"] = it }
        return JsonObject(value)
    }

    @Test fun allReasonsUseOneTypedCodeAndClosedFamilyWithNativeGenericRoundTrips() {
        for (case in cases) {
            val value = wire(case)
            val reason = EgressDenialReason.entries.first { it.value == case.getValue("reason").jsonPrimitive.content }
            assertEquals(case.getValue("code").jsonPrimitive.content, ErrorCatalogue.egressProblemCode(reason).value)
            val model = PennilogicJson.json.decodeFromJsonElement<OperationProblemDetail>(value)
            assertEquals(value, PennilogicJson.json.encodeToJsonElement(model))
            assertEquals(reason, model.egressDenialReason)
            if (reason == EgressDenialReason.STEP_UP_REQUIRED) {
                assertEquals(value, PennilogicJson.json.encodeToJsonElement(
                    PennilogicJson.json.decodeFromJsonElement<AuthenticationProblemDetail>(value)))
            } else {
                assertEquals(value, PennilogicJson.json.encodeToJsonElement(
                    PennilogicJson.json.decodeFromJsonElement<EgressDeniedProblemDetail>(value)))
            }
            assertFails { PennilogicJson.json.decodeFromJsonElement<ServiceProblemDetail>(value) }
        }
        val values = JsonArray(cases.map { wire(it) })
        val serializer = ListSerializer(OperationProblemDetail.serializer())
        assertEquals(values, PennilogicJson.json.encodeToJsonElement(serializer,
            PennilogicJson.json.decodeFromJsonElement(serializer, values)))
        Fixtures.load("error-provider.v1.json").getValue("examples").jsonArray.forEach { case ->
            val value = JsonObject(wire(JsonObject(mapOf("code" to case.jsonObject.getValue("code")))) + case.jsonObject)
            assertEquals(value, PennilogicJson.json.encodeToJsonElement(
                PennilogicJson.json.decodeFromJsonElement<OperationProblemDetail>(value)))
        }
    }

    @Test fun authenticationNeverProjectsToServiceStatesOrInventsAnUnrelatedEgressReason() {
        for (case in authentication) {
            val value = wire(case)
            val model = PennilogicJson.json.decodeFromJsonElement<AuthenticationProblemDetail>(value)
            assertEquals(value, PennilogicJson.json.encodeToJsonElement(model))
            assertNull(ErrorCatalogue.authenticationPolicy(model.code).state)
            assertFails { ErrorCatalogue.policy(model.code) }
            if (model.status == 401) {
                assertEquals(value, PennilogicJson.json.encodeToJsonElement(
                    PennilogicJson.json.decodeFromJsonElement<AuthenticationRequiredProblemDetail>(value)))
            } else {
                assertFails { PennilogicJson.json.decodeFromJsonElement<AuthenticationRequiredProblemDetail>(value) }
                assertFails { PennilogicJson.json.decodeFromJsonElement<OperationProblemDetail>(value) }
            }
        }
    }

    @Test fun positiveServiceSubsetRetainsGlobalCodeTypeConstructorsAndSafeFamilyPartition() {
        val value = wire(JsonObject(mapOf("code" to JsonPrimitive("egress_denied"))))
        val model = PennilogicJson.json.decodeFromJsonElement<ServiceProblemDetail>(value)
        val code: ProblemCode = model.code
        assertEquals("egress_denied", code.value)
        assertEquals(value, PennilogicJson.json.encodeToJsonElement(
            ServiceProblemDetail(model.type, model.title, model.status, model.detail, code, model.correlationId)))
        assertFails { model.copy(code = ProblemCode.entries.single { it.value == "authentication_required" }) }
        assertFails { model.copy(status = 401) }
        assertFails { model.copy(detail = "PRIVATE_SYNTHETIC_CANARY") }
        assertFails { PennilogicJson.json.decodeFromJsonElement<OperationProblemDetail>(value) }
        assertFails { PennilogicJson.json.decodeFromJsonElement<EgressDeniedProblemDetail>(value) }
        assertFails { PennilogicJson.json.decodeFromJsonElement<AuthenticationProblemDetail>(value) }
        for (case in authentication) {
            assertFails { PennilogicJson.json.decodeFromJsonElement<ServiceProblemDetail>(wire(case)) }
        }
    }

    @Test fun negativesFailBeforeProjectionAndInvalidCopiesCannotBecomeWireValues() {
        val originals = cases.associate { it.getValue("reason").jsonPrimitive.content to wire(it) }
        fixture.getValue("invalid").jsonArray.forEach { item ->
            val negative = item.jsonObject
            val value = originals.getValue(negative.getValue("base").jsonPrimitive.content).toMutableMap()
            negative["set"]?.jsonObject?.let { value.putAll(it) }
            negative["remove"]?.jsonArray?.forEach { value.remove(it.jsonPrimitive.content) }
            val failure = assertFails(negative.getValue("name").jsonPrimitive.content) {
                PennilogicJson.json.decodeFromJsonElement<OperationProblemDetail>(JsonObject(value))
            }
            assertFalse(failure.message.orEmpty().contains("PRIVATE_SYNTHETIC_CANARY"))
        }
        val model = PennilogicJson.json.decodeFromJsonElement<OperationProblemDetail>(originals.getValue("destination_denied"))
        assertFails { model.copy(status = 401) }
        assertFails { model.copy(detail = "PRIVATE_SYNTHETIC_CANARY") }
        assertFails {
            OperationProblemDetail(model.type, model.title, model.status, model.detail, model.code, model.correlationId)
        }
    }

    @Test fun actualNon2xxResponsesDecodeUsingTheGeneratedSharedTypesAndExposeRequiredHeaders() = runBlocking<Unit> {
        val registration = PennilogicJson.json.decodeFromJsonElement<CustomDestinationRegistrationRequest>(
            Fixtures.load("custom-destination-wire.v1.json").getValue("registration"))
        for (case in cases + authentication.filter { it.getValue("status").jsonPrimitive.int == 401 }) {
            val value = wire(case)
            val status = value.getValue("status").jsonPrimitive.int
            var sends = 0
            val responseHeaders = Headers.build {
                fixture.getValue("safe_headers").jsonObject.forEach { (name, entry) -> append(name, entry.jsonPrimitive.content) }
                if (status == 401) fixture.getValue("nonce_headers").jsonObject.forEach { (name, entry) ->
                    append(name, entry.jsonPrimitive.content)
                }
                value["retry_after_seconds"]?.let { append("Retry-After", it.jsonPrimitive.content) }
            }
            val engine = MockEngine {
                sends += 1
                respond(value.toString(), HttpStatusCode.fromValue(status), responseHeaders)
            }
            try {
                val api = CustomDestinationsApi(baseUrl = "https://api.pennilogic.example/v1", httpClientEngine = engine)
                api.setApiKey("DPoP synthetic.access.signature", "Authorization")
                val response = api.registerCustomDestination("header.register.signature", "synthetic-step-up",
                    "00000000-0000-4000-8000-000000000010", registration)
                val received = if (status == 401) {
                    val model = response.typedBody<AuthenticationRequiredProblemDetail>(typeInfo<AuthenticationRequiredProblemDetail>())
                    assertEquals(response.status, model.status)
                    PennilogicJson.json.encodeToJsonElement(model)
                } else {
                    val model = response.typedBody<OperationProblemDetail>(typeInfo<OperationProblemDetail>())
                    assertEquals(response.status, model.status)
                    PennilogicJson.json.encodeToJsonElement(model)
                }
                assertEquals(1, sends)
                assertEquals(value, received)
                assertEquals("application/problem+json", response.response.headers["Content-Type"])
                assertEquals("no-store", response.response.headers["Cache-Control"])
                if (status == 401) {
                    assertEquals("DPoP error=\"use_dpop_nonce\"", response.response.headers["WWW-Authenticate"])
                    assertEquals("synthetic-response-nonce", response.response.headers["DPoP-Nonce"])
                }
                value["retry_after_seconds"]?.let {
                    assertEquals(it.jsonPrimitive.content, response.response.headers["Retry-After"])
                }
            } finally {
                engine.close()
            }
        }
    }

    @Test fun actualPrivateAndWrongFamilyErrorsFailGeneratedResponseDecodingSafely() = runBlocking<Unit> {
        val base = wire(cases.first())
        for ((status, value) in listOf(
            403 to JsonObject(base + ("host" to JsonPrimitive("PRIVATE_SYNTHETIC_CANARY"))), 401 to base,
        )) {
            val engine = MockEngine {
                respond(value.toString(), HttpStatusCode.fromValue(status), Headers.build {
                    append("Content-Type", "application/problem+json")
                })
            }
            try {
                val api = CustomDestinationsApi(baseUrl = "https://api.pennilogic.example/v1", httpClientEngine = engine)
                val response = api.listCustomDestinations("header.list.signature")
                val failure = assertFails {
                    if (status == 401) {
                        response.typedBody<AuthenticationRequiredProblemDetail>(typeInfo<AuthenticationRequiredProblemDetail>())
                    } else {
                        response.typedBody<OperationProblemDetail>(typeInfo<OperationProblemDetail>())
                    }
                }
                assertFalse(failure.message.orEmpty().contains("PRIVATE_SYNTHETIC_CANARY"))
            } finally {
                engine.close()
            }
        }
    }

    @Test fun unknownForeignSyntheticRefusalsCannotGainExistenceOrResourceMembers() {
        val control = fixture.getValue("unknown_foreign_control").jsonObject
        val value = wire(control)
        control.getValue("contexts").jsonArray.forEach {
            assertEquals(value, PennilogicJson.json.encodeToJsonElement(
                PennilogicJson.json.decodeFromJsonElement<OperationProblemDetail>(value)))
        }
        control.getValue("forbidden").jsonArray.forEach { member ->
            assertFails {
                PennilogicJson.json.decodeFromJsonElement<OperationProblemDetail>(
                    JsonObject(value + (member.jsonPrimitive.content to JsonPrimitive("PRIVATE_SYNTHETIC_CANARY"))))
            }
        }
    }
}
