package `in`.kharcha.app.sync

import `in`.kharcha.app.data.OutboxEvent
import `in`.kharcha.app.data.OutboxStatus
import java.time.Instant
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import org.junit.Assert.assertEquals
import org.junit.Test

/** The same fixture is validated by backend/tests/contract/test_contract_android.py. */
class UploadContractTest {
    private val json = Json { explicitNulls = true; encodeDefaults = true }

    private fun event(
        id: String, type: String, iso: String, app: String?, sender: String?, title: String?, text: String,
        device: String? = null,
    ) =
        OutboxEvent(
            eventId = id, type = type, sourceApp = app, sender = sender, title = title, text = text,
            postedAtMs = Instant.parse(iso).toEpochMilli(), capturedAtMs = 0, dedupeKey = id,
            status = OutboxStatus.PENDING, deviceParseJson = device,
        )

    @Test
    fun serializedBatchMatchesFixture() {
        val batch = UploadBatchDto(
            listOf(
                event(
                    "0192a0b4-2d40-7abc-8def-0123456789ab", "RAW_NOTIFICATION", "2026-10-03T13:45:12Z",
                    "com.phonepe.app", null, "Paid to Zomato",
                    "Paid Rs.349.00 to zomato@hdfcbank from A/c XX1234. UPI Ref 412345678901",
                ),
                event(
                    "0192a0b4-2d41-7abc-8def-0123456789ac", "RAW_SMS", "2026-10-03T13:46:00.500Z",
                    null, "AX-HDFCBK", null, "Rs 500 debited from A/c XX1234. Ref No 412345678902",
                    device = "-",
                ),
                event(
                    "0192a0b4-2d42-7abc-8def-0123456789ad", "RAW_NOTIFICATION", "2026-10-03T13:47:00Z",
                    "com.phonepe.app", null, "Paid", "Paid Rs.89 to BLINKIT via Paytm UPI. Ref: 512300011122",
                    device = """{"modelVersion":"kharcha-parser-v1-q4_k_m","result":{"amountPaise":8900,""" +
                        """"direction":"DEBIT","channel":"UPI","status":"SUCCESS","merchantRaw":"BLINKIT",""" +
                        """"referenceId":"512300011122"},"latencyMs":820}""",
                ),
            ).map { it.toDto("d_91ab") },
        )
        val fixture = javaClass.classLoader!!.getResource("contract/upload_batch.json")!!.readText()
        assertEquals(
            json.parseToJsonElement(fixture),
            json.encodeToJsonElement(UploadBatchDto.serializer(), batch) as JsonElement,
        )
    }
}
