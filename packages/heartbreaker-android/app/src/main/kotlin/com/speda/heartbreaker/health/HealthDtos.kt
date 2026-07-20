package com.speda.heartbreaker.health

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/**
 * Wire DTOs for POST /health/ingest — the mirror of `app/schemas/health.py`.
 * See docs/ATOMIX_HEALTH_SYNC.md §3.1.
 */
@Serializable
data class HealthSampleDto(
    val metric: String,
    /**
     * ISO-8601 WITH the device's UTC offset ("2026-07-18T23:41:00+03:00").
     * The offset is load-bearing, not decoration: the backend derives the
     * owner's local calendar day from it before storing UTC. Send a bare
     * instant and a 00:30 bedtime files itself under the previous day.
     */
    val start: String,
    val end: String,
    val value: Double,
    val unit: String = "",
    val detail: JsonObject? = null,
    /** The writing app as Health Connect reports it; part of the identity key. */
    val origin: String = "",
)

@Serializable
data class HealthIngestRequest(
    val device: String = "",
    val samples: List<HealthSampleDto>,
)

@Serializable
data class HealthIngestResult(
    val accepted: Int = 0,
    val duplicates: Int = 0,
    @SerialName("days_rolled") val daysRolled: Int = 0,
)

@Serializable
data class HealthStatusDto(
    val samples: Int = 0,
    @SerialName("per_metric") val perMetric: Map<String, Int> = emptyMap(),
    @SerialName("last_ingest") val lastIngest: String? = null,
    @SerialName("first_day") val firstDay: String? = null,
    @SerialName("last_day") val lastDay: String? = null,
)

/**
 * The record types the owner can toggle, in display order. `key` is the
 * DataStore/wire identifier and must match the backend's metric vocabulary in
 * `services/health.py`; adding a type here plus a permission in the manifest is
 * the whole job — the backend schema is metric-generic.
 */
enum class HealthType(val key: String, val label: String, val defaultOn: Boolean) {
    Steps("steps", "Steps & distance", true),
    Sleep("sleep_session", "Sleep", true),
    HeartRate("heart_rate", "Heart rate", true),
    Exercise("exercise_session", "Exercise", true),
    Weight("weight", "Weight & body comp", true),
    OxygenSaturation("oxygen_saturation", "Blood oxygen", false),
    ;

    companion object {
        fun fromKey(key: String): HealthType? = entries.firstOrNull { it.key == key }
        val defaults: Set<String> get() = entries.filter { it.defaultOn }.map { it.key }.toSet()
    }
}
