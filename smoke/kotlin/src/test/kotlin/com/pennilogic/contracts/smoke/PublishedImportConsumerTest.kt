package com.pennilogic.contracts.smoke

import com.pennilogic.contracts.models.ImportRowError
import com.pennilogic.contracts.models.ProblemCode
import com.pennilogic.contracts.models.ServiceProblemDetail
import com.pennilogic.contracts.serialization.PennilogicJson
import com.pennilogic.contracts.serialization.ProviderWireException
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertSame

class PublishedImportConsumerTest {
    private fun problem(): ServiceProblemDetail = PennilogicJson.json.decodeFromString(
        """{"type":"urn:pennilogic:problem:validation_rejected","title":"Validation rejected","status":422,"detail":"Review the indicated field.","code":"validation_rejected","field":"amount","reason":"scale_mismatch","correlation_id":"cor_00000000-0000-4000-8000-000000000027"}"""
    )

    @Test fun publishedPositionalConstructorCopyAndComponents() {
        val problem = problem()
        val original = ImportRowError(problem, 1)
        assertSame(problem, original.component1())
        assertEquals(1, original.component2())
        assertEquals(null, original.component3())
        val copied = original.copy(problem, 2, 3)
        val (first, second, third) = copied
        assertSame(problem, first)
        assertEquals(2, second)
        assertEquals(3, third)
        val wire = PennilogicJson.json.encodeToString(copied)
        assertEquals(copied, PennilogicJson.json.decodeFromString<ImportRowError>(wire))
        assertEquals(ProblemCode.VALIDATION_REJECTED, copied.problem.code)
    }

    @Test fun positionalCopyRetainsStrictValidation() {
        val problem = problem()
        val original = ImportRowError(problem, 1, 2)
        assertFailsWith<ProviderWireException> { original.copy(problem, 0, 2) }
        assertFailsWith<ProviderWireException> { original.copy(problem, 1, 257) }
        assertFailsWith<ProviderWireException> {
            PennilogicJson.json.decodeFromString<ImportRowError>(
                PennilogicJson.json.encodeToString(original).dropLast(1) + ""","unexpected":"synthetic"}"""
            )
        }
    }
}
