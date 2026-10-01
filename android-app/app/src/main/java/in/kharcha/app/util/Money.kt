package `in`.kharcha.app.util

/** Paise are `Long` everywhere (CLAUDE.md rule 1). Same output as backend `format_inr`. */
object Money {
    /** Rupees as written ("150", "1,249.50", "₹1,200") -> paise; null if invalid or not > 0. */
    fun parseRupees(input: String): Long? {
        val cleaned = input.replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").trim()
        if (cleaned.isEmpty()) return null
        return try {
            val paise = java.math.BigDecimal(cleaned).movePointRight(2)
            if (paise.signum() <= 0 || paise.stripTrailingZeros().scale() > 0) null else paise.longValueExact()
        } catch (e: NumberFormatException) {
            null
        } catch (e: ArithmeticException) {
            null
        }
    }

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
