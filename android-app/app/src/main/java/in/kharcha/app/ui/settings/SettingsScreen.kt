package `in`.kharcha.app.ui.settings

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Switch
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import `in`.kharcha.app.data.AppSettings
import `in`.kharcha.app.data.SettingsRepository
import `in`.kharcha.app.sync.ApiClient
import `in`.kharcha.app.sync.ApiResult
import `in`.kharcha.app.sync.SyncScheduler
import `in`.kharcha.app.sync.UserSettingsDto
import `in`.kharcha.app.sync.UserSettingsUpdateDto
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val repo: SettingsRepository,
    private val scheduler: SyncScheduler,
    private val api: ApiClient,
) : ViewModel() {
    val settings: StateFlow<AppSettings?> =
        repo.settings.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), null)

    private val _account = MutableStateFlow<UserSettingsDto?>(null)
    val account: StateFlow<UserSettingsDto?> = _account
    private val _message = MutableStateFlow<String?>(null)
    val message: StateFlow<String?> = _message

    fun loadAccount() = viewModelScope.launch {
        val config = repo.current()
        if (!config.isConfigured) return@launch
        (api.userSettings(config.serverUrl, config.apiKey) as? ApiResult.Ok)?.let { _account.value = it.value }
    }

    fun update(change: UserSettingsUpdateDto) = viewModelScope.launch {
        val config = repo.current()
        when (val r = api.updateUserSettings(config.serverUrl, config.apiKey, change)) {
            is ApiResult.Ok -> {
                _account.value = r.value
                _message.value = "Saved"
            }
            else -> _message.value = "Could not save, try again"
        }
    }

    fun deleteEverything() = viewModelScope.launch {
        val config = repo.current()
        when (api.deleteMe(config.serverUrl, config.apiKey)) {
            is ApiResult.Ok -> {
                _account.value = null
                _message.value = "All your data on the server is deleted"
            }
            else -> _message.value = "Delete failed, try again"
        }
    }

    fun saveServer(url: String, key: String) = viewModelScope.launch {
        repo.saveServer(url, key)
        scheduler.requestUpload()
    }

    fun addPackage(pkg: String) = viewModelScope.launch {
        val current = settings.value?.allowlist ?: return@launch
        if (pkg.isNotBlank()) repo.setAllowlist(current + pkg.trim())
    }

    fun removePackage(pkg: String) = viewModelScope.launch {
        val current = settings.value?.allowlist ?: return@launch
        repo.setAllowlist(current - pkg)
    }
}

@SuppressLint("BatteryLife") // the user asked to be taken to this setting
@Composable
fun SettingsScreen(viewModel: SettingsViewModel = hiltViewModel()) {
    val context = LocalContext.current
    val settings by viewModel.settings.collectAsStateWithLifecycle()
    var url by remember { mutableStateOf("") }
    var key by remember { mutableStateOf("") }
    var newPackage by remember { mutableStateOf("") }
    val account by viewModel.account.collectAsStateWithLifecycle()
    val message by viewModel.message.collectAsStateWithLifecycle()
    var confirmDelete by remember { mutableStateOf(false) }
    LaunchedEffect(settings?.serverUrl, settings?.apiKey) {
        settings?.let { url = it.serverUrl; key = it.apiKey }
        viewModel.loadAccount()
    }

    Column(
        Modifier.fillMaxSize().padding(16.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Server", style = MaterialTheme.typography.titleMedium)
        OutlinedTextField(url, { url = it }, label = { Text("Server URL") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(
            key, { key = it }, label = { Text("API key") },
            visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(),
        )
        Button(onClick = { viewModel.saveServer(url, key) }) { Text("Save") }
        message?.let { Text(it, style = MaterialTheme.typography.bodySmall) }

        account?.let { a ->
            HorizontalDivider()
            Text("Roast level", style = MaterialTheme.typography.titleMedium)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("OFF", "MILD", "MEDIUM", "SAVAGE").forEach { level ->
                    FilterChip(
                        selected = a.roastLevel == level,
                        onClick = { viewModel.update(UserSettingsUpdateDto(roastLevel = level)) },
                        label = { Text(level.lowercase().replaceFirstChar { it.uppercase() }) },
                    )
                }
            }
            Text(
                "Quiet hours ${a.quietStart.take(5)}-${a.quietEnd.take(5)} IST: no nudges then.",
                style = MaterialTheme.typography.bodySmall,
            )

            HorizontalDivider()
            Text("Privacy and consent", style = MaterialTheme.typography.titleMedium)
            ConsentRow(
                title = "Help train the Kharcha parser",
                body = "Your redacted payment messages (no account numbers, no names) may be used to " +
                    "train our own small model. Off by default; turn it off any time.",
                checked = a.mlConsent,
                onChange = { viewModel.update(UserSettingsUpdateDto(mlConsent = it)) },
            )
            ConsentRow(
                title = "Try new nudge styles",
                body = "Kharcha may test different message styles to learn what actually helps you.",
                checked = a.experimentOptIn,
                onChange = { viewModel.update(UserSettingsUpdateDto(experimentOptIn = it)) },
            )
            OutlinedButton(onClick = { confirmDelete = true }) { Text("Delete all my data") }
        }
        if (confirmDelete) {
            AlertDialog(
                onDismissRequest = { confirmDelete = false },
                title = { Text("Delete everything?") },
                text = {
                    Text(
                        "This removes your payments, cash entries, messages and settings from the " +
                            "server. It cannot be undone.",
                    )
                },
                confirmButton = {
                    TextButton(onClick = {
                        confirmDelete = false
                        viewModel.deleteEverything()
                    }) { Text("Delete") }
                },
                dismissButton = { TextButton(onClick = { confirmDelete = false }) { Text("Cancel") } },
            )
        }

        HorizontalDivider()
        Text("Permissions", style = MaterialTheme.typography.titleMedium)
        OutlinedButton(onClick = {
            context.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
        }) { Text("Notification access") }
        Text(
            "Some phones stop background apps. Allow Kharcha to run unrestricted so payments are not missed.",
            style = MaterialTheme.typography.bodySmall,
        )
        OutlinedButton(onClick = {
            context.startActivity(
                Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${context.packageName}")),
            )
        }) { Text("Battery optimisation") }

        HorizontalDivider()
        Text("Apps to read", style = MaterialTheme.typography.titleMedium)
        settings?.allowlist?.sorted()?.forEach { pkg ->
            Row(Modifier.fillMaxWidth()) {
                Text(pkg, modifier = Modifier.weight(1f).padding(top = 12.dp), style = MaterialTheme.typography.bodySmall)
                TextButton(onClick = { viewModel.removePackage(pkg) }) { Text("Remove") }
            }
        }
        OutlinedTextField(
            newPackage, { newPackage = it }, label = { Text("Add package name") }, modifier = Modifier.fillMaxWidth(),
        )
        OutlinedButton(onClick = { viewModel.addPackage(newPackage); newPackage = "" }) { Text("Add") }
    }
}

@Composable
private fun ConsentRow(title: String, body: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleSmall)
            Text(body, style = MaterialTheme.typography.bodySmall)
        }
        Switch(checked = checked, onCheckedChange = onChange)
    }
}
