// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.spedatox.atomixwear.sync

import android.content.Context
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.spedatox.atomixwear.AtomixWear

/**
 * Asks Igor "are you waiting on me?" and syncs if so.
 *
 * This is the fallback for the FCM path, not the primary mechanism — the whole
 * point of a watch client is that it *can* be pushed (§1.2). It exists because
 * push is not guaranteed: a watch that was off-wrist, in flight mode, or whose
 * message was dropped never learns a demand was raised, and would otherwise sit
 * on fresh data while a briefing reported a link outage.
 *
 * Fifteen minutes is WorkManager's floor, so this is a safety net measured in
 * minutes rather than the seconds the push path delivers.
 */
class SyncDemandWorker(
    context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val app = applicationContext as AtomixWear
        if (!app.igor.isConfigured) return Result.success()

        val demand = app.igor.syncDemand().getOrElse {
            // A failed poll is ordinary — the watch is offline often. Retry
            // rather than fail so backoff handles a flaky link.
            return Result.retry()
        }

        if (demand.outstanding) {
            Log.i(TAG, "Igor is waiting on a sync (${demand.reason}) — uploading now")
            SyncScheduler.syncNow(applicationContext)
        }
        return Result.success()
    }

    private companion object { const val TAG = "SyncDemandWorker" }
}
