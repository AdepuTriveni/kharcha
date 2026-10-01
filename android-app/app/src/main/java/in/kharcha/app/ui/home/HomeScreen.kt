package `in`.kharcha.app.ui.home

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
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
import `in`.kharcha.app.sync.ForecastDto
import `in`.kharcha.app.ui.cash.parseRupeesToPaise
import `in`.kharcha.app.util.Money
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

private val DAY = DateTimeFormatter.ofPattern("d MMM")

/** "18 Oct" or "after 15 Nov" when a run never goes broke inside the horizon. */
fun brokeLabel(date: String?, horizonDays: Int?, today: LocalDate = LocalDate.now()): String =
    date?.let { LocalDate.parse(it).format(DAY) }
        ?: "after ${today.plusDays((horizonDays ?: 45).toLong()).format(DAY)}"

/** Range text: p20 is the pessimistic end, p80 the optimistic one (PROJECT_SPEC §14). */
fun rangeText(f: ForecastDto, today: LocalDate = LocalDate.now()): String {
    val low = brokeLabel(f.brokeP20, f.horizonDays, today)
    val high = brokeLabel(f.brokeP80, f.horizonDays, today)
    return if (low == high) low else "$low – $high"
}

data class HomeState(
    val forecast: ForecastDto? = null,
    val whatIf: ForecastDto? = null,
    val error: String? = null,
)

@HiltViewModel
class HomeViewModel @Inject constructor(
    private val settings: SettingsRepository,
    private val api: ApiClient,
) : ViewModel() {
    private val _state = MutableStateFlow(HomeState())
    val state: StateFlow<HomeState> = _state

    fun refresh(balancePaise: Long? = null) = viewModelScope.launch {
        val config = settings.current()
        if (!config.isConfigured) {
            _state.value = HomeState(error = "Set the server and API key in Settings")
            return@launch
        }
        _state.value = when (val r = api.forecast(config.serverUrl, config.apiKey, balancePaise = balancePaise)) {
            is ApiResult.Ok -> HomeState(forecast = r.value)
            else -> HomeState(error = "Forecast unavailable right now")
        }
    }

    fun whatIf(category: String, reductionPct: Int) = viewModelScope.launch {
        val config = settings.current()
        val r = api.forecast(config.serverUrl, config.apiKey, skip = category, reductionPct = reductionPct)
        if (r is ApiResult.Ok) _state.value = _state.value.copy(whatIf = r.value)
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun HomeScreen(viewModel: HomeViewModel = hiltViewModel()) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { viewModel.refresh() }
    var selected by remember { mutableStateOf<String?>(null) }
    var cut by remember { mutableFloatStateOf(100f) }
    var balanceInput by remember { mutableStateOf("") }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        state.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        val f = state.forecast
        if (f?.status == "NEEDS_BALANCE") {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("What's in your bank account?", style = MaterialTheme.typography.titleMedium)
                    Text("Your bank's messages haven't shown a balance yet. Asked once; we'll keep it up to date.")
                    OutlinedTextField(
                        value = balanceInput,
                        onValueChange = { balanceInput = it },
                        label = { Text("Balance (₹)") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                        singleLine = true,
                    )
                    Button(
                        enabled = parseRupeesToPaise(balanceInput) != null,
                        onClick = { viewModel.refresh(parseRupeesToPaise(balanceInput)) },
                    ) { Text("Show my broke date") }
                }
            }
        } else if (f != null) {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("Broke date", style = MaterialTheme.typography.titleMedium)
                    Text(rangeText(f), style = MaterialTheme.typography.headlineMedium)
                    f.daysLeftP50?.let { Text("Most likely in about $it days") }
                        ?: Text("You're fine for the next ${f.horizonDays ?: 45} days at this pace")
                    f.moneyNowPaise?.let {
                        val cash = f.cashPaise?.takeIf { c -> c > 0 }?.let { c -> " (incl. ${Money.format(c)} cash)" } ?: ""
                        Text("Money now: ${Money.format(it)}$cash", style = MaterialTheme.typography.bodySmall)
                    }
                    if (f.balanceFresh == false) {
                        Text("Bank balance is a few days old", style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
            if (f.topCategories.isNotEmpty()) {
                Text("What if I cut…", style = MaterialTheme.typography.titleSmall)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    f.topCategories.forEach { c ->
                        FilterChip(
                            selected = selected == c.category,
                            onClick = {
                                selected = c.category
                                viewModel.whatIf(c.category, cut.toInt())
                            },
                            label = { Text("${Category.labelOf(c.category)} · ${Money.format(c.dailyAvgPaise)}/day") },
                        )
                    }
                }
                selected?.let { category ->
                    Text("Cut ${cut.toInt()}% of ${Category.labelOf(category)}")
                    Slider(
                        value = cut,
                        onValueChange = { cut = it },
                        onValueChangeFinished = { viewModel.whatIf(category, cut.toInt()) },
                        valueRange = 10f..100f,
                        steps = 8,
                    )
                    state.whatIf?.whatIf?.let { w ->
                        val gained = w.daysGained
                        Text(
                            when {
                                gained == null -> "No change inside ${f.horizonDays ?: 45} days"
                                gained > 0 -> "+$gained days → broke around ${brokeLabel(w.brokeP50, f.horizonDays)}"
                                else -> "Barely moves your broke date"
                            },
                            style = MaterialTheme.typography.titleMedium,
                        )
                    }
                }
            }
        }
    }
}
