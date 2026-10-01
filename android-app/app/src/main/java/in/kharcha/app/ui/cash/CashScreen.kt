package `in`.kharcha.app.ui.cash

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import `in`.kharcha.app.cash.CashEntries
import `in`.kharcha.app.cash.CashPreset
import `in`.kharcha.app.cash.CashPresets
import `in`.kharcha.app.cash.UndoResult
import `in`.kharcha.app.data.SettingsRepository
import `in`.kharcha.app.sync.ApiClient
import `in`.kharcha.app.sync.ApiResult
import `in`.kharcha.app.util.Money
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/** Parses what the user typed ("150", "150.5", "1,200") into paise; null if invalid. */
fun parseRupeesToPaise(input: String): Long? = Money.parseRupees(input)

@HiltViewModel
class CashViewModel @Inject constructor(
    private val cash: CashEntries,
    private val settings: SettingsRepository,
    private val api: ApiClient,
) : ViewModel() {
    private val _balance = MutableStateFlow<String?>(null)
    val balance: StateFlow<String?> = _balance

    fun refreshBalance() = viewModelScope.launch {
        val config = settings.current()
        if (!config.isConfigured) {
            _balance.value = "Set the server in Settings to see your cash balance"
            return@launch
        }
        _balance.value = when (val result = api.cashBalance(config.serverUrl, config.apiKey)) {
            is ApiResult.Ok -> result.value.text
            else -> "Cash balance unavailable offline"
        }
    }

    suspend fun add(amountPaise: Long, note: String): String = cash.quickAdd(amountPaise, note)

    suspend fun addPreset(preset: CashPreset): String = cash.widgetTap(preset)

    suspend fun undo(eventId: String): UndoResult = cash.undo(eventId).also { refreshBalance() }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun CashScreen(viewModel: CashViewModel = hiltViewModel()) {
    val balance by viewModel.balance.collectAsStateWithLifecycle()
    var amount by remember { mutableStateOf("") }
    var note by remember { mutableStateOf("") }
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    LaunchedEffect(Unit) { viewModel.refreshBalance() }

    fun confirm(label: String, eventId: String) = scope.launch {
        val action = snackbar.showSnackbar("Logged $label", actionLabel = "Undo", duration = SnackbarDuration.Long)
        if (action == SnackbarResult.ActionPerformed) {
            val message = when (val result = viewModel.undo(eventId)) {
                UndoResult.Removed -> "Removed"
                UndoResult.NotYetOnServer -> "Still syncing, try Undo again in a minute"
                is UndoResult.Failed -> "Undo failed: ${result.reason}"
            }
            snackbar.showSnackbar(message)
        }
    }

    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp)) {
                Text("Cash wallet", style = MaterialTheme.typography.titleMedium)
                Text(balance ?: "…", style = MaterialTheme.typography.headlineSmall)
            }
        }
        Text("Quick add", style = MaterialTheme.typography.titleSmall)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            CashPresets.DEFAULT.forEach { preset ->
                AssistChip(
                    onClick = { scope.launch { confirm(preset.label, viewModel.addPreset(preset)) } },
                    label = { Text(preset.label) },
                )
            }
        }
        OutlinedTextField(
            value = amount,
            onValueChange = { amount = it },
            label = { Text("Amount (₹)") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = note,
            onValueChange = { note = it.take(60) },
            label = { Text("What for? (vada pav, auto, from mom…)") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        val paise = parseRupeesToPaise(amount)
        Button(
            enabled = paise != null,
            onClick = {
                val value = paise ?: return@Button
                scope.launch {
                    val id = viewModel.add(value, note)
                    confirm("₹$amount ${note.trim()}".trim(), id)
                    amount = ""
                    note = ""
                }
            },
        ) { Text("Add cash entry") }
        Text(
            "Tip: \"got 500 from mom\" adds cash, \"2000 atm\" logs a withdrawal.",
            style = MaterialTheme.typography.bodySmall,
        )
        SnackbarHost(snackbar)
    }
}
