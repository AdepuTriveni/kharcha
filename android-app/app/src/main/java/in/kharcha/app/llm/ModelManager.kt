package `in`.kharcha.app.llm

import android.app.ActivityManager
import android.content.Context
import dagger.hilt.android.qualifiers.ApplicationContext
import `in`.kharcha.app.data.SettingsRepository
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request

/** `GET /v1/models/parser/latest` (PROJECT_SPEC §9, §15.6). */
@Serializable
data class ParserManifest(
    val id: String,
    val version: String,
    val url: String,
    val sha256: String,
    val sizeBytes: Long,
    val minRamMb: Int,
    val promptVersion: String,
)

data class InstalledModel(val version: String, val path: String, val sha256: String)

enum class ModelUpdate { INSTALLED, UP_TO_DATE, NO_MODEL, NOT_ENOUGH_RAM, BAD_CHECKSUM, FAILED, NOT_CONFIGURED }

/**
 * Downloads the active parser GGUF (Wi-Fi only: enforced by the worker's UNMETERED
 * constraint), skips phones below the RAM threshold, verifies sha256 before use and keeps
 * only one model on disk.
 */
@Singleton
class ModelManager @Inject constructor(
    @ApplicationContext private val context: Context,
    private val http: OkHttpClient,
    private val settings: SettingsRepository,
    private val json: Json,
) {
    private val dir: File get() = File(context.filesDir, "models").also { it.mkdirs() }
    private val manifestFile: File get() = File(dir, "installed.json")

    var totalRamMb: () -> Long = {
        val info = ActivityManager.MemoryInfo()
        context.getSystemService(ActivityManager::class.java).getMemoryInfo(info)
        info.totalMem / (1024 * 1024)
    }

    fun installed(): InstalledModel? {
        val file = manifestFile.takeIf { it.exists() } ?: return null
        val manifest = runCatching { json.decodeFromString(ParserManifest.serializer(), file.readText()) }.getOrNull()
            ?: return null
        val gguf = File(dir, fileName(manifest))
        return if (gguf.exists()) InstalledModel(manifest.version, gguf.absolutePath, manifest.sha256) else null
    }

    suspend fun refresh(): ModelUpdate = withContext(Dispatchers.IO) {
        val config = settings.current()
        if (!config.isConfigured) return@withContext ModelUpdate.NOT_CONFIGURED
        val request = Request.Builder().url("${config.serverUrl}/v1/models/parser/latest")
            .header("Authorization", "Bearer ${config.apiKey}").build()
        val manifest = try {
            http.newCall(request).execute().use { response ->
                when {
                    response.code == 404 -> return@withContext ModelUpdate.NO_MODEL
                    !response.isSuccessful -> return@withContext ModelUpdate.FAILED
                    else -> json.decodeFromString(ParserManifest.serializer(), response.body!!.string())
                }
            }
        } catch (e: IOException) {
            return@withContext ModelUpdate.FAILED
        }
        if (totalRamMb() < manifest.minRamMb) return@withContext ModelUpdate.NOT_ENOUGH_RAM
        if (installed()?.sha256 == manifest.sha256) return@withContext ModelUpdate.UP_TO_DATE
        download(manifest)
    }

    private fun download(manifest: ParserManifest): ModelUpdate {
        val part = File(dir, fileName(manifest) + ".part")
        val digest = MessageDigest.getInstance("SHA-256")
        try {
            http.newCall(Request.Builder().url(manifest.url).build()).execute().use { response ->
                if (!response.isSuccessful) return ModelUpdate.FAILED
                response.body!!.byteStream().use { input ->
                    part.outputStream().use { output ->
                        val buffer = ByteArray(1 shl 16)
                        while (true) {
                            val n = input.read(buffer)
                            if (n < 0) break
                            digest.update(buffer, 0, n)
                            output.write(buffer, 0, n)
                        }
                    }
                }
            }
        } catch (e: IOException) {
            part.delete()
            return ModelUpdate.FAILED
        }
        val actual = digest.digest().joinToString("") { "%02x".format(it) }
        if (!actual.equals(manifest.sha256, ignoreCase = true)) {
            part.delete()
            return ModelUpdate.BAD_CHECKSUM
        }
        dir.listFiles()?.filter { it.name.endsWith(".gguf") }?.forEach { it.delete() }
        part.renameTo(File(dir, fileName(manifest)))
        manifestFile.writeText(json.encodeToString(ParserManifest.serializer(), manifest))
        return ModelUpdate.INSTALLED
    }

    private fun fileName(m: ParserManifest) = "parser-${m.version}.gguf"
}
