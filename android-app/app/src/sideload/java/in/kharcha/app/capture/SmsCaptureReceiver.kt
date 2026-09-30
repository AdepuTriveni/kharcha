package `in`.kharcha.app.capture

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.provider.Telephony
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/** Sideload build only: captures SMS from bank sender ids (PROJECT_SPEC §26). */
@AndroidEntryPoint
class SmsCaptureReceiver : BroadcastReceiver() {
    @Inject lateinit var processor: CaptureProcessor

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Telephony.Sms.Intents.SMS_RECEIVED_ACTION) return
        val parts = Telephony.Sms.Intents.getMessagesFromIntent(intent) ?: return
        // Multi-part SMS arrive as several PDUs from the same sender.
        val messages = parts.groupBy { it.originatingAddress.orEmpty() }.map { (sender, pdus) ->
            CapturedMessage(
                type = RawEventType.RAW_SMS,
                sourceApp = null,
                sender = sender,
                sourceKey = pdus.first().timestampMillis.toString(),
                title = null,
                text = pdus.joinToString("") { it.messageBody.orEmpty() },
                postedAtMs = pdus.first().timestampMillis,
            )
        }
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                messages.forEach { processor.capture(it) }
            } finally {
                pending.finish()
            }
        }
    }
}
