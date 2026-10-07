package com.pennilogic.contracts.smoke

import com.pennilogic.contracts.apis.ProbeApi
import com.pennilogic.contracts.models.NullableProbe
import com.pennilogic.contracts.models.NullableChoice
import com.pennilogic.contracts.serialization.PennilogicJson
import com.pennilogic.contracts.serialization.ProviderPresence
import com.pennilogic.contracts.success.ProbeNullableSuccessStatus200
import com.pennilogic.contracts.success.ProbeNullableSuccessStatus204
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import kotlin.test.*
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*

class NullableStatusProbeTest {
    private val json = PennilogicJson.json
    private val wire = json.parseToJsonElement("""{"only_null":null,"number":null,"label":null,"values":[null,1],"nested":[null,[null,"ok"]],"choice":null,"choice_alias":null,"choices":[null,"alpha"],"choices_nested":[null,[null,"beta"]],"nonnull_choice":"alpha"}""")

    @Test fun genericNullableFieldsKeepRequiredNullPresenceAliasesAndNestedArrays() {
        val value = json.decodeFromJsonElement<NullableProbe>(wire)
        val shared: NullableChoice? = value.choice
        val sharedItems: List<NullableChoice?> = value.choices
        val nestedSharedItems: List<Set<NullableChoice?>?> = value.choicesNested
        assertNull(shared)
        assertEquals(listOf(null, NullableChoice.entries.single { it.value == "alpha" }), sharedItems)
        assertEquals(listOf(null, setOf(null, NullableChoice.entries.single { it.value == "beta" })), nestedSharedItems)
        assertEquals(wire, json.encodeToJsonElement(value))
        assertEquals(ProviderPresence.Absent, value.optionalFlag)
        assertEquals(ProviderPresence.Absent, value.optionalChoice)
        val explicit = value.copy(optionalFlag = ProviderPresence.Present(null))
        assertEquals(JsonNull, json.encodeToJsonElement(explicit).jsonObject["optional_flag"])
        val explicitChoice = value.copy(optionalChoice = ProviderPresence.Present(null))
        assertEquals(JsonNull, json.encodeToJsonElement(explicitChoice).jsonObject["optional_choice"])
        val selected = value.copy(choice = NullableChoice.entries.single { it.value == "beta" })
        assertEquals(JsonPrimitive("beta"), json.encodeToJsonElement(selected).jsonObject["choice"])
        for (field in wire.jsonObject.keys) {
            assertFails { json.decodeFromJsonElement<NullableProbe>(JsonObject(wire.jsonObject - field)) }
        }
        for ((field, invalid) in mapOf(
            "only_null" to JsonPrimitive("wrong"), "number" to JsonPrimitive(true),
            "label" to JsonPrimitive(""), "values" to JsonArray(listOf(JsonPrimitive("1"))),
            "nested" to JsonArray(listOf(JsonArray(listOf(JsonPrimitive(1))))),
            "optional_flag" to JsonPrimitive("true"), "private" to JsonPrimitive("CANARY"),
            "choice" to JsonPrimitive("unknown"), "choice_alias" to JsonPrimitive(1),
            "choices" to JsonArray(listOf(JsonPrimitive("unknown"))), "optional_choice" to JsonPrimitive("unknown"),
            "choices_nested" to JsonArray(listOf(JsonArray(listOf(JsonNull, JsonNull)))),
            "nonnull_choice" to JsonNull,
        )) {
            val error = assertFails { json.decodeFromJsonElement<NullableProbe>(JsonObject(wire.jsonObject + (field to invalid))) }
            assertFalse(error.message.orEmpty().contains("CANARY"))
        }
    }

    @Test fun actualEmptyAndBodyStatusCasesRefuseWrongMediaStatusOrNonempty204() = runBlocking<Unit> {
        val responses = mutableListOf(
            Triple(200, wire.toString(), "application/json"), Triple(204, "", "application/json"),
            Triple(204, "null", "application/json"), Triple(200, "", "application/json"),
            Triple(200, wire.toString(), "text/plain"), Triple(206, wire.toString(), "application/json"),
        )
        val api = ProbeApi(httpClientEngine = MockEngine {
            val (status, body, media) = responses.removeAt(0)
            respond(body, HttpStatusCode.fromValue(status), headersOf("Content-Type", media))
        })
        val body = api.probeNullable().body()
        assertIs<ProbeNullableSuccessStatus200>(body)
        assertEquals(wire, json.encodeToJsonElement(body.body))
        assertEquals(ProbeNullableSuccessStatus204, api.probeNullable().body())
        repeat(4) { assertFails { api.probeNullable().body() } }
    }
}
