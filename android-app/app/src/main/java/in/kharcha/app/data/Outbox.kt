package `in`.kharcha.app.data

import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Index
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.RoomDatabase
import kotlinx.coroutines.flow.Flow

object OutboxStatus {
    const val PENDING = "PENDING"
    const val SENT = "SENT"
    const val REJECTED = "REJECTED"
}

/** A redacted event waiting for (or done with) upload. Raw text is never stored. */
@Entity(
    tableName = "outbox_events",
    indices = [Index(value = ["dedupeKey"], unique = true), Index(value = ["status"])],
)
data class OutboxEvent(
    @PrimaryKey val eventId: String,
    val type: String,
    val sourceApp: String?,
    val sender: String?,
    val title: String?,
    val text: String?,
    val postedAtMs: Long,
    val capturedAtMs: Long,
    val dedupeKey: String,
    val status: String,
    val attempts: Int = 0,
    val lastError: String? = null,
)

@Dao
interface OutboxDao {
    /** Returns -1 when an event with the same dedupe key already exists. */
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(event: OutboxEvent): Long

    @Query("SELECT * FROM outbox_events WHERE status = 'PENDING' ORDER BY postedAtMs LIMIT :limit")
    suspend fun pending(limit: Int): List<OutboxEvent>

    @Query("UPDATE outbox_events SET status = :status, lastError = :reason WHERE eventId = :eventId")
    suspend fun setStatus(eventId: String, status: String, reason: String?)

    @Query("UPDATE outbox_events SET attempts = attempts + 1, lastError = :error WHERE eventId IN (:ids)")
    suspend fun recordAttempt(ids: List<String>, error: String?)

    @Query("SELECT * FROM outbox_events ORDER BY postedAtMs DESC LIMIT :limit")
    fun observeRecent(limit: Int = 200): Flow<List<OutboxEvent>>

    @Query("SELECT COUNT(*) FROM outbox_events WHERE status = 'PENDING'")
    fun observePendingCount(): Flow<Int>

    @Query("SELECT * FROM outbox_events WHERE eventId = :eventId")
    suspend fun get(eventId: String): OutboxEvent?

    /** Undo before upload. Returns 1 if the event was still waiting and is now gone. */
    @Query("DELETE FROM outbox_events WHERE eventId = :eventId AND status = 'PENDING'")
    suspend fun deleteIfPending(eventId: String): Int
}

@Database(entities = [OutboxEvent::class], version = 1, exportSchema = true)
abstract class KharchaDatabase : RoomDatabase() {
    abstract fun outbox(): OutboxDao
}
