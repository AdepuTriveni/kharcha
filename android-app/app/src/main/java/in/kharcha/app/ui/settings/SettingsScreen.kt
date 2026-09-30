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
import androidx.compose.material3.Button
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
import `in`.kharcha.app.sync.SyncScheduler
import javax.inject.Inject
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val repo: SettingsRepository,
    private val scheduler: SyncScheduler,
) : ViewModel() {
    val settings: StateFlow<AppSettings?> =
        repo.settings.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), null)

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
    LaunchedEffect(settings?.serverUrl, settings?.apiKey) {
        settings?.let { url = it.serverUrl; key = it.apiKey }
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
