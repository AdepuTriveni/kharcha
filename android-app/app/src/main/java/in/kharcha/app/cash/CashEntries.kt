package `in`.kharcha.app.cash

import `in`.kharcha.app.data.OutboxDao
import `in`.kharcha.app.data.OutboxEvent
import `in`.kharcha.app.data.OutboxStatus
import `in`.kharcha.app.data.SettingsRepository
import `in`.kharcha.app.redact.Redactor
import `in`.kharcha.app.sync.ApiClient
import `in`.kharcha.app.sync.ApiResult
import `in`.kharcha.app.sync.SyncScheduler
import `in`.kharcha.app.util.Uuid7
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.delay

/** One-tap widget amounts. The backend checks that each text parses (contract fixture). */
data class CashPreset(val amountPaise: Long, val note: String) {
    val label: String get() = "₹${amountPaise / 100} $note"
}

object CashPresets {
    val DEFAULT = listOf(
        CashPreset(1_000, "chai"),
        CashPreset(2_000, "chai"),
        CashPreset(5_000, "snacks"),
        CashPreset(10_000, "auto"),
        CashPreset(20_000, "lunch"),
    )
}

sealed interface UndoResult {
    data object Removed : UndoResult
    data object NotYetOnServer : UndoResult
    data class Failed(val reason: String) : UndoResult
}

/**
 * Cash quick-add, widget taps and Undo (PROJECT_SPEC §13). Entries go through the outbox as
 * MANUAL_TEXT / WIDGET_TAP raw events, so they work offline and the server's cash parser
 * handles them exactly like Telegram messages ("150 vada pav").
 */
@Singleton
class CashEntries @Inject constructor(
    private val outbox: OutboxDao,
    private val settings: SettingsRepository,
    private val api: ApiClient,
    private val scheduler: SyncScheduler,
) {
    /** Returns the event id, which is also the Undo handle. */
    suspend fun quickAdd(amountPaise: Long, note: String): String =
        store(TYPE_MANUAL_TEXT, entryText(amountPaise, note))

    suspend fun widgetTap(preset: CashPreset): String =
        store(TYPE_WIDGET_TAP, entryText(preset.amountPaise, preset.note))

    suspend fun undo(eventId: String, retries: Int = 3, waitMs: Long = 2_000): UndoResult {
        if (outbox.deleteIfPending(eventId) == 1) return UndoResult.Removed
        val config = settings.current()
        repeat(retries) { attempt ->
            when (val result = api.deleteCash(config.serverUrl, config.apiKey, eventId)) {
                is ApiResult.Ok -> return UndoResult.Removed
                ApiResult.NotFound -> if (attempt < retries - 1) delay(waitMs) // still in Kafka
                ApiResult.Unauthorized -> return UndoResult.Failed("check API key")
                is ApiResult.Failed -> return UndoResult.Failed(result.reason)
            }
        }
        return UndoResult.NotYetOnServer
    }

    private suspend fun store(type: String, text: String): String {
        val config = settings.current()
        val now = System.currentTimeMillis()
        val eventId = Uuid7.generate(now).toString()
        outbox.insert(
            OutboxEvent(
                eventId = eventId,
                type = type,
                sourceApp = null,
                sender = null,
                title = null,
                // Typed notes can contain phone numbers too; redact like any other text.
                text = Redactor(config.vpaKey).redact(text),
                postedAtMs = now,
                capturedAtMs = now,
                dedupeKey = "cash:$eventId",
                status = OutboxStatus.PENDING,
            ),
        )
        scheduler.requestUpload()
        return eventId
    }

    companion object {
        const val TYPE_MANUAL_TEXT = "MANUAL_TEXT"
        const val TYPE_WIDGET_TAP = "WIDGET_TAP"

        /** `"150 vada pav"`, `"150.50 vada pav"`: amount first, as the cash parser expects. */
        fun entryText(amountPaise: Long, note: String): String {
            require(amountPaise > 0) { "amount must be positive" }
            val rupees = amountPaise / 100
            val paise = amountPaise % 100
            val amount = if (paise == 0L) "$rupees" else "$rupees.${"%02d".format(paise)}"
            return "$amount ${note.trim()}".trim()
        }
    }
}
