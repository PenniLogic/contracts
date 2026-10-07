package com.pennilogic.contracts.serialization

import kotlinx.serialization.KSerializer
import kotlinx.serialization.Serializable
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder

/** Optional nullable fields must distinguish absence from an explicitly supplied null. */
@Serializable(with = ProviderPresenceSerializer::class)
sealed interface ProviderPresence<out T> {
    data object Absent : ProviderPresence<Nothing>
    data class Present<T>(val value: T) : ProviderPresence<T>
}

class ProviderPresenceSerializer<T>(private val valueSerializer: KSerializer<T>) : KSerializer<ProviderPresence<T>> {
    override val descriptor: SerialDescriptor get() = valueSerializer.descriptor

    override fun deserialize(decoder: Decoder): ProviderPresence<T> =
        ProviderPresence.Present(decoder.decodeSerializableValue(valueSerializer))

    override fun serialize(encoder: Encoder, value: ProviderPresence<T>) {
        when (value) {
            ProviderPresence.Absent -> throw ProviderWireException()
            is ProviderPresence.Present -> encoder.encodeSerializableValue(valueSerializer, value.value)
        }
    }
}
