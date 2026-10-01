package `in`.kharcha.app.util

/** Paise are `Long` everywhere (CLAUDE.md rule 1). Same output as backend `format_inr`. */
object Money {
    /** `12345678 -> "₹1,23,456.78"` (Indian digit grouping). */
    fun format(paise: Long, symbol: String = "₹"): String {
        val sign = if (paise < 0) "-" else ""
        val abs = if (paise == Long.MIN_VALUE) error("out of range") else kotlin.math.abs(paise)
        val rupees = (abs / 100).toString()
        val rem = abs % 100
        val grouped = if (rupees.length <= 3) {
            rupees
        } else {
            val head = rupees.dropLast(3)
            val tail = rupees.takeLast(3)
            head.reversed().chunked(2).joinToString(",").reversed() + "," + tail
        }
        return "$sign$symbol$grouped.${"%02d".format(rem)}"
    }
}
