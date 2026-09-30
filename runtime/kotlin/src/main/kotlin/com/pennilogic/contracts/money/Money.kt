// PenniLogic hand-written seam shipped with every generated Kotlin client (ADR-015 §1, §2).
package com.pennilogic.contracts.money

import kotlinx.serialization.KSerializer
import kotlinx.serialization.Serializable
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.descriptors.buildClassSerialDescriptor
import kotlinx.serialization.descriptors.element
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonEncoder
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** Reasons in the fixed ADR-015 §1.5 check order; a rejection names the reason and the field, never the value. */
enum class MoneyReason(val wireName: String) {
    SHAPE("shape"),
    NUMBER_NOT_STRING("number_not_string"),
    GRAMMAR("grammar"),
    CURRENCY_UNKNOWN("currency_unknown"),
    SCALE_MISMATCH("scale_mismatch"),
    OUT_OF_RANGE("out_of_range"),
}

class MoneyWireException(val reason: MoneyReason, val field: String) :
    IllegalArgumentException("money rejected: ${reason.wireName} at ${field.ifEmpty { "<value>" }}")

/**
 * Integer minor units with currency; the only representation of money in application code.
 * Construct with [parse] (canonical string), [ofMinorUnits] (Long) or through [MoneySerializer]
 * (the wire object). Double, Float and BigDecimal never carry money.
 */
@Serializable(with = MoneySerializer::class)
class Money private constructor(val minorUnits: Long, val currency: String) : Comparable<Money> {

    /**
     * Render the wire object; the amount is self-checked against the grammar and re-parsed. The raw
     * shape is internal to the client (ADR-015 §1.1): only [MoneySerializer] puts it on the wire, and
     * application code reads [minorUnits] and [currency].
     */
    internal fun toWire(): MoneyWire {
        val exponent = CurrencyRegistry.exponentOf(currency) ?: throw MoneyWireException(MoneyReason.CURRENCY_UNKNOWN, "currency")
        val amount = formatMinorUnits(minorUnits, exponent)
        check(minorUnitsFromCanonical(amount, exponent) == minorUnits) { "money destruction seam produced a non-canonical amount" }
        return MoneyWire(amount, currency)
    }

    operator fun plus(other: Money): Money = ofMinorUnits(Math.addExact(minorUnits, sameCurrency(other).minorUnits), currency)

    operator fun minus(other: Money): Money = ofMinorUnits(Math.subtractExact(minorUnits, sameCurrency(other).minorUnits), currency)

    operator fun unaryMinus(): Money = ofMinorUnits(Math.negateExact(minorUnits), currency)

    override fun compareTo(other: Money): Int = minorUnits.compareTo(sameCurrency(other).minorUnits)

    private fun sameCurrency(other: Money): Money {
        require(other.currency == currency) { "Money operates only within one currency" }
        return other
    }

    override fun equals(other: Any?): Boolean = other is Money && other.currency == currency && other.minorUnits == minorUnits

    override fun hashCode(): Int = 31 * minorUnits.hashCode() + currency.hashCode()

    override fun toString(): String = toWire().let { "${it.amount} ${it.currency}" }

    companion object {
        const val MAX_MINOR_UNITS: Long = Long.MAX_VALUE
        private val GRAMMAR = Regex("^(0(\\.[0-9]+)?|-?[1-9][0-9]*(\\.[0-9]+)?|-0\\.[0-9]*[1-9][0-9]*)$")

        fun ofMinorUnits(minorUnits: Long, currency: String): Money {
            CurrencyRegistry.exponentOf(currency) ?: throw MoneyWireException(MoneyReason.CURRENCY_UNKNOWN, "currency")
            if (minorUnits == Long.MIN_VALUE) throw MoneyWireException(MoneyReason.OUT_OF_RANGE, "amount")
            return Money(minorUnits, currency)
        }

        /** Construct from a canonical amount string, checking grammar, currency, scale and range. */
        fun parse(amount: String, currency: String): Money {
            if (!GRAMMAR.matches(amount)) throw MoneyWireException(MoneyReason.GRAMMAR, "amount")
            val exponent = CurrencyRegistry.exponentOf(currency) ?: throw MoneyWireException(MoneyReason.CURRENCY_UNKNOWN, "currency")
            return Money(minorUnitsFromCanonical(amount, exponent), currency)
        }

        /** Construct from a decoded JSON element, checking in the fixed ADR-015 §1.5 order. */
        fun fromWire(element: JsonElement): Money {
            val obj = element as? JsonObject ?: throw MoneyWireException(MoneyReason.SHAPE, "")
            for (member in MEMBERS) if (!obj.containsKey(member)) throw MoneyWireException(MoneyReason.SHAPE, member)
            for (key in obj.keys) if (key !in MEMBERS) throw MoneyWireException(MoneyReason.SHAPE, key)
            for (member in MEMBERS) {
                val value = obj[member]
                if (value !is JsonPrimitive || value is JsonNull || (!value.isString && value.content.toDoubleOrNull() == null)) {
                    throw MoneyWireException(MoneyReason.SHAPE, member)
                }
            }
            for (member in MEMBERS) if (!(obj[member] as JsonPrimitive).isString) throw MoneyWireException(MoneyReason.NUMBER_NOT_STRING, member)
            return parse((obj["amount"] as JsonPrimitive).content, (obj["currency"] as JsonPrimitive).content)
        }

        private val MEMBERS = listOf("amount", "currency")

        /** Grammar, scale and range of a canonical string; the sign is applied after an unsigned parse so -2^63 is refused. */
        internal fun minorUnitsFromCanonical(amount: String, exponent: Int): Long {
            if (!GRAMMAR.matches(amount)) throw MoneyWireException(MoneyReason.GRAMMAR, "amount")
            val negative = amount.startsWith("-")
            val unsigned = if (negative) amount.substring(1) else amount
            val point = unsigned.indexOf('.')
            val integerPart = if (point == -1) unsigned else unsigned.substring(0, point)
            val fraction = if (point == -1) "" else unsigned.substring(point + 1)
            if (fraction.length != exponent) throw MoneyWireException(MoneyReason.SCALE_MISMATCH, "amount")
            val digits = integerPart + fraction
            if (digits.length > 19) throw MoneyWireException(MoneyReason.OUT_OF_RANGE, "amount")
            val magnitude = digits.toLongOrNull() ?: throw MoneyWireException(MoneyReason.OUT_OF_RANGE, "amount")
            return if (negative) -magnitude else magnitude
        }

        internal fun formatMinorUnits(minorUnits: Long, exponent: Int): String {
            val negative = minorUnits < 0
            val digits = (if (negative) -minorUnits else minorUnits).toString().padStart(exponent + 1, '0')
            val integerPart = digits.substring(0, digits.length - exponent)
            val fraction = digits.substring(digits.length - exponent)
            val unsigned = if (exponent == 0) integerPart else "$integerPart.$fraction"
            return if (negative) "-$unsigned" else unsigned
        }
    }
}

/** The raw wire shape; internal to the client and produced only by [Money.toWire]. Deliberately not @Serializable: the only serializable money type is [Money]. */
internal data class MoneyWire(val amount: String, val currency: String)

/** The single construction and destruction seam between the wire object and [Money]. */
object MoneySerializer : KSerializer<Money> {
    override val descriptor: SerialDescriptor = buildClassSerialDescriptor("com.pennilogic.contracts.money.Money") {
        element<String>("amount")
        element<String>("currency")
    }

    override fun deserialize(decoder: Decoder): Money {
        val jsonDecoder = decoder as? JsonDecoder ?: throw IllegalStateException("Money is a JSON-only wire type")
        return Money.fromWire(jsonDecoder.decodeJsonElement())
    }

    override fun serialize(encoder: Encoder, value: Money) {
        val jsonEncoder = encoder as? JsonEncoder ?: throw IllegalStateException("Money is a JSON-only wire type")
        val wire = value.toWire()
        jsonEncoder.encodeJsonElement(JsonObject(mapOf("amount" to JsonPrimitive(wire.amount), "currency" to JsonPrimitive(wire.currency))))
    }
}
