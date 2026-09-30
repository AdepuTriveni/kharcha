package `in`.kharcha.app.ui.events

import android.content.Intent
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.LifecycleResumeEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import `in`.kharcha.app.data.OutboxDao
import `in`.kharcha.app.data.OutboxEvent
import `in`.kharcha.app.sync.SyncScheduler
import `in`.kharcha.app.ui.isCaptureEnabled
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import javax.inject.Inject
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn

@HiltViewModel
class EventsViewModel @Inject constructor(
    outbox: OutboxDao,
    private val scheduler: SyncScheduler,
) : ViewModel() {
    val events: StateFlow<List<OutboxEvent>> =
        outbox.observeRecent().stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())
    val pending: StateFlow<Int> =
        outbox.observePendingCount().stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), 0)

    fun syncNow() = scheduler.requestUpload()
}

private val IST = ZoneId.of("Asia/Kolkata")
private val TIME = DateTimeFormatter.ofPattern("d MMM, h:mm a")

@Composable
fun EventsScreen(onOpenSettings: () -> Unit, viewModel: EventsViewModel = hiltViewModel()) {
    val context = LocalContext.current
    val events by viewModel.events.collectAsStateWithLifecycle()
    val pending by viewModel.pending.collectAsStateWithLifecycle()
    var captureEnabled by remember { mutableStateOf(context.isCaptureEnabled()) }
    LifecycleResumeEffect(Unit) {
        captureEnabled = context.isCaptureEnabled()
        onPauseOrDispose { }
    }

    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        if (!captureEnabled) {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Notification access is off", style = MaterialTheme.typography.titleMedium)
                    Text("Kharcha needs it to read payment notifications from your UPI and bank apps.")
                    Button(onClick = {
                        context.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
                    }) { Text("Turn on") }
                }
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                "${events.size} captured · $pending waiting to sync",
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.weight(1f).padding(top = 10.dp),
            )
            OutlinedButton(onClick = viewModel::syncNow) { Text("Sync now") }
            OutlinedButton(onClick = onOpenSettings) { Text("Setup") }
        }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(events, key = { it.eventId }) { EventRow(it) }
        }
    }
}

@Composable
private fun EventRow(event: OutboxEvent) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    event.sender ?: event.sourceApp ?: event.type,
                    style = MaterialTheme.typography.labelLarge,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    Instant.ofEpochMilli(event.postedAtMs).atZone(IST).format(TIME),
                    style = MaterialTheme.typography.labelMedium,
                )
            }
            event.title?.let { Text(it, style = MaterialTheme.typography.titleSmall) }
            event.text?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
            AssistChip(onClick = {}, label = {
                Text(event.status + (event.lastError?.let { " · $it" } ?: ""))
            })
        }
    }
}
