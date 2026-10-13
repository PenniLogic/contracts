package com.pennilogic.contracts.serialization

import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerializationException
import kotlinx.serialization.descriptors.*
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.*
import kotlinx.serialization.modules.SerializersModule

class ProviderWireException : SerializationException("provider value rejected")

@OptIn(ExperimentalSerializationApi::class)
abstract class StrictProviderSerializer<T>(private val delegate: KSerializer<T>, private val schemaName: String) : KSerializer<T> {
    override val descriptor: SerialDescriptor get() = delegate.descriptor

    protected open fun validateContent(value: JsonElement) {}
    protected open fun verify(value: T) {}

    fun validateValue(value: T) {
        verify(value)
        val wire = safe { PennilogicJson.json.encodeToJsonElement(delegate, value) }
        validate(wire, PennilogicJson.json.serializersModule)
    }

    private fun validate(value: JsonElement, module: SerializersModule) {
        validateKinds(value, descriptor, module)
        ProviderConstraints.validate(schemaName, value)
        validateContent(value)
    }

    override fun deserialize(decoder: Decoder): T {
        if (decoder !is JsonDecoder) throw ProviderWireException()
        val wire = safe { decoder.decodeJsonElement() }
        validate(wire, decoder.serializersModule)
        return safe { decoder.json.decodeFromJsonElement(delegate, wire) }.also { verify(it) }
    }

    override fun serialize(encoder: Encoder, value: T) {
        if (encoder !is JsonEncoder) throw ProviderWireException()
        verify(value)
        val wire = safe { encoder.json.encodeToJsonElement(delegate, value) }
        validate(wire, encoder.serializersModule)
        encoder.encodeJsonElement(wire)
    }

    private fun <V> safe(block: () -> V): V = try { block() }
    catch (_: SerializationException) { throw ProviderWireException() }

    private fun validateKinds(value: JsonElement, declared: SerialDescriptor, module: SerializersModule) {
        val type = if (declared.kind == SerialKind.CONTEXTUAL) module.getContextualDescriptor(declared) ?: declared else declared
        if (value == JsonNull) {
            if (declared.isNullable) return
            throw ProviderWireException()
        }
        fun primitive(predicate: (JsonPrimitive) -> Boolean) {
            if (value !is JsonPrimitive || value == JsonNull || !predicate(value)) throw ProviderWireException()
        }
        when (type.kind) {
            PrimitiveKind.BOOLEAN -> primitive { !it.isString && it.booleanOrNull != null }
            PrimitiveKind.BYTE -> primitive { !it.isString && it.intOrNull?.let { number -> number in Byte.MIN_VALUE..Byte.MAX_VALUE } == true }
            PrimitiveKind.SHORT -> primitive { !it.isString && it.intOrNull?.let { number -> number in Short.MIN_VALUE..Short.MAX_VALUE } == true }
            PrimitiveKind.INT -> primitive { !it.isString && it.intOrNull != null }
            PrimitiveKind.LONG -> primitive { !it.isString && it.longOrNull != null }
            PrimitiveKind.STRING, PrimitiveKind.CHAR -> primitive { it.isString }
            PrimitiveKind.FLOAT, PrimitiveKind.DOUBLE -> throw ProviderWireException()
            SerialKind.ENUM -> primitive { it.isString && (0 until type.elementsCount).any { index -> type.getElementName(index) == it.content } }
            StructureKind.LIST -> {
                if (value !is JsonArray) throw ProviderWireException()
                value.forEach { validateKinds(it, type.getElementDescriptor(0), module) }
            }
            StructureKind.CLASS, StructureKind.OBJECT -> {
                if (value !is JsonObject) throw ProviderWireException()
                val fields = (0 until type.elementsCount).associateBy { type.getElementName(it) }
                if (value.keys.any { it !in fields } || fields.any { (key, index) -> !type.isElementOptional(index) && key !in value }) {
                    throw ProviderWireException()
                }
                value.forEach { (key, member) ->
                    validateKinds(member, type.getElementDescriptor(fields.getValue(key)), module)
                }
            }
            SerialKind.CONTEXTUAL -> {
                // Type-mapped dates and referenced enums retain their own serializers; field guards run before them.
                if (value == JsonNull) throw ProviderWireException()
            }
            else -> throw ProviderWireException()
        }
    }
}
