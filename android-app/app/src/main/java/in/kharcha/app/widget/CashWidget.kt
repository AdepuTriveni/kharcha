package `in`.kharcha.app.widget

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.dp
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.glance.Button
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.action.ActionParameters
import androidx.glance.action.actionParametersOf
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.action.ActionCallback
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.provideContent
import androidx.glance.appwidget.state.updateAppWidgetState
import androidx.glance.background
import androidx.glance.currentState
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.padding
import androidx.glance.layout.width
import androidx.glance.state.GlanceStateDefinition
import androidx.glance.state.PreferencesGlanceStateDefinition
import androidx.glance.text.Text
import androidx.glance.unit.ColorProvider
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.components.SingletonComponent
import `in`.kharcha.app.cash.CashEntries
import `in`.kharcha.app.cash.CashPresets

/** Home-screen widget: one tap logs a preset cash spend (PROJECT_SPEC §13, WIDGET_TAP). */
class CashWidget : GlanceAppWidget() {
    override val stateDefinition: GlanceStateDefinition<*> = PreferencesGlanceStateDefinition

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        provideContent { Content() }
    }

    @Composable
    private fun Content() {
        val last = currentState<Preferences>()[LAST_LOGGED]
        Column(
            modifier = GlanceModifier.fillMaxWidth()
                .background(ColorProvider(androidx.compose.ui.graphics.Color(0xFFF4F1EA)))
                .padding(8.dp),
        ) {
            Text(last?.let { "Logged $it · undo in app" } ?: "Kharcha · tap to log cash")
            Row(modifier = GlanceModifier.fillMaxWidth().padding(top = 4.dp)) {
                CashPresets.DEFAULT.take(VISIBLE).forEachIndexed { index, preset ->
                    if (index > 0) Spacer(GlanceModifier.width(4.dp))
                    Button(
                        text = preset.label,
                        onClick = actionRunCallback<LogPresetAction>(actionParametersOf(PRESET to index)),
                    )
                }
            }
        }
    }

    companion object {
        const val VISIBLE = 4
        val PRESET = ActionParameters.Key<Int>("preset")
        val LAST_LOGGED = stringPreferencesKey("last_logged")
    }
}

@EntryPoint
@InstallIn(SingletonComponent::class)
interface WidgetEntryPoint {
    fun cashEntries(): CashEntries
}

class LogPresetAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val preset = CashPresets.DEFAULT.getOrNull(parameters[CashWidget.PRESET] ?: return) ?: return
        EntryPointAccessors.fromApplication(context, WidgetEntryPoint::class.java)
            .cashEntries()
            .widgetTap(preset)
        updateAppWidgetState(context, glanceId) { it[CashWidget.LAST_LOGGED] = preset.label }
        CashWidget().update(context, glanceId)
    }
}

class CashWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = CashWidget()
}
