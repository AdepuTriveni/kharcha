package `in`.kharcha.app.redact

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

@Serializable
private data class Case(val name: String, val input: String, val expected: String)

class RedactorTest {
    private val key = ByteArray(32) { it.toByte() }
    private val redactor = Redactor(key)

    @Test
    fun corpus() {
        val text = javaClass.classLoader!!.getResource("redaction_corpus.json")!!.readText()
        val cases = Json.decodeFromString<List<Case>>(text)
        assertTrue(cases.isNotEmpty())
        for (case in cases) {
            assertEquals(case.name, case.expected, redactor.redact(case.input))
        }
    }

    @Test
    fun personVpaIsHashedButHandleKept() {
        val out = redactor.redact("Rs 150 sent to ravi.kumar@okaxis")
        assertFalse(out.contains("ravi"))
        assertTrue(Regex("Rs 150 sent to p[0-9a-f]{8}@okaxis").matches(out))
    }

    @Test
    fun personVpaHashIsStablePerKey() {
        val a = redactor.redact("to ravi@okaxis")
        assertEquals(a, redactor.redact("to RAVI@okaxis"))
        assertNotEquals(a, Redactor(ByteArray(32) { 7 }).redact("to ravi@okaxis"))
    }

    @Test
    fun phoneVpaIsPerson() {
        val out = redactor.redact("Paid Rs 20 to 9876543210@ybl")
        assertFalse(out.contains("9876543210"))
        assertTrue(out.endsWith("@ybl"))
    }

    @Test
    fun merchantVpaOnConsumerHandleIsKept() {
        val text = "Paid Rs 99 to paytmqr281005050101@paytm and swiggy.stores@icici"
        assertEquals(text, redactor.redact(text))
    }

    @Test
    fun isPersonVpa() {
        assertTrue(Redactor.isPersonVpa("ravi", "okaxis"))
        assertFalse(Redactor.isPersonVpa("zomato", "hdfcbank"))
        assertFalse(Redactor.isPersonVpa("bharatpe.9000123456", "fbpe"))
    }
}
