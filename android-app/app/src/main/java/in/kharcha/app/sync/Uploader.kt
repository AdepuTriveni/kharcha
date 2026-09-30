package `in`.kharcha.app.sync

import `in`.kharcha.app.data.OutboxDao
import `in`.kharcha.app.data.OutboxStatus
import `in`.kharcha.app.data.SettingsRepository
import javax.inject.Inject
import javax.inject.Singleton

enum class UploadOutcome { DONE, RETRY, NEEDS_CONFIG }

/**
 * Drains the outbox in batches of ≤ 100 (PROJECT_SPEC §26). An event becomes SENT only when
 * the server answers ACCEPTED or DUPLICATE; REJECTED events are kept with the reason.
 */
@Singleton
class Uploader @Inject constructor(
    private val outbox: OutboxDao,
    private val settings: SettingsRepository,
    private val api: ApiClient,
) {
    suspend fun drain(maxBatches: Int = 10): UploadOutcome {
        val config = settings.current()
        if (!config.isConfigured) return UploadOutcome.NEEDS_CONFIG
        repeat(maxBatches) {
            val batch = outbox.pending(BATCH_SIZE)
            if (batch.isEmpty()) return UploadOutcome.DONE
            val ids = batch.map { it.eventId }
            val dto = UploadBatchDto(batch.map { it.toDto(config.deviceId) })
            when (val result = api.uploadBatch(config.serverUrl, config.apiKey, dto)) {
                is UploadResult.Ok -> {
                    val byId = result.results.filter { it.eventId != null }.associateBy { it.eventId!! }
                    for (id in ids) {
                        val r = byId[id]
                        when (r?.status) {
                            "ACCEPTED", "DUPLICATE" -> outbox.setStatus(id, OutboxStatus.SENT, null)
                            "REJECTED" -> outbox.setStatus(id, OutboxStatus.REJECTED, r.reason)
                            else -> outbox.recordAttempt(listOf(id), "missing result")
                        }
                    }
                    if (batch.size < BATCH_SIZE) return UploadOutcome.DONE
                }
                is UploadResult.RetryLater -> {
                    outbox.recordAttempt(ids, result.reason)
                    return UploadOutcome.RETRY
                }
                is UploadResult.BadRequest -> {
                    outbox.recordAttempt(ids, "HTTP ${result.code}")
                    return UploadOutcome.RETRY
                }
                UploadResult.Unauthorized -> {
                    outbox.recordAttempt(ids, "unauthorized: check API key")
                    return UploadOutcome.NEEDS_CONFIG
                }
            }
        }
        return UploadOutcome.DONE
    }

    companion object {
        const val BATCH_SIZE = 100
    }
}
