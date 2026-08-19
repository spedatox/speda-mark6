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

/**
 * One congestion band, addressing points of the DECODED polyline — so it is only
 * meaningful beside the geometry it was computed against, which is why both
 * arrive from the same fetch and neither passes through the model.
 *
 * [speed] is Google's own scale, kept verbatim: NORMAL | SLOW | TRAFFIC_JAM.
 */
data class TrafficBand(val start: Int, val end: Int, val speed: String)

/** One turn. [distanceM] is how far to drive BEFORE performing it. */
data class RouteStep(
    val instruction: String,
    val maneuver: String? = null,
    val distanceM: Int? = null,
    val durationMin: Int? = null,
)

/**
 * One place from a `find_places` result set, fetched whole by placesId.
 *
 * Everything past name/lat/lng is optional because Places returns a ragged set
 * (a barber has no website, a chain has no editorial summary) — the card hides
 * what it doesn't know rather than printing em-dashes.
 */
data class MapPlace(
    val name: String,
    val lat: Double,
    val lng: Double,
    val placeId: String? = null,
    val address: String? = null,
    val category: String? = null,
    val summary: String? = null,
    val status: String? = null,
    val rating: Double? = null,
    val reviews: Int? = null,
    val openNow: Boolean? = null,
    val hours: List<String> = emptyList(),
    val priceLevel: String? = null,
    val phone: String? = null,
    val website: String? = null,
    val mapsUri: String? = null,
    val distanceKm: Double? = null,
)

/** What GET /navigation/route/{id} hands back: the parts of a route no model
 *  may retype — the line, the congestion along it, and the turns. */
data class RouteGeometry(
    val polyline: String,
    val traffic: List<TrafficBand> = emptyList(),
    val steps: List<RouteStep> = emptyList(),
)

data class MapRoute(
    /**
     * The drawn geometry. Empty when the fence referenced [routeId] instead —
     * the client resolves it from Igor before drawing. Never hand-written by a
     * model: an encoded polyline is ~500 delta-encoded characters, and one wrong
     * character sends the whole line somewhere else while still looking like a
     * road (see app/models/route.py).
     */
    val polyline: String = "",
    /** Server-held geometry reference, e.g. "r_1a2b3c4d". */
    val routeId: String? = null,
    val label: String? = null,
    val durationMin: Int? = null,
    val noTrafficMin: Int? = null,
    val distanceKm: Double? = null,
    val mode: String = "drive",
    val primary: Boolean = false,
    /** Filled in by the [routeId] fetch, never by the model. Empty means "no
     *  traffic data" — which is not the same as "the road is clear". */
    val traffic: List<TrafficBand> = emptyList(),
    val steps: List<RouteStep> = emptyList(),
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
    /** placesId — an entire find_places result set, resolved client-side. The
     *  fence carries one string; the records never pass through the model. */
    val places: String? = null,
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
        // Either form is valid: a routeId (current) or an inline polyline
        // (messages written before routeIds existed). One of them must be there.
        val poly = r.str("polyline")?.takeIf { it.isNotBlank() }
        val routeId = r.str("routeId")?.takeIf { it.isNotBlank() }
        if (poly == null && routeId == null) return@mapNotNull null
        MapRoute(
            polyline = poly ?: "",
            routeId = routeId,
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

    val places = o.str("places")?.takeIf { it.isNotBlank() }

    MapSpec(
        title = o.str("title"),
        center = center,
        zoom = o.dbl("zoom"),
        markers = markers,
        routes = routes,
        navigate = navigate,
        autoNavigate = o.boolv("autoNavigate") ?: false,
        height = o.intv("height"),
        places = places,
    ).takeIf {
        it.markers.isNotEmpty() || it.routes.isNotEmpty() || it.center != null || it.places != null
    }
}.getOrNull()

/* ── Igor payloads (never model-written; see app/models/route.py, place.py) ──── */

/** GET /navigation/route/{id} → geometry + congestion + turns. Null when the
 *  body isn't what we expect, which the card draws as a missing line rather
 *  than a wrong one. */
fun parseRouteGeometry(raw: String): RouteGeometry? = runCatching {
    val o = LenientJson.parseToJsonElement(raw) as? JsonObject ?: return null
    val polyline = o.str("polyline").orEmpty()
    if (polyline.isBlank()) return null
    RouteGeometry(
        polyline = polyline,
        traffic = (o["traffic"] as? JsonArray).orEmptyArr().mapNotNull { el ->
            val b = el as? JsonObject ?: return@mapNotNull null
            val start = b.intv("start") ?: 0
            val end = b.intv("end") ?: return@mapNotNull null
            val speed = b.str("speed") ?: return@mapNotNull null
            TrafficBand(start, end, speed)
        },
        steps = (o["steps"] as? JsonArray).orEmptyArr().mapNotNull { el ->
            val s = el as? JsonObject ?: return@mapNotNull null
            val instruction = s.str("instruction")?.takeIf { it.isNotBlank() }
                ?: return@mapNotNull null
            RouteStep(
                instruction = instruction,
                maneuver = s.str("maneuver"),
                distanceM = s.intv("distanceM"),
                durationMin = s.intv("durationMin"),
            )
        },
    )
}.getOrNull()

/** GET /navigation/places/{id} → the result set. A place with no coordinates is
 *  dropped: it cannot be put on the map, and a list row pointing nowhere is
 *  worse than one fewer result. */
fun parsePlaceSet(raw: String): List<MapPlace>? = runCatching {
    val o = LenientJson.parseToJsonElement(raw) as? JsonObject ?: return null
    (o["places"] as? JsonArray).orEmptyArr().mapNotNull { el ->
        val p = el as? JsonObject ?: return@mapNotNull null
        val lat = p.dbl("lat") ?: return@mapNotNull null
        val lng = p.dbl("lng") ?: return@mapNotNull null
        MapPlace(
            name = p.str("name") ?: "(unnamed)",
            lat = lat, lng = lng,
            placeId = p.str("placeId"),
            address = p.str("address"),
            category = p.str("category"),
            summary = p.str("summary"),
            status = p.str("status"),
            rating = p.dbl("rating"),
            reviews = p.intv("reviews"),
            openNow = p.boolv("openNow"),
            hours = (p["hours"] as? JsonArray).orEmptyArr().mapNotNull {
                (it as? JsonPrimitive)?.content
            },
            priceLevel = p.str("priceLevel"),
            phone = p.str("phone"),
            website = p.str("website"),
            mapsUri = p.str("mapsUri"),
            distanceKm = p.dbl("distanceKm"),
        )
    }
}.getOrNull()

/* ── Aircraft tracking ─────────────────────────────────────────────────────── */

/**
 * The ```aircraft fence contract from prompts/core/06_visual_output.md, and
 * also the shape of GET /aircraft/track/{tail} — unlike routes/places, a live
 * position is polled directly by tail number rather than resolved once from a
 * generated id (see app/services/aircraft.py for why). One type covers both:
 * the fence gives the initial snapshot, the poll refreshes the same fields.
 */
data class AircraftSpec(
    val tail: String,
    val icao24: String? = null,
    val callsign: String? = null,
    val aircraftType: String? = null,
    val lat: Double,
    val lng: Double,
    val altitudeFt: Int? = null,
    val onGround: Boolean = false,
    val groundSpeedKt: Double? = null,
    val headingDeg: Double? = null,
    val squawk: String? = null,
    val emergency: String? = null,
)

/** Returns null when the fence/response isn't valid JSON yet, or is missing the
 *  minimum a card needs to place a marker (tail + position). */
fun parseAircraftSpec(raw: String): AircraftSpec? = runCatching {
    val o = LenientJson.parseToJsonElement(raw) as? JsonObject ?: return null
    val tail = o.str("tail")?.takeIf { it.isNotBlank() } ?: return null
    val lat = o.dbl("lat") ?: return null
    val lng = o.dbl("lng") ?: return null
    AircraftSpec(
        tail = tail,
        icao24 = o.str("icao24"),
        callsign = o.str("callsign"),
        aircraftType = o.str("aircraftType"),
        lat = lat,
        lng = lng,
        altitudeFt = o.intv("altitudeFt"),
        onGround = o.boolv("onGround") ?: false,
        groundSpeedKt = o.dbl("groundSpeedKt"),
        headingDeg = o.dbl("headingDeg"),
        squawk = o.str("squawk"),
        emergency = o.str("emergency"),
    )
}.getOrNull()

/* ── JsonObject scalar helpers (null-soft) ───────────────────────────────────── */

private fun JsonObject.prim(key: String): JsonPrimitive? =
    (this[key] as? JsonPrimitive)?.takeIf { it !is kotlinx.serialization.json.JsonNull }

private fun JsonObject.str(key: String): String? = prim(key)?.content
private fun JsonObject.dbl(key: String): Double? = prim(key)?.doubleOrNull
private fun JsonObject.intv(key: String): Int? = prim(key)?.intOrNull
private fun JsonObject.boolv(key: String): Boolean? = prim(key)?.booleanOrNull
private fun JsonArray?.orEmptyArr(): JsonArray = this ?: JsonArray(emptyList())
