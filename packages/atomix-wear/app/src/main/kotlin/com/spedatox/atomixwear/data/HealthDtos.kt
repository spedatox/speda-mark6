// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.spedatox.atomixwear.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/**
 * The health ingest wire format. Mirrors `igor/app/schemas/health.py`
 * (docs/ATOMIX_WEAR.md §3.2) and, deliberately, Speda GO's `HealthDtos.kt` —
 * the two clients speak the identical dialect to the identical endpoint, which
 * is what lets Phase 1 ship with no backend change at all.
 *
 * If the Python schema changes, both of these change with it. They are one
 * contract with three implementations.
 */
@Serializable
data class HealthSampleDto(
    val metric: String,
    /**
     * Offset-aware ISO-8601, e.g. "2026-09-04T23:41:00+03:00".
     *
     * The offset is load-bearing and dropping it is a real bug, not a
     * formatting nicety: `services/health.py` derives the owner's LOCAL day
     * from this offset before storing UTC. A bare instant is read as UTC, and a
     * 00:30+03:00 reading is 21:30 UTC the PREVIOUS day — which files it under
     * a day it does not belong to and quietly corrupts every daily rollup that
     * touches midnight.
     */
    val start: String,
    val end: String? = null,
    val value: Double,
    val unit: String = "",
    val detail: JsonObject? = null,
    /**
     * Part of the server's identity key `(metric, start_ts, origin)`. Always
     * [ORIGIN] for this app, which is precisely what keeps a watch-recorded walk
     * and a Samsung-recorded walk as two honest rows rather than one silently
     * overwriting the other while both pipes run (§8.4).
     */
    val origin: String = ORIGIN,
) {
    companion object {
        /** This application, as the server will record it. Changing this string
         *  orphans every sample already stored under the old one. */
        const val ORIGIN = "atomix-wear"
    }
}

@Serializable
data class HealthIngestRequest(
    val device: String = "",
    val samples: List<HealthSampleDto> = emptyList(),
)

@Serializable
data class HealthIngestResponse(
    val accepted: Int = 0,
    val duplicates: Int = 0,
)

/** Igor's answer to "are you waiting on a sync right now?" — `/health/sync-demand`. */
@Serializable
data class SyncDemandResponse(
    val outstanding: Boolean = false,
    val at: Long = 0,
    val reason: String = "",
)

@Serializable
data class DeviceRegisterRequest(
    val device: String,
    val fid: String,
    val token: String? = null,
)

/**
 * A metric this app knows how to collect. The `wire` value is the string the
 * backend stores and `skills/health_data.py` queries by, so these names are not
 * ours to choose freely — they must match `_KNOWN_METRICS` there exactly, or a
 * sample lands in the store under a name no query will ever match.
 */
enum class Metric(val wire: String, val unit: String) {
    HeartRate("heart_rate", "bpm"),
    RestingHeartRate("resting_heart_rate", "bpm"),
    Steps("steps", "count"),
    Distance("distance", "m"),
    ExerciseSession("exercise_session", "min"),
    ;

    @SerialName("wire")
    override fun toString(): String = wire
}
