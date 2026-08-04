package com.speda.heartbreaker.health

import android.content.Context
import android.os.Build
import android.util.Log
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.OutOfQuotaPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import com.speda.heartbreaker.data.IgorApi
import com.speda.heartbreaker.data.SettingsStore
import com.speda.heartbreaker.domain.AppConfig
import kotlinx.coroutines.flow.first
import java.time.Instant
import java.time.temporal.ChronoUnit
import java.util.concurrent.TimeUnit

/**
 * Owns the sync lifecycle: permission state, first-run backfill, differential
 * sync, token bookkeeping and the WorkManager schedule.
 * See docs/ATOMIX_HEALTH_SYNC.md §1.2 / §2.
 *
 * The invariant that makes this safe to retry: **the token is advanced only
 * after Igor has accepted the batch.** A failed POST leaves the token where it
 * was, so the next cycle re-reads the same window; the backend upserts on
 * (metric, start, origin), so the re-send collapses instead of duplicating.
 */
class HealthSyncManager(
    context: Context,
    private val settings: SettingsStore,
    private val api: IgorApi,
) {
    private val appContext = context.applicationContext
    val source = HealthConnectSource(appContext)

    /** What the Settings tab renders. */
    data class SyncState(
        val availability: HealthConnectSource.Availability,
        val enabled: Boolean,
        val selectedTypes: Set<HealthType>,
        val grantedTypes: Set<HealthType>,
        val lastSyncMillis: Long,
        val backfillDone: Boolean,
    )

    /** Outcome of one sync attempt, surfaced verbatim by SYNC NOW. */
    sealed interface Result {
        data class Synced(val read: Int, val accepted: Int, val duplicates: Int) : Result
        data object NothingNew : Result
        data class Failed(val message: String) : Result
        data object NotPermitted : Result
    }

    suspend fun state(): SyncState {
        val s = settings.settings.first()
        val selected = selectedTypes(s.healthTypes)
        return SyncState(
            availability = source.availability,
            enabled = s.healthEnabled,
            selectedTypes = selected,
            grantedTypes = if (source.availability == HealthConnectSource.Availability.AVAILABLE) {
                source.grantedTypes(selected)
            } else {
                emptySet()
            },
            lastSyncMillis = s.healthLastSync,
            backfillDone = s.healthBackfillDone,
        )
    }

    private fun selectedTypes(keys: Set<String>): Set<HealthType> =
        (keys.ifEmpty { HealthType.defaults }).mapNotNull { HealthType.fromKey(it) }.toSet()

    /**
     * One sync cycle. Backfills on first run (or after a token expiry), then
     * reads only what changed.
     */
    suspend fun sync(config: AppConfig): Result {
        val s = settings.settings.first()
        if (!s.healthEnabled) return Result.NotPermitted
        if (source.availability != HealthConnectSource.Availability.AVAILABLE) {
            return Result.Failed("Health Connect isn't available on this device.")
        }

        val selected = selectedTypes(s.healthTypes)
        // The OS is the truth. An unchecked box stops us reading a type; a
        // revoked grant means we CAN'T, and syncing the rest is still correct.
        val granted = source.grantedTypes(selected)
        if (granted.isEmpty()) return Result.NotPermitted

        val needsBackfill = !s.healthBackfillDone || s.healthChangesToken.isBlank()
        val samples: List<HealthSampleDto>
        var nextToken: String? = s.healthChangesToken.ifBlank { null }

        if (needsBackfill) {
            val end = Instant.now()
            val start = end.minus(BACKFILL_DAYS.toLong(), ChronoUnit.DAYS)
            // Take the token BEFORE reading: anything written during the
            // backfill then shows up as a change rather than being skipped.
            nextToken = source.changesToken(granted)
            samples = source.readSamples(granted, start, end)
        } else {
            val changes = source.changesSince(s.healthChangesToken)
            if (changes.expired) {
                // Health Connect drops idle tokens after ~30 days. Re-backfill
                // transparently rather than silently syncing nothing forever.
                settings.setHealthBackfillDone(false)
                settings.setHealthChangesToken("")
                return sync(config)
            }
            samples = changes.samples
            nextToken = changes.nextToken
        }

        if (samples.isEmpty()) {
            // Still a successful cycle — advance the token and the stamp, or a
            // quiet day would look like a broken pipe in the UI.
            nextToken?.let { settings.setHealthChangesToken(it) }
            settings.setHealthBackfillDone(true)
            settings.setHealthLastSync(System.currentTimeMillis())
            return Result.NothingNew
        }

        var totalAccepted = 0
        var totalDuplicates = 0

        for (chunk in samples.chunked(4000)) {
            val result = api.ingestHealth(config, deviceName(), chunk)
                ?: return Result.Failed("Igor didn't accept the batch — will retry.")
            totalAccepted += result.accepted
            totalDuplicates += result.duplicates
        }

        nextToken?.let { settings.setHealthChangesToken(it) }
        settings.setHealthBackfillDone(true)
        settings.setHealthLastSync(System.currentTimeMillis())
        Log.i(TAG, "health sync: read=${samples.size} accepted=$totalAccepted dup=$totalDuplicates")
        return Result.Synced(samples.size, totalAccepted, totalDuplicates)
    }

    /** DISCONNECT + WIPE — clears the server, then the local sync state. */
    suspend fun disconnectAndWipe(config: AppConfig): Boolean {
        val ok = api.wipeHealth(config)
        settings.clearHealthSyncState()
        cancelSchedule()
        return ok
    }

    // ── Schedule ─────────────────────────────────────────────────────────────

    fun ensureScheduled() {
        val request = PeriodicWorkRequestBuilder<HealthSyncWorker>(SYNC_INTERVAL_HOURS, TimeUnit.HOURS)
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(androidx.work.NetworkType.CONNECTED)
                    .setRequiresBatteryNotLow(true)
                    .build(),
            )
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.MINUTES)
            .build()
        WorkManager.getInstance(appContext).enqueueUniquePeriodicWork(
            WORK_NAME,
            // KEEP, not UPDATE: re-enqueueing on every app start would reset the
            // period and turn a 4h trickle into a sync on every launch.
            ExistingPeriodicWorkPolicy.KEEP,
            request,
        )

        // The demand check — see HealthDemandWorker. Separate schedule because
        // it answers a different question: the trickle asks "is it time yet",
        // this asks "is Igor waiting on me". Battery-not-low is deliberately
        // NOT required here; a briefing that refuses to run is a worse outcome
        // than one GET on a low battery.
        val demand = PeriodicWorkRequestBuilder<HealthDemandWorker>(
            DEMAND_INTERVAL_MINUTES, TimeUnit.MINUTES,
        )
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(androidx.work.NetworkType.CONNECTED)
                    .build(),
            )
            .build()
        WorkManager.getInstance(appContext).enqueueUniquePeriodicWork(
            DEMAND_WORK_NAME,
            ExistingPeriodicWorkPolicy.KEEP,
            demand,
        )
    }

    /**
     * Sync immediately, out of band with the trickle. What an FCM wake lands on.
     *
     * Expedited because Atomix is holding a turn open on the other end: a sync
     * that arrives after the briefing gave up waiting is worth the same as no
     * sync at all. RUN_AS_NON_EXPEDITED_WORK_REQUEST is the required fallback
     * for when the app's expedited quota is spent — it still runs, just queued.
     *
     * REPLACE rather than KEEP: a second demand means the first one's data is
     * already stale, so the newer request should win rather than be dropped.
     */
    fun syncNow() {
        val request = OneTimeWorkRequestBuilder<HealthSyncWorker>()
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(androidx.work.NetworkType.CONNECTED)
                    .build(),
            )
            .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST)
            .build()
        WorkManager.getInstance(appContext).enqueueUniqueWork(
            NOW_WORK_NAME,
            ExistingWorkPolicy.REPLACE,
            request,
        )
    }

    fun cancelSchedule() {
        WorkManager.getInstance(appContext).cancelUniqueWork(WORK_NAME)
        WorkManager.getInstance(appContext).cancelUniqueWork(DEMAND_WORK_NAME)
    }

    private fun deviceName(): String =
        listOf(Build.MANUFACTURER, Build.MODEL).filter { it.isNotBlank() }.joinToString(" ").take(96)

    companion object {
        const val WORK_NAME = "atomix-health-sync"
        const val DEMAND_WORK_NAME = "atomix-health-demand"
        const val NOW_WORK_NAME = "atomix-health-sync-now"
        const val BACKFILL_DAYS = 30
        const val SYNC_INTERVAL_HOURS = 4L
        // WorkManager's floor for periodic work. Anything smaller is silently
        // clamped to this, so state it rather than pretend to a faster cadence.
        const val DEMAND_INTERVAL_MINUTES = 15L
        private const val TAG = "HealthSync"
    }
}
