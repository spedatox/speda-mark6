// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.spedatox.atomixwear.sync

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.OutOfQuotaPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import java.util.concurrent.TimeUnit

/**
 * Every path by which samples reach Igor (docs/ATOMIX_WEAR.md §3.3).
 *
 * Three triggers, in descending order of urgency:
 *
 * 1. **[syncNow]** — an FCM wake. Someone is holding a turn open on the other
 *    end waiting for an answer about the present.
 * 2. **[requestOpportunisticSync]** — new data just landed and the network
 *    happens to be up. Costs nothing when it is not.
 * 3. **[ensureScheduled]** — the trickle, so a watch that is never pushed and
 *    never opportunistically online still drains.
 */
object SyncScheduler {

    fun ensureScheduled(context: Context) {
        val trickle = PeriodicWorkRequestBuilder<HealthSyncWorker>(
            TRICKLE_INTERVAL_MINUTES, TimeUnit.MINUTES,
        )
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .setRequiresBatteryNotLow(true)
                    .build(),
            )
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 10, TimeUnit.MINUTES)
            .build()

        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            TRICKLE_WORK,
            // KEEP, not UPDATE: re-enqueueing on every app start would reset the
            // period and turn a scheduled trickle into a sync on every launch.
            ExistingPeriodicWorkPolicy.KEEP,
            trickle,
        )

        // The demand poll — the fallback for a watch that missed its push.
        // Battery-not-low is deliberately NOT required: a briefing that refuses
        // to run is a worse outcome than one GET on a low battery. Same
        // reasoning as Speda GO's HealthDemandWorker.
        val demand = PeriodicWorkRequestBuilder<SyncDemandWorker>(
            DEMAND_INTERVAL_MINUTES, TimeUnit.MINUTES,
        )
            .setConstraints(
                Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build(),
            )
            .build()

        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            DEMAND_WORK,
            ExistingPeriodicWorkPolicy.KEEP,
            demand,
        )
    }

    /**
     * Upload immediately, out of band. What an FCM wake lands on.
     *
     * Expedited because Atomix is holding a turn open on the other end: a sync
     * that arrives after the briefing gave up waiting is worth the same as no
     * sync at all — see the dated post-mortem in `skills/health_data.py`.
     * `RUN_AS_NON_EXPEDITED_WORK_REQUEST` is the required fallback for a spent
     * quota; it still runs, just queued.
     *
     * REPLACE rather than KEEP: a second demand means the first one's data is
     * already stale, so the newer request should win rather than be dropped.
     */
    fun syncNow(context: Context) {
        val request = OneTimeWorkRequestBuilder<HealthSyncWorker>()
            .setConstraints(
                Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build(),
            )
            .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST)
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            NOW_WORK, ExistingWorkPolicy.REPLACE, request,
        )
    }

    /**
     * Nudge an upload after new data arrives, without expediting.
     *
     * KEEP rather than REPLACE here: passive deliveries can arrive in bursts,
     * and replacing on each one would keep cancelling a job that was about to
     * run. The first request drains whatever is in the queue by the time it
     * executes, including everything the burst added after it was enqueued.
     */
    fun requestOpportunisticSync(context: Context) {
        val request = OneTimeWorkRequestBuilder<HealthSyncWorker>()
            .setConstraints(
                Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build(),
            )
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            OPPORTUNISTIC_WORK, ExistingWorkPolicy.KEEP, request,
        )
    }

    fun cancelAll(context: Context) = with(WorkManager.getInstance(context)) {
        cancelUniqueWork(TRICKLE_WORK)
        cancelUniqueWork(DEMAND_WORK)
        cancelUniqueWork(NOW_WORK)
        cancelUniqueWork(OPPORTUNISTIC_WORK)
    }

    const val TRICKLE_WORK = "atomix-wear-trickle"
    const val DEMAND_WORK = "atomix-wear-demand"
    const val NOW_WORK = "atomix-wear-sync-now"
    const val OPPORTUNISTIC_WORK = "atomix-wear-opportunistic"

    /**
     * Thirty minutes against Speda GO's four hours — the watch has the data at
     * the moment it is measured, so there is nothing to wait for.
     *
     * TODO(Phase 4): both of these belong in Igor's config schema and its
     * settings surface per CLAUDE.md, delivered to the watch rather than
     * compiled into it. They are constants here only until Phase 4 wires the
     * config path; that is a scoped commitment, not an accepted default.
     */
    const val TRICKLE_INTERVAL_MINUTES = 30L

    /** WorkManager's floor for periodic work. Anything smaller is silently
     *  clamped to this, so state it rather than pretend to a faster cadence. */
    const val DEMAND_INTERVAL_MINUTES = 15L
}
