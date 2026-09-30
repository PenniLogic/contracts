package com.pennilogic.contracts.smoke

import java.io.File
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/** Fixture access shared by the Kotlin smoke tests; the repository root is passed by build.gradle.kts. */
object Fixtures {
    val root: File = File(System.getProperty("pennilogic.contracts.root") ?: error("pennilogic.contracts.root system property is not set"))
    private val json = Json { ignoreUnknownKeys = false }

    fun load(name: String): JsonObject = json.parseToJsonElement(root.resolve("spec/fixtures/$name").readText(Charsets.UTF_8)).jsonObject

    fun vectors(fixture: JsonObject, key: String): List<JsonObject> = fixture.getValue(key).jsonArray.map { it.jsonObject }

    fun string(vector: JsonObject, key: String): String = vector.getValue(key).jsonPrimitive.content
}
