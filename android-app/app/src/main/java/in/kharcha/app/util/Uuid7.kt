package `in`.kharcha.app.util

import java.security.SecureRandom
import java.util.UUID

/** UUIDv7 (RFC 9562): 48-bit Unix ms timestamp, version 7, variant 10, random rest. */
object Uuid7 {
    private val random = SecureRandom()

    fun generate(unixMs: Long = System.currentTimeMillis()): UUID {
        require(unixMs in 0 until (1L shl 48)) { "timestamp out of range for UUIDv7" }
        val randA = random.nextInt(1 shl 12).toLong()
        val randB = random.nextLong() and 0x3FFF_FFFF_FFFF_FFFFL
        val msb = (unixMs shl 16) or (0x7L shl 12) or randA
        val lsb = (0b10L shl 62) or randB
        return UUID(msb, lsb)
    }

    fun unixMs(uuid: UUID): Long {
        require(uuid.version() == 7) { "not a UUIDv7" }
        return uuid.mostSignificantBits ushr 16
    }
}
