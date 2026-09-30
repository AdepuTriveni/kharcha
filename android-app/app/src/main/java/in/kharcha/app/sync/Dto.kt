package `in`.kharcha.app.sync

import `in`.kharcha.app.data.OutboxEvent
import java.time.Instant
import kotlinx.serialization.Serializable

/**
 * Wire format of `POST /v1/events:batch` (PROJECT_SPEC §7.3, §9). Must match the backend's
 * `UploadEvent`; the shared fixture in `src/test/resources/contract/` is checked on both sides.
 */
@Serializable
data class UploadBatchDto(val events: List<UploadEventDto>)

@Serializable
data class UploadEventDto(
    val eventId: String,
    val type: String,
    val occurredAt: String,
    val payload: RawEventPayloadDto,
)

@Serializable
data class RawEventPayloadDto(
    val sourceApp: String?,
    val sender: String?,
    val title: String?,
    val text: String?,
    val postedAt: String,
    val deviceId: String,
    val redacted: Boolean,
)

@Serializable
data class BatchResponseDto(val results: List<EventResultDto>)

@Serializable
data class EventResultDto(val eventId: String?, val status: String, val reason: String? = null)

fun OutboxEvent.toDto(deviceId: String): UploadEventDto {
    val posted = Instant.ofEpochMilli(postedAtMs).toString()
    return UploadEventDto(
        eventId = eventId,
        type = type,
        occurredAt = posted,
        payload = RawEventPayloadDto(
            sourceApp = sourceApp,
            sender = sender,
            title = title,
            text = text,
            postedAt = posted,
            deviceId = deviceId,
            redacted = true, // only redacted text is ever stored in the outbox
        ),
    )
}
