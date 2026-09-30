package `in`.kharcha.app.util

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class Uuid7Test {
    @Test
    fun layout() {
        val ms = 1_790_000_000_000L
        val uuid = Uuid7.generate(ms)
        assertEquals(7, uuid.version())
        assertEquals(2, uuid.variant())
        assertEquals(ms, Uuid7.unixMs(uuid))
        assertEquals('7', uuid.toString()[14])
    }

    @Test
    fun timeOrdered() {
        assertTrue(Uuid7.generate(1_000).toString() < Uuid7.generate(2_000).toString())
    }
}
