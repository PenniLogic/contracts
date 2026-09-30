package com.pennilogic.contracts.smoke

import com.pennilogic.contracts.money.Money
import com.pennilogic.contracts.money.MoneyWireException
import java.security.MessageDigest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive

/** CrossLanguageMoneyRoundTripTest (ADR-015 §7) — Kotlin leg over spec/fixtures/money-roundtrip-generated.v1.json. */
class CrossLanguageMoneyRoundTripTest {
    private val fixture = Fixtures.load("money-roundtrip-generated.v1.json")
    private val rows = Fixtures.vectors(fixture, "values")

    @Test
    fun `the header is consistent`() {
        assertEquals(fixture.getValue("count").jsonPrimitive.content.toInt(), rows.size)
        assertEquals(rows.size, fixture.getValue("boundary_count").jsonPrimitive.content.toInt() + fixture.getValue("generated_count").jsonPrimitive.content.toInt())
        assertTrue(fixture.getValue("generated_count").jsonPrimitive.content.toInt() >= 10_000)
        assertTrue(Regex("^0x[0-9A-F]{16}$").matches(fixture.getValue("seed").jsonPrimitive.content))
        assertEquals(setOf("INR", "JPY", "KWD"), rows.map { (it.getValue("wire") as JsonObject).getValue("currency").jsonPrimitive.content }.toSet())
    }

    @Test
    fun `every value round-trips and the emitted lines hash to the shared digest`() {
        val digest = MessageDigest.getInstance("SHA-256")
        for (row in rows) {
            val wire = row.getValue("wire") as JsonObject
            val name = row["name"]?.jsonPrimitive?.content ?: wire.getValue("amount").jsonPrimitive.content
            val money = Money.fromWire(wire)
            val rendered = money.toWire()
            assertEquals(wire.getValue("amount").jsonPrimitive.content, rendered.amount, name)
            assertEquals(wire.getValue("currency").jsonPrimitive.content, rendered.currency, name)
            assertEquals(row.getValue("minor_units").jsonPrimitive.content, money.minorUnits.toString(), name)
            assertEquals(money, Money.ofMinorUnits(money.minorUnits, money.currency), name)
            digest.update("${rendered.amount}|${rendered.currency}|${money.minorUnits}\n".toByteArray(Charsets.US_ASCII))
        }
        assertEquals(fixture.getValue("round_trip_sha256").jsonPrimitive.content, digest.digest().joinToString("") { "%02x".format(it) })
    }

    @Test
    fun `boundary rows are present for every currency`() {
        val named = rows.filter { it.containsKey("name") }.associateBy { it.getValue("name").jsonPrimitive.content }
        for (code in listOf("INR", "JPY", "KWD")) {
            assertEquals(Long.MAX_VALUE, named.getValue("maximum $code").getValue("minor_units").jsonPrimitive.content.toLong())
            assertEquals(-Long.MAX_VALUE, named.getValue("minimum $code").getValue("minor_units").jsonPrimitive.content.toLong())
            assertEquals(9007199254740993L, named.getValue("2^53+1 $code").getValue("minor_units").jsonPrimitive.content.toLong())
            assertEquals(0L, named.getValue("zero $code").getValue("minor_units").jsonPrimitive.content.toLong())
        }
    }

    @Test
    fun `parse checks in the shared order with the digit bound first`() {
        val wireFixture = Fixtures.load("money-wire-fixtures.v1.json")
        assertEquals(listOf("grammar", "currency_unknown", "scale_mismatch", "out_of_range"), wireFixture.getValue("parse_reason_order").jsonArray.map { it.jsonPrimitive.content })
        for (vector in Fixtures.vectors(wireFixture, "parse_invalid")) {
            val name = Fixtures.string(vector, "name")
            val error = assertFailsWith<MoneyWireException>(name) { Money.parse(Fixtures.string(vector, "amount"), Fixtures.string(vector, "currency")) }
            assertEquals(Fixtures.string(vector, "reason"), error.reason.wireName, name)
        }
        assertEquals("out_of_range", assertFailsWith<MoneyWireException> { Money.parse("9".repeat(5003), "JPY") }.reason.wireName)
    }
}
