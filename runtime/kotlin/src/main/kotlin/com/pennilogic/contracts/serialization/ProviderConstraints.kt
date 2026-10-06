package com.pennilogic.contracts.serialization

import com.pennilogic.contracts.time.InstantCodec
import com.pennilogic.contracts.time.InstantWireException
import com.pennilogic.contracts.time.LocalDateSerializer
import java.net.URI
import java.net.URISyntaxException
import java.util.concurrent.ConcurrentHashMap
import java.util.regex.PatternSyntaxException
import kotlinx.serialization.json.*

internal object ProviderConstraints {
    private val patterns = ConcurrentHashMap<String, Regex>()

    fun patternMatches(source: String, value: String): Boolean {
        val pattern = patterns.computeIfAbsent(source) {
            val translated = StringBuilder()
            var inClass = false
            var escaped = false
            source.forEach { character ->
                when {
                    escaped -> {
                        if (character == 's' && inClass) {
                            translated.setLength(translated.length - 1)
                            translated.append("\\u0009-\\u000d\\u0020\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff")
                        } else translated.append(character)
                        escaped = false
                    }
                    character == '\\' -> { translated.append(character); escaped = true }
                    character == '[' -> { translated.append(character); inClass = true }
                    character == ']' -> { translated.append(character); inClass = false }
                    // ECMAScript dot excludes these four terminators, but includes NEL.
                    character == '.' && !inClass -> translated.append("[^\\n\\r\\u2028\\u2029]")
                    else -> translated.append(character)
                }
            }
            try { Regex(translated.toString()) }
            catch (_: PatternSyntaxException) { throw ProviderWireException() }
        }
        return pattern.matches(value)
    }

    fun validate(name: String, value: JsonElement) {
        val schema = ProviderConstraintData.schemas[name] ?: throw ProviderWireException()
        if (!matches(value, schema)) throw ProviderWireException()
    }

    private fun format(value: JsonPrimitive, name: String): Boolean {
        return when (name) {
            "date-time" -> try { InstantCodec.parse(value.content); true } catch (_: InstantWireException) { false }
            "date" -> try {
                PennilogicJson.json.decodeFromJsonElement(LocalDateSerializer, value); true
            } catch (_: InstantWireException) { false }
            "uri-reference" -> {
                if (value.content.any { it.code !in 33..126 } || Regex("%(?![0-9a-fA-F]{2})").containsMatchIn(value.content)) false
                else try { URI(value.content); true } catch (_: URISyntaxException) { false }
            }
            "uuid" -> UuidSerializer.accepts(value.content)
            else -> throw ProviderWireException()
        }
    }

    private fun matches(value: JsonElement, schema: JsonObject): Boolean {
        schema["ref"]?.jsonPrimitive?.content?.let { name ->
            val target = ProviderConstraintData.schemas[name] ?: throw ProviderWireException()
            if (!matches(value, target)) return false
        }
        if (value == JsonNull) return false
        val kind = schema["type"]?.jsonPrimitive?.content
        val primitive = value as? JsonPrimitive
        val integer = primitive?.takeUnless { it.isString }?.longOrNull
        val rightKind = when (kind) {
            null -> true
            "object" -> value is JsonObject
            "array" -> value is JsonArray
            "string" -> primitive?.isString == true
            "integer" -> integer != null
            "boolean" -> primitive != null && !primitive.isString && primitive.booleanOrNull != null
            else -> throw ProviderWireException()
        }
        if (!rightKind) return false
        if ("const" in schema && value != schema["const"]) return false
        if ("enum" in schema && value !in schema.getValue("enum").jsonArray) return false
        if (integer != null) {
            val declaredFormat = schema["format"]?.jsonPrimitive?.content
            if (declaredFormat == "int32" && integer !in Int.MIN_VALUE.toLong()..Int.MAX_VALUE.toLong()) return false
            if (schema["minimum"]?.jsonPrimitive?.long?.let { integer < it } == true ||
                schema["maximum"]?.jsonPrimitive?.long?.let { integer > it } == true ||
                schema["exclusiveMinimum"]?.jsonPrimitive?.long?.let { integer <= it } == true ||
                schema["exclusiveMaximum"]?.jsonPrimitive?.long?.let { integer >= it } == true) return false
        }
        if (primitive?.isString == true) {
            val length = primitive.content.codePointCount(0, primitive.content.length)
            if (schema["minLength"]?.jsonPrimitive?.int?.let { length < it } == true ||
                schema["maxLength"]?.jsonPrimitive?.int?.let { length > it } == true) return false
            schema["pattern"]?.jsonPrimitive?.content?.let { pattern ->
                if (!patternMatches(pattern, primitive.content)) return false
            }
            schema["format"]?.jsonPrimitive?.content?.let { if (!format(primitive, it)) return false }
        }
        if (value is JsonArray) {
            if (schema["minItems"]?.jsonPrimitive?.int?.let { value.size < it } == true ||
                schema["maxItems"]?.jsonPrimitive?.int?.let { value.size > it } == true) return false
            if (schema["uniqueItems"]?.jsonPrimitive?.booleanOrNull == true && value.distinct().size != value.size) return false
            schema["items"]?.jsonObject?.let { item ->
                if (value.any { !matches(it, item) }) return false
            }
        }
        if (value is JsonObject) {
            val properties = schema["properties"]?.jsonObject ?: JsonObject(emptyMap())
            if (schema["required"]?.jsonArray?.any { it.jsonPrimitive.content !in value } == true) return false
            if (schema["additionalProperties"]?.jsonPrimitive?.booleanOrNull == false &&
                value.keys.any { it !in properties }) return false
            if (properties.any { (key, declaration) -> value[key]?.let { !matches(it, declaration.jsonObject) } == true }) return false
        }
        if (schema["allOf"]?.jsonArray?.any { !matches(value, it.jsonObject) } == true) return false
        if (schema["anyOf"]?.jsonArray?.none { matches(value, it.jsonObject) } == true) return false
        if (schema["oneOf"]?.jsonArray?.count { matches(value, it.jsonObject) }?.let { it != 1 } == true) return false
        if (schema["not"]?.jsonObject?.let { matches(value, it) } == true) return false
        schema["if"]?.jsonObject?.let { condition ->
            val branch = if (matches(value, condition)) "then" else "else"
            if (schema[branch]?.jsonObject?.let { !matches(value, it) } == true) return false
        }
        return true
    }
}
