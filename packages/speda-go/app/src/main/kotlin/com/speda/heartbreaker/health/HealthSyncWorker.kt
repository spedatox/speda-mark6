package com.speda.heartbreaker.health

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.speda.heartbreaker.HeartbreakerApp
import com.speda.heartbreaker.data.UplinkState
import com.speda.heartbreaker.domain.AppConfig
import kotlinx.coroutines.flow.first

/**
 * The ~4h trickle (docs/ATOMIX_HEALTH_SYNC.md §1.2). A plain CoroutineWorker —
 * no foreground service and no persistent notification, because this is a small
 * local read plus one HTTPS POST, not a stream.
 */
class HealthSyncWorker(
    context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val graph = (applicationContext as? HeartbreakerApp)?.graph ?: return Result.success()
        // Not set up yet (or the owner reset the uplink) — nothing to sync to.
        val state = graph.uplink.state.first()
        val uplink = (state as? UplinkState.Configured)?.uplink ?: return Result.success()
        val config = AppConfig(apiBase = uplink.apiBase, apiKey = uplink.apiKey, agentId = "atomix")

        return when (graph.healthSync.sync(config)) {
            is HealthSyncManager.Result.Failed ->
                // Retry with the configured backoff. The token was NOT advanced,
                // so the next attempt re-reads the same window — idempotent.
                if (runAttemptCount < MAX_ATTEMPTS) Result.retry() else Result.failure()
            // Disabled, unavailable or nothing granted: succeed quietly. The
            // owner turned it off or revoked a grant; that is not an error and
            // must not produce retry spam.
            HealthSyncManager.Result.NotPermitted -> Result.success()
            else -> Result.success()
        }
    }

    private companion object {
        const val MAX_ATTEMPTS = 5
    }
}
