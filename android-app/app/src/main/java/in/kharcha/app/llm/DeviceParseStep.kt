package `in`.kharcha.app.llm

import android.content.Context
import androidx.hilt.work.HiltWorker
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import dagger.assisted.Assisted
import dagger.assisted.AssistedInject
import dagger.hilt.android.qualifiers.ApplicationContext
import `in`.kharcha.app.data.OutboxDao
import `in`.kharcha.app.sync.DeviceParseDto
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.serialization.json.Json

/**
 * Before upload, run the on-device model over a few pending bank messages and attach the
 * result (tier 2). The server re-validates; "-" marks "tried, nothing usable" so a message is
 * never parsed twice. Only latency is recorded, never content.
 */
@Singleton
class DeviceParseStep @Inject constructor(
    private val outbox: OutboxDao,
    private val parser: OnDeviceParser,
    private val json: Json,
) {
    suspend fun annotatePending(limit: Int = 10): Int {
        var parsed = 0
        for (event in outbox.pendingWithoutDeviceParse(limit)) {
            val text = event.text ?: continue
            val result = parser.parse(event.sender, event.sourceApp, text)
            outbox.setDeviceParse(
                event.eventId,
                result?.let { json.encodeToString(DeviceParseDto.serializer(), it) } ?: "-",
            )
            if (result != null) parsed++
        }
        return parsed
    }
}

/** Daily model refresh on unmetered networks only (Wi-Fi), while charging (PROJECT_SPEC §15.6). */
@HiltWorker
class ModelUpdateWorker @AssistedInject constructor(
    @Assisted context: Context,
    @Assisted params: WorkerParameters,
    private val models: ModelManager,
) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result = when (models.refresh()) {
        ModelUpdate.FAILED -> Result.retry()
        else -> Result.success()
    }
}

@Singleton
class ModelScheduler @Inject constructor(@ApplicationContext private val context: Context) {
    fun scheduleDaily() {
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.UNMETERED)
            .setRequiresCharging(true)
            .setRequiresStorageNotLow(true)
            .build()
        val request = PeriodicWorkRequestBuilder<ModelUpdateWorker>(24, TimeUnit.HOURS)
            .setConstraints(constraints)
            .build()
        WorkManager.getInstance(context)
            .enqueueUniquePeriodicWork("parser-model", ExistingPeriodicWorkPolicy.KEEP, request)
    }
}
