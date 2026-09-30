package `in`.kharcha.app.data

import android.content.Context
import android.util.Base64
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.core.stringSetPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import dagger.hilt.android.qualifiers.ApplicationContext
import `in`.kharcha.app.BuildConfig
import `in`.kharcha.app.capture.CaptureRules
import java.security.SecureRandom
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "settings")

data class AppSettings(
    val serverUrl: String,
    val apiKey: String,
    val deviceId: String,
    val vpaKey: ByteArray,
    val allowlist: Set<String>,
) {
    val isConfigured: Boolean get() = serverUrl.isNotBlank() && apiKey.isNotBlank()
}

@Singleton
class SettingsRepository @Inject constructor(@ApplicationContext private val context: Context) {
    private object Keys {
        val SERVER_URL = stringPreferencesKey("server_url")
        val API_KEY = stringPreferencesKey("api_key")
        val DEVICE_ID = stringPreferencesKey("device_id")
        val VPA_KEY = stringPreferencesKey("vpa_key")
        val ALLOWLIST = stringSetPreferencesKey("allowlist")
    }

    val settings: Flow<AppSettings> = context.dataStore.data.map { prefs ->
        AppSettings(
            serverUrl = prefs[Keys.SERVER_URL] ?: BuildConfig.DEFAULT_SERVER_URL,
            apiKey = prefs[Keys.API_KEY] ?: "",
            deviceId = prefs[Keys.DEVICE_ID] ?: "",
            vpaKey = prefs[Keys.VPA_KEY]?.let { Base64.decode(it, Base64.NO_WRAP) } ?: ByteArray(0),
            allowlist = prefs[Keys.ALLOWLIST] ?: CaptureRules.DEFAULT_PACKAGES,
        )
    }

    /** Current settings; creates the device id and VPA hashing key on first use. */
    suspend fun current(): AppSettings {
        val now = settings.first()
        if (now.deviceId.isNotEmpty() && now.vpaKey.isNotEmpty()) return now
        context.dataStore.edit { prefs ->
            if (prefs[Keys.DEVICE_ID].isNullOrEmpty()) prefs[Keys.DEVICE_ID] = "d_" + randomHex(6)
            if (prefs[Keys.VPA_KEY].isNullOrEmpty()) {
                val key = ByteArray(32).also(SecureRandom()::nextBytes)
                prefs[Keys.VPA_KEY] = Base64.encodeToString(key, Base64.NO_WRAP)
            }
        }
        return settings.first()
    }

    suspend fun saveServer(serverUrl: String, apiKey: String) {
        context.dataStore.edit {
            it[Keys.SERVER_URL] = serverUrl.trim().trimEnd('/')
            it[Keys.API_KEY] = apiKey.trim()
        }
    }

    suspend fun setAllowlist(packages: Set<String>) {
        context.dataStore.edit { it[Keys.ALLOWLIST] = packages }
    }

    private fun randomHex(bytes: Int): String =
        ByteArray(bytes).also(SecureRandom()::nextBytes).joinToString("") { "%02x".format(it) }
}
