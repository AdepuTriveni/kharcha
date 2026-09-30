package `in`.kharcha.app.capture

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CaptureRulesTest {
    @Test
    fun bankSenders() {
        listOf("AX-HDFCBK", "VM-SBIINB", "JD-ICICIT-S", "ad-kotakb").forEach {
            assertTrue(it, CaptureRules.isBankSender(it))
        }
        listOf("+919876543210", "Mom", "HDFCBK", "AX-").forEach {
            assertFalse(it, CaptureRules.isBankSender(it))
        }
    }

    @Test
    fun dedupeKeySeparatesParts() {
        assertEquals(CaptureRules.dedupeKey("a", "b"), CaptureRules.dedupeKey("a", "b"))
        assertNotEquals(CaptureRules.dedupeKey("ab", ""), CaptureRules.dedupeKey("a", "b"))
        assertNotEquals(CaptureRules.dedupeKey("a", null, "x"), CaptureRules.dedupeKey("a", "x", null))
    }

    @Test
    fun messagingAppsAreInDefaultAllowlist() {
        assertTrue("com.google.android.apps.messaging" in CaptureRules.DEFAULT_PACKAGES)
        assertTrue("com.phonepe.app" in CaptureRules.DEFAULT_PACKAGES)
    }
}
