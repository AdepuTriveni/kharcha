package `in`.kharcha.app.ui.transactions

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
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
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import `in`.kharcha.app.data.Category
import `in`.kharcha.app.data.SettingsRepository
import `in`.kharcha.app.sync.ApiClient
import `in`.kharcha.app.sync.ApiResult
import `in`.kharcha.app.sync.CorrectionDto
import `in`.kharcha.app.sync.TransactionDto
import `in`.kharcha.app.util.Money
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

data class TransactionsState(
    val items: List<TransactionDto> = emptyList(),
    val nextCursor: String? = null,
    val loading: Boolean = false,
    val error: String? = null,
)

@HiltViewModel
class TransactionsViewModel @Inject constructor(
    private val settings: SettingsRepository,
    private val api: ApiClient,
) : ViewModel() {
    private val _state = MutableStateFlow(TransactionsState())
    val state: StateFlow<TransactionsState> = _state

    fun refresh() = load(cursor = null)

    fun loadMore() = _state.value.nextCursor?.let { load(it) }

    private fun load(cursor: String?) = viewModelScope.launch {
        val config = settings.current()
        if (!config.isConfigured) {
            _state.value = TransactionsState(error = "Set the server and API key in Settings")
            return@launch
        }
        _state.value = _state.value.copy(loading = true, error = null)
        _state.value = when (val result = api.transactions(config.serverUrl, config.apiKey, cursor)) {
            is ApiResult.Ok -> TransactionsState(
                items = (if (cursor == null) emptyList() else _state.value.items) + result.value.items,
                nextCursor = result.value.nextCursor,
            )
            ApiResult.Unauthorized -> _state.value.copy(loading = false, error = "Check your API key")
            else -> _state.value.copy(loading = false, error = "Could not load transactions")
        }
    }

    fun correct(id: String, correction: CorrectionDto) = viewModelScope.launch {
        val config = settings.current()
        when (val result = api.correct(config.serverUrl, config.apiKey, id, correction)) {
            is ApiResult.Ok -> _state.value = _state.value.copy(
                items = _state.value.items.map { if (it.id == id) result.value else it },
            )
            else -> _state.value = _state.value.copy(error = "Correction not saved, try again")
        }
    }
}

private val IST = ZoneId.of("Asia/Kolkata")
private val TIME = DateTimeFormatter.ofPattern("d MMM, h:mm a")

@Composable
fun TransactionsScreen(viewModel: TransactionsViewModel = hiltViewModel()) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    var editing by remember { mutableStateOf<TransactionDto?>(null) }
    LaunchedEffect(Unit) { viewModel.refresh() }

    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Transactions", style = MaterialTheme.typography.titleLarge, modifier = Modifier.weight(1f))
            OutlinedButton(onClick = { viewModel.refresh() }) { Text("Refresh") }
        }
        Text("Tap a payment to fix its merchant or category.", style = MaterialTheme.typography.bodySmall)
        state.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(state.items, key = { it.id }) { txn ->
                TransactionRow(txn, onClick = { editing = txn })
            }
            if (state.nextCursor != null) {
                item {
                    TextButton(onClick = { viewModel.loadMore() }, enabled = !state.loading) { Text("Load more") }
                }
            }
        }
    }

    editing?.let { txn ->
        CorrectionDialog(
            txn = txn,
            onDismiss = { editing = null },
            onSave = { correction ->
                viewModel.correct(txn.id, correction)
                editing = null
            },
        )
    }
}

@Composable
private fun TransactionRow(txn: TransactionDto, onClick: () -> Unit) {
    val sign = if (txn.direction == "CREDIT") "+" else "−"
    Card(Modifier.fillMaxWidth().clickable(onClick = onClick)) {
        Column(Modifier.padding(12.dp)) {
            Row {
                Text(
                    txn.merchantName ?: txn.merchantRaw ?: txn.kind.lowercase().replace('_', ' '),
                    style = MaterialTheme.typography.titleSmall,
                    modifier = Modifier.weight(1f),
                )
                Text("$sign${Money.format(txn.amountPaise)}", style = MaterialTheme.typography.titleSmall)
            }
            val time = TIME.format(Instant.parse(txn.txnTime).atZone(IST))
            val corrected = if (txn.userCorrected) " · corrected" else ""
            Text("${Category.labelOf(txn.category)} · $time$corrected", style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun CorrectionDialog(txn: TransactionDto, onDismiss: () -> Unit, onSave: (CorrectionDto) -> Unit) {
    var category by remember { mutableStateOf(txn.category) }
    var merchant by remember { mutableStateOf(txn.merchantName ?: "") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Fix this payment") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                txn.merchantRaw?.let { Text("Bank text: $it", style = MaterialTheme.typography.bodySmall) }
                OutlinedTextField(
                    value = merchant,
                    onValueChange = { merchant = it.take(60) },
                    label = { Text("Merchant") },
                    singleLine = true,
                )
                Column(Modifier.heightIn(max = 240.dp).verticalScroll(rememberScrollState())) {
                    Category.entries.forEach { option ->
                        FilterChip(
                            selected = category == option.name,
                            onClick = { category = option.name },
                            label = { Text(option.label) },
                        )
                    }
                }
                Text("Future payments to this merchant will use your choice.", style = MaterialTheme.typography.bodySmall)
            }
        },
        confirmButton = {
            TextButton(onClick = {
                val newMerchant = merchant.trim().takeIf { it.isNotEmpty() && it != txn.merchantName }
                val newCategory = category.takeIf { it != txn.category }
                if (newMerchant == null && newCategory == null) onDismiss()
                else onSave(CorrectionDto(category = newCategory, merchantName = newMerchant))
            }) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
