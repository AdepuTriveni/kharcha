package `in`.kharcha.app.capture

import `in`.kharcha.app.data.OutboxDao
import `in`.kharcha.app.data.OutboxEvent
import `in`.kharcha.app.data.OutboxStatus
import `in`.kharcha.app.data.SettingsRepository
import `in`.kharcha.app.redact.Redactor
import `in`.kharcha.app.sync.SyncScheduler
import `in`.kharcha.app.util.Uuid7
import javax.inject.Inject
import javax.inject.Singleton

enum class RawEventType { RAW_NOTIFICATION, RAW_SMS }

/** A message as seen by the phone, before redaction. Never stored or logged in this form. */
data class CapturedMessage(
    val type: RawEventType,
    val sourceApp: String?,
    val sender: String?,
    val sourceKey: String?,
    val title: String?,
    val text: String?,
    val postedAtMs: Long,
)

/** Filters, redacts, de-dupes and stores captured messages in the outbox. */
@Singleton
class CaptureProcessor @Inject constructor(
    private val outbox: OutboxDao,
    private val settings: SettingsRepository,
    private val scheduler: SyncScheduler,
) {
    /** Returns true when a new outbox event was stored. */
    suspend fun capture(message: CapturedMessage): Boolean {
        if (message.text.isNullOrBlank() && message.title.isNullOrBlank()) return false
        val config = settings.current()
        val fromMessagingApp = message.sourceApp in CaptureRules.MESSAGING_PACKAGES
        val allowed = when (message.type) {
            RawEventType.RAW_NOTIFICATION ->
                message.sourceApp in config.allowlist &&
                    (!fromMessagingApp || message.title?.let(CaptureRules::isBankSender) == true)
            RawEventType.RAW_SMS -> message.sender?.let(CaptureRules::isBankSender) == true
        }
        if (!allowed) return false

        val redactor = Redactor(config.vpaKey)
        val event = OutboxEvent(
            eventId = Uuid7.generate(message.postedAtMs).toString(),
            type = message.type.name,
            sourceApp = message.sourceApp,
            sender = if (fromMessagingApp) message.title else message.sender,
            title = message.title?.let(redactor::redact),
            text = message.text?.let(redactor::redact),
            postedAtMs = message.postedAtMs,
            capturedAtMs = System.currentTimeMillis(),
            dedupeKey = CaptureRules.dedupeKey(
                message.sourceApp ?: message.sender, message.sourceKey, message.title, message.text,
            ),
            status = OutboxStatus.PENDING,
        )
        val inserted = outbox.insert(event) != -1L
        if (inserted) scheduler.requestUpload()
        return inserted
    }
}
