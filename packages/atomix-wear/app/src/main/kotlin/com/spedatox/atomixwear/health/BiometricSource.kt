// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.spedatox.atomixwear.health

import android.content.Context
import android.content.pm.PackageManager
import android.util.Log
import androidx.core.content.ContextCompat
import androidx.health.services.client.HealthServices
import androidx.health.services.client.PassiveMonitoringClient
import androidx.health.services.client.data.DataType
import androidx.health.services.client.data.PassiveListenerConfig
import kotlinx.coroutines.guava.await

/**
 * The wrist's own sensors, via Health Services passive monitoring.
 *
 * This is the file that deletes three hops from the pipeline
 * (docs/ATOMIX_WEAR.md §1.1). Everything above it speaks
 * [com.spedatox.atomixwear.data.HealthSampleDto]; this is the only place that
 * knows Health Services' API shape, exactly as `HealthConnectSource.kt` is the
 * only place in Speda GO that knows Health Connect's.
 *
 * ## Why passive monitoring rather than ExerciseClient
 *
 * `ExerciseClient` is for an active, user-started workout: it holds sensors at
 * high duty cycle and expects a foreground service. That is the wrong shape for
 * a background biometric feed the owner never explicitly starts — it would cost
 * visible battery all day to collect data we are happy to receive in batches.
 *
 * `PassiveMonitoringClient` batches through Doze, survives process death, and
 * re-delivers to a declared service. The latency it adds is minutes, against the
 * tens of minutes to hours the Samsung path costs — see §1.1.
 */
class BiometricSource(private val context: Context) {

    private val client: PassiveMonitoringClient by lazy {
        HealthServices.getClient(context).passiveMonitoringClient
    }

    /**
     * The types this app would like, mapped to what the backend stores.
     *
     * ⚠ UNVERIFIED — docs/ATOMIX_WEAR.md §8.2. This is the *requested* catalog,
     * not the supported one. Which of these a given Galaxy Watch actually
     * exposes passively must be read from [supported] at runtime and never
     * assumed: `RESTING_HEART_RATE` in particular may be a Samsung-derived
     * value with no Health Services passive type behind it.
     *
     * [register] intersects this with [supported] before asking for anything,
     * so an unsupported type degrades to "not collected" rather than failing
     * the whole registration.
     *
     * ## STEPS, not STEPS_DAILY — this distinction is load-bearing
     *
     * `STEPS_DAILY` and `DISTANCE_DAILY` report a running total for the day so
     * far. `services/health.py` aggregates cumulative metrics by SUMMING every
     * sample in a day, so feeding it running totals would add the whole day's
     * count again on every delivery — 8,000 steps arriving twelve times becomes
     * a six-figure day. The interval types report the delta since the previous
     * delivery, which is what sums correctly into the rollup.
     *
     * The daily variants are the intuitive choice and the wrong one. Do not
     * "simplify" this back.
     */
    private val wanted: Set<DataType<*, *>> = setOf(
        DataType.HEART_RATE_BPM,
        DataType.STEPS,
        DataType.DISTANCE,
        DataType.RESTING_HEART_RATE,
    )

    /** What this watch will actually deliver passively. Empty on failure —
     *  a watch that cannot answer is treated as supporting nothing, which
     *  disables collection rather than crashing it. */
    suspend fun supported(): Set<DataType<*, *>> = try {
        client.getCapabilitiesAsync().await().supportedDataTypesPassiveMonitoring
    } catch (e: Exception) {
        Log.w(TAG, "capability query failed: ${e.message}")
        emptySet()
    }

    /**
     * True once BODY_SENSORS has been granted. Background collection
     * additionally needs BODY_SENSORS_BACKGROUND, which on API 33+ must be
     * requested *separately and afterwards* — the system rejects a combined
     * request, and without it passive delivery silently stops the moment the
     * app leaves the foreground.
     */
    fun hasSensorPermission(): Boolean =
        ContextCompat.checkSelfPermission(context, android.Manifest.permission.BODY_SENSORS) ==
            PackageManager.PERMISSION_GRANTED

    fun hasBackgroundSensorPermission(): Boolean =
        ContextCompat.checkSelfPermission(
            context, android.Manifest.permission.BODY_SENSORS_BACKGROUND,
        ) == PackageManager.PERMISSION_GRANTED

    /**
     * Start (or re-point) passive collection at [BiometricListenerService].
     *
     * Returns the types actually registered, which is the intersection of what
     * we want and what the watch supports — the caller shows this to the owner
     * rather than claiming to collect something it cannot.
     *
     * Safe to call repeatedly: re-registering replaces the existing config, so
     * this runs on boot, on app start and after a permission grant without
     * needing to track whether it already ran.
     */
    suspend fun register(): Set<DataType<*, *>> {
        if (!hasSensorPermission()) {
            Log.i(TAG, "BODY_SENSORS not granted — not registering")
            return emptySet()
        }
        val types = wanted intersect supported()
        if (types.isEmpty()) {
            Log.w(TAG, "watch supports none of the requested passive types")
            return emptySet()
        }
        return try {
            client.setPassiveListenerServiceAsync(
                BiometricListenerService::class.java,
                PassiveListenerConfig.builder().setDataTypes(types).build(),
            ).await()
            Log.i(TAG, "passive collection registered for ${types.map { it.name }}")
            types
        } catch (e: Exception) {
            Log.e(TAG, "passive registration failed: ${e.message}")
            emptySet()
        }
    }

    suspend fun unregister() {
        runCatching { client.clearPassiveListenerServiceAsync().await() }
            .onFailure { Log.w(TAG, "unregister failed: ${it.message}") }
    }

    private companion object { const val TAG = "BiometricSource" }
}
