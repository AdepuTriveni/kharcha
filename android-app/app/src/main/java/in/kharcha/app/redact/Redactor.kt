package `in`.kharcha.app.redact

import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * Redacts a bank/UPI message before it is stored or uploaded (PROJECT_SPEC §26, §28.1).
 *
 * Keeps: amounts, merchant names, merchant VPAs, UPI reference numbers, last 4 digits of
 * accounts and cards. Masks: phone numbers, emails, Aadhaar- and PAN-like ids, other long
 * digit runs. Person VPAs keep their bank handle but the local part is replaced by a keyed
 * hash, so the same person maps to the same token on this device without revealing them.
 */
class Redactor(private val vpaKey: ByteArray) {

    fun redact(text: String): String {
        var out = text
        out = EMAIL.replace(out) { "[email]" }
        out = VPA.replace(out) { m -> redactVpa(m.groupValues[1], m.groupValues[2]) }
        out = PAN.replace(out) { "[pan]" }
        // Cards before Aadhaar: a spaced 16-digit card would otherwise look like 4-4-4 + 4.
        out = CARD.replace(out) { m -> "XX" + m.value.filter(Char::isDigit).takeLast(4) }
        out = AADHAAR.replace(out) { "[aadhaar]" }
        out = ACCOUNT.replace(out) { m ->
            val digits = m.groupValues[3]
            if (digits.length <= 4) m.value else m.groupValues[1] + "XX" + digits.takeLast(4)
        }
        out = PHONE.replace(out) { m -> if (isReference(out, m.range.first)) m.value else "[phone]" }
        out = LONG_DIGITS.replace(out) { m ->
            if (isReference(out, m.range.first)) m.value else "XX" + m.value.takeLast(4)
        }
        return out
    }

    private fun redactVpa(local: String, handle: String): String {
        return if (isPersonVpa(local, handle)) "p${hash(local.lowercase())}@$handle" else "$local@$handle"
    }

    private fun hash(value: String): String {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(vpaKey, "HmacSHA256"))
        return mac.doFinal(value.toByteArray()).take(4).joinToString("") { "%02x".format(it) }
    }

    private fun isReference(text: String, start: Int): Boolean {
        val before = text.substring(maxOf(0, start - 24), start)
        return REFERENCE_CONTEXT.containsMatchIn(before)
    }

    companion object {
        /** Handles that mostly belong to individuals (consumer UPI apps). */
        private val PERSON_HANDLES = setOf(
            "okaxis", "oksbi", "okhdfcbank", "okicici", "ybl", "ibl", "axl", "apl", "upi",
            "paytm", "pthdfc", "ptyes", "ptaxis", "ptsbi", "fbl", "yapl", "ikwik", "freecharge",
            "airtel", "jupiteraxis", "naviaxis", "superyes", "slc", "kotak811",
        )

        /** Local-part hints that the VPA is a merchant even on a consumer handle. */
        private val MERCHANT_HINT = Regex(
            "(paytmqr|bharatpe|razorpay|rzp|merchant|store|shop|mart|cafe|restaurant|" +
                "pvt|ltd|enterprise|traders|gpay-|^q\\d{6,}|\\.payu|cashfree|phonepe|swiggy|zomato)",
            RegexOption.IGNORE_CASE,
        )

        fun isPersonVpa(local: String, handle: String): Boolean {
            if (local.all(Char::isDigit) && local.length >= 10) return true
            return handle.lowercase() in PERSON_HANDLES && !MERCHANT_HINT.containsMatchIn(local)
        }

        private val EMAIL = Regex("[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\\.[A-Za-z0-9-]+)*\\.[A-Za-z]{2,}")
        private val VPA = Regex("\\b([A-Za-z0-9._-]{2,64})@([A-Za-z][A-Za-z0-9]{1,31})\\b(?!\\.[A-Za-z])")
        private val PAN = Regex("\\b[A-Z]{5}[0-9]{4}[A-Z]\\b")
        private val AADHAAR = Regex(
            "\\b[2-9][0-9]{3}[\\s-][0-9]{4}[\\s-][0-9]{4}\\b|(?i:aadhaar\\D{0,12})[0-9]{12}\\b"
        )
        private val CARD = Regex("(?<![0-9])(?:[0-9]{4}[\\s-]){3}[0-9]{4}(?![0-9])|(?<![0-9])[0-9]{16}(?![0-9])")
        private val ACCOUNT = Regex(
            "(\\b(?i:a/c|acct|account|ac)\\.?\\s*(?i:no\\.?)?\\s*[:\\-]?\\s*)([Xx*]*)([0-9]{5,18})(?![0-9])"
        )
        private val PHONE = Regex("(?<![0-9])(?:\\+91[\\s-]?)?[6-9][0-9]{9}(?![0-9])")
        // Not inside words or VPAs (merchant ids like paytmqr2810050501@paytm stay intact).
        private val LONG_DIGITS = Regex("(?<![0-9A-Za-z])[0-9]{9,}(?![0-9A-Za-z@])")
        // "Ref No.", "UPI:", "Txn ID", "Order ID": references are kept for refund cases (§19).
        private val REFERENCE_CONTEXT = Regex(
            "(ref|rrn|utr|txn|upi|transaction|imps|neft|rtgs|order)(\\s*(id|no|number))?[\\s.:/#-]*$",
            RegexOption.IGNORE_CASE,
        )
    }
}
