package com.pennilogic.contracts.smoke

import com.pennilogic.contracts.money.CurrencyRegistry
import com.pennilogic.contracts.money.Money
import com.pennilogic.contracts.money.MoneyReason
import com.pennilogic.contracts.money.MoneyWireException
import java.util.HashMap
import java.util.HashSet
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class MoneyCollectionTest {
    private val currencies = CurrencyRegistry.entries.keys
    private val capacity = 128

    private fun corpus(additionalMinor: Long): List<Money> =
        (1L..16L).map { Money.ofMinorUnits((it shl 32) or it, "INR") } +
            Money.ofMinorUnits(additionalMinor, "JPY")

    private fun equalKey(value: Money): Money = Money.ofMinorUnits(value.minorUnits, value.currency)

    private fun assertSetKeys(values: List<Money>) {
        val set = HashSet<Money>(capacity)
        for (value in values) assertTrue(set.add(value))
        assertEquals(values.size, set.size)
        for (value in values) {
            val key = equalKey(value)
            assertEquals(value, key)
            assertEquals(value.hashCode(), key.hashCode())
            assertTrue(set.contains(key))
            assertFalse(set.add(key))
        }
        assertEquals(values.size, set.size)
        for (value in values) {
            assertTrue(set.remove(equalKey(value)))
            assertFalse(set.contains(equalKey(value)))
        }
        assertTrue(set.isEmpty())
    }

    private fun assertMapKeys(values: List<Money>) {
        val map = HashMap<Money, Int>(capacity)
        for ((index, value) in values.withIndex()) assertNull(map.put(value, index))
        assertEquals(values.size, map.size)
        for ((index, value) in values.withIndex()) {
            assertEquals(index, map[equalKey(value)])
            assertEquals(index, map.put(equalKey(value), index + values.size))
            assertEquals(index + values.size, map[equalKey(value)])
        }
        assertEquals(values.size, map.size)
        for ((index, value) in values.withIndex()) {
            assertEquals(index + values.size, map.remove(equalKey(value)))
            assertFalse(map.containsKey(equalKey(value)))
        }
        assertTrue(map.isEmpty())
    }

    private fun assertCollectionKeys(values: List<Money>) {
        for (order in listOf(values, values.reversed())) {
            assertSetKeys(order)
            assertMapKeys(order)
        }
    }

    @Test
    fun `reported valid corpus survives hash set insertion and equal key lookup`() {
        val values = corpus(3_463_683_270L)
        assertSetKeys(values)
        assertSetKeys(values.reversed())
    }

    @Test
    fun `reported valid corpus survives hash map insertion and equal key lookup`() {
        val values = corpus(3_463_683_270L)
        assertMapKeys(values)
        assertMapKeys(values.reversed())
    }

    @Test
    fun `second original corpus stays usable in either insertion order`() {
        assertCollectionKeys(corpus(831_284_026L))
    }

    @Test
    fun `same currency hash collision controls preserve value key behavior`() {
        for (currency in currencies) {
            val values = (1L..16L).map { Money.ofMinorUnits((it shl 32) or it, currency) }
            assertEquals(1, values.map { it.hashCode() }.distinct().size, "control must contain actual hash collisions")
            assertCollectionKeys(values)
        }
    }

    private fun bucket(value: Money): Int {
        val hash = value.hashCode()
        // JDK HashMap spreads the hash before selecting a bucket.
        return (hash xor (hash ushr 16)) and (capacity - 1)
    }

    @Test
    fun `different currencies sharing a hash table bucket preserve value key behavior`() {
        val candidates = currencies.flatMap { currency ->
            (0L..4095L).map { Money.ofMinorUnits(it, currency) }
        }
        val sharedBucket = candidates.groupBy(::bucket).values.first { values ->
            currencies.all { currency -> values.count { it.currency == currency } >= 16 }
        }
        val values = currencies.flatMap { currency ->
            val seed = sharedBucket.first { it.currency == currency }.minorUnits
            // Different Longs with the seed's Long hash force full-hash collisions inside each currency.
            (1L..16L).map { Money.ofMinorUnits((it shl 32) or (it xor seed), currency) }
        }
        for (currency in currencies) {
            assertEquals(1, values.filter { it.currency == currency }.map { it.hashCode() }.distinct().size)
        }
        assertEquals(1, values.map(::bucket).distinct().size, "control must occupy one bucket")
        assertTrue(values.map { it.hashCode() }.distinct().size > 1, "bucket collisions need not be equal hashes")
        assertTrue(values.size <= capacity * 3 / 4, "control must not resize before treeification")
        assertCollectionKeys(values)
    }

    @Test
    fun `boundary values are interchangeable keys across every accepted currency`() {
        val minors = listOf(0L, -1L, 1L, -Long.MAX_VALUE, Long.MAX_VALUE, 9_007_199_254_740_991L, 9_007_199_254_740_993L)
        val values = currencies.flatMap { currency -> minors.map { Money.ofMinorUnits(it, currency) } }
        assertCollectionKeys(values)
    }

    @Test
    fun `public Comparable and arithmetic still reject every mixed currency pair`() {
        for (currency in currencies) {
            val negative = Money.ofMinorUnits(-1, currency)
            val positive = Money.ofMinorUnits(1, currency)
            val comparable: Comparable<Money> = negative
            assertTrue(comparable.compareTo(positive) < 0)
            assertTrue(positive.compareTo(negative) > 0)
            assertEquals(0, comparable.compareTo(equalKey(negative)))
            assertEquals(Money.ofMinorUnits(0, currency), negative + positive)
            assertEquals(Money.ofMinorUnits(-2, currency), negative - positive)
            assertEquals(positive, -negative)
            for (otherCurrency in currencies.filter { it != currency }) {
                val other = Money.ofMinorUnits(-1, otherCurrency)
                assertFalse(negative == other)
                assertFalse(other == negative)
                assertFailsWith<IllegalArgumentException> { comparable.compareTo(other) }
                assertFailsWith<IllegalArgumentException> { negative + other }
                assertFailsWith<IllegalArgumentException> { negative - other }
            }
        }
    }

    @Test
    fun `symmetric bounds and checked arithmetic remain unchanged for every currency`() {
        for (currency in currencies) {
            val maximum = Money.ofMinorUnits(Long.MAX_VALUE, currency)
            val minimum = Money.ofMinorUnits(-Long.MAX_VALUE, currency)
            val one = Money.ofMinorUnits(1, currency)
            assertEquals(maximum, -minimum)
            assertEquals(minimum, -maximum)
            assertEquals(MoneyReason.OUT_OF_RANGE, assertFailsWith<MoneyWireException> {
                Money.ofMinorUnits(Long.MIN_VALUE, currency)
            }.reason)
            assertFailsWith<ArithmeticException> { maximum + one }
            assertEquals(MoneyReason.OUT_OF_RANGE, assertFailsWith<MoneyWireException> { minimum - one }.reason)
            assertFailsWith<ArithmeticException> { minimum - Money.ofMinorUnits(2, currency) }
        }
    }
}
