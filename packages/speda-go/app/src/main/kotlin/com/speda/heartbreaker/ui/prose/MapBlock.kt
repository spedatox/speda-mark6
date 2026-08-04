package com.speda.heartbreaker.ui.prose

import android.content.Context
import android.content.Intent
import android.graphics.RectF
import android.location.Geocoder
import android.net.Uri
import android.view.MotionEvent
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import com.speda.heartbreaker.designsystem.theme.HbPalette
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.MapNavigate
import com.speda.heartbreaker.domain.MapPlace
import com.speda.heartbreaker.domain.MapRoute
import com.speda.heartbreaker.domain.MapSpec
import com.speda.heartbreaker.domain.RouteGeometry
import com.speda.heartbreaker.domain.RouteStep
import com.speda.heartbreaker.domain.decodePolyline
import com.speda.heartbreaker.domain.looksIncomplete
import com.speda.heartbreaker.domain.parseMapSpec
import org.maplibre.android.MapLibre
import org.maplibre.android.camera.CameraUpdateFactory
import org.maplibre.android.geometry.LatLngBounds
import org.maplibre.android.maps.MapLibreMap
import org.maplibre.android.maps.MapView
import org.maplibre.android.maps.Style
import org.maplibre.android.style.expressions.Expression
import org.maplibre.android.style.layers.CircleLayer
import org.maplibre.android.style.layers.LineLayer
import org.maplibre.android.style.layers.Property
import org.maplibre.android.style.layers.PropertyFactory
import org.maplibre.android.style.layers.SymbolLayer
import org.maplibre.android.style.sources.GeoJsonSource
import org.maplibre.geojson.Feature
import org.maplibre.geojson.FeatureCollection
import org.maplibre.geojson.LineString
import org.maplibre.geojson.Point
import java.util.Locale
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.maplibre.android.geometry.LatLng as MlLatLng

/**
 * True only while the message that owns this prose is actively streaming. Provided
 * by MessageItem; defaults false so anything rendered from history (a reopened
 * session) is treated as replay. [MapBlock] uses it to gate autoNavigate — a live
 * "take me there" may open Google Maps; a replayed one must never re-fire.
 */
val LocalMessageStreaming = compositionLocalOf { false }

/**
 * Resolves a routeId ("r_1a2b3c4d") into its geometry, live congestion and turn
 * list by asking Igor. Provided at the app root; defaults to "cannot resolve" so
 * previews and tests render the card without a line instead of crashing.
 *
 * This exists because the geometry must never pass through the model: a
 * hand-copied polyline that loses one character still parses, still looks like
 * a road, and quietly ends 19 km from the destination.
 */
val LocalRouteResolver = compositionLocalOf<suspend (String) -> RouteGeometry?> { { null } }

/**
 * Resolves a placesId ("pl_1a2b3c4d") into the whole find_places result set —
 * every field the place cards show. Same rule as routes: the model holds a
 * reference, never the directory.
 */
val LocalPlaceResolver = compositionLocalOf<suspend (String) -> List<MapPlace>?> { { null } }

/**
 * Google's own traffic colours, near enough that the owner reads them without a
 * key: green is moving, amber is slow, red is stopped. Deliberately NOT the
 * agent accent — congestion is the one thing on this card that has to mean the
 * same regardless of which agent is speaking.
 */
private val TrafficClear = Color(0xFF3FD08A)
private val TrafficSlow = Color(0xFFE8B53C)
private val TrafficJam = Color(0xFFE0533D)

private fun trafficColor(speed: String): Color = when (speed) {
    "SLOW" -> TrafficSlow
    "TRAFFIC_JAM" -> TrafficJam
    else -> TrafficClear
}

private fun trafficWord(speed: String): String = when (speed) {
    "SLOW" -> "SLOW"
    "TRAFFIC_JAM" -> "JAM"
    else -> "CLEAR"
}

/**
 * ```map fences — the Stark map card. MapLibre GL Native draws our own dark style
 * (assets/map_style_stark.json, OpenFreeMap vector tiles, no Google Play Services);
 * routes, congestion bands and place pins are runtime layers tinted in the active
 * agent's accent, so they render even when tiles are unavailable.
 *
 * The card is a working map, not a picture of one: alternative routes switch from
 * a header strip, the drawn line is coloured by live traffic the way Google's is,
 * every place found is a tappable pin that opens its own record (address, hours,
 * phone, website, its own NAVIGATE), and the turn list is one tap away. Handing
 * off to Google Maps stays available — it is no longer the only thing you can do.
 *
 * Backend `get_route` / `find_places` supply everything by id; the client never
 * holds a Maps key and the model never holds the data.
 */
@Composable
fun MapBlock(raw: String, modifier: Modifier = Modifier) {
    val spec = remember(raw) { parseMapSpec(raw) }
    val resolve = LocalRouteResolver.current

    // Routes referenced by id need one round trip before anything can be drawn.
    // Resolving here (rather than inside the map surface) keeps the draw path a
    // pure function of a complete spec — no half-drawn lines, no redraw dance.
    val pending = spec?.routes?.any { it.polyline.isBlank() && it.routeId != null } == true
    val ready by produceState(initialValue = if (pending) null else spec, spec) {
        val s = spec ?: return@produceState
        value = if (!pending) s else s.copy(
            routes = s.routes.map { r ->
                if (r.polyline.isNotBlank() || r.routeId == null) r
                else {
                    val g = resolve(r.routeId)
                    // The fence's own figures win — they are what the model told
                    // the owner in prose, and a card disagreeing with the
                    // sentence above it is worse than one missing a field.
                    r.copy(
                        polyline = g?.polyline.orEmpty(),
                        traffic = g?.traffic.orEmpty(),
                        steps = g?.steps.orEmpty(),
                    )
                }
            },
        )
    }

    when {
        ready != null -> MapCard(ready!!, modifier)
        spec != null -> Materializing("MAP", modifier)      // fetching geometry
        looksIncomplete(raw) -> Materializing("MAP", modifier)
        else -> ParseError("MAP", raw, modifier)
    }
}

@Composable
private fun MapCard(spec: MapSpec, modifier: Modifier) {
    val palette = LocalHbPalette.current
    val context = LocalContext.current
    val resolvePlaces = LocalPlaceResolver.current

    val places by produceState<List<MapPlace>?>(null, spec.places) {
        value = spec.places?.let { resolvePlaces(it) }
    }

    // Which route the card is currently about. Starts on the model's pick and
    // then belongs to the owner — the drawn line, every readout, the turn list
    // and NAVIGATE all follow this one number.
    var selected by remember(spec) {
        mutableIntStateOf(spec.routes.indexOfFirst { it.primary }.coerceAtLeast(0))
    }
    var openPlace by remember(places) { mutableStateOf<Int?>(null) }
    var expanded by remember(spec) { mutableStateOf(false) }
    var showSteps by remember(spec) { mutableStateOf(false) }

    val active = spec.routes.getOrNull(selected) ?: spec.routes.firstOrNull()
    val place = openPlace?.let { places?.getOrNull(it) }

    Column(
        modifier
            .fillMaxWidth()
            .padding(vertical = 12.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(Color(0xFF060E16).copy(alpha = 0.6f))
            .border(1.dp, palette.edge, RoundedCornerShape(12.dp)),
    ) {
        MapHeader(spec.title, active, places?.size, palette)

        if (spec.routes.size > 1) {
            RouteTabs(spec.routes, selected, palette) { selected = it }
        }

        val mapHeight = (if (expanded) 460 else (spec.height ?: 240)).dp
        Box(
            Modifier
                .fillMaxWidth()
                .height(mapHeight)
                .wireframeFallback(palette),
        ) {
            MapSurface(spec, places, selected, openPlace, palette) { openPlace = it }
            ExpandChip(expanded, palette, Modifier.align(Alignment.TopStart)) { expanded = !expanded }
        }

        active?.let { if (it.traffic.isNotEmpty()) TrafficBar(it, palette) }

        // The opened record replaces the coordinate footer — both answer "what
        // is this point", and the record answers it far better.
        if (place != null) {
            PlaceDetail(place, context, palette) { openPlace = null }
        } else {
            focusPoint(spec)?.let { (lat, lng) -> CoordinateFooter(lat, lng, palette) }
        }

        places?.takeIf { it.isNotEmpty() }?.let { list ->
            PlaceList(list, openPlace, palette) { openPlace = if (openPlace == it) null else it }
        }

        active?.takeIf { it.steps.isNotEmpty() }?.let { r ->
            StepList(r.steps, showSteps, palette) { showSteps = !showSteps }
        }

        MapActionBar(spec, place, active, context, palette)

        if (spec.autoNavigate && spec.navigate != null) {
            AutoNavigateCountdown(spec.navigate, context, palette)
        }
    }
}

/* ── MapLibre surface ────────────────────────────────────────────────────────── */

/** Live handles to the map, published as state so the effects below can wait for
 *  the style without polling. */
private class MapHolder {
    var map: MapLibreMap? = null
    var style: Style? by mutableStateOf(null)
}

private const val PLACES_SRC = "places-src"
private const val PLACES_LAYER = "places-layer"

@Composable
private fun MapSurface(
    spec: MapSpec,
    places: List<MapPlace>?,
    selectedRoute: Int,
    openPlace: Int?,
    palette: HbPalette,
    onPlaceClick: (Int?) -> Unit,
) {
    val accent = palette.accent.toArgb()
    val accentBright = palette.accentBright.toArgb()
    val dim = palette.accentDim.toArgb()
    val mapView = rememberMapViewWithLifecycle()
    val holder = remember { MapHolder() }
    // The map is built once; the click listener must still reach the CURRENT
    // handler, or a tap would open a record from a stale composition.
    val click by rememberUpdatedState(onPlaceClick)

    AndroidView(
        // The factory receives a Context and must RETURN the view; we return the
        // lifecycle-managed MapView and kick off style loading once.
        factory = {
            // Pan/zoom/rotate are ON. While a touch is on the map, stop the chat
            // list from stealing it (otherwise a pan-drag scrolls the conversation).
            mapView.setOnTouchListener { v, e ->
                when (e.actionMasked) {
                    MotionEvent.ACTION_DOWN, MotionEvent.ACTION_MOVE ->
                        v.parent?.requestDisallowInterceptTouchEvent(true)
                    MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL ->
                        v.parent?.requestDisallowInterceptTouchEvent(false)
                }
                false  // don't consume — let the MapView handle the gesture
            }
            mapView.getMapAsync { map ->
                map.uiSettings.setAllGesturesEnabled(true)   // zoom, pan, rotate, tilt
                map.uiSettings.isAttributionEnabled = true    // OSM/OpenMapTiles attribution stays
                map.uiSettings.isLogoEnabled = false
                map.setStyle(Style.Builder().fromUri("asset://map_style_stark.json")) { style ->
                    // Every route gets its full layer stack up front — casing,
                    // line, congestion overlay — and selection only flips
                    // properties. Tearing sources down on each tap would flicker
                    // and re-fit the camera under the owner's thumb.
                    runCatching { drawRoutes(style, spec, accent, accentBright, dim) }
                    runCatching { drawMarkers(style, spec, accent, accentBright, dim) }
                    map.addOnMapClickListener { latLng ->
                        val hit = placeAt(map, latLng)
                        click(hit)          // null on empty map → closes the sheet
                        hit != null
                    }
                    holder.map = map
                    holder.style = style
                }
            }
            mapView
        },
        modifier = Modifier.fillMaxSize(),  // the parent Box fixes the height
    )

    // Places arrive one fetch after the map. Drawn (and re-drawn on selection)
    // here rather than in the factory, which runs exactly once.
    LaunchedEffect(holder.style, places, openPlace) {
        val style = holder.style ?: return@LaunchedEffect
        runCatching { drawPlaces(style, places.orEmpty(), openPlace, accent, accentBright) }
    }

    // Camera: fit once everything that will be drawn is drawn.
    LaunchedEffect(holder.map, holder.style, places) {
        val map = holder.map ?: return@LaunchedEffect
        runCatching { fitCamera(map, spec, places.orEmpty()) }
    }

    LaunchedEffect(holder.style, selectedRoute) {
        val style = holder.style ?: return@LaunchedEffect
        runCatching { applySelection(style, spec, selectedRoute) }
    }

    // Opened place → bring it into view. Only ever an ease: yanking the camera
    // while the owner is reading the list is disorienting.
    LaunchedEffect(openPlace) {
        val map = holder.map ?: return@LaunchedEffect
        val p = openPlace?.let { places?.getOrNull(it) } ?: return@LaunchedEffect
        runCatching { map.easeCamera(CameraUpdateFactory.newLatLng(MlLatLng(p.lat, p.lng)), 420) }
    }
}

/** Which place pin (if any) sits under a tap. A generous box, because a fingertip
 *  is wider than a 7 dp circle. */
private fun placeAt(map: MapLibreMap, latLng: MlLatLng): Int? {
    val pt = map.projection.toScreenLocation(latLng)
    val box = RectF(pt.x - 28f, pt.y - 28f, pt.x + 28f, pt.y + 28f)
    val hit = map.queryRenderedFeatures(box, PLACES_LAYER).firstOrNull() ?: return null
    return hit.getNumberProperty("idx")?.toInt()
}

/**
 * Every route's layers, built once. Selection later only flips opacity — a
 * switcher that tore sources down on each tap would flicker and re-fit the
 * camera under the owner's thumb.
 *
 * Laid down in four passes rather than route by route, and that ordering is the
 * point: a style has no "bring to front", so if route 0's layers were all added
 * before route 1's, selecting route 0 would leave it drawn UNDERNEATH route 1's
 * line wherever the two share a road. Every route's dim line goes down first,
 * then every glow, then every bright line, then every congestion overlay — so
 * whichever route is selected is on top by construction.
 */
private fun drawRoutes(style: Style, spec: MapSpec, accent: Int, accentBright: Int, dim: Int) {
    val geometry = spec.routes.map { route ->
        decodePolyline(route.polyline).map { Point.fromLngLat(it.lng, it.lat) }
    }

    geometry.forEachIndexed { i, pts ->
        if (pts.size < 2) return@forEachIndexed
        style.addSource(GeoJsonSource("route-src-$i", LineString.fromLngLats(pts)))
        style.addLayer(
            LineLayer("route-dim-$i", "route-src-$i").withProperties(
                PropertyFactory.lineColor(dim),
                PropertyFactory.lineOpacity(0.65f),
                PropertyFactory.lineWidth(2.5f),
                PropertyFactory.lineCap(Property.LINE_CAP_ROUND),
                PropertyFactory.lineJoin(Property.LINE_JOIN_ROUND),
            ),
        )
    }

    geometry.forEachIndexed { i, pts ->
        if (pts.size < 2) return@forEachIndexed
        style.addLayer(
            LineLayer("route-glow-$i", "route-src-$i").withProperties(
                PropertyFactory.lineColor(accent),
                PropertyFactory.lineOpacity(0f),
                PropertyFactory.lineWidth(12f),
                PropertyFactory.lineCap(Property.LINE_CAP_ROUND),
                PropertyFactory.lineJoin(Property.LINE_JOIN_ROUND),
            ),
        )
    }

    geometry.forEachIndexed { i, pts ->
        if (pts.size < 2) return@forEachIndexed
        // With congestion data this becomes the casing under the coloured
        // bands; without it, it IS the route and carries the accent.
        val hasTraffic = spec.routes[i].traffic.isNotEmpty()
        style.addLayer(
            LineLayer("route-line-$i", "route-src-$i").withProperties(
                PropertyFactory.lineColor(
                    if (hasTraffic) android.graphics.Color.parseColor("#0A1A22") else accentBright,
                ),
                PropertyFactory.lineOpacity(0f),
                PropertyFactory.lineWidth(if (hasTraffic) 7f else 4.5f),
                PropertyFactory.lineCap(Property.LINE_CAP_ROUND),
                PropertyFactory.lineJoin(Property.LINE_JOIN_ROUND),
            ),
        )
    }

    geometry.forEachIndexed { i, pts ->
        if (pts.size < 2) return@forEachIndexed
        // Congestion bands: one feature per band, coloured by its own speed.
        // Bands index THIS decoded line, so anything out of range is dropped
        // rather than clamped — a band that doesn't fit came from another route,
        // and drawing it would paint traffic onto the wrong road.
        val bands = spec.routes[i].traffic.mapNotNull { b ->
            val start = b.start.coerceAtLeast(0)
            val end = b.end.coerceAtMost(pts.lastIndex)
            if (end <= start) return@mapNotNull null
            Feature.fromGeometry(LineString.fromLngLats(pts.subList(start, end + 1))).apply {
                addStringProperty("speed", b.speed)
            }
        }
        if (bands.isEmpty()) return@forEachIndexed
        style.addSource(GeoJsonSource("traffic-src-$i", FeatureCollection.fromFeatures(bands)))
        style.addLayer(
            LineLayer("traffic-line-$i", "traffic-src-$i").withProperties(
                PropertyFactory.lineColor(
                    Expression.match(
                        Expression.get("speed"),
                        Expression.literal("SLOW"), Expression.color(TrafficSlow.toArgb()),
                        Expression.literal("TRAFFIC_JAM"), Expression.color(TrafficJam.toArgb()),
                        Expression.color(TrafficClear.toArgb()),   // default: moving
                    ),
                ),
                PropertyFactory.lineWidth(5f),
                PropertyFactory.lineOpacity(0f),
                PropertyFactory.lineCap(Property.LINE_CAP_ROUND),
                PropertyFactory.lineJoin(Property.LINE_JOIN_ROUND),
            ),
        )
    }
}

/** Selection → opacity only. No source churn and no re-ordering, so switching
 *  routes is instant and the camera never moves under the owner. */
private fun applySelection(style: Style, spec: MapSpec, selected: Int) {
    spec.routes.indices.forEach { i ->
        val on = i == selected
        style.getLayer("route-dim-$i")?.setProperties(
            PropertyFactory.lineOpacity(if (on) 0f else 0.65f),
        )
        style.getLayer("route-glow-$i")?.setProperties(
            PropertyFactory.lineOpacity(if (on) 0.26f else 0f),
        )
        style.getLayer("route-line-$i")?.setProperties(
            PropertyFactory.lineOpacity(if (on) 1f else 0f),
        )
        style.getLayer("traffic-line-$i")?.setProperties(
            PropertyFactory.lineOpacity(if (on) 1f else 0f),
        )
    }
}

private fun drawMarkers(style: Style, spec: MapSpec, accent: Int, accentBright: Int, dim: Int) {
    if (spec.markers.isEmpty()) return
    val features = spec.markers.map { m ->
        Feature.fromGeometry(Point.fromLngLat(m.lng, m.lat)).apply {
            addStringProperty("kind", m.kind)
        }
    }
    style.addSource(GeoJsonSource("markers-src", FeatureCollection.fromFeatures(features)))

    val colorByKind = Expression.match(
        Expression.get("kind"),
        Expression.literal("origin"), Expression.color(accentBright),
        Expression.literal("destination"), Expression.color(accent),
        Expression.color(dim),  // poi / pin default
    )
    val radiusByKind = Expression.match(
        Expression.get("kind"),
        Expression.literal("origin"), Expression.literal(7f),
        Expression.literal("destination"), Expression.literal(8f),
        Expression.literal(5f),
    )
    style.addLayer(
        CircleLayer("markers-layer", "markers-src").withProperties(
            PropertyFactory.circleColor(colorByKind),
            PropertyFactory.circleRadius(radiusByKind),
            PropertyFactory.circleStrokeColor(android.graphics.Color.WHITE),
            PropertyFactory.circleStrokeWidth(1.6f),
            PropertyFactory.circleStrokeOpacity(0.85f),
        ),
    )
}

/**
 * Place pins — numbered to match the list below, so "the third one" means the
 * same thing in the chat, in the list and on the map. Re-set (not rebuilt) when
 * the opened place changes; the `sel` property is what swells the active pin.
 */
private fun drawPlaces(
    style: Style,
    places: List<MapPlace>,
    openPlace: Int?,
    accent: Int,
    accentBright: Int,
) {
    if (places.isEmpty()) return
    val features = places.mapIndexed { i, p ->
        Feature.fromGeometry(Point.fromLngLat(p.lng, p.lat)).apply {
            addNumberProperty("idx", i)
            addStringProperty("num", (i + 1).toString())
            addBooleanProperty("sel", i == openPlace)
        }
    }
    val collection = FeatureCollection.fromFeatures(features)
    val existing = style.getSourceAs<GeoJsonSource>(PLACES_SRC)
    if (existing != null) {
        existing.setGeoJson(collection)
        return
    }

    style.addSource(GeoJsonSource(PLACES_SRC, collection))
    style.addLayer(
        CircleLayer(PLACES_LAYER, PLACES_SRC).withProperties(
            PropertyFactory.circleColor(
                Expression.switchCase(
                    Expression.get("sel"), Expression.color(accentBright),
                    Expression.color(accent),
                ),
            ),
            PropertyFactory.circleRadius(
                Expression.switchCase(
                    Expression.get("sel"), Expression.literal(13f),
                    Expression.literal(10f),
                ),
            ),
            PropertyFactory.circleStrokeColor(android.graphics.Color.WHITE),
            PropertyFactory.circleStrokeWidth(1.6f),
            PropertyFactory.circleStrokeOpacity(0.85f),
        ),
    )
    // The number rides in its own symbol layer. Glyphs come from the style's
    // font endpoint, so offline the circles still stand — the pin is never
    // dependent on the label.
    style.addLayer(
        SymbolLayer("places-num", PLACES_SRC).withProperties(
            PropertyFactory.textField(Expression.get("num")),
            PropertyFactory.textFont(arrayOf("Noto Sans Regular")),
            PropertyFactory.textSize(11f),
            PropertyFactory.textColor(android.graphics.Color.parseColor("#04121A")),
            PropertyFactory.textAllowOverlap(true),
            PropertyFactory.textIgnorePlacement(true),
        ),
    )
}

private fun fitCamera(map: MapLibreMap, spec: MapSpec, places: List<MapPlace>) {
    val pts = buildList {
        spec.markers.forEach { add(MlLatLng(it.lat, it.lng)) }
        spec.routes.forEach { r -> decodePolyline(r.polyline).forEach { add(MlLatLng(it.lat, it.lng)) } }
        places.forEach { add(MlLatLng(it.lat, it.lng)) }
    }
    // Explicit centre/zoom wins when the model set it. Camera is moved via
    // CameraUpdateFactory — MapLibreMap exposes no public cameraPosition setter.
    if (spec.center != null) {
        map.moveCamera(
            CameraUpdateFactory.newLatLngZoom(
                MlLatLng(spec.center.lat, spec.center.lng), spec.zoom ?: 13.0,
            ),
        )
        return
    }
    when {
        pts.size >= 2 -> {
            val bounds = LatLngBounds.Builder().includes(pts).build()
            // Padding in px; newLatLngBounds can throw before layout — caller guards.
            map.moveCamera(CameraUpdateFactory.newLatLngBounds(bounds, 90))
        }
        pts.size == 1 -> map.moveCamera(
            CameraUpdateFactory.newLatLngZoom(pts.first(), spec.zoom ?: 14.0),
        )
    }
}

@Composable
private fun rememberMapViewWithLifecycle(): MapView {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val mapView = remember {
        MapLibre.getInstance(context)          // must precede MapView construction
        MapView(context).apply { onCreate(null) }
    }
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_START -> mapView.onStart()
                Lifecycle.Event.ON_RESUME -> mapView.onResume()
                Lifecycle.Event.ON_PAUSE -> mapView.onPause()
                Lifecycle.Event.ON_STOP -> mapView.onStop()
                else -> Unit
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        mapView.onStart()
        mapView.onResume()
        onDispose {
            lifecycleOwner.lifecycle.removeObserver(observer)
            mapView.onPause()
            mapView.onStop()
            mapView.onDestroy()
        }
    }
    return mapView
}

/* ── Chrome: header, route switcher, readouts ─────────────────────────────────── */

@Composable
private fun MapHeader(title: String?, primary: MapRoute?, placeCount: Int?, palette: HbPalette) {
    if (title.isNullOrBlank() && primary == null && placeCount == null) return
    Box(
        Modifier
            .fillMaxWidth()
            .height(28.dp)
            .background(palette.accent.copy(alpha = 0.10f))
            .padding(horizontal = 12.dp),
        contentAlignment = Alignment.CenterStart,
    ) {
        val i = (title ?: "").indexOf('_')
        val text = buildAnnotatedString {
            val t = title ?: "MAP"
            withStyle(SpanStyle(color = Color.White)) { append(if (i > -1) t.substring(0, i) else t) }
            if (i > -1) withStyle(SpanStyle(color = palette.accent)) { append(t.substring(i)) }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            BasicText(text, style = HbType.headerBar.copy(fontSize = 13.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.2.em))
            val readout = when {
                primary != null -> listOf(
                    primary.distanceKm?.let { "${fmt(it)} KM" }.orEmpty(),
                    primary.durationMin?.let { "$it MIN" }.orEmpty(),
                ).filter { it.isNotBlank() }.joinToString(" · ")
                placeCount != null -> "$placeCount FOUND"
                else -> ""
            }
            if (readout.isNotBlank()) {
                BasicText(AnnotatedString(readout), style = HbType.readout.copy(fontSize = 11.sp, letterSpacing = 0.1.em, color = palette.accentBright))
            }
        }
    }
    Box(Modifier.fillMaxWidth().height(1.dp).background(palette.accent.copy(alpha = 0.22f)))
}

/**
 * The route switcher. Every option get_route returned, side by side with the one
 * number that decides between them (ETA) and the one that explains it (delay vs
 * free-flow). Picking one re-draws the map and re-points NAVIGATE — comparing
 * routes was the whole reason to ask for alternatives, and a card that only ever
 * showed the winner threw that away.
 */
@Composable
private fun RouteTabs(routes: List<MapRoute>, selected: Int, palette: HbPalette, onSelect: (Int) -> Unit) {
    val fastest = routes.indices.minByOrNull { routes[it].durationMin ?: Int.MAX_VALUE }
    Row(
        Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState())
            .padding(horizontal = 10.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        routes.forEachIndexed { i, r ->
            val on = i == selected
            val delay = r.trafficDelayMin
            val heavy = delay != null && (r.noTrafficMin ?: 0) > 0 &&
                delay.toFloat() / r.noTrafficMin!! > 0.25f
            Column(
                Modifier
                    .widthIn(min = 116.dp)
                    .clip(RoundedCornerShape(7.dp))
                    .background(if (on) palette.accent.copy(alpha = 0.16f) else Color.White.copy(alpha = 0.03f))
                    .border(
                        1.dp,
                        palette.accent.copy(alpha = if (on) 0.5f else 0.16f),
                        RoundedCornerShape(7.dp),
                    )
                    .clickable { onSelect(i) }
                    .padding(horizontal = 9.dp, vertical = 6.dp),
            ) {
                Row(verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    BasicText(
                        AnnotatedString(r.durationMin?.toString() ?: "?"),
                        style = HbType.readout.copy(
                            fontSize = 15.sp, fontWeight = FontWeight.Bold,
                            color = if (on) palette.accentBright else palette.textDim,
                        ),
                    )
                    BasicText(
                        AnnotatedString("MIN"),
                        style = HbType.readout.copy(
                            fontSize = 9.sp, letterSpacing = 0.12.em,
                            color = if (on) palette.accentBright else palette.textDim,
                        ),
                    )
                    if (i == fastest && routes.size > 1) {
                        BasicText(
                            AnnotatedString("FASTEST"),
                            style = HbType.readout.copy(fontSize = 8.sp, letterSpacing = 0.1.em, color = TrafficClear),
                        )
                    }
                }
                val sub = listOfNotNull(
                    r.distanceKm?.let { "${fmt(it)} km" },
                    r.label?.takeIf { it.isNotBlank() },
                ).joinToString(" · ").ifBlank { "ROUTE" }
                BasicText(
                    AnnotatedString(sub),
                    style = HbType.readout.copy(fontSize = 10.sp, color = palette.textFaint),
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
                if (delay != null && delay > 0) {
                    BasicText(
                        AnnotatedString("+$delay MIN TRAFFIC"),
                        style = HbType.readout.copy(
                            fontSize = 9.sp, letterSpacing = 0.08.em,
                            color = if (heavy) palette.amber else palette.textFaint,
                        ),
                    )
                }
            }
        }
    }
}

/**
 * The congestion bar: the same green/amber/red as the line, as one proportional
 * strip. It answers "how bad, and is it bad the whole way or in one place" at a
 * glance — which the +N MIN number alone never could.
 */
@Composable
private fun TrafficBar(route: MapRoute, palette: HbPalette) {
    val spans = remember(route) {
        val acc = linkedMapOf("NORMAL" to 0, "SLOW" to 0, "TRAFFIC_JAM" to 0)
        route.traffic.forEach { b ->
            val key = if (acc.containsKey(b.speed)) b.speed else "NORMAL"
            acc[key] = (acc[key] ?: 0) + (b.end - b.start).coerceAtLeast(0)
        }
        acc.filterValues { it > 0 }
    }
    val total = spans.values.sum()
    if (total <= 0) return
    val delay = route.trafficDelayMin
    val heavy = delay != null && (route.noTrafficMin ?: 0) > 0 &&
        delay.toFloat() / route.noTrafficMin!! > 0.25f

    Column(Modifier.fillMaxWidth().padding(start = 12.dp, end = 12.dp, top = 8.dp)) {
        Row(
            Modifier.fillMaxWidth().height(5.dp).clip(RoundedCornerShape(3.dp)),
            horizontalArrangement = Arrangement.spacedBy(1.dp),
        ) {
            spans.forEach { (speed, span) ->
                Box(Modifier.weight(span.toFloat()).fillMaxSize().background(trafficColor(speed)))
            }
        }
        Row(
            Modifier.fillMaxWidth().padding(top = 5.dp),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            spans.forEach { (speed, span) ->
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    Box(Modifier.size(7.dp).clip(RoundedCornerShape(2.dp)).background(trafficColor(speed)))
                    BasicText(
                        AnnotatedString("${Math.round(100f * span / total)}% ${trafficWord(speed)}"),
                        style = HbType.readout.copy(fontSize = 10.sp, letterSpacing = 0.1.em, color = palette.textFaint),
                    )
                }
            }
            if (delay != null && delay > 0) {
                BasicText(
                    AnnotatedString("+$delay MIN VS FREE-FLOW"),
                    style = HbType.readout.copy(
                        fontSize = 10.sp, letterSpacing = 0.1.em,
                        color = if (heavy) palette.amber else palette.textDim,
                    ),
                )
            }
        }
    }
}

@Composable
private fun ExpandChip(expanded: Boolean, palette: HbPalette, modifier: Modifier, onClick: () -> Unit) {
    Box(
        modifier
            .padding(8.dp)
            .clip(RoundedCornerShape(6.dp))
            .background(Color(0xFF060E16).copy(alpha = 0.72f))
            .border(1.dp, palette.accent.copy(alpha = 0.35f), RoundedCornerShape(6.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = 8.dp, vertical = 4.dp),
    ) {
        BasicText(
            AnnotatedString(if (expanded) "⤡ SHRINK" else "⤢ EXPAND"),
            style = HbType.headerBar.copy(
                fontSize = 10.sp, letterSpacing = 0.14.em,
                fontWeight = FontWeight.Bold, color = palette.accentBright,
            ),
        )
    }
}

/* ── Places ──────────────────────────────────────────────────────────────────── */

/** Rating, open state, distance, price — the line that decides between two
 *  otherwise identical results. */
@Composable
private fun PlaceMeta(p: MapPlace, palette: HbPalette) {
    val text = buildAnnotatedString {
        var first = true
        fun sep() { if (!first) append("  ·  "); first = false }
        val rating = p.rating
        if (rating != null) {
            sep()
            val stars = String.format(Locale.US, "%.1f★", rating) +
                (p.reviews?.let { " ($it)" } ?: "")
            withStyle(SpanStyle(color = palette.accentBright)) { append(stars) }
        }
        val open = p.openNow
        if (open != null) {
            sep()
            withStyle(SpanStyle(color = if (open) TrafficClear else palette.amber)) {
                append(if (open) "OPEN" else "CLOSED")
            }
        }
        p.distanceKm?.let { sep(); append("${fmt(it)} km") }
        p.priceLevel?.let { sep(); append(it) }
        val status = p.status
        if (status != null) {
            sep()
            withStyle(SpanStyle(color = palette.amber)) {
                append(status.replace('_', ' ').lowercase(Locale.getDefault()))
            }
        }
    }
    if (text.isEmpty()) return
    BasicText(text, style = HbType.readout.copy(fontSize = 10.sp, letterSpacing = 0.06.em, color = palette.textFaint))
}

/**
 * The result list. Numbered to match the pins, and tapping a row is the same
 * gesture as tapping its pin. Not scrollable on purpose — a nested scroller
 * inside the transcript fights the transcript for the same drag, and a search
 * returns twenty results at most.
 */
@Composable
private fun PlaceList(places: List<MapPlace>, open: Int?, palette: HbPalette, onSelect: (Int) -> Unit) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp)) {
        places.forEachIndexed { i, p ->
            val on = i == open
            Row(
                Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(4.dp))
                    .background(if (on) palette.accent.copy(alpha = 0.14f) else Color.Transparent)
                    .clickable { onSelect(i) }
                    .padding(horizontal = 6.dp, vertical = 5.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Box(
                    Modifier
                        .size(18.dp)
                        .clip(CircleShape)
                        .background(if (on) palette.accentBright else palette.accent.copy(alpha = 0.28f)),
                    contentAlignment = Alignment.Center,
                ) {
                    BasicText(
                        AnnotatedString("${i + 1}"),
                        style = HbType.readout.copy(
                            fontSize = 10.sp, fontWeight = FontWeight.Bold,
                            color = if (on) Color(0xFF04121A) else palette.accentBright,
                        ),
                    )
                }
                Column(Modifier.weight(1f)) {
                    BasicText(
                        AnnotatedString(p.name),
                        style = HbType.readout.copy(
                            fontSize = 13.sp,
                            color = if (on) Color.White else palette.textDim,
                        ),
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                    PlaceMeta(p, palette)
                }
            }
        }
    }
}

/** The opened record: everything needed before deciding to go, and the actions to
 *  act on it — call, open the site, see it on Google. */
@Composable
private fun PlaceDetail(p: MapPlace, context: Context, palette: HbPalette, onClose: () -> Unit) {
    var hoursOpen by remember(p.placeId, p.name) { mutableStateOf(false) }
    Column(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 8.dp)
            .padding(top = 8.dp)
            .clip(RoundedCornerShape(8.dp))
            .background(palette.accent.copy(alpha = 0.09f))
            .border(1.dp, palette.accent.copy(alpha = 0.28f), RoundedCornerShape(8.dp))
            .padding(horizontal = 10.dp, vertical = 8.dp),
    ) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Column(Modifier.weight(1f)) {
                BasicText(
                    AnnotatedString(p.name),
                    style = HbType.headerBar.copy(fontSize = 15.sp, fontWeight = FontWeight.Bold, color = Color.White),
                )
                p.category?.let {
                    BasicText(
                        AnnotatedString(it.uppercase(Locale.getDefault())),
                        style = HbType.readout.copy(fontSize = 10.sp, letterSpacing = 0.1.em, color = palette.accent),
                    )
                }
                PlaceMeta(p, palette)
                p.address?.let {
                    BasicText(AnnotatedString(it), style = HbType.readout.copy(fontSize = 11.sp, color = palette.textDim))
                }
                p.summary?.let {
                    BasicText(AnnotatedString(it), style = HbType.readout.copy(fontSize = 11.sp, color = palette.textFaint))
                }
            }
            Box(Modifier.clickable(onClick = onClose).padding(4.dp)) {
                BasicText(AnnotatedString("✕"), style = HbType.readout.copy(fontSize = 13.sp, color = palette.textFaint))
            }
        }

        if (p.hours.isNotEmpty()) {
            Box(Modifier.clickable { hoursOpen = !hoursOpen }.padding(top = 6.dp)) {
                BasicText(
                    AnnotatedString(if (hoursOpen) "▾ HOURS" else "▸ HOURS"),
                    style = HbType.readout.copy(
                        fontSize = 10.sp, letterSpacing = 0.12.em,
                        fontWeight = FontWeight.Bold, color = palette.accent,
                    ),
                )
            }
            if (hoursOpen) {
                Column(Modifier.padding(top = 3.dp)) {
                    p.hours.forEach {
                        BasicText(AnnotatedString(it), style = HbType.readout.copy(fontSize = 11.sp, color = palette.textDim))
                    }
                }
            }
        }

        Row(
            Modifier.fillMaxWidth().padding(top = 8.dp).horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            p.phone?.let { phone ->
                ActionChip("☏ $phone", filled = false, palette = palette) {
                    // ACTION_DIAL, not ACTION_CALL: the number lands in the
                    // dialler and the owner decides. No permission, no surprise
                    // call from a mis-tap on a map card.
                    runCatching {
                        context.startActivity(
                            Intent(Intent.ACTION_DIAL, Uri.parse("tel:${phone.filter { it.isDigit() || it == '+' }}")),
                        )
                    }
                }
            }
            p.website?.let { url ->
                ActionChip("⧉ WEBSITE", filled = false, palette = palette) { openUrl(context, url) }
            }
            p.mapsUri?.let { url ->
                ActionChip("◎ GOOGLE PAGE", filled = false, palette = palette) { openUrl(context, url) }
            }
        }
    }
}

/* ── Turn-by-turn ────────────────────────────────────────────────────────────── */

/** The turn list, collapsed by default: the card answers "how long" first, and
 *  "which way, exactly" only when asked. */
@Composable
private fun StepList(steps: List<RouteStep>, open: Boolean, palette: HbPalette, onToggle: () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp)) {
        Box(Modifier.clickable(onClick = onToggle)) {
            BasicText(
                AnnotatedString(
                    "${if (open) "▾" else "▸"} ${steps.size} ${if (steps.size == 1) "STEP" else "STEPS"}",
                ),
                style = HbType.readout.copy(
                    fontSize = 11.sp, letterSpacing = 0.14.em,
                    fontWeight = FontWeight.Bold, color = palette.accent,
                ),
            )
        }
        if (!open) return
        steps.forEachIndexed { i, s ->
            Row(
                Modifier.fillMaxWidth().padding(vertical = 3.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                BasicText(
                    AnnotatedString("${i + 1}"),
                    style = HbType.readout.copy(fontSize = 10.sp, color = palette.textFaint),
                    modifier = Modifier.width(18.dp),
                )
                BasicText(
                    AnnotatedString(s.instruction),
                    style = HbType.readout.copy(fontSize = 11.5.sp, color = palette.textDim),
                    modifier = Modifier.weight(1f),
                )
                BasicText(
                    AnnotatedString(fmtDistance(s.distanceM)),
                    style = HbType.readout.copy(fontSize = 10.sp, color = palette.textFaint),
                )
            }
        }
    }
}

/* ── Actions ─────────────────────────────────────────────────────────────────── */

@Composable
private fun MapActionBar(
    spec: MapSpec,
    place: MapPlace?,
    active: MapRoute?,
    context: Context,
    palette: HbPalette,
) {
    // NAVIGATE targets whatever the card is currently about: an opened place
    // first, then the fence's own target, then a destination marker. Naming it
    // matters once a place is open — an unlabelled NAVIGATE would be a coin
    // flip. An ORIGIN marker is never a fallback: a places card carries one for
    // "you are here", and offering to navigate the owner to where they already
    // stand is worse than offering nothing.
    val target: MapNavigate? = place?.let { MapNavigate(it.lat, it.lng, active?.mode ?: "drive", it.name) }
        ?: spec.navigate
        ?: spec.markers.firstOrNull { it.kind == "destination" }?.let { MapNavigate(it.lat, it.lng) }
        ?: spec.markers.firstOrNull { it.kind != "origin" }?.let { MapNavigate(it.lat, it.lng) }
    if (target == null) return

    val label = place?.let { "▸ NAVIGATE · ${it.name.uppercase(Locale.getDefault())}" } ?: "▸ NAVIGATE"
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 10.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        ActionChip(label, filled = true, palette = palette, modifier = Modifier.weight(1f)) {
            launchNavigation(context, target)
        }
        ActionChip("⧉ OPEN IN MAPS", filled = false, palette = palette, modifier = Modifier.weight(1f)) {
            openInMaps(context, target)
        }
    }
}

@Composable
private fun ActionChip(
    label: String,
    filled: Boolean,
    palette: HbPalette,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    Box(
        modifier
            .clip(RoundedCornerShape(8.dp))
            .background(if (filled) palette.accent.copy(alpha = 0.16f) else Color.Transparent)
            .border(1.dp, palette.accent.copy(alpha = if (filled) 0.45f else 0.25f), RoundedCornerShape(8.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 10.dp),
        contentAlignment = Alignment.Center,
    ) {
        BasicText(
            AnnotatedString(label),
            style = HbType.headerBar.copy(
                fontSize = 11.sp, letterSpacing = 0.14.em, fontWeight = FontWeight.Bold,
                color = if (filled) palette.accentBright else palette.textDim,
            ),
            maxLines = 1, overflow = TextOverflow.Ellipsis,
        )
    }
}

@Composable
private fun AutoNavigateCountdown(nav: MapNavigate, context: Context, palette: HbPalette) {
    val streaming = LocalMessageStreaming.current
    // Eligible only if this card first mounted while its message was live — a
    // history replay mounts with streaming=false and must never auto-fire.
    val bornLive = remember { streaming }
    var consumed by remember { mutableStateOf(false) }
    var remaining by remember { mutableIntStateOf(4) }

    if (!bornLive || consumed) return

    LaunchedEffect(Unit) {
        while (remaining > 0) {
            kotlinx.coroutines.delay(1000)
            remaining -= 1
        }
        if (!consumed) {
            consumed = true
            launchNavigation(context, nav)
        }
    }

    Row(
        Modifier.fillMaxWidth().padding(start = 10.dp, end = 10.dp, bottom = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        BasicText(
            AnnotatedString("OPENING GOOGLE MAPS IN $remaining…"),
            style = HbType.readout.copy(fontSize = 11.sp, letterSpacing = 0.1.em, color = palette.accentBright),
            modifier = Modifier.weight(1f),
        )
        ActionChip("CANCEL", filled = false, palette = palette) { consumed = true }
    }
}

/* ── Coordinate + address footer ──────────────────────────────────────────────── */

/** The point the footer describes: nav target → destination marker → centre → any marker. */
private fun focusPoint(spec: MapSpec): Pair<Double, Double>? =
    spec.navigate?.let { it.lat to it.lng }
        ?: spec.markers.firstOrNull { it.kind == "destination" }?.let { it.lat to it.lng }
        ?: spec.center?.let { it.lat to it.lng }
        ?: spec.markers.firstOrNull()?.let { it.lat to it.lng }

@Composable
private fun CoordinateFooter(lat: Double, lng: Double, palette: HbPalette) {
    val address = rememberAddress(lat, lng)
    Row(
        Modifier.fillMaxWidth().padding(start = 12.dp, end = 12.dp, top = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        BasicText(AnnotatedString("◎"), style = HbType.readout.copy(fontSize = 12.sp, color = palette.accentBright))
        Column(Modifier.weight(1f)) {
            BasicText(
                AnnotatedString(String.format(Locale.US, "%.5f, %.5f", lat, lng)),
                style = HbType.readout.copy(fontSize = 11.sp, letterSpacing = 0.06.em, color = palette.accentBright),
            )
            if (!address.isNullOrBlank()) {
                BasicText(
                    AnnotatedString(address),
                    style = HbType.readout.copy(fontSize = 10.5.sp, letterSpacing = 0.02.em, color = palette.textDim),
                )
            }
        }
    }
}

/** Reverse-geocodes on the platform Geocoder (no Play Services), off the main
 * thread. Null until resolved, or when the device has no geocoder backend. */
@Composable
private fun rememberAddress(lat: Double, lng: Double): String? {
    val context = LocalContext.current
    var address by remember(lat, lng) { mutableStateOf<String?>(null) }
    LaunchedEffect(lat, lng) {
        address = withContext(Dispatchers.IO) {
            runCatching {
                if (!Geocoder.isPresent()) return@runCatching null
                val geocoder = Geocoder(context, Locale.getDefault())
                @Suppress("DEPRECATION") // async overload is API 33+; this covers 31/32
                val results = geocoder.getFromLocation(lat, lng, 1)
                val a = results?.firstOrNull() ?: return@runCatching null
                listOfNotNull(
                    a.thoroughfare ?: a.subLocality ?: a.featureName,
                    a.locality ?: a.subAdminArea,
                    a.adminArea,
                ).distinct().take(2).joinToString(", ").ifBlank { null }
            }.getOrNull()
        }
    }
    return address
}

/* ── Wireframe fallback + intents ─────────────────────────────────────────────── */

/** A faint Stark grid drawn behind the MapView so a dead tile server reads as a
 * deliberate wireframe, not a grey hole. Route/marker layers draw over it. */
private fun Modifier.wireframeFallback(palette: HbPalette): Modifier = drawBehind {
    val step = 28.dp.toPx()
    val c = palette.accent.copy(alpha = 0.06f)
    var x = 0f
    while (x < size.width) { drawLine(c, Offset(x, 0f), Offset(x, size.height), 1f); x += step }
    var y = 0f
    while (y < size.height) { drawLine(c, Offset(0f, y), Offset(size.width, y), 1f); y += step }
}

private fun launchNavigation(context: Context, nav: MapNavigate) {
    val fallback = Intent(
        Intent.ACTION_VIEW,
        Uri.parse("https://www.google.com/maps/dir/?api=1&destination=${nav.lat},${nav.lng}&travelmode=${webMode(nav.mode)}"),
    )
    // google.navigation: has no transit mode — route transit through the web URL.
    val gmmMode = when (nav.mode) {
        "walk" -> "w"; "bicycle" -> "b"; "two_wheeler" -> "l"; "transit" -> null; else -> "d"
    }
    runCatching {
        if (gmmMode != null) {
            val gmm = Intent(Intent.ACTION_VIEW, Uri.parse("google.navigation:q=${nav.lat},${nav.lng}&mode=$gmmMode"))
                .setPackage("com.google.android.apps.maps")
            if (gmm.resolveActivity(context.packageManager) != null) {
                context.startActivity(gmm); return
            }
        }
        context.startActivity(fallback)
    }.onFailure { runCatching { context.startActivity(fallback) } }
}

private fun openInMaps(context: Context, t: MapNavigate) {
    val url = "https://www.google.com/maps/dir/?api=1&destination=${t.lat},${t.lng}&travelmode=${webMode(t.mode)}"
    runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }
}

private fun openUrl(context: Context, url: String) {
    runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }
}

private fun webMode(mode: String): String = when (mode) {
    "walk" -> "walking"; "bicycle" -> "bicycling"; "transit" -> "transit"; else -> "driving"
}

private fun fmt(v: Double): String =
    if (v == v.toLong().toDouble()) v.toLong().toString() else String.format(Locale.US, "%.1f", v)

/** "1.4 km" / "600 m" — metres below a kilometre, where the turn actually is. */
private fun fmtDistance(metres: Int?): String = when {
    metres == null -> ""
    metres >= 1000 -> String.format(Locale.US, "%.1f km", metres / 1000.0)
    else -> "$metres m"
}
