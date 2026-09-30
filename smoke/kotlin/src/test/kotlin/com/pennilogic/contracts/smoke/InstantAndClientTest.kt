package com.pennilogic.contracts.smoke

import com.pennilogic.contracts.infrastructure.ApiClient
import com.pennilogic.contracts.models.ProblemDetail
import com.pennilogic.contracts.money.Money
import com.pennilogic.contracts.time.InstantCodec
import com.pennilogic.contracts.time.InstantReason
import com.pennilogic.contracts.time.InstantWireException
import com.pennilogic.contracts.time.PennilogicSerializers
import java.io.File
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneOffset
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue
import kotlinx.serialization.Contextual
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/** InstantWireConformanceTest and client import test for the generated Kotlin client (ADR-015 §3.1). */
class InstantAndClientTest {
    private val fixture = Fixtures.load("instant-wire-fixtures.v1.json")
    private val json = Json { serializersModule = PennilogicSerializers.module }

    /** What a generated model with instant and date members looks like: `@Contextual` java.time types crossing the seam. */
    @Serializable
    private data class Stamped(@Contextual val at: OffsetDateTime, @Contextual val day: LocalDate, val total: Money)

    @Test
    fun `every valid instant round-trips with the expected epoch milliseconds`() {
        for (vector in Fixtures.vectors(fixture, "valid")) {
            val name = Fixtures.string(vector, "name")
            val wire = Fixtures.string(vector, "wire")
            val parsed = InstantCodec.parse(wire)
            assertEquals(Fixtures.string(vector, "epoch_millis").toLong(), parsed.toInstant().toEpochMilli(), name)
            assertEquals(wire, InstantCodec.format(parsed), name)
            assertEquals(wire, InstantCodec.format(InstantCodec.parseInstant(wire)), name)
        }
    }

    @Test
    fun `every invalid instant is rejected with exactly the fixture reason`() {
        for (vector in Fixtures.vectors(fixture, "invalid")) {
            val name = Fixtures.string(vector, "name")
            val wire = vector.getValue("wire")
            val expected = Fixtures.string(vector, "reason")
            val document = """{"at":${json.encodeToString(wire)},"day":"2026-09-30","total":{"amount":"0.00","currency":"INR"}}"""
            if (wire is JsonNull) {
                // A JSON null never reaches the seam: kotlinx.serialization refuses it for a non-nullable member first.
                assertFailsWith<Exception>(name) { json.decodeFromString<Stamped>(document) }
                continue
            }
            val error = assertFailsWith<InstantWireException>(name) { json.decodeFromString<Stamped>(document) }
            assertEquals(expected, error.reason.wireName, name)
        }
    }

    @Test
    fun `the contextual serializers render the canonical forms and self-check precision`() {
        val stamped = Stamped(OffsetDateTime.of(2026, 9, 30, 4, 52, 8, 439_000_000, ZoneOffset.UTC), LocalDate.of(2026, 9, 30), Money.parse("-1234.56", "INR"))
        val text = json.encodeToString(stamped)
        assertEquals("""{"at":"2026-09-30T04:52:08.439Z","day":"2026-09-30","total":{"amount":"-1234.56","currency":"INR"}}""", text)
        assertEquals(stamped, json.decodeFromString<Stamped>(text))
        val offset = OffsetDateTime.of(2026, 9, 30, 10, 22, 8, 439_000_000, ZoneOffset.ofHoursMinutes(5, 30))
        assertEquals("2026-09-30T04:52:08.439Z", InstantCodec.format(offset))
        assertFailsWith<IllegalArgumentException> { InstantCodec.format(OffsetDateTime.of(2026, 9, 30, 4, 52, 8, 439_000_001, ZoneOffset.UTC)) }
        assertEquals(InstantReason.CALENDAR, assertFailsWith<InstantWireException> { json.decodeFromString<Stamped>("""{"at":"2026-09-30T04:52:08.439Z","day":"2026-02-30","total":{"amount":"0.00","currency":"INR"}}""") }.reason)
        assertEquals(InstantReason.GRAMMAR, assertFailsWith<InstantWireException> { json.decodeFromString<Stamped>("""{"at":"2026-09-30T04:52:08.439Z","day":"2026-9-30","total":{"amount":"0.00","currency":"INR"}}""") }.reason)
    }

    @Test
    fun `the generated client compiles and a ProblemDetail decodes`() {
        val client = ApiClient(baseUrl = ApiClient.BASE_URL, httpClientEngine = null)
        assertEquals("https://api.pennilogic.example/v1", ApiClient.BASE_URL)
        assertEquals(ApiClient.BASE_URL, client.let { ApiClient.BASE_URL })
        val problem = json.decodeFromString<ProblemDetail>(
            """{"type":"about:blank","title":"Validation rejected","status":422,"code":"validation_rejected","correlation_id":"req-01HZY0000000000000000000","field":"amount","reason":"scale_mismatch"}""",
        )
        assertEquals(422, problem.status)
        assertEquals("validation_rejected", problem.code)
        assertEquals("scale_mismatch", problem.reason)
    }

    @Test
    fun `the manifest records the specification and generator versions`() {
        val manifest = Json.parseToJsonElement(Fixtures.root.resolve("build/generated/kotlin/contracts-manifest.json").readText()).jsonObject
        assertEquals("kotlin", manifest.getValue("target").jsonPrimitive.content)
        assertTrue(Regex("""\d+\.\d+\.\d+""").matches(manifest.getValue("spec_version").jsonPrimitive.content))
        assertEquals("7.25.0", (manifest.getValue("generator") as JsonObject).getValue("version").jsonPrimitive.content)
        assertEquals(64, manifest.getValue("tree_sha256").jsonPrimitive.content.length)
        assertTrue(manifest.getValue("files").jsonArray.isNotEmpty())
        assertTrue(File(Fixtures.root, "spec/openapi.yaml").isFile)
    }
}
