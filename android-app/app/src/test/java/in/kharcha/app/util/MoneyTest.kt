package `in`.kharcha.app.util

import org.junit.Assert.assertEquals
import org.junit.Test

class MoneyTest {
    @Test
    fun indianGrouping() {
        assertEquals("₹0.05", Money.format(5))
        assertEquals("₹349.00", Money.format(34_900))
        assertEquals("₹1,249.50", Money.format(124_950))
        assertEquals("₹1,00,000.00", Money.format(10_000_000))
        assertEquals("₹1,23,456.78", Money.format(12_345_678))
        assertEquals("₹12,34,56,789.00", Money.format(12_345_678_900))
        assertEquals("-₹300.00", Money.format(-30_000))
    }
}
