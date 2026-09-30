package `in`.kharcha.app.sync

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import `in`.kharcha.app.data.KharchaDatabase
import `in`.kharcha.app.data.OutboxEvent
import `in`.kharcha.app.data.OutboxStatus
import `in`.kharcha.app.data.SettingsRepository
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class UploaderTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private lateinit var db: KharchaDatabase
    private lateinit var server: MockWebServer
    private lateinit var settings: SettingsRepository
    private lateinit var uploader: Uploader
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = true; encodeDefaults = true }

    @Before
    fun setUp() = runTest {
        db = Room.inMemoryDatabaseBuilder(context, KharchaDatabase::class.java).allowMainThreadQueries().build()
        server = MockWebServer().also { it.start() }
        settings = SettingsRepository(context)
        settings.saveServer(server.url("/").toString(), "khk_test")
        uploader = Uploader(db.outbox(), settings, ApiClient(OkHttpClient(), json))
    }

    @After
    fun tearDown() {
        db.close()
        server.shutdown()
    }

    private suspend fun add(id: String, dedupe: String = id) = db.outbox().insert(
        OutboxEvent(
            eventId = id, type = "RAW_NOTIFICATION", sourceApp = "com.phonepe.app", sender = null,
            title = "Paid", text = "Paid Rs.10 to x@y", postedAtMs = 1_790_000_000_000,
            capturedAtMs = 0, dedupeKey = dedupe, status = OutboxStatus.PENDING,
        ),
    )

    private suspend fun statuses(): Map<String, String> =
        db.outbox().observeRecent().first().associate { it.eventId to it.status }

    @Test
    fun marksSentOnlyForAcceptedOrDuplicate() = runTest {
        add("e1"); add("e2"); add("e3")
        server.enqueue(
            MockResponse().setBody(
                """{"results":[{"eventId":"e1","status":"ACCEPTED"},{"eventId":"e2","status":"DUPLICATE"},""" +
                    """{"eventId":"e3","status":"REJECTED","reason":"NOT_REDACTED"}]}""",
            ),
        )

        assertEquals(UploadOutcome.DONE, uploader.drain())

        assertEquals(mapOf("e1" to "SENT", "e2" to "SENT", "e3" to "REJECTED"), statuses())
        val request = server.takeRequest()
        assertEquals("/v1/events:batch", request.path)
        assertEquals("Bearer khk_test", request.getHeader("Authorization"))
        val body = json.parseToJsonElement(request.body.readUtf8()).jsonObject
        val first = body["events"]!!.jsonArray[0].jsonObject
        assertEquals("true", first["payload"]!!.jsonObject["redacted"]!!.jsonPrimitive.content)
    }

    @Test
    fun serverErrorKeepsEventsPending() = runTest {
        add("e1")
        server.enqueue(MockResponse().setResponseCode(503))
        assertEquals(UploadOutcome.RETRY, uploader.drain())
        assertEquals(mapOf("e1" to "PENDING"), statuses())
    }

    @Test
    fun unauthorizedNeedsConfig() = runTest {
        add("e1")
        server.enqueue(MockResponse().setResponseCode(401))
        assertEquals(UploadOutcome.NEEDS_CONFIG, uploader.drain())
        assertEquals(mapOf("e1" to "PENDING"), statuses())
    }

    @Test
    fun duplicateDedupeKeyIsIgnored() = runTest {
        assertEquals(true, add("e1", dedupe = "same") != -1L)
        assertEquals(-1L, add("e2", dedupe = "same"))
    }
}
