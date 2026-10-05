// PenniLogic hand-written seam shipped with every generated Kotlin client (ADR-015 §3.1).
package com.pennilogic.contracts.time

import com.pennilogic.contracts.serialization.UuidSerializer

import java.time.Instant
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import kotlinx.serialization.KSerializer
import kotlinx.serialization.descriptors.PrimitiveKind
import kotlinx.serialization.descriptors.PrimitiveSerialDescriptor
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.modules.SerializersModule
import kotlinx.serialization.modules.contextual

enum class InstantReason(val wireName: String) { SHAPE("shape"), GRAMMAR("grammar"), CALENDAR("calendar") }

class InstantWireException(val reason: InstantReason) : IllegalArgumentException("instant rejected: ${reason.wireName}")

/**
 * The wire form is exactly `YYYY-MM-DDTHH:MM:SS.sssZ`. Parsing validates the grammar and the
 * calendar and requires the re-formatted value to equal the input; formatting always yields the
 * 24-character form. The generated models type instants as `@Contextual java.time.OffsetDateTime`
 * (always at offset Z here); [toInstant] and [ofInstant] convert to and from `java.time.Instant`.
 */
object InstantCodec {
    private val GRAMMAR = Regex(
        "^(000[1-9]|00[1-9][0-9]|0[1-9][0-9]{2}|[1-9][0-9]{3})-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])" +
            "T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]\\.[0-9]{3}Z$",
    )
    private val FORMAT = DateTimeFormatter.ofPattern("uuuu-MM-dd'T'HH:mm:ss.SSSX")

    fun parse(text: String): OffsetDateTime {
        if (!GRAMMAR.matches(text)) throw InstantWireException(InstantReason.GRAMMAR)
        val value = try {
            OffsetDateTime.of(
                text.substring(0, 4).toInt(), text.substring(5, 7).toInt(), text.substring(8, 10).toInt(),
                text.substring(11, 13).toInt(), text.substring(14, 16).toInt(), text.substring(17, 19).toInt(),
                text.substring(20, 23).toInt() * 1_000_000, ZoneOffset.UTC,
            )
        } catch (error: java.time.DateTimeException) {
            throw InstantWireException(InstantReason.CALENDAR)
        }
        if (format(value) != text) throw InstantWireException(InstantReason.CALENDAR)
        return value
    }

    fun parseInstant(text: String): Instant = parse(text).toInstant()

    /** Format at offset Z with exactly three fraction digits; sub-millisecond precision is a caller error. */
    fun format(value: OffsetDateTime): String {
        val utc = value.withOffsetSameInstant(ZoneOffset.UTC)
        require(utc.nano % 1_000_000 == 0) { "instants are truncated to milliseconds by the application before they reach the seam" }
        require(utc.year in 1..9999) { "instant year must be within 0001-9999" }
        val text = FORMAT.format(utc)
        check(GRAMMAR.matches(text)) { "instant destruction seam produced a non-canonical value" }
        return text
    }

    fun format(value: Instant): String = format(value.atOffset(ZoneOffset.UTC))
}

/** Contextual serializer for `java.time.OffsetDateTime`, the generated models' instant type. */
object InstantSerializer : KSerializer<OffsetDateTime> {
    override val descriptor: SerialDescriptor = PrimitiveSerialDescriptor("com.pennilogic.contracts.time.Instant", PrimitiveKind.STRING)

    override fun deserialize(decoder: Decoder): OffsetDateTime {
        val jsonDecoder = decoder as? JsonDecoder ?: throw IllegalStateException("Instant is a JSON-only wire type")
        val element = jsonDecoder.decodeJsonElement()
        if (element !is JsonPrimitive || !element.isString) throw InstantWireException(InstantReason.SHAPE)
        return InstantCodec.parse(element.content)
    }

    override fun serialize(encoder: Encoder, value: OffsetDateTime) = encoder.encodeString(InstantCodec.format(value))
}

/** Contextual serializer for `java.time.LocalDate`: exactly `YYYY-MM-DD` (ADR-015 §3.1 date-only facts). */
object LocalDateSerializer : KSerializer<LocalDate> {
    private val GRAMMAR = Regex("^(000[1-9]|00[1-9][0-9]|0[1-9][0-9]{2}|[1-9][0-9]{3})-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$")
    override val descriptor: SerialDescriptor = PrimitiveSerialDescriptor("com.pennilogic.contracts.time.LocalDate", PrimitiveKind.STRING)

    override fun deserialize(decoder: Decoder): LocalDate {
        val jsonDecoder = decoder as? JsonDecoder ?: throw IllegalStateException("LocalDate is a JSON-only wire type")
        val element = jsonDecoder.decodeJsonElement()
        if (element !is JsonPrimitive || !element.isString) throw InstantWireException(InstantReason.SHAPE)
        val text = element.content
        if (!GRAMMAR.matches(text)) throw InstantWireException(InstantReason.GRAMMAR)
        val value = try {
            LocalDate.of(text.substring(0, 4).toInt(), text.substring(5, 7).toInt(), text.substring(8, 10).toInt())
        } catch (error: java.time.DateTimeException) {
            throw InstantWireException(InstantReason.CALENDAR)
        }
        if (value.toString() != text) throw InstantWireException(InstantReason.CALENDAR)
        return value
    }

    override fun serialize(encoder: Encoder, value: LocalDate) {
        require(value.year in 1..9999) { "date year must be within 0001-9999" }
        encoder.encodeString(value.toString())
    }
}

/** Register with `Json { serializersModule = PennilogicSerializers.module }` so every `@Contextual` instant and date crosses the seam. */
object PennilogicSerializers {
    val module: SerializersModule = SerializersModule {
        contextual(InstantSerializer)
        contextual(LocalDateSerializer)
        contextual(UuidSerializer)
    }
}
