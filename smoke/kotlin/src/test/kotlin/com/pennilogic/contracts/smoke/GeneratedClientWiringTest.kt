package com.pennilogic.contracts.smoke

import com.pennilogic.contracts.infrastructure.ApiClient
import com.pennilogic.contracts.infrastructure.RequestConfig
import com.pennilogic.contracts.infrastructure.RequestMethod
import com.pennilogic.contracts.infrastructure.wrap
import com.pennilogic.contracts.money.Money
import com.pennilogic.contracts.money.MoneyWireException
import com.pennilogic.contracts.time.InstantWireException
import io.ktor.client.engine.HttpClientEngine
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.client.request.HttpRequestData
import io.ktor.client.statement.HttpResponse
import io.ktor.content.TextContent
import io.ktor.http.ContentType
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import io.ktor.serialization.JsonConvertException
import io.ktor.utils.io.core.toByteArray
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneOffset
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertIs
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.Contextual
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Proves ADR-015 §2 for the artifact itself: the generated `ApiClient` installs `PennilogicJson` as
 * its `ContentNegotiation` converter (template override under `generator/templates/kotlin`), so a
 * request body and a response body with `Money` and `@Contextual` instants and dates cross the
 * seams without the consumer configuring anything. `Envelope` mirrors exactly what the generator
 * emits for a Money-bearing model (asserted on generated source by `scripts/tests/test_generate.py`).
 */
class GeneratedClientWiringTest {

    @Serializable
    private data class Envelope(
        @SerialName("total") val total: Money,
        @Contextual @SerialName("recorded_at") val recordedAt: OffsetDateTime,
        @Contextual @SerialName("booked_on") val bookedOn: LocalDate,
        @SerialName("note") val note: String? = null,
    )

    /** The generated client keeps its request methods protected; the smoke consumer subclasses it like a generated API class would. */
    private class Probe(engine: HttpClientEngine) : ApiClient(baseUrl = "https://api.pennilogic.example/v1", httpClientEngine = engine) {
        suspend fun post(body: Envelope): HttpResponse =
            jsonRequest(RequestConfig<Unit>(RequestMethod.POST, "/probe", requiresAuthentication = false), body, listOf())

        suspend fun get(): HttpResponse =
            jsonRequest(RequestConfig<Unit>(RequestMethod.GET, "/probe", requiresAuthentication = false), null, listOf())
    }

    private val responseBody = """{"total":{"amount":"-1234.56","currency":"INR"},"recorded_at":"2026-09-30T04:52:08.439Z","booked_on":"2026-09-30","extra_member_from_a_newer_server":1}"""

    private fun engine(body: String, capture: (HttpRequestData) -> Unit = {}): MockEngine = MockEngine { request ->
        capture(request)
        respond(body, HttpStatusCode.OK, headersOf("Content-Type", ContentType.Application.Json.toString()))
    }

    @Test
    fun `request bodies cross the seams through the generated client`() = runBlocking {
        var sent: String? = null
        val client = Probe(engine(responseBody) { request -> sent = (request.body as TextContent).text })
        client.post(Envelope(Money.parse("-1234.56", "INR"), OffsetDateTime.of(2026, 9, 30, 10, 22, 8, 439_000_000, ZoneOffset.ofHoursMinutes(5, 30)), LocalDate.of(2026, 9, 30)))
        assertEquals("""{"total":{"amount":"-1234.56","currency":"INR"},"recorded_at":"2026-09-30T04:52:08.439Z","booked_on":"2026-09-30"}""", sent)
    }

    @Test
    fun `response bodies cross the seams through the generated client and tolerate additive members`() = runBlocking {
        val response = Probe(engine(responseBody)).get()
        val envelope = response.wrap<Envelope>().body()
        assertEquals(Money.parse("-1234.56", "INR"), envelope.total)
        assertEquals(-123456L, envelope.total.minorUnits)
        assertEquals("2026-09-30T04:52:08.439Z", com.pennilogic.contracts.time.InstantCodec.format(envelope.recordedAt))
        assertEquals(LocalDate.of(2026, 9, 30), envelope.bookedOn)
    }

    @Test
    fun `a JSON number for money or a non-canonical instant fails closed inside the generated client`() = runBlocking {
        // Ktor wraps a converter failure in JsonConvertException; the seam exception is its cause and carries reason + field, never the value.
        val numberBody = responseBody.replace("\"-1234.56\"", "-1234.56")
        val moneyError = assertFailsWith<JsonConvertException> { Probe(engine(numberBody)).get().wrap<Envelope>().body() }
        val moneyCause = assertIs<MoneyWireException>(moneyError.cause)
        assertEquals("number_not_string", moneyCause.reason.wireName)
        assertFalse(moneyError.message.orEmpty().contains("1234"))
        val offsetBody = responseBody.replace("2026-09-30T04:52:08.439Z", "2026-09-30T09:52:08.439+05:00")
        val instantError = assertFailsWith<JsonConvertException> { Probe(engine(offsetBody)).get().wrap<Envelope>().body() }
        assertEquals("grammar", assertIs<InstantWireException>(instantError.cause).reason.wireName)
    }

    @Suppress("unused")
    private fun keepImports(): ByteArray = "".toByteArray()
}
