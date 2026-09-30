package com.pennilogic.contracts.smoke

import com.pennilogic.contracts.money.CurrencyRegistry
import com.pennilogic.contracts.money.Money
import com.pennilogic.contracts.money.MoneyReason
import com.pennilogic.contracts.money.MoneyWireException
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNotEquals
import kotlin.test.assertTrue
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive

/** MoneyWireConformanceTest for the generated Kotlin client (ADR-015 §2, §7). */
class MoneyWireConformanceTest {
    private val fixture = Fixtures.load("money-wire-fixtures.v1.json")
    private val json = Json

    @Serializable
    private data class Holder(val total: Money)

    private fun reasonOf(block: () -> Unit): Pair<String, String> {
        val error = assertFailsWith<MoneyWireException> { block() }
        return error.reason.wireName to error.field
    }

    @Test
    fun `money is Long minor units with currency and never a floating point type`() {
        val money = Money.parse("-1234.56", "INR")
        assertEquals(-123456L, money.minorUnits)
        assertEquals("INR", money.currency)
        assertEquals(Long::class, money.minorUnits::class)
    }

    @Test
    fun `a JSON number on the wire is rejected with number_not_string`() {
        assertEquals("number_not_string" to "amount", reasonOf { json.decodeFromString<Money>("""{"amount": 1234.56, "currency": "INR"}""") })
        assertEquals("number_not_string" to "amount", reasonOf { json.decodeFromString<Money>("""{"amount": 123456, "currency": "INR"}""") })
        assertEquals("number_not_string" to "amount", reasonOf { Money.fromWire(JsonObject(mapOf("amount" to JsonPrimitive(1234.56), "currency" to JsonPrimitive("INR")))) })
    }

    @Test
    fun `reason order matches the fixture`() {
        val expected = fixture.getValue("reason_order").jsonArray.map { it.jsonPrimitive.content }
        assertEquals(expected, MoneyReason.entries.map { it.wireName })
    }

    @Test
    fun `every valid vector round-trips byte-identically with the expected minor units`() {
        for (vector in Fixtures.vectors(fixture, "valid")) {
            val name = Fixtures.string(vector, "name")
            val wire = vector.getValue("wire") as JsonObject
            val money = Money.fromWire(wire)
            assertEquals(Fixtures.string(vector, "minor_units").toLong(), money.minorUnits, name)
            val rendered = money.toWire()
            assertEquals(wire["amount"]!!.jsonPrimitive.content, rendered.amount, name)
            assertEquals(wire["currency"]!!.jsonPrimitive.content, rendered.currency, name)
            assertEquals(money, Money.parse(rendered.amount, rendered.currency), name)
            assertEquals(money, Money.ofMinorUnits(money.minorUnits, money.currency), name)
            assertEquals(json.encodeToString(Holder(money)), """{"total":${json.encodeToString(wire)}}""", name)
            assertEquals(money, json.decodeFromString<Holder>("""{"total":${json.encodeToString(wire)}}""").total, name)
        }
    }

    @Test
    fun `every invalid vector is rejected with exactly the fixture reason and field`() {
        for (vector in Fixtures.vectors(fixture, "invalid")) {
            val name = Fixtures.string(vector, "name")
            val wire = vector.getValue("wire")
            val expected = Fixtures.string(vector, "reason") to Fixtures.string(vector, "field")
            assertEquals(expected, reasonOf { Money.fromWire(wire) }, name)
            assertEquals(expected, reasonOf { json.decodeFromString<Money>(json.encodeToString(wire)) }, name)
        }
    }

    @Test
    fun `rejections never echo the offending value`() {
        val error = assertFailsWith<MoneyWireException> { Money.parse("1234.567", "INR") }
        assertFalse(error.message!!.contains("1234"))
        assertEquals(MoneyReason.SCALE_MISMATCH, error.reason)
    }

    @Test
    fun `range excludes Long MIN_VALUE and arithmetic is overflow-checked`() {
        assertEquals("9223372036854775807", Money.ofMinorUnits(Long.MAX_VALUE, "JPY").toWire().amount)
        assertEquals("-9223372036854775807", Money.ofMinorUnits(-Long.MAX_VALUE, "JPY").toWire().amount)
        assertEquals(MoneyReason.OUT_OF_RANGE, assertFailsWith<MoneyWireException> { Money.ofMinorUnits(Long.MIN_VALUE, "JPY") }.reason)
        assertFailsWith<ArithmeticException> { Money.ofMinorUnits(Long.MAX_VALUE, "JPY") + Money.ofMinorUnits(1, "JPY") }
        assertFailsWith<ArithmeticException> { Money.ofMinorUnits(-Long.MAX_VALUE, "JPY") - Money.ofMinorUnits(2, "JPY") }
    }

    @Test
    fun `arithmetic and comparison stay within one currency`() {
        val a = Money.parse("1.50", "INR")
        val b = Money.parse("-0.75", "INR")
        assertEquals("0.75", (a + b).toWire().amount)
        assertEquals("2.25", (a - b).toWire().amount)
        assertEquals("0.75", (-b).toWire().amount)
        assertTrue(a > b)
        assertFailsWith<IllegalArgumentException> { a + Money.parse("1", "JPY") }
        assertFailsWith<IllegalArgumentException> { a.compareTo(Money.parse("1", "JPY")) }
        assertNotEquals(Money.parse("150", "JPY"), a)
        assertEquals(Money.parse("1.50", "INR"), a)
        assertEquals(a.hashCode(), Money.parse("1.50", "INR").hashCode())
    }

    @Test
    fun `formatting matches the registry exponent for every currency and is never scientific`() {
        for ((code, entry) in CurrencyRegistry.entries) {
            for (minor in listOf(0L, 1L, 10L, 100L, 1_000_000_000_000_000_000L, -1_000_000_000_000_000_000L)) {
                val amount = Money.ofMinorUnits(minor, code).toWire().amount
                val fraction = amount.substringAfter('.', "")
                assertEquals(entry.exponent, fraction.length, "$code $minor")
                assertFalse(amount.contains('E') || amount.contains('e'))
            }
        }
    }
}
