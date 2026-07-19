package com.speda.heartbreaker.domain

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull

/**
 * The ```map fence contract from prompts/core/06_visual_output.md.
 *
 *   { title?, center?{lat,lng}, zoom?, markers:[…], routes:[…], navigate?, autoNavigate? }
 *
 * Parsed leniently by hand (like [parseMapSpec]'s sibling [parseChartSpec]) rather
 * than with @Serializable: a half-streamed spec must fail softly to null, and the
 * route polylines are opaque encoded strings we decode separately ([decodePolyline]).
 */
data class LatLng(val lat: Double, val lng: Double)

data class MapMarker(
    val lat: Double,
    val lng: Double,
    val label: String? = null,
    /** origin | destination | poi | pin — drives glyph + colour. */
    val kind: String = "pin",
    val subtitle: String? = null,
)

data class MapRoute(
    val polyline: String,
    val label: String? = null,
    val durationMin: Int? = null,
    val noTrafficMin: Int? = null,
    val distanceKm: Double? = null,
    val mode: String = "drive",
    val primary: Boolean = false,
) {
    /** Congestion delay in minutes, when both timings are present and positive. */
    val trafficDelayMin: Int?
        get() = if (durationMin != null && noTrafficMin != null && durationMin > noTrafficMin)
            durationMin - noTrafficMin else null
}

data class MapNavigate(
    val lat: Double,
    val lng: Double,
    val mode: String = "drive",
    val label: String? = null,
)

data class MapSpec(
    val title: String? = null,
    val center: LatLng? = null,
    val zoom: Double? = null,
    val markers: List<MapMarker> = emptyList(),
    val routes: List<MapRoute> = emptyList(),
    val navigate: MapNavigate? = null,
    val autoNavigate: Boolean = false,
    val height: Int? = null,
) {
    /** The route drawn in the accent (marked primary, else the first). */
    val primaryRoute: MapRoute?
        get() = routes.firstOrNull { it.primary } ?: routes.firstOrNull()
}

private val LenientJson = Json { ignoreUnknownKeys = true; isLenient = true }

/** Returns null when the fence isn't valid JSON yet (still streaming, or malformed). */
fun parseMapSpec(raw: String): MapSpec? = runCatching {
    val o = LenientJson.parseToJsonElement(raw) as? JsonObject ?: return null

    val markers = (o["markers"] as? JsonArray).orEmptyArr().mapNotNull { el ->
        val m = el as? JsonObject ?: return@mapNotNull null
        val lat = m.dbl("lat") ?: return@mapNotNull null
        val lng = m.dbl("lng") ?: return@mapNotNull null
        MapMarker(
            lat = lat, lng = lng,
            label = m.str("label"),
            kind = (m.str("kind") ?: "pin").lowercase(),
            subtitle = m.str("subtitle"),
        )
    }

    val routes = (o["routes"] as? JsonArray).orEmptyArr().mapNotNull { el ->
        val r = el as? JsonObject ?: return@mapNotNull null
        val poly = r.str("polyline")?.takeIf { it.isNotBlank() } ?: return@mapNotNull null
        MapRoute(
            polyline = poly,
            label = r.str("label"),
            durationMin = r.intv("durationMin"),
            noTrafficMin = r.intv("noTrafficMin"),
            distanceKm = r.dbl("distanceKm"),
            mode = (r.str("mode") ?: "drive").lowercase(),
            primary = r.boolv("primary") ?: false,
        )
    }

    val center = (o["center"] as? JsonObject)?.let { c ->
        val lat = c.dbl("lat"); val lng = c.dbl("lng")
        if (lat != null && lng != null) LatLng(lat, lng) else null
    }

    val navigate = (o["navigate"] as? JsonObject)?.let { n ->
        val lat = n.dbl("lat"); val lng = n.dbl("lng")
        if (lat != null && lng != null)
            MapNavigate(lat, lng, (n.str("mode") ?: "drive").lowercase(), n.str("label")) else null
    }

    MapSpec(
        title = o.str("title"),
        center = center,
        zoom = o.dbl("zoom"),
        markers = markers,
        routes = routes,
        navigate = navigate,
        autoNavigate = o.boolv("autoNavigate") ?: false,
        height = o.intv("height"),
    ).takeIf { it.markers.isNotEmpty() || it.routes.isNotEmpty() || it.center != null }
}.getOrNull()

/* ── JsonObject scalar helpers (null-soft) ───────────────────────────────────── */

private fun JsonObject.prim(key: String): JsonPrimitive? =
    (this[key] as? JsonPrimitive)?.takeIf { it !is kotlinx.serialization.json.JsonNull }

private fun JsonObject.str(key: String): String? = prim(key)?.content
private fun JsonObject.dbl(key: String): Double? = prim(key)?.doubleOrNull
private fun JsonObject.intv(key: String): Int? = prim(key)?.intOrNull
private fun JsonObject.boolv(key: String): Boolean? = prim(key)?.booleanOrNull
private fun JsonArray?.orEmptyArr(): JsonArray = this ?: JsonArray(emptyList())
