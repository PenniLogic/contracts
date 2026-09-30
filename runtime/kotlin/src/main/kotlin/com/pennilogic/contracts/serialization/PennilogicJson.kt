// PenniLogic hand-written seam shipped with every generated Kotlin client (ADR-015 §2).
package com.pennilogic.contracts.serialization

import com.pennilogic.contracts.time.PennilogicSerializers
import kotlinx.serialization.json.Json

/**
 * The one `Json` configuration of the generated client. `ApiClient` installs it as the Ktor
 * `ContentNegotiation` converter (`json(PennilogicJson.json)`; see the template override under
 * `generator/templates/kotlin`), so every request and response body crosses the ADR-015 seams:
 * `Money` through `MoneySerializer` and `@Contextual` instants and dates through
 * `PennilogicSerializers.module`. Consumers that build their own `Json` use this instance or copy
 * its module; a `Json` without the module fails closed on the first `@Contextual` member.
 *
 * `ignoreUnknownKeys` is on because contract evolution is additive: a newer server may send an
 * optional member an older client does not know. `Money` still refuses an extra member because
 * `MoneySerializer` inspects the JSON object itself (ADR-015 §1.1).
 */
object PennilogicJson {
    val json: Json = Json {
        serializersModule = PennilogicSerializers.module
        ignoreUnknownKeys = true
        isLenient = false
        coerceInputValues = false
    }
}