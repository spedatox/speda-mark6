// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.spedatox.atomixwear.health

import android.os.SystemClock
import android.util.Log
import androidx.health.services.client.PassiveListenerService
import androidx.health.services.client.data.DataPointContainer
import androidx.health.services.client.data.DataType
import androidx.health.services.client.data.IntervalDataPoint
import androidx.health.services.client.data.SampleDataPoint
import com.spedatox.atomixwear.AtomixWear
import com.spedatox.atomixwear.data.HealthSampleDto
import com.spedatox.atomixwear.data.Metric
import com.spedatox.atomixwear.sync.SyncScheduler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

/**
 * Where the watch's own sensor data arrives. Health Services wakes the process
 * and hands over a batch; this maps it to the wire format and persists it.
 *
 * **Persist first, upload second.** This callback frequently runs with no
 * network — in Doze, off-wrist, mid-flight. Writing to [SampleQueue] and letting
 * a worker deal with transport is what makes a dead radio cost a delay instead
 * of a lost reading.
 */
class BiometricListenerService : PassiveListenerService() {

    // The service is short-lived and system-managed, so work is launched on an
    // application-scoped coroutine rather than a scope tied to this instance —
    // the queue write must outlive the callback returning.
    private val scope: CoroutineScope
        get() = (application as AtomixWear).appScope

    override fun onNewDataPointsReceived(dataPoints: DataPointContainer) {
        val bootInstant = bootInstant()
        val samples = buildList {
            addAll(sampled(dataPoints, DataType.HEART_RATE_BPM, Metric.HeartRate, bootInstant))
            addAll(sampled(dataPoints, DataType.RESTING_HEART_RATE, Metric.RestingHeartRate, bootInstant))
            addAll(interval(dataPoints, DataType.STEPS, Metric.Steps, bootInstant) { it.toDouble() })
            addAll(interval(dataPoints, DataType.DISTANCE, Metric.Distance, bootInstant) { it })
        }
        if (samples.isEmpty()) return

        Log.i(TAG, "received ${samples.size} samples")
        val app = application as AtomixWear
        scope.launch(Dispatchers.IO) {
            app.queue.offer(samples)
            // Opportunistic, not urgent: this only asks WorkManager to upload
            // when its constraints are already satisfiable. The trickle and the
            // FCM wake remain the scheduled paths (§3.3).
            SyncScheduler.requestOpportunisticSync(applicationContext)
        }
    }

    /** Instantaneous readings — one value at one moment. */
    private fun <T : Number> sampled(
        container: DataPointContainer,
        type: DataType<T, SampleDataPoint<T>>,
        metric: Metric,
        bootInstant: Instant,
    ): List<HealthSampleDto> = container.getData(type).mapNotNull { point ->
        runCatching {
            val at = point.getTimeInstant(bootInstant)
            dto(metric, at, at, point.value.toDouble())
        }.getOrNull()
    }

    /**
     * Accumulating readings — a delta over a window. The window is preserved
     * because the backend files a sample under the LOCAL day of its start, and
     * a step count spanning midnight belongs to the day it began in.
     */
    private fun <T : Number> interval(
        container: DataPointContainer,
        type: DataType<T, IntervalDataPoint<T>>,
        metric: Metric,
        bootInstant: Instant,
        value: (T) -> Double,
    ): List<HealthSampleDto> = container.getData(type).mapNotNull { point ->
        runCatching {
            dto(
                metric,
                point.getStartInstant(bootInstant),
                point.getEndInstant(bootInstant),
                value(point.value),
            )
        }.getOrNull()
    }

    /**
     * Build a wire sample stamped with the device's zone offset for that
     * instant.
     *
     * The offset is not decoration. `services/health.py` derives the owner's
     * local day from it before storing UTC; a bare instant is read as UTC, and a
     * 00:30+03:00 reading is 21:30 UTC on the previous day. Health Services
     * gives us instants with no zone at all, so the device's offset *at that
     * instant* is the closest honest answer — and using the offset for the
     * reading's own moment rather than for "now" is what keeps a batch that
     * spans a DST change from misfiling half of itself.
     */
    private fun dto(metric: Metric, start: Instant, end: Instant, value: Double): HealthSampleDto {
        val zone = ZoneId.systemDefault()
        return HealthSampleDto(
            metric = metric.wire,
            start = iso(start, zone.rules.getOffset(start)),
            end = iso(end, zone.rules.getOffset(end)),
            value = value,
            unit = metric.unit,
        )
    }

    private fun iso(instant: Instant, offset: ZoneOffset): String =
        OffsetDateTime.ofInstant(instant, offset).format(DateTimeFormatter.ISO_OFFSET_DATE_TIME)

    /**
     * Health Services timestamps data points as a duration since boot, so
     * converting to wall-clock needs the instant the device booted.
     *
     * Recomputed per delivery rather than cached: `elapsedRealtime` and the
     * wall clock drift apart, and a value cached at install time would skew
     * every later reading. Re-deriving it costs nothing and stays correct
     * across a clock adjustment.
     */
    private fun bootInstant(): Instant =
        Instant.now().minusMillis(SystemClock.elapsedRealtime())

    private companion object { const val TAG = "BiometricListener" }
}
