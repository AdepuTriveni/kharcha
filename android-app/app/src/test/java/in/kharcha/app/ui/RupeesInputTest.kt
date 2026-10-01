package `in`.kharcha.app.ui

import `in`.kharcha.app.ui.cash.parseRupeesToPaise
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class RupeesInputTest {
    @Test
    fun parsesTypedAmounts() {
        assertEquals(15_000L, parseRupeesToPaise("150"))
        assertEquals(15_050L, parseRupeesToPaise("150.5"))
        assertEquals(120_000L, parseRupeesToPaise("₹1,200"))
        assertNull(parseRupeesToPaise(""))
        assertNull(parseRupeesToPaise("0"))
        assertNull(parseRupeesToPaise("-5"))
        assertNull(parseRupeesToPaise("1.234"))
        assertNull(parseRupeesToPaise("abc"))
    }
}
