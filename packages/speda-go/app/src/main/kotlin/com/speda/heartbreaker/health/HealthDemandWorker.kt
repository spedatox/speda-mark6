package com.speda.heartbreaker.health

import android.content.Context
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.speda.heartbreaker.HeartbreakerApp
import com.speda.heartbreaker.data.UplinkState
import com.speda.heartbreaker.domain.AppConfig
import kotlinx.coroutines.flow.first

/**
 * The other half of the ~4h trickle: a 15-minute check for whether Igor is
 * *asking* for a sync, rather than waiting for the next scheduled one.
 *
 * Atomix refuses to write a health briefing from stale data — a resting heart
 * rate from four days ago reported under today's date is a false statement
 * about the owner's body, not merely old news. When it needs biometrics that
 * describe the present it raises a demand server-side. SPEDA GO carries no
 * Firebase, so nothing can wake this app from outside; the demand is a note
 * left where the app will find it, and this is the app looking.
 *
 * Fifteen minutes is WorkManager's floor for periodic work, and the check is
 * sized to run at that rate forever: one small authenticated GET that almost
 * always answers "no" and does nothing. The actual sync only happens on a hit,
 * so the cost of the schedule is ~96 requests a day, not ~96 syncs.
 */
class HealthDemandWorker(
    context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val graph = (applicationContext as? HeartbreakerApp)?.graph ?: return Result.success()
        val state = graph.uplink.state.first()
        val uplink = (state as? UplinkState.Configured)?.uplink ?: return Result.success()
        val config = AppConfig(apiBase = uplink.apiBase, apiKey = uplink.apiKey, agentId = "atomix")

        if (!graph.api.healthSyncDemanded(config)) return Result.success()

        Log.i(TAG, "sync demanded by Igor — syncing now")
        return when (val outcome = graph.healthSync.sync(config)) {
            // Retrying here would race the 15-minute schedule for no gain: the
            // next tick re-reads the demand, which is still outstanding because
            // only a delivered batch clears it. Failing quietly keeps one dead
            // network moment from turning into a backoff chain.
            is HealthSyncManager.Result.Failed -> {
                Log.w(TAG, "demanded sync failed: ${outcome.message}")
                Result.success()
            }
            else -> Result.success()
        }
    }

    private companion object {
        const val TAG = "HealthDemand"
    }
}
