package com.pennilogic.contracts.serialization

import java.util.UUID
import kotlinx.serialization.KSerializer
import kotlinx.serialization.descriptors.PrimitiveKind
import kotlinx.serialization.descriptors.PrimitiveSerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.JsonPrimitive

object UuidSerializer : KSerializer<UUID> {
    private val grammar = Regex("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
    override val descriptor = PrimitiveSerialDescriptor("java.util.UUID", PrimitiveKind.STRING)

    fun accepts(value: String): Boolean = grammar.matches(value)

    override fun deserialize(decoder: Decoder): UUID {
        if (decoder !is JsonDecoder) throw ProviderWireException()
        val value = decoder.decodeJsonElement()
        if (value !is JsonPrimitive || !value.isString || !accepts(value.content)) throw ProviderWireException()
        return UUID.fromString(value.content)
    }

    override fun serialize(encoder: Encoder, value: UUID) {
        encoder.encodeString(value.toString())
    }
}
