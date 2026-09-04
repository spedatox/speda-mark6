// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.spedatox.atomixwear.data

import android.util.Log
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.Json
import java.io.File

/**
 * The pending-upload buffer: everything collected off the wrist that Igor has
 * not yet acknowledged.
 *
 * This exists because collection and upload happen at completely different
 * rhythms. Health Services wakes the process to hand over a batch whenever it
 * feels like it — often with no network, often in Doze. Losing those readings
 * because the radio was down would reintroduce, on our own side, exactly the
 * data loss this application was built to eliminate.
 *
 * ## Why a whole-file rewrite
 *
 * Same reasoning as Ultron Wear's `AttendanceStore`, and the same conclusion for
 * a different volume. The queue holds at most [MAX_SAMPLES] rows and is drained
 * every sync cycle, so in steady state it is small enough to hold in memory and
 * rewrite whole. Room would add an annotation processor, a schema, a migration
 * surface and several hundred kilobytes of dex to index a collection that is
 * read exactly one way: oldest first, all of it.
 *
 * Writes are atomic (temp file, then rename) and serialised behind a mutex,
 * because the writer is a system-invoked service and the reader is a worker;
 * they genuinely can run at once.
 */
class SampleQueue(private val file: File) {

    private val mutex = Mutex()
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    /**
     * Add newly collected samples. Called from the Health Services callback,
     * which may be woken with no network — persisting here is the entire point.
     */
    suspend fun offer(samples: List<HealthSampleDto>) {
        if (samples.isEmpty()) return
        mutex.withLock {
            val current = readUnlocked().toMutableList()
            current += samples
            writeUnlocked(cap(current))
        }
    }

    /**
     * The oldest [limit] samples, left in place.
     *
     * Deliberately NOT a destructive read. The rows are removed only by
     * [acknowledge], after Igor has accepted them — so a crash or a failed POST
     * between peek and acknowledge costs a re-send, which the server's
     * `(metric, start_ts, origin)` constraint collapses, rather than costing the
     * readings themselves. Draining first and uploading second would make every
     * network failure a silent data loss.
     */
    suspend fun peek(limit: Int): List<HealthSampleDto> = mutex.withLock {
        readUnlocked().take(limit)
    }

    /** Drop the first [count] samples — call only after a 2xx from Igor. */
    suspend fun acknowledge(count: Int) {
        if (count <= 0) return
        mutex.withLock {
            val remaining = readUnlocked().drop(count)
            writeUnlocked(remaining)
        }
    }

    suspend fun size(): Int = mutex.withLock { readUnlocked().size }

    suspend fun clear() = mutex.withLock { writeUnlocked(emptyList()) }

    /**
     * Bound the queue so an extended outage cannot fill the watch's storage.
     *
     * Drops the OLDEST rows, which is the correct direction for this data: a
     * present-tense health query (`live=true` in `skills/health_data.py`) is
     * refused on staleness, so the newest readings are the ones that can still
     * answer a question. Week-old heart rate that never uploaded has already
     * failed at its job.
     *
     * The drop is logged loudly. Silently discarding the owner's biometrics is
     * not something that should ever happen without a trace to find later.
     */
    private fun cap(samples: List<HealthSampleDto>): List<HealthSampleDto> {
        if (samples.size <= MAX_SAMPLES) return samples
        val dropped = samples.size - MAX_SAMPLES
        Log.w(TAG, "queue over $MAX_SAMPLES — dropping $dropped oldest samples; upload has been failing")
        return samples.takeLast(MAX_SAMPLES)
    }

    private fun readUnlocked(): List<HealthSampleDto> {
        if (!file.exists()) return emptyList()
        return try {
            json.decodeFromString<List<HealthSampleDto>>(file.readText())
        } catch (e: Exception) {
            // Quarantine rather than discard, matching Ultron Wear's treatment
            // of a corrupt ledger. These are readings that exist nowhere else
            // yet — if parsing broke, that is a bug worth being able to inspect.
            Log.e(TAG, "queue unreadable, quarantining: ${e.message}")
            runCatching { file.copyTo(File(file.parentFile, "${file.name}.corrupt"), overwrite = true) }
            runCatching { file.delete() }
            emptyList()
        }
    }

    private fun writeUnlocked(samples: List<HealthSampleDto>) {
        val tmp = File(file.parentFile, "${file.name}.tmp")
        try {
            tmp.writeText(json.encodeToString(samples))
            if (!tmp.renameTo(file)) {
                // rename is the atomic step; without it a half-written file
                // could be read as the whole queue.
                tmp.copyTo(file, overwrite = true)
                tmp.delete()
            }
        } catch (e: Exception) {
            Log.e(TAG, "queue write failed: ${e.message}")
            runCatching { tmp.delete() }
        }
    }

    companion object {
        const val FILE_NAME = "pending-samples.json"

        /**
         * Roughly two days of passive collection at the cadence Health Services
         * actually delivers. Sized to survive a weekend with no network without
         * letting a permanently broken upload path grow without bound.
         *
         * TODO(Phase 4): this belongs in Igor's config schema and the settings
         * surface, per CLAUDE.md's no-hardcoded-values rule, delivered to the
         * watch alongside the sync interval.
         */
        const val MAX_SAMPLES = 20_000

        /** How many rows one POST carries. Speda GO chunks at 4000 against the
         *  same endpoint, whose schema caps a request at 5000. */
        const val BATCH_SIZE = 2_000

        private const val TAG = "SampleQueue"
    }
}
