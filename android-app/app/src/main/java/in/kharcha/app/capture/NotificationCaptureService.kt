package `in`.kharcha.app.capture

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/** Captures allowlisted bank/UPI notifications in the background (FR-1, PROJECT_SPEC §26). */
@AndroidEntryPoint
class NotificationCaptureService : NotificationListenerService() {
    @Inject lateinit var processor: CaptureProcessor

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        val notification = sbn.notification ?: return
        val flags = notification.flags
        if (flags and Notification.FLAG_ONGOING_EVENT != 0) return
        if (flags and Notification.FLAG_GROUP_SUMMARY != 0) return
        if (sbn.packageName == packageName) return

        val extras = notification.extras
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString()
        val text = (extras.getCharSequence(Notification.EXTRA_BIG_TEXT)
            ?: extras.getCharSequence(Notification.EXTRA_TEXT))?.toString()
        val message = CapturedMessage(
            type = RawEventType.RAW_NOTIFICATION,
            sourceApp = sbn.packageName,
            sender = null,
            sourceKey = sbn.key,
            title = title,
            text = text,
            postedAtMs = sbn.postTime,
        )
        scope.launch { processor.capture(message) }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }
}
