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

/** Result of a plain JSON API call. */
sealed interface ApiResult<out T> {
    data class Ok<T>(val value: T) : ApiResult<T>
    data object NotFound : ApiResult<Nothing>
    data object Unauthorized : ApiResult<Nothing>
    data class Failed(val reason: String) : ApiResult<Nothing>
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

    suspend fun cashBalance(serverUrl: String, apiKey: String): ApiResult<CashBalanceDto> =
        call(Request.Builder().url("$serverUrl/v1/cash/balance").get(), apiKey) {
            json.decodeFromString(CashBalanceDto.serializer(), it)
        }

    /** Undo: `entryId` is the outbox event id of a quick-add or widget tap. */
    suspend fun deleteCash(serverUrl: String, apiKey: String, entryId: String): ApiResult<Unit> =
        call(Request.Builder().url("$serverUrl/v1/cash/$entryId").delete(), apiKey) { }

    suspend fun userSettings(serverUrl: String, apiKey: String): ApiResult<UserSettingsDto> =
        call(Request.Builder().url("$serverUrl/v1/settings").get(), apiKey) {
            json.decodeFromString(UserSettingsDto.serializer(), it)
        }

    suspend fun updateUserSettings(
        serverUrl: String,
        apiKey: String,
        update: UserSettingsUpdateDto,
    ): ApiResult<UserSettingsDto> {
        val body = SPARSE.encodeToString(UserSettingsUpdateDto.serializer(), update).toRequestBody(JSON)
        return call(Request.Builder().url("$serverUrl/v1/settings").put(body), apiKey) {
            json.decodeFromString(UserSettingsDto.serializer(), it)
        }
    }

    /** `DELETE /v1/me`: removes everything the server holds for this user. */
    suspend fun deleteMe(serverUrl: String, apiKey: String): ApiResult<Unit> =
        call(Request.Builder().url("$serverUrl/v1/me").delete(), apiKey) { }

    suspend fun forecast(
        serverUrl: String,
        apiKey: String,
        skip: String? = null,
        reductionPct: Int = 100,
        balancePaise: Long? = null,
    ): ApiResult<ForecastDto> {
        val url = buildString {
            append("$serverUrl/v1/forecast?reductionPct=$reductionPct")
            if (skip != null) append("&skip=$skip")
            if (balancePaise != null) append("&balancePaise=$balancePaise")
        }
        return call(Request.Builder().url(url).get(), apiKey) {
            json.decodeFromString(ForecastDto.serializer(), it)
        }
    }

    suspend fun transactions(
        serverUrl: String,
        apiKey: String,
        cursor: String?,
        limit: Int = 50,
    ): ApiResult<TransactionPageDto> {
        val url = buildString {
            append("$serverUrl/v1/transactions?limit=$limit")
            if (cursor != null) append("&cursor=").append(java.net.URLEncoder.encode(cursor, "UTF-8"))
        }
        return call(Request.Builder().url(url).get(), apiKey) {
            json.decodeFromString(TransactionPageDto.serializer(), it)
        }
    }

    suspend fun correct(
        serverUrl: String,
        apiKey: String,
        transactionId: String,
        correction: CorrectionDto,
    ): ApiResult<TransactionDto> {
        val body = json.encodeToString(CorrectionDto.serializer(), correction).toRequestBody(JSON)
        return call(Request.Builder().url("$serverUrl/v1/transactions/$transactionId").patch(body), apiKey) {
            json.decodeFromString(TransactionDto.serializer(), it)
        }
    }

    private suspend fun <T> call(
        builder: Request.Builder,
        apiKey: String,
        decode: (String) -> T,
    ): ApiResult<T> = withContext(Dispatchers.IO) {
        val request = builder.header("Authorization", "Bearer $apiKey").build()
        try {
            http.newCall(request).execute().use { response ->
                when {
                    response.isSuccessful -> ApiResult.Ok(decode(response.body?.string().orEmpty()))
                    response.code == 404 -> ApiResult.NotFound
                    response.code == 401 || response.code == 403 -> ApiResult.Unauthorized
                    else -> ApiResult.Failed("HTTP ${response.code}")
                }
            }
        } catch (e: IOException) {
            ApiResult.Failed(e.javaClass.simpleName)
        } catch (e: kotlinx.serialization.SerializationException) {
            ApiResult.Failed("bad response")
        }
    }

    private companion object {
        val JSON = "application/json".toMediaType()
        val SPARSE = Json { explicitNulls = false }
    }
}
