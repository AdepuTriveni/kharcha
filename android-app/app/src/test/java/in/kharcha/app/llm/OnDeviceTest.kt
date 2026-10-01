package `in`.kharcha.app.llm

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import `in`.kharcha.app.data.KharchaDatabase
import `in`.kharcha.app.data.OutboxEvent
import `in`.kharcha.app.data.OutboxStatus
import `in`.kharcha.app.data.SettingsRepository
import `in`.kharcha.app.sync.DeviceParseDto
import `in`.kharcha.app.sync.decodeDeviceParse
import `in`.kharcha.app.sync.toDto
import java.io.File
import java.security.MessageDigest
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okio.Buffer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class OnDeviceTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = true; encodeDefaults = true }
    private lateinit var server: MockWebServer
    private lateinit var db: KharchaDatabase

    @Before
    fun setUp() = runTest {
        server = MockWebServer().also { it.start() }
        db = Room.inMemoryDatabaseBuilder(context, KharchaDatabase::class.java).allowMainThreadQueries().build()
        SettingsRepository(context).saveServer(server.url("/").toString(), "khk_test")
        File(context.filesDir, "models").deleteRecursively()
    }

    @After
    fun tearDown() {
        server.shutdown()
        db.close()
    }

    // --- assets stay copies of the server prompt and the ml grammar -------------------------

    @Test
    fun assetsMatchTheirSources() {
        val root = File("").absoluteFile.parentFile.parentFile // android-app/app -> repo
        val assets = File("src/main/assets")
        assertEquals(
            File(root, "backend/prompts/parser/v1.md").readText(),
            File(assets, "parser_prompt.md").readText(),
        )
        assertEquals(
            File(root, "ml/src/kharcha_ml/export/parser.gbnf").readText(),
            File(assets, "parser.gbnf").readText(),
        )
        val prompt = PromptSections.parse(File(assets, "parser_prompt.md").readText())
        assert(prompt.system.startsWith("Extract ONE financial transaction"))
        assert(prompt.userFor("AX-HDFCBK", null, "a >>> b").contains("a > > > b"))
    }

    // --- model output -> wire DTO ------------------------------------------------------------

    @Test
    fun modelOutputBecomesDeviceParse() {
        val raw = """{"isTransaction":true,"amount":"1,249.50","direction":"DEBIT","channel":"CARD",""" +
            """"status":"SUCCESS","merchantRaw":"AMAZON","counterpartyVpa":null,"referenceId":null,""" +
            """"accountHint":"4321","balanceAfter":null,"promisedRefundDays":null}"""
        val parsed = toDeviceParse(raw, "kharcha-parser-v1", 812)!!
        assertEquals(124_950L, parsed.result.amountPaise)
        assertEquals("CARD", parsed.result.channel)
        assertEquals(812L, parsed.latencyMs)
        assertNull(toDeviceParse("""{"isTransaction":false}""", "v1", 1))
        assertNull(toDeviceParse("garbage", "v1", 1))
        assertNull(toDeviceParse("""{"isTransaction":true,"amount":"abc","direction":"DEBIT"}""", "v1", 1))
    }

    // --- tier 2 annotation before upload -----------------------------------------------------

    private class FakeParser(val answer: DeviceParseDto?) : OnDeviceParser {
        val seen = mutableListOf<String>()
        override suspend fun parse(sender: String?, sourceApp: String?, text: String): DeviceParseDto? {
            seen += text
            return answer
        }
    }

    private suspend fun add(id: String, type: String = "RAW_SMS") = db.outbox().insert(
        OutboxEvent(
            eventId = id, type = type, sourceApp = null, sender = "AX-HDFCBK", title = null,
            text = "Rs.89 debited. Ref 512300011122", postedAtMs = 1, capturedAtMs = 1,
            dedupeKey = id, status = OutboxStatus.PENDING,
        ),
    )

    @Test
    fun pendingBankMessagesGetDeviceParseOnce() = runTest {
        add("e1")
        add("e2", type = "MANUAL_TEXT")
        val answer = toDeviceParse(
            """{"isTransaction":true,"amount":"89","direction":"DEBIT","channel":"UPI","status":"SUCCESS"}""",
            "v1",
            500,
        )
        val parser = FakeParser(answer)
        val step = DeviceParseStep(db.outbox(), parser, json)
        assertEquals(1, step.annotatePending())
        assertEquals(0, step.annotatePending()) // never twice
        assertEquals(1, parser.seen.size)
        val stored = db.outbox().get("e1")!!
        assertEquals(8_900L, decodeDeviceParse(stored.deviceParseJson)!!.result.amountPaise)
        assertEquals(8_900L, stored.toDto("d").payload.deviceParse!!.result.amountPaise)
        assertNull(db.outbox().get("e2")!!.deviceParseJson)

        add("e3")
        DeviceParseStep(db.outbox(), FakeParser(null), json).annotatePending()
        assertEquals("-", db.outbox().get("e3")!!.deviceParseJson)
        assertNull(db.outbox().get("e3")!!.toDto("d").payload.deviceParse)
    }

    // --- model download --------------------------------------------------------------------

    private fun manager(ramMb: Long = 8_000) =
        ModelManager(context, OkHttpClient(), SettingsRepository(context), json).also { it.totalRamMb = { ramMb } }

    private fun manifest(sha: String, minRam: Int = 3_000) =
        """{"id":"parser-v1-q4_k_m","version":"v1","url":"${server.url("/files/v1.gguf")}","sha256":"$sha",""" +
            """"sizeBytes":8,"minRamMb":$minRam,"promptVersion":"parser/v1"}"""

    private val modelBytes = "GGUFdata".toByteArray()
    private val modelSha = MessageDigest.getInstance("SHA-256").digest(modelBytes).joinToString("") { "%02x".format(it) }

    @Test
    fun downloadsVerifiesAndSkips() = runTest {
        server.enqueue(MockResponse().setResponseCode(404))
        assertEquals(ModelUpdate.NO_MODEL, manager().refresh())

        server.enqueue(MockResponse().setBody(manifest(modelSha, minRam = 16_000)))
        assertEquals(ModelUpdate.NOT_ENOUGH_RAM, manager(ramMb = 4_000).refresh())

        server.enqueue(MockResponse().setBody(manifest("00".repeat(32))))
        server.enqueue(MockResponse().setBody(Buffer().write(modelBytes)))
        assertEquals(ModelUpdate.BAD_CHECKSUM, manager().refresh())
        assertNull(manager().installed())

        server.enqueue(MockResponse().setBody(manifest(modelSha)))
        server.enqueue(MockResponse().setBody(Buffer().write(modelBytes)))
        assertEquals(ModelUpdate.INSTALLED, manager().refresh())
        assertNotNull(manager().installed())
        val installed = manager().installed()!!
        assertEquals("v1", installed.version)
        assertEquals(modelBytes.toList(), File(installed.path).readBytes().toList())

        server.enqueue(MockResponse().setBody(manifest(modelSha)))
        assertEquals(ModelUpdate.UP_TO_DATE, manager().refresh())
        val manifestRequest = server.takeRequest()
        assertEquals("/v1/models/parser/latest", manifestRequest.path)
        assertEquals("Bearer khk_test", manifestRequest.getHeader("Authorization"))
    }
}
