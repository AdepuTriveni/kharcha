package `in`.kharcha.app.capture

import java.security.MessageDigest

/** Which sources are captured (PROJECT_SPEC §26). Verify package names on a real device. */
object CaptureRules {
    /** Default allowlist: common UPI apps and bank apps. Users can edit it in Settings. */
    val DEFAULT_PACKAGES: Set<String> = setOf(
        "com.phonepe.app",
        "com.google.android.apps.nbu.paisa.user", // Google Pay
        "net.one97.paytm",
        "in.org.npci.upiapp", // BHIM
        "com.dreamplug.androidapp", // CRED
        "in.amazon.mShop.android.shopping",
        "com.snapwork.hdfc", // HDFC Bank
        "com.sbi.lotusintouch", // SBI YONO
        "com.csam.icici.bank.imobile", // ICICI iMobile
        "com.axis.mobile", // Axis Bank
        "com.msf.kbank.mobile", // Kotak
        "com.google.android.apps.messaging", // bank SMS shown as notifications
    )

    /** SMS apps: only notifications whose title is a bank sender id are captured (privacy). */
    val MESSAGING_PACKAGES: Set<String> = setOf(
        "com.google.android.apps.messaging",
        "com.samsung.android.messaging",
        "com.android.mms",
    )

    /** Bank SMS sender ids look like AX-HDFCBK or VM-SBIINB-S (DLT headers). */
    private val BANK_SENDER = Regex("^[A-Z]{2}-[A-Z0-9]{3,9}(-[A-Z])?$")

    fun isBankSender(sender: String): Boolean = BANK_SENDER.matches(sender.uppercase())

    /** Key used to drop re-posts of the same notification (package, key, text hash). */
    fun dedupeKey(vararg parts: String?): String {
        val digest = MessageDigest.getInstance("SHA-256")
        parts.forEach { digest.update((it ?: "").toByteArray()); digest.update(0) }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}
