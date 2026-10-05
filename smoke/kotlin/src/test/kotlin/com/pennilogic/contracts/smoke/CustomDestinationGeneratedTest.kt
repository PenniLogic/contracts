package com.pennilogic.contracts.smoke

import com.pennilogic.contracts.apis.CustomDestinationsApi
import com.pennilogic.contracts.models.CredentialHeader
import com.pennilogic.contracts.models.CustomDestination
import com.pennilogic.contracts.models.CustomDestinationLifecycleRequest
import com.pennilogic.contracts.models.CustomDestinationRegistrationRequest
import com.pennilogic.contracts.models.CustomDestinationState
import com.pennilogic.contracts.models.DestinationClass
import com.pennilogic.contracts.models.EgressDenialReason
import com.pennilogic.contracts.serialization.PennilogicJson
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.client.request.HttpRequestData
import io.ktor.content.TextContent
import io.ktor.http.ContentType
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import java.util.UUID
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFails
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import org.junit.jupiter.api.DynamicTest
import org.junit.jupiter.api.TestFactory
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

class CustomDestinationGeneratedTest {
    private val fixture = Fixtures.load("custom-destination-wire.v1.json")
    private val registration = CustomDestinationRegistrationRequest(
        host = "models.pennilogic.example",
        credentialHeader = CredentialHeader.AUTHORIZATION_BEARER,
        models = setOf("fixture/model-v1"),
    )

    @Test
    fun `generated enums equal all canonical values`() {
        val source = PennilogicJson.json.parseToJsonElement(
            Fixtures.root.resolve("spec/adr022/ai-egress-consequences.json").readText(Charsets.UTF_8)
        ).jsonObject.getValue("enums").jsonObject
        val generated = mapOf(
            "DestinationClass" to DestinationClass.entries.map { it.value },
            "CredentialHeader" to CredentialHeader.entries.map { it.value },
            "CustomDestinationState" to CustomDestinationState.entries.map { it.value },
            "EgressDenialReason" to EgressDenialReason.entries.map { it.value },
        )
        for ((name, values) in generated) {
            assertEquals(source.getValue(name).jsonArray.map { it.jsonPrimitive.content }, values, name)
        }
    }

    @Test
    fun `six real generated transports keep raw DPoP and scope headers on core routes`() = runBlocking {
        val captured = mutableListOf<HttpRequestData>()
        val engine = MockEngine { request ->
            captured.add(request)
            respond("{}", HttpStatusCode.OK, headersOf("Content-Type", ContentType.Application.Json.toString()))
        }
        val api = CustomDestinationsApi(baseUrl = "https://api.pennilogic.example/v1", httpClientEngine = engine)
        val authorization = "DPoP synthetic.access.signature"
        api.setApiKey(authorization, "Authorization")
        val key = "00000000-0000-4000-8000-000000000010"
        val id = UUID.fromString("00000000-0000-4000-8000-000000000001")
        val lifecycle = CustomDestinationLifecycleRequest("1")
        api.registerCustomDestination("header.register.signature", "synthetic-step-up", key, registration)
        api.listCustomDestinations("header.list.signature")
        api.validateCustomDestination("header.validate.signature", key, id, lifecycle)
        api.activateCustomDestination("header.activate.signature", "synthetic-step-up", key, id, lifecycle)
        api.suspendCustomDestination("header.suspend.signature", key, id, lifecycle)
        api.revokeCustomDestination("header.revoke.signature", key, id, lifecycle)
        val expected = listOf(
            Triple("POST", "", "register"), Triple("GET", "", "list"),
            Triple("POST", "/$id/validate", "validate"), Triple("POST", "/$id/activate", "activate"),
            Triple("POST", "/$id/suspend", "suspend"), Triple("DELETE", "/$id", "revoke"),
        )
        assertEquals(6, captured.size)
        for ((index, request) in captured.withIndex()) {
            val (method, suffix, action) = expected[index]
            assertEquals(method, request.method.value)
            assertEquals("/v1/ai/custom-destinations$suffix", request.url.encodedPath)
            assertEquals("header.$action.signature", request.headers["DPoP"])
            assertEquals(authorization, request.headers["Authorization"])
            assertFalse(request.headers["Authorization"].orEmpty().startsWith("Bearer "))
            assertFalse(request.headers["Authorization"].orEmpty().startsWith("DPoP DPoP "))
            assertEquals(index == 0 || index == 3, request.headers.contains("Step-Up-Token"))
            assertEquals(index != 1, request.headers.contains("Idempotency-Key"))
            assertFalse(request.headers.contains("DPoP-Nonce"))
            assertEquals("api.pennilogic.example", request.url.host)
            assertTrue(request.url.encodedPath.startsWith("/v1/ai/custom-destinations"))
        }
        assertEquals(6, captured.map { it.headers["DPoP"] }.toSet().size)
        assertEquals("DELETE", captured.last().method.value)
        assertEquals("/v1/ai/custom-destinations/$id", captured.last().url.encodedPath)
        val body = PennilogicJson.json.parseToJsonElement((captured.first().body as TextContent).text).jsonObject
        assertEquals(setOf("host", "credentialHeader", "models"), body.keys)
        assertEquals(JsonArray(listOf(JsonPrimitive("fixture/model-v1"))), body["models"])
    }

    @Test
    fun `valid generated wire decoding includes optional enrolled key references`() {
        val decoded = PennilogicJson.json.decodeFromString<CustomDestinationRegistrationRequest>(
            fixture.getValue("registration").toString()
        )
        assertEquals(fixture.getValue("registration"), PennilogicJson.json.parseToJsonElement(PennilogicJson.json.encodeToString(decoded)))
        PennilogicJson.json.decodeFromString<CustomDestination>(fixture.getValue("destination").toString())
    }

    @Test
    fun `T6 generated response rejects an address in nested registered model data`() {
        val value = fixture.getValue("destination").jsonObject
        PennilogicJson.json.decodeFromString<CustomDestination>(value.toString())
        val model = value.getValue("models").jsonArray.first().jsonObject
        val invalid = JsonObject(value + ("models" to JsonArray(listOf(
            JsonObject(model + ("host" to JsonPrimitive("blocked.example")))
        ))))
        assertFails {
            PennilogicJson.json.decodeFromString<CustomDestination>(invalid.toString())
            Unit
        }
    }

    @TestFactory
    fun `T6 generated registration rejects every invalid fixture`(): List<DynamicTest> {
        val valid = PennilogicJson.json.encodeToString(registration)
        PennilogicJson.json.decodeFromString<CustomDestinationRegistrationRequest>(valid)
        val value = PennilogicJson.json.parseToJsonElement(valid).jsonObject
        return Fixtures.vectors(fixture, "invalid_registration").map { vector ->
            DynamicTest.dynamicTest("T6 registration ${Fixtures.string(vector, "name")}") {
                assertFails {
                    PennilogicJson.json.decodeFromString<CustomDestinationRegistrationRequest>(
                        JsonObject(value + vector.getValue("add").jsonObject).toString()
                    )
                    Unit
                }
            }
        }
    }

    @TestFactory
    fun `T6 generated lifecycle rejects every invalid fixture`(): List<DynamicTest> {
        PennilogicJson.json.decodeFromString<CustomDestinationLifecycleRequest>(fixture.getValue("lifecycle").toString())
        return Fixtures.vectors(fixture, "invalid_lifecycle").map { vector ->
            DynamicTest.dynamicTest("T6 lifecycle ${Fixtures.string(vector, "name")}") {
                assertFails {
                    PennilogicJson.json.decodeFromString<CustomDestinationLifecycleRequest>(vector.getValue("wire").toString())
                    Unit
                }
            }
        }
    }
}
