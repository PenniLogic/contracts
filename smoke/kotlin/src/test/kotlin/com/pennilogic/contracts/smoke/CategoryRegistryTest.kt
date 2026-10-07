package com.pennilogic.contracts.smoke

import com.pennilogic.contracts.categories.CategoryRegistry
import com.pennilogic.contracts.models.CategorySystemKey
import com.pennilogic.contracts.models.CategoryIcon
import com.pennilogic.contracts.models.CategoryColour
import com.pennilogic.contracts.serialization.PennilogicJson
import kotlin.test.*
import kotlinx.serialization.json.*

class CategoryRegistryTest {
    @Test fun everyKeyParentNaturePresentationAndExactLocaleFallbackMatchesAcceptedSource() {
        val json = PennilogicJson.json
        val seed = json.parseToJsonElement(Fixtures.root.resolve("spec/category-seed.v1.json").readText()).jsonObject
        assertEquals(59, CategoryRegistry.entries.size)
        for (element in seed.getValue("categories").jsonArray) {
            val row = element.jsonObject
            val key = CategorySystemKey.entries.single { it.value == row.getValue("key").jsonPrimitive.content }
            val value = CategoryRegistry.entries.getValue(key)
            assertEquals(if (row["parent_key"] == JsonNull) null else row.getValue("parent_key").jsonPrimitive.content, value.parentKey?.value)
            assertEquals(row.getValue("nature").jsonPrimitive.content, value.nature.value)
            assertEquals(row.getValue("icon").jsonPrimitive.content, value.icon.value)
            assertEquals(row.getValue("colour").jsonPrimitive.content, value.colour.value)
            assertEquals(row.getValue("introduced_in").jsonPrimitive.int, value.introducedIn)
            assertEquals(row.getValue("retired_in").jsonPrimitive.intOrNull, value.retiredIn)
            assertEquals(row.getValue("sort_order").jsonPrimitive.int, value.sortOrder)
            assertEquals(row.getValue("labels").jsonObject.getValue("en-IN").jsonPrimitive.content, CategoryRegistry.label(key, "hi-IN"))
            assertFailsWith<UnsupportedOperationException> { (value.labels as MutableMap<String, String>)["en-IN"] = "changed" }
        }
        for (element in seed.getValue("icons").jsonArray) {
            val row = element.jsonObject
            val icon = CategoryIcon.entries.single { it.value == row.getValue("id").jsonPrimitive.content }
            assertEquals(row.getValue("code_point").jsonPrimitive.content, CategoryRegistry.iconCodePoints.getValue(icon))
        }
        for (element in seed.getValue("colours").jsonArray) {
            val row = element.jsonObject
            val colour = CategoryColour.entries.single { it.value == row.getValue("id").jsonPrimitive.content }
            assertEquals(row.getValue("rgb").jsonArray.map { it.jsonPrimitive.int }, CategoryRegistry.colourRgb.getValue(colour))
            assertFailsWith<UnsupportedOperationException> { (CategoryRegistry.colourRgb.getValue(colour) as MutableList<Int>)[0] = 999 }
        }
        assertFails { json.decodeFromString<CategorySystemKey>("\"SYNTHETIC_UNKNOWN\"") }
        assertFails { json.decodeFromString<CategoryIcon>("\"SYNTHETIC_UNKNOWN\"") }
        assertFails { json.decodeFromString<CategoryColour>("\"SYNTHETIC_UNKNOWN\"") }
    }
}
