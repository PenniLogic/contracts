package com.pennilogic.contracts.smoke

import com.pennilogic.contracts.apis.AuthApi
import com.pennilogic.contracts.apis.CategoriesApi
import com.pennilogic.contracts.apis.TransactionsApi
import com.pennilogic.contracts.models.*
import com.pennilogic.contracts.serialization.PennilogicJson
import com.pennilogic.contracts.serialization.ProviderPresence
import com.pennilogic.contracts.success.PostTransactionSuccessStatus201
import com.pennilogic.contracts.success.PostTransactionSuccessStatus202
import com.pennilogic.contracts.time.InstantCodec
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.http.*
import io.ktor.util.reflect.typeInfo
import java.util.UUID
import kotlin.test.*
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.*

class CoreEndpointGeneratedTest {
    private val json = PennilogicJson.json
    private val core = Fixtures.load("core-endpoints.v1.json").getValue("payloads").jsonObject
    private val auth = Fixtures.load("auth-endpoints.v1.json").getValue("payloads").jsonObject
    private val key = "00000000-0000-4000-8000-000000000010"

    @Test
    fun `readonly projection response preserves omission null and uuid with no write payload field`() = runBlocking<Unit> {
        val category = "00000000-0000-7000-8000-000000000008"
        for (extra in listOf(emptyMap(), mapOf("category_id" to JsonNull), mapOf("category_id" to JsonPrimitive(category)))) {
            val wire = JsonObject(core.getValue("transaction").jsonObject + extra)
            val value = json.decodeFromJsonElement<Transaction>(wire)
            assertEquals(wire, json.encodeToJsonElement(value))
            if (extra.isEmpty()) assertEquals(ProviderPresence.Absent, value.categoryId)
            else assertEquals(ProviderPresence.Present(if (extra.getValue("category_id") == JsonNull) null else UUID.fromString(category)), value.categoryId)
            assertEquals(JsonArray(listOf(wire)), json.encodeToJsonElement(listOf(value)))
            val api = TransactionsApi(httpClientEngine = MockEngine {
                respond(wire.toString(), HttpStatusCode.OK, headersOf("Content-Type", "application/json"))
            })
            api.setApiKey("DPoP synthetic.access.signature", "Authorization")
            assertEquals(wire, json.encodeToJsonElement(api.getTransaction(
                "synthetic.projection.signature", "rec_00000000-0000-4000-8000-000000000004").body()))
        }
        for (projected in listOf(JsonNull, JsonPrimitive(category))) {
            val response = JsonObject(core.getValue("categorisation").jsonObject + ("category_id" to projected))
            assertEquals(response, json.encodeToJsonElement(json.decodeFromJsonElement<Categorisation>(response)))
            val request = JsonObject(core.getValue("post_transaction").jsonObject + ("category_id" to projected))
            assertFails { json.decodeFromJsonElement<PostTransactionRequest>(request) }
        }
        assertFails { json.decodeFromJsonElement<Categorisation>(
            JsonObject(core.getValue("categorisation").jsonObject - "category_id")) }
    }

    @Test fun `all eight auth contexts use actual error transports with exact typed wire and no disclosure`() = runBlocking<Unit> {
        val fixture = Fixtures.load("authentication-errors.v1.json")
        val catalogue = json.parseToJsonElement(Fixtures.root.resolve("spec/error-catalogue.v1.json").readText()).jsonObject
        val entries = catalogue.getValue("authentication_codes").jsonArray.associate {
            it.jsonObject.getValue("code").jsonPrimitive.content to it.jsonObject
        }
        assertEquals(8, fixture.getValue("examples").jsonArray.size)
        for (row in fixture.getValue("examples").jsonArray) {
            val code = row.jsonObject.getValue("code").jsonPrimitive.content
            val entry = entries.getValue(code)
            val wire = JsonObject(mapOf(
                "type" to JsonPrimitive("urn:pennilogic:problem:$code"), "title" to entry.getValue("title"),
                "status" to entry.getValue("status"), "detail" to entry.getValue("detail"),
                "code" to JsonPrimitive(code), "correlation_id" to fixture.getValue("correlation_id"),
            ) + row.jsonObject.getValue("context").jsonObject)
            val api = AuthApi(httpClientEngine = MockEngine {
                respond(wire.toString(), HttpStatusCode.fromValue(entry.getValue("status").jsonPrimitive.int),
                    Headers.build { append("Content-Type", "application/problem+json"); append("Cache-Control", "no-store") })
            })
            api.setApiKey("DPoP synthetic.access.signature", "Authorization")
            val response = api.getProfile("synthetic.auth.signature")
            val model = response.typedBody<ApplicationProblemDetail>(typeInfo<ApplicationProblemDetail>())
            assertEquals(wire, json.encodeToJsonElement(model))
            val invalid = JsonObject(wire + ("provider" to JsonPrimitive("PRIVATE_SYNTHETIC_CANARY")))
            val error = assertFails { json.decodeFromJsonElement<ApplicationProblemDetail>(invalid) }
            assertFalse(error.message.orEmpty().contains("PRIVATE_SYNTHETIC_CANARY"))
        }
    }

    @Test
    fun `required null optional absence native presence and nested refs round trip`() {
        val view = CategorisationView(CategorisationViewMode.CURRENT, null)
        assertEquals("""{"mode":"CURRENT","as_of":null}""", json.encodeToString(view))
        assertFails { json.decodeFromString<CategorisationView>("""{"mode":"CURRENT"}""") }
        assertFails { json.decodeFromString<CategorisationView>("""{"mode":"AS_RECORDED","as_of":null}""") }
        val id = UUID.fromString("00000000-0000-7000-8000-000000000024")
        val pending = AuthRecoveryProgress(id, AuthRecoveryState.NOTIFICATION_PENDING,
            recoveryProof = "synthetic_recovery_instrument", windowEndsAt = ProviderPresence.Present(null), retryAfterSeconds = 30)
        assertEquals(auth.getValue("notification_pending"), json.encodeToJsonElement(pending))
        assertFails { AuthRecoveryProgress(id, AuthRecoveryState.NOTIFICATION_PENDING,
            recoveryProof = "synthetic_recovery_instrument", retryAfterSeconds = 30) }
        val unproven = AuthRecoveryProgress(id, AuthRecoveryState.UNPROVEN)
        assertFalse(json.encodeToJsonElement(unproven).jsonObject.containsKey("window_ends_at"))
        val nested = json.decodeFromJsonElement<Categorisation>(core.getValue("categorisation"))
        assertEquals(core.getValue("categorisation"), json.encodeToJsonElement(nested))
        assertEquals(JsonNull, json.encodeToJsonElement(listOf(nested)).jsonArray[0].jsonObject["view"]!!.jsonObject["as_of"])
    }

    @Test
    fun `actual created and decision statuses produce distinct nominal generated bodies`() = runBlocking<Unit> {
        val statuses = mutableListOf(201, 202, 206)
        val decision = Fixtures.load("import-provider.v1.json").getValue("screen")
        val proofs = mutableListOf<String?>()
        val api = TransactionsApi(httpClientEngine = MockEngine { request ->
            proofs.add(request.headers["DPoP"])
            val status = statuses.removeAt(0)
            respond((if (status == 201) core.getValue("transaction") else decision).toString(),
                HttpStatusCode.fromValue(status), headersOf("Content-Type", "application/json"))
        })
        api.setApiKey("DPoP synthetic.access.signature", "Authorization")
        val body = json.decodeFromJsonElement<PostTransactionRequest>(core.getValue("post_transaction"))
        val created = api.postTransaction("synthetic.first.signature", key, body).body()
        val duplicate = api.postTransaction("synthetic.second.signature", key, body).body()
        assertIs<PostTransactionSuccessStatus201>(created)
        assertIs<PostTransactionSuccessStatus202>(duplicate)
        assertEquals(core.getValue("transaction"), json.encodeToJsonElement(created.body))
        assertEquals(decision, json.encodeToJsonElement(duplicate.body))
        assertFails { api.postTransaction("synthetic.third.signature", key, body).body() }
        assertEquals(3, proofs.toSet().size)
    }

    @Test
    fun `actual instant filter and nullable response use canonical seams`() = runBlocking<Unit> {
        var occurred: String? = null
        val transactions = TransactionsApi(httpClientEngine = MockEngine { request ->
            occurred = request.url.parameters["occurred_from"]
            respond(JsonObject(mapOf("transactions" to JsonArray(emptyList()), "page" to core.getValue("cursor_end"))).toString(),
                HttpStatusCode.OK, headersOf("Content-Type", "application/json"))
        })
        transactions.setApiKey("DPoP synthetic.access.signature", "Authorization")
        transactions.listTransactions("synthetic.query.signature",
            occurredFrom = InstantCodec.parse("2026-10-01T00:00:00.000Z")).body()
        assertEquals("2026-10-01T00:00:00.000Z", occurred)
        val categories = CategoriesApi(httpClientEngine = MockEngine {
            respond(core.getValue("categorisation").toString(), HttpStatusCode.OK, headersOf("Content-Type", "application/json"))
        })
        categories.setApiKey("DPoP synthetic.access.signature", "Authorization")
        val value = categories.getCategorisation("synthetic.read.signature",
            "rec_00000000-0000-4000-8000-000000000004").body()
        assertEquals(core.getValue("categorisation"), json.encodeToJsonElement(value))
    }

    @Test
    fun `bootstrap configured global proof never replaces explicit physical proof`() = runBlocking<Unit> {
        var proof: String? = null
        val api = AuthApi(httpClientEngine = MockEngine { request ->
            proof = request.headers["DPoP"]
            respond("""{"enrollment_id":"00000000-0000-7000-8000-000000000021","expires_at":"2026-10-01T00:00:00.000Z"}""",
                HttpStatusCode.Accepted, headersOf("Content-Type", "application/json"))
        })
        api.setApiKey("SYNTHETIC_REUSED_PROOF", "DPoP")
        val body = json.decodeFromJsonElement<AuthEnrollmentRequest>(auth.getValue("enrollment"))
        api.startEnrollment("synthetic.fresh.signature", body).body()
        assertEquals("synthetic.fresh.signature", proof)
    }
}
