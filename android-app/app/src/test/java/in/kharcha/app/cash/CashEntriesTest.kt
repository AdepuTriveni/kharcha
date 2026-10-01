package `in`.kharcha.app.cash

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.work.testing.WorkManagerTestInitHelper
import `in`.kharcha.app.data.KharchaDatabase
import `in`.kharcha.app.data.OutboxStatus
import `in`.kharcha.app.data.SettingsRepository
import `in`.kharcha.app.sync.ApiClient
import `in`.kharcha.app.sync.SyncScheduler
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@Serializable
private data class PresetFixture(val amountPaise: Long, val note: String)

@RunWith(RobolectricTestRunner::class)
class CashEntriesTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private lateinit var db: KharchaDatabase
    private lateinit var server: MockWebServer
    private lateinit var cash: CashEntries
    private val json = Json { ignoreUnknownKeys = true }

    @Before
    fun setUp() = runTest {
        WorkManagerTestInitHelper.initializeTestWorkManager(context)
        db = Room.inMemoryDatabaseBuilder(context, KharchaDatabase::class.java).allowMainThreadQueries().build()
        server = MockWebServer().also { it.start() }
        val settings = SettingsRepository(context)
        settings.saveServer(server.url("/").toString(), "khk_test")
        cash = CashEntries(db.outbox(), settings, ApiClient(OkHttpClient(), json), SyncScheduler(context))
    }

    @After
    fun tearDown() {
        db.close()
        server.shutdown()
    }

    @Test
    fun entryTextMatchesCashParserFormat() {
        assertEquals("150 vada pav", CashEntries.entryText(15_000, " vada pav "))
        assertEquals("150.50 vada pav", CashEntries.entryText(15_050, "vada pav"))
        assertEquals("0.05 tip", CashEntries.entryText(5, "tip"))
        assertEquals("20", CashEntries.entryText(2_000, ""))
    }

    @Test
    fun presetsMatchContractFixture() {
        val text = javaClass.classLoader!!.getResource("contract/cash_presets.json")!!.readText()
        val fixture = Json.decodeFromString<List<PresetFixture>>(text)
        assertEquals(fixture.map { it.amountPaise to it.note }, CashPresets.DEFAULT.map { it.amountPaise to it.note })
        assertEquals("₹20 chai", CashPresets.DEFAULT[1].label)
    }

    @Test
    fun quickAddAndWidgetTapGoToOutboxRedacted() = runTest {
        val id = cash.quickAdd(15_000, "vada pav, call 9876543210")
        val event = db.outbox().get(id)!!
        assertEquals("MANUAL_TEXT", event.type)
        assertEquals("150 vada pav, call [phone]", event.text)
        assertEquals(OutboxStatus.PENDING, event.status)

        val tap = db.outbox().get(cash.widgetTap(CashPresets.DEFAULT[0]))!!
        assertEquals("WIDGET_TAP", tap.type)
        assertEquals("10 chai", tap.text)
    }

    @Test
    fun undoBeforeUploadDeletesLocally() = runTest {
        val id = cash.quickAdd(2_000, "chai")
        assertEquals(UndoResult.Removed, cash.undo(id))
        assertNull(db.outbox().get(id))
        assertEquals(0, server.requestCount)
    }

    @Test
    fun undoAfterUploadCallsServerAndRetriesWhileNotFound() = runTest {
        val id = cash.quickAdd(2_000, "chai")
        db.outbox().setStatus(id, OutboxStatus.SENT, null)
        server.enqueue(MockResponse().setResponseCode(404))
        server.enqueue(MockResponse().setResponseCode(204))

        assertEquals(UndoResult.Removed, cash.undo(id, waitMs = 1))

        val first = server.takeRequest()
        assertEquals("DELETE", first.method)
        assertEquals("/v1/cash/$id", first.path)
        assertEquals("Bearer khk_test", first.getHeader("Authorization"))
        assertEquals(2, server.requestCount)
    }

    @Test
    fun undoGivesUpAfterRetries() = runTest {
        val id = cash.quickAdd(2_000, "chai")
        db.outbox().setStatus(id, OutboxStatus.SENT, null)
        repeat(2) { server.enqueue(MockResponse().setResponseCode(404)) }
        assertEquals(UndoResult.NotYetOnServer, cash.undo(id, retries = 2, waitMs = 1))
    }
}
