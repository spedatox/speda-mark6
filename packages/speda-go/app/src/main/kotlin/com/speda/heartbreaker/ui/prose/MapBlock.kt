package com.speda.heartbreaker.ui.prose

import android.content.Context
import android.content.Intent
import android.location.Geocoder
import android.net.Uri
import android.view.MotionEvent
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
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
import androidx.compose.runtime.setValue
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
import com.speda.heartbreaker.domain.MapRoute
import com.speda.heartbreaker.domain.MapSpec
import com.speda.heartbreaker.domain.decodePolyline
import com.speda.heartbreaker.domain.looksIncomplete
import com.speda.heartbreaker.domain.parseMapSpec
import org.maplibre.android.MapLibre
import org.maplibre.android.camera.CameraUpdateFactory
import org.maplibre.android.geometry.LatLngBounds
import org.maplibre.android.maps.MapView
import org.maplibre.android.maps.Style
import org.maplibre.android.style.expressions.Expression
import org.maplibre.android.style.layers.CircleLayer
import org.maplibre.android.style.layers.LineLayer
import org.maplibre.android.style.layers.Property
import org.maplibre.android.style.layers.PropertyFactory
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
 * ```map fences — the Stark map card. MapLibre GL Native draws our own dark style
 * (assets/map_style_stark.json, OpenFreeMap vector tiles, no Google Play Services);
 * routes and markers are runtime layers tinted in the active agent's accent, so
 * they render even when tiles are unavailable. Map gestures are disabled by design
 * — the card is a glance, and real interaction hands off to Google Maps via the
 * NAVIGATE / OPEN IN MAPS actions (plan D-M4). Backend get_route supplies encoded
 * polylines + live-traffic timings; the client never holds a Maps key.
 */
@Composable
fun MapBlock(raw: String, modifier: Modifier = Modifier) {
    val spec = remember(raw) { parseMapSpec(raw) }
    when {
        spec != null -> MapCard(spec, modifier)
        looksIncomplete(raw) -> Materializing("MAP", modifier)
        else -> ParseError("MAP", raw, modifier)
    }
}

@Composable
private fun MapCard(spec: MapSpec, modifier: Modifier) {
    val palette = LocalHbPalette.current
    val context = LocalContext.current
    val primary = spec.primaryRoute

    Column(
        modifier
            .fillMaxWidth()
            .padding(vertical = 12.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(Color(0xFF060E16).copy(alpha = 0.6f))
            .border(1.dp, palette.edge, RoundedCornerShape(12.dp)),
    ) {
        MapHeader(spec.title, primary, palette)

        val mapHeight = (spec.height ?: 240).dp
        Box(
            Modifier
                .fillMaxWidth()
                .height(mapHeight)
                .wireframeFallback(palette),
        ) {
            MapSurface(spec, palette)
        }

        // Coordinates + reverse-geocoded address of the focus point (the nav
        // target, else the destination marker, else the centre).
        focusPoint(spec)?.let { (lat, lng) -> CoordinateFooter(lat, lng, palette) }

        // Traffic readout for the primary route.
        primary?.let { TrafficReadout(it, palette) }

        MapActionBar(spec, context, palette)

        if (spec.autoNavigate && spec.navigate != null) {
            AutoNavigateCountdown(spec.navigate, context, palette)
        }
    }
}

/* ── MapLibre surface ────────────────────────────────────────────────────────── */

@Composable
private fun MapSurface(spec: MapSpec, palette: HbPalette) {
    val accent = palette.accent.toArgb()
    val accentBright = palette.accentBright.toArgb()
    val dim = palette.accentDim.toArgb()
    val mapView = rememberMapViewWithLifecycle()

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
                    runCatching { drawRoutes(style, spec, accent, accentBright, dim) }
                    runCatching { drawMarkers(style, spec, accent, accentBright, dim) }
                    runCatching { fitCamera(map, spec) }
                }
            }
            mapView
        },
        modifier = Modifier.fillMaxSize(),  // the parent Box fixes the height
    )
}

private fun drawRoutes(style: Style, spec: MapSpec, accent: Int, accentBright: Int, dim: Int) {
    // Alternatives first (below), then the primary glow + line on top.
    val ordered = spec.routes.sortedBy { it.primary }  // false < true → primary last
    ordered.forEachIndexed { i, route ->
        val pts = decodePolyline(route.polyline).map { Point.fromLngLat(it.lng, it.lat) }
        if (pts.size < 2) return@forEachIndexed
        val src = GeoJsonSource("route-src-$i", LineString.fromLngLats(pts))
        style.addSource(src)
        if (route.primary) {
            style.addLayer(
                LineLayer("route-glow-$i", "route-src-$i").withProperties(
                    PropertyFactory.lineColor(accent),
                    PropertyFactory.lineOpacity(0.28f),
                    PropertyFactory.lineWidth(11f),
                    PropertyFactory.lineCap(Property.LINE_CAP_ROUND),
                    PropertyFactory.lineJoin(Property.LINE_JOIN_ROUND),
                ),
            )
            style.addLayer(
                LineLayer("route-line-$i", "route-src-$i").withProperties(
                    PropertyFactory.lineColor(accentBright),
                    PropertyFactory.lineWidth(4.5f),
                    PropertyFactory.lineCap(Property.LINE_CAP_ROUND),
                    PropertyFactory.lineJoin(Property.LINE_JOIN_ROUND),
                ),
            )
        } else {
            style.addLayer(
                LineLayer("route-line-$i", "route-src-$i").withProperties(
                    PropertyFactory.lineColor(dim),
                    PropertyFactory.lineOpacity(0.7f),
                    PropertyFactory.lineWidth(2.5f),
                    PropertyFactory.lineDasharray(arrayOf(2f, 1.5f)),
                    PropertyFactory.lineCap(Property.LINE_CAP_ROUND),
                ),
            )
        }
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

private fun fitCamera(map: org.maplibre.android.maps.MapLibreMap, spec: MapSpec) {
    val pts = buildList {
        spec.markers.forEach { add(MlLatLng(it.lat, it.lng)) }
        spec.routes.forEach { r -> decodePolyline(r.polyline).forEach { add(MlLatLng(it.lat, it.lng)) } }
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

/* ── Chrome: header, traffic readout, actions ─────────────────────────────────── */

@Composable
private fun MapHeader(title: String?, primary: MapRoute?, palette: HbPalette) {
    if (title.isNullOrBlank() && primary == null) return
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
            if (primary != null) {
                val dist = primary.distanceKm?.let { "${fmt(it)} KM" } ?: ""
                val eta = primary.durationMin?.let { "$it MIN" } ?: ""
                val readout = listOf(dist, eta).filter { it.isNotBlank() }.joinToString(" · ")
                if (readout.isNotBlank()) {
                    BasicText(AnnotatedString(readout), style = HbType.readout.copy(fontSize = 11.sp, letterSpacing = 0.1.em, color = palette.accentBright))
                }
            }
        }
    }
    Box(Modifier.fillMaxWidth().height(1.dp).background(palette.accent.copy(alpha = 0.22f)))
}

@Composable
private fun TrafficReadout(route: MapRoute, palette: HbPalette) {
    val delay = route.trafficDelayMin ?: return
    val heavy = route.durationMin != null && route.noTrafficMin != null &&
        route.noTrafficMin > 0 && delay.toFloat() / route.noTrafficMin > 0.25f
    Row(
        Modifier.fillMaxWidth().padding(start = 12.dp, end = 12.dp, top = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        BasicText(
            AnnotatedString("TRAFFIC +$delay MIN"),
            style = HbType.readout.copy(
                fontSize = 11.sp, letterSpacing = 0.12.em,
                color = if (heavy) palette.amber else palette.textDim,
            ),
        )
        route.label?.let {
            BasicText(AnnotatedString("· ${it.uppercase(Locale.getDefault())}"),
                style = HbType.readout.copy(fontSize = 10.sp, color = palette.textFaint))
        }
    }
}

@Composable
private fun MapActionBar(spec: MapSpec, context: Context, palette: HbPalette) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 10.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        spec.navigate?.let { nav ->
            ActionChip("▸ NAVIGATE", filled = true, palette = palette, modifier = Modifier.weight(1f)) {
                launchNavigation(context, nav)
            }
        }
        val openTarget = spec.navigate ?: spec.markers.firstOrNull { it.kind == "destination" }
            ?.let { MapNavigate(it.lat, it.lng) }
            ?: spec.markers.firstOrNull()?.let { MapNavigate(it.lat, it.lng) }
        openTarget?.let { t ->
            ActionChip("⧉ OPEN IN MAPS", filled = false, palette = palette, modifier = Modifier.weight(1f)) {
                openInMaps(context, t)
            }
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
            .padding(vertical = 10.dp),
        contentAlignment = Alignment.Center,
    ) {
        BasicText(
            AnnotatedString(label),
            style = HbType.headerBar.copy(
                fontSize = 11.sp, letterSpacing = 0.14.em, fontWeight = FontWeight.Bold,
                color = if (filled) palette.accentBright else palette.textDim,
            ),
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

private fun webMode(mode: String): String = when (mode) {
    "walk" -> "walking"; "bicycle" -> "bicycling"; "transit" -> "transit"; else -> "driving"
}

private fun fmt(v: Double): String =
    if (v == v.toLong().toDouble()) v.toLong().toString() else String.format(Locale.US, "%.1f", v)
