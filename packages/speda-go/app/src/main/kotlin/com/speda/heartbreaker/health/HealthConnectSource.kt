package com.speda.heartbreaker.health

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.changes.UpsertionChange
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.BodyFatRecord
import androidx.health.connect.client.records.DistanceRecord
import androidx.health.connect.client.records.ExerciseSessionRecord
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.OxygenSaturationRecord
import androidx.health.connect.client.records.Record
import androidx.health.connect.client.records.RestingHeartRateRecord
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.records.WeightRecord
import androidx.health.connect.client.request.ChangesTokenRequest
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import kotlin.reflect.KClass

/**
 * Thin wrapper over [HealthConnectClient]: availability, permissions, time-range
 * reads and the differential Changes API. The ONLY file that knows Health
 * Connect's API shape — everything above it speaks [HealthSampleDto].
 *
 * Deliberately read-only (docs/ATOMIX_HEALTH_SYNC.md §3.4): v1 reads the owner's
 * biometrics and never writes anything back.
 */
class HealthConnectSource(private val context: Context) {

    enum class Availability { AVAILABLE, NOT_INSTALLED, UNSUPPORTED }

    /** Differential read outcome. [expired] means the token aged out (Health
     *  Connect drops them after ~30 idle days) and the caller must re-backfill. */
    data class Changes(
        val samples: List<HealthSampleDto>,
        val nextToken: String?,
        val expired: Boolean = false,
    )

    val availability: Availability
        get() = when (HealthConnectClient.getSdkStatus(context)) {
            HealthConnectClient.SDK_AVAILABLE -> Availability.AVAILABLE
            HealthConnectClient.SDK_UNAVAILABLE_PROVIDER_UPDATE_REQUIRED -> Availability.NOT_INSTALLED
            else -> Availability.UNSUPPORTED
        }

    private val client: HealthConnectClient? by lazy {
        runCatching { HealthConnectClient.getOrCreate(context) }.getOrNull()
    }

    // ── Permissions ──────────────────────────────────────────────────────────

    /** Read permissions for one toggled type. A type maps to one or more record
     *  classes — "Steps & distance" is one owner-facing choice, two records. */
    fun permissionsFor(type: HealthType): Set<String> = recordsFor(type)
        .map { HealthPermission.getReadPermission(it) }
        .toSet()

    fun permissionsFor(types: Set<HealthType>): Set<String> =
        types.flatMap { permissionsFor(it) }.toSet()

    /** Which of [types] the OS has actually granted. The system sheet is the
     *  source of truth — the checkboxes reflect it, never the other way round. */
    suspend fun grantedTypes(types: Set<HealthType>): Set<HealthType> {
        val c = client ?: return emptySet()
        val granted = runCatching { c.permissionController.getGrantedPermissions() }
            .getOrDefault(emptySet())
        return types.filter { granted.containsAll(permissionsFor(it)) }.toSet()
    }

    private fun recordsFor(type: HealthType): List<KClass<out Record>> = when (type) {
        HealthType.Steps -> listOf(StepsRecord::class, DistanceRecord::class)
        HealthType.Sleep -> listOf(SleepSessionRecord::class)
        HealthType.HeartRate -> listOf(HeartRateRecord::class, RestingHeartRateRecord::class)
        HealthType.Exercise -> listOf(ExerciseSessionRecord::class)
        HealthType.Weight -> listOf(WeightRecord::class, BodyFatRecord::class)
        HealthType.OxygenSaturation -> listOf(OxygenSaturationRecord::class)
    }

    // ── Reads ────────────────────────────────────────────────────────────────

    /**
     * Every record for [types] in [start, end), flattened to wire DTOs. Used for
     * the first-run backfill and whenever the changes token is unusable.
     *
     * A failure on one record class is swallowed: one revoked permission should
     * degrade that metric, not abort the whole sync.
     */
    suspend fun readSamples(types: Set<HealthType>, start: Instant, end: Instant): List<HealthSampleDto> {
        val c = client ?: return emptyList()
        val range = TimeRangeFilter.between(start, end)
        val out = mutableListOf<HealthSampleDto>()
        for (klass in types.flatMap { recordsFor(it) }.distinct()) {
            runCatching {
                var token: String? = null
                do {
                    val page = c.readRecords(
                        ReadRecordsRequest(recordType = klass, timeRangeFilter = range, pageToken = token),
                    )
                    page.records.forEach { out += mapRecord(it) }
                    token = page.pageToken
                } while (token != null)
            }
        }
        return out
    }

    /** A fresh differential token covering exactly [types]. */
    suspend fun changesToken(types: Set<HealthType>): String? {
        val c = client ?: return null
        val records = types.flatMap { recordsFor(it) }.distinct().toSet()
        if (records.isEmpty()) return null
        return runCatching { c.getChangesToken(ChangesTokenRequest(recordTypes = records)) }.getOrNull()
    }

    /**
     * Records changed since [token]. Deletions are ignored on purpose: v1 never
     * removes a sample the owner's collector retracted, because the backend's
     * only delete path is the deliberate WIPE — a silent server-side delete
     * driven by a background sync is not something we want happening quietly.
     */
    suspend fun changesSince(token: String): Changes {
        val c = client ?: return Changes(emptyList(), token)
        val out = mutableListOf<HealthSampleDto>()
        var cursor = token
        return runCatching {
            while (true) {
                val response = c.getChanges(cursor)
                if (response.changesTokenExpired) return Changes(emptyList(), null, expired = true)
                response.changes.filterIsInstance<UpsertionChange>().forEach { out += mapRecord(it.record) }
                cursor = response.nextChangesToken
                if (!response.hasMore) break
            }
            Changes(out, cursor)
        }.getOrElse { Changes(emptyList(), token) }
    }

    // ── Record → wire ────────────────────────────────────────────────────────

    /** One Health Connect record → zero or more wire samples. Shared by the
     *  backfill and the differential path so they can never disagree. */
    private fun mapRecord(record: Record): List<HealthSampleDto> {
        val origin = record.metadata.dataOrigin.packageName
        return when (record) {
            is StepsRecord -> listOf(
                sample("steps", record.startTime, record.startZoneOffset, record.endTime, record.count.toDouble(), "count", origin),
            )
            is DistanceRecord -> listOf(
                sample("distance", record.startTime, record.startZoneOffset, record.endTime, record.distance.inMeters, "m", origin),
            )
            is SleepSessionRecord -> listOf(
                sample(
                    "sleep_session", record.startTime, record.startZoneOffset, record.endTime,
                    minutesBetween(record.startTime, record.endTime), "min", origin,
                    detail = sleepStages(record),
                ),
            )
            // One HeartRateRecord holds many beat samples; each is its own
            // reading with its own timestamp, which is also its identity key.
            is HeartRateRecord -> record.samples.map { s ->
                sample("heart_rate", s.time, record.startZoneOffset, s.time, s.beatsPerMinute.toDouble(), "bpm", origin)
            }
            is RestingHeartRateRecord -> listOf(
                sample("resting_heart_rate", record.time, record.zoneOffset, record.time, record.beatsPerMinute.toDouble(), "bpm", origin),
            )
            is ExerciseSessionRecord -> listOf(
                sample(
                    "exercise_session", record.startTime, record.startZoneOffset, record.endTime,
                    minutesBetween(record.startTime, record.endTime), "min", origin,
                    detail = buildJsonObject {
                        put("type", JsonPrimitive(record.exerciseType))
                        record.title?.let { put("title", JsonPrimitive(it)) }
                    },
                ),
            )
            is WeightRecord -> listOf(
                sample("weight", record.time, record.zoneOffset, record.time, record.weight.inKilograms, "kg", origin),
            )
            is BodyFatRecord -> listOf(
                sample("body_fat", record.time, record.zoneOffset, record.time, record.percentage.value, "%", origin),
            )
            is OxygenSaturationRecord -> listOf(
                sample("oxygen_saturation", record.time, record.zoneOffset, record.time, record.percentage.value, "%", origin),
            )
            else -> emptyList()
        }
    }

    private fun sleepStages(r: SleepSessionRecord): JsonObject {
        val totals = mutableMapOf<String, Double>()
        for (stage in r.stages) {
            val name = when (stage.stage) {
                SleepSessionRecord.STAGE_TYPE_DEEP -> "deep"
                SleepSessionRecord.STAGE_TYPE_REM -> "rem"
                SleepSessionRecord.STAGE_TYPE_LIGHT -> "light"
                SleepSessionRecord.STAGE_TYPE_AWAKE,
                SleepSessionRecord.STAGE_TYPE_AWAKE_IN_BED,
                -> "awake"
                else -> continue   // UNKNOWN / OUT_OF_BED is noise, not signal
            }
            totals[name] = (totals[name] ?: 0.0) + minutesBetween(stage.startTime, stage.endTime)
        }
        return buildJsonObject {
            if (totals.isNotEmpty()) {
                put("stages", buildJsonObject { totals.forEach { (k, v) -> put(k, JsonPrimitive(v)) } })
            }
        }
    }

    /**
     * Build a wire sample, stamping the ZONE OFFSET the record was recorded in.
     * Health Connect's per-record offset is nullable; when it's absent we use the
     * device's offset for that instant, which is the closest honest guess. The
     * alternative — sending a bare UTC instant — silently misfiles anything
     * recorded near midnight into the wrong day on the backend.
     */
    private fun sample(
        metric: String,
        start: Instant,
        offset: ZoneOffset?,
        end: Instant,
        value: Double,
        unit: String,
        origin: String,
        detail: JsonObject? = null,
    ): HealthSampleDto {
        val zone = offset ?: ZoneId.systemDefault().rules.getOffset(start)
        return HealthSampleDto(
            metric = metric,
            start = iso(start, zone),
            end = iso(end, zone),
            value = value,
            unit = unit,
            detail = detail?.takeIf { it.isNotEmpty() },
            origin = origin,
        )
    }

    private fun iso(instant: Instant, offset: ZoneOffset): String =
        OffsetDateTime.ofInstant(instant, offset).format(DateTimeFormatter.ISO_OFFSET_DATE_TIME)

    private fun minutesBetween(start: Instant, end: Instant): Double =
        (end.toEpochMilli() - start.toEpochMilli()).coerceAtLeast(0L) / 60_000.0

    companion object {
        const val HEALTH_CONNECT_PACKAGE = "com.google.android.apps.healthdata"
        const val PLAY_LISTING = "https://play.google.com/store/apps/details?id=$HEALTH_CONNECT_PACKAGE"
        /** Health Connect's own settings screen (where grants are revoked). */
        const val ACTION_HEALTH_CONNECT_SETTINGS = "androidx.health.ACTION_HEALTH_CONNECT_SETTINGS"
    }
}
