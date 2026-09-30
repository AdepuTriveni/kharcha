package `in`.kharcha.app.sync

import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

sealed interface UploadResult {
    data class Ok(val results: List<EventResultDto>) : UploadResult
    data object Unauthorized : UploadResult
    data class RetryLater(val reason: String) : UploadResult
    data class BadRequest(val code: Int) : UploadResult
}

@Singleton
class ApiClient @Inject constructor(private val http: OkHttpClient, private val json: Json) {

    suspend fun uploadBatch(serverUrl: String, apiKey: String, batch: UploadBatchDto): UploadResult =
        withContext(Dispatchers.IO) {
            val request = Request.Builder()
                .url("$serverUrl/v1/events:batch")
                .header("Authorization", "Bearer $apiKey")
                .post(json.encodeToString(UploadBatchDto.serializer(), batch).toRequestBody(JSON))
                .build()
            try {
                http.newCall(request).execute().use { response ->
                    when {
                        response.isSuccessful -> UploadResult.Ok(
                            json.decodeFromString(BatchResponseDto.serializer(), response.body!!.string()).results,
                        )
                        response.code == 401 || response.code == 403 -> UploadResult.Unauthorized
                        response.code >= 500 || response.code == 429 -> UploadResult.RetryLater("HTTP ${response.code}")
                        else -> UploadResult.BadRequest(response.code)
                    }
                }
            } catch (e: IOException) {
                UploadResult.RetryLater(e.javaClass.simpleName)
            }
        }

    private companion object {
        val JSON = "application/json".toMediaType()
    }
}
