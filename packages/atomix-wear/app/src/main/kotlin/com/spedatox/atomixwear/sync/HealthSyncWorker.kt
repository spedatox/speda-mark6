// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.spedatox.atomixwear.sync

import android.content.Context
import android.os.Build
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.spedatox.atomixwear.AtomixWear
import com.spedatox.atomixwear.data.SampleQueue

/**
 * Drains [SampleQueue] to Igor.
 *
 * A plain `CoroutineWorker` — no foreground notification. This is background
 * upload the owner should never be told about; a persistent notification on a
 * watch is a real cost for zero information.
 *
 * ## The retry contract
 *
 * Rows leave the queue only after Igor returns 2xx, and never before. Combined
 * with the server's `(metric, start_ts, origin)` unique constraint, that gives
 * the property the whole pipeline rests on: **a failed upload costs a re-send,
 * never a reading.** A batch that was accepted but whose acknowledgement was
 * lost to a crash is simply sent again and collapses server-side.
 *
 * Returning [Result.retry] rather than [Result.failure] on a transport error is
 * deliberate — WorkManager's exponential backoff is exactly the right behaviour
 * for a watch that is offline for an hour, and `failure` would abandon the data.
 */
class HealthSyncWorker(
    context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val app = applicationContext as AtomixWear
        if (!app.igor.isConfigured) {
            // Not an error: an unconfigured build collects and queues, waiting
            // for a build that knows where Igor lives. Retrying would burn
            // backoff slots forever, so stop cleanly and let the next trigger
            // re-evaluate.
            Log.i(TAG, "Igor not configured — collected samples stay queued")
            return Result.success()
        }

        var uploaded = 0
        var duplicates = 0
        while (true) {
            val batch = app.queue.peek(SampleQueue.BATCH_SIZE)
            if (batch.isEmpty()) break

            val outcome = app.igor.ingestHealth(deviceName(), batch)
            val response = outcome.getOrElse { error ->
                Log.w(TAG, "ingest failed after $uploaded uploaded: ${error.message}")
                // Anything already acknowledged stays acknowledged; the rest is
                // still on disk and will go next attempt.
                return if (uploaded > 0) Result.success() else Result.retry()
            }

            // Acknowledge exactly what we sent, not what the server counted.
            // `accepted` excludes duplicates, and dropping only the accepted
            // count would leave the duplicate rows in the queue to be re-sent
            // forever — a queue that never drains and a sync that never ends.
            app.queue.acknowledge(batch.size)
            uploaded += response.accepted
            duplicates += response.duplicates
        }

        if (uploaded > 0 || duplicates > 0) {
            Log.i(TAG, "sync complete: accepted=$uploaded duplicates=$duplicates")
        }
        return Result.success()
    }

    private fun deviceName(): String =
        listOf(Build.MANUFACTURER, Build.MODEL)
            .filter { it.isNotBlank() }
            .joinToString(" ")
            .take(96)

    private companion object { const val TAG = "HealthSyncWorker" }
}
