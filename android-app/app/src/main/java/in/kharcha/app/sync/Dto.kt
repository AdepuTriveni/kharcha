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

/** `GET /v1/cash/balance`. `balancePaise` may be negative; show `text`, never a minus sign. */
@Serializable
data class CashBalanceDto(
    val balancePaise: Long,
    val inflowPaise: Long,
    val outflowPaise: Long,
    val text: String,
)

@Serializable
data class TransactionDto(
    val id: String,
    val amountPaise: Long,
    val direction: String,
    val kind: String,
    val status: String,
    val channel: String,
    val merchantId: String?,
    val merchantName: String?,
    val merchantRaw: String?,
    val category: String,
    val isEssential: Boolean,
    val txnTime: String,
    val userCorrected: Boolean,
    val version: Int,
)

@Serializable
data class TransactionPageDto(val items: List<TransactionDto>, val nextCursor: String?)

/** `PATCH /v1/transactions/{id}` body; null fields are left unchanged by the server. */
@Serializable
data class CorrectionDto(val category: String? = null, val merchantName: String? = null)

@Serializable
data class CategorySpendDto(val category: String, val dailyAvgPaise: Long)

@Serializable
data class WhatIfDto(
    val category: String,
    val reductionPct: Int,
    val brokeP50: String?,
    val daysGained: Int?,
)

/** `GET /v1/forecast` (PROJECT_SPEC §14). Dates are IST calendar days (yyyy-MM-dd). */
@Serializable
data class ForecastDto(
    val status: String,
    val moneyNowPaise: Long? = null,
    val bankPaise: Long? = null,
    val cashPaise: Long? = null,
    val balanceFresh: Boolean? = null,
    val brokeP20: String? = null,
    val brokeP50: String? = null,
    val brokeP80: String? = null,
    val daysLeftP50: Int? = null,
    val probBroke: Double? = null,
    val horizonDays: Int? = null,
    val dailySpendP50Paise: Long? = null,
    val topCategories: List<CategorySpendDto> = emptyList(),
    val whatIf: WhatIfDto? = null,
)

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
