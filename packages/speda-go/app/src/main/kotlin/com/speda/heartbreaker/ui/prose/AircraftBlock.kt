package com.speda.heartbreaker.ui.prose

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import com.speda.heartbreaker.designsystem.theme.HbPalette
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AircraftSpec
import com.speda.heartbreaker.domain.looksIncomplete
import com.speda.heartbreaker.domain.parseAircraftSpec
import com.speda.heartbreaker.i18n.LocalStrings
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import org.maplibre.android.camera.CameraUpdateFactory
import org.maplibre.android.maps.MapLibreMap
import org.maplibre.android.maps.Style
import org.maplibre.android.style.expressions.Expression
import org.maplibre.android.style.layers.CircleLayer
import org.maplibre.android.style.layers.Property
import org.maplibre.android.style.layers.PropertyFactory
import org.maplibre.android.style.layers.SymbolLayer
import org.maplibre.android.style.sources.GeoJsonSource
import org.maplibre.geojson.Feature
import org.maplibre.geojson.Point
import org.maplibre.android.geometry.LatLng as MlLatLng

/**
 * Resolves a tail number into its live ADS-B position by asking Igor —
 * GET /aircraft/track/{tail}. Provided at the app root next to
 * [LocalRouteResolver]/[LocalPlaceResolver], but this one is polled
 * repeatedly rather than resolved once: a live position changes every few
 * seconds and, unlike a route polyline, a tail number is short enough for
 * the model to carry safely without an id-store behind it (see
 * app/services/aircraft.py).
 */
val LocalAircraftResolver = compositionLocalOf<suspend (String) -> AircraftSpec?> { { null } }

private val EmergencyRed = Color(0xFFE0533D)

private val EMERGENCY_SQUAWKS = setOf("7500", "7600", "7700")

private fun isEmergency(spec: AircraftSpec): Boolean =
    (!spec.emergency.isNullOrBlank() && spec.emergency != "none") ||
        (spec.squawk != null && spec.squawk in EMERGENCY_SQUAWKS)

// Landing auto-pauses the card — once the feed confirms the aircraft is
// down, further movement is gate/apron-scale and not worth a standing poll.
// 20 consecutive misses (~5min) auto-pauses it the other way: a permanently
// lost signal (landed with the transponder off before we caught the ground
// report, out of coverage for good) rather than a plane that's genuinely
// still up. Either is just the DEFAULT guess though — the owner has the
// final say via the header chip, which un-pauses (with a fresh miss count)
// or pauses on demand regardless of what auto-pause decided. Without either
// of these, a chat screen left open would poll every 15s forever.
private const val GIVE_UP_AFTER_MISSES = 20

/**
 * ```aircraft fences — the live ADS-B tracking card. Renders the model's
 * initial snapshot immediately, then polls [LocalAircraftResolver] every 15s
 * (well under airplanes.live's 1 req/s guidance) to move the marker — no
 * further tool call, no id, just the tail number the fence already carries.
 * Polling stops either on its own (see GIVE_UP_AFTER_MISSES above) or from
 * the owner tapping the header's Live/Paused chip; either way, resuming
 * always restarts with a clean miss count.
 */
@Composable
fun AircraftBlock(raw: String, modifier: Modifier = Modifier) {
    val t = LocalStrings.current
    val seed = remember(raw) { parseAircraftSpec(raw) }
    val resolve = LocalAircraftResolver.current

    var live by remember(seed?.tail) { mutableStateOf<AircraftSpec?>(null) }
    var misses by remember(seed?.tail) { mutableIntStateOf(0) }
    var autoPaused by remember(seed?.tail) { mutableStateOf(false) }
    var userPaused by remember(seed?.tail) { mutableStateOf(false) }
    val paused = autoPaused || userPaused

    LaunchedEffect(seed?.tail, paused) {
        val tail = seed?.tail ?: return@LaunchedEffect
        if (paused) return@LaunchedEffect
        while (isActive) {
            delay(15_000)
            val next = resolve(tail)
            if (next != null) {
                live = next
                misses = 0
                if (next.onGround) autoPaused = true
            } else {
                misses += 1
                if (misses >= GIVE_UP_AFTER_MISSES) autoPaused = true
            }
        }
    }

    val toggle: () -> Unit = {
        if (paused) {
            autoPaused = false
            userPaused = false
            misses = 0
        } else {
            userPaused = true
        }
    }

    val current = live ?: seed
    when {
        current != null -> AircraftCard(current, stale = misses >= 3, paused = paused, autoPaused = autoPaused, onToggle = toggle, modifier = modifier)
        looksIncomplete(raw) -> Materializing(t.proseKind.aircraft, modifier)
        else -> ParseError(t.proseKind.aircraft, raw, modifier)
    }
}

@Composable
private fun AircraftCard(
    spec: AircraftSpec,
    stale: Boolean,
    paused: Boolean,
    autoPaused: Boolean,
    onToggle: () -> Unit,
    modifier: Modifier,
) {
    val palette = LocalHbPalette.current
    val emergency = isEmergency(spec)
    val borderColor = if (emergency) EmergencyRed.copy(alpha = 0.55f) else palette.edge

    Column(
        modifier
            .fillMaxWidth()
            .padding(vertical = 12.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(Color(0xFF060E16).copy(alpha = 0.6f))
            .border(1.dp, borderColor, RoundedCornerShape(12.dp)),
    ) {
        AircraftHeader(spec, stale, emergency, paused, onToggle, palette)

        Box(Modifier.fillMaxWidth().height(200.dp)) {
            AircraftSurface(spec, palette)
        }

        StatusReadout(spec, stale, paused, autoPaused, emergency, palette)
    }
}

@Composable
private fun AircraftHeader(
    spec: AircraftSpec,
    stale: Boolean,
    emergency: Boolean,
    paused: Boolean,
    onToggle: () -> Unit,
    palette: HbPalette,
) {
    val t = LocalStrings.current
    val statusColor = when {
        emergency -> EmergencyRed
        stale -> palette.amber
        else -> palette.accentBright
    }
    val statusWord = when {
        emergency -> t.aircraft.emergency
        stale -> t.aircraft.signalLost
        spec.onGround -> t.aircraft.onGround
        else -> t.aircraft.airborne
    }
    Row(
        Modifier
            .fillMaxWidth()
            .height(28.dp)
            .background(if (emergency) EmergencyRed.copy(alpha = 0.14f) else palette.accent.copy(alpha = 0.1f))
            .padding(start = 12.dp, end = 6.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        val title = spec.tail + (spec.callsign?.let { " · $it" } ?: "")
        BasicText(
            AnnotatedString(title),
            style = HbType.readout.copy(fontSize = 12.5.sp, letterSpacing = 0.16.em, color = Color.White),
        )
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            BasicText(
                AnnotatedString(statusWord),
                style = HbType.readout.copy(fontSize = 10.5.sp, letterSpacing = 0.1.em, color = statusColor),
            )
            LiveToggle(paused, onToggle, palette)
        }
    }
}

/** The owner's own on/off switch for the 15s poll — always available
 * regardless of why polling is or isn't running (still tracking, landed,
 * gave up after GIVE_UP_AFTER_MISSES, or a manual pause from a prior tap).
 * Resuming always restarts with a clean miss count. */
@Composable
private fun LiveToggle(paused: Boolean, onToggle: () -> Unit, palette: HbPalette) {
    val t = LocalStrings.current
    Row(
        Modifier
            .clip(RoundedCornerShape(999.dp))
            .background(if (paused) Color.White.copy(alpha = 0.06f) else palette.accent.copy(alpha = 0.16f))
            .border(
                1.dp,
                if (paused) palette.edge else palette.accent.copy(alpha = 0.4f),
                RoundedCornerShape(999.dp),
            )
            .clickable(onClick = onToggle)
            .padding(horizontal = 9.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(5.dp),
    ) {
        Box(
            Modifier
                .size(6.dp)
                .clip(CircleShape)
                .background(if (paused) palette.textFaint else palette.accentBright),
        )
        BasicText(
            AnnotatedString(if (paused) t.aircraft.paused else t.aircraft.live),
            style = HbType.readout.copy(
                fontSize = 9.5.sp, letterSpacing = 0.08.em, fontWeight = FontWeight.Bold,
                color = if (paused) palette.textFaint else palette.accentBright,
            ),
        )
    }
}

/* ── MapLibre surface — one live-position marker, no routes/places ─────────── */

private class AircraftMapHolder {
    var map: MapLibreMap? = null
    var style: Style? = null
}

private const val AIRCRAFT_SRC = "aircraft-src"

@Composable
private fun AircraftSurface(spec: AircraftSpec, palette: HbPalette) {
    val accentBright = palette.accentBright.toArgb()
    val mapView = rememberMapViewWithLifecycle()
    val holder = remember { AircraftMapHolder() }

    AndroidView(
        factory = {
            mapView.getMapAsync { map ->
                map.uiSettings.setAllGesturesEnabled(true)
                map.uiSettings.isAttributionEnabled = true
                map.uiSettings.isLogoEnabled = false
                map.setStyle(Style.Builder().fromUri("asset://map_style_stark.json")) { style ->
                    style.addSource(GeoJsonSource(AIRCRAFT_SRC, aircraftFeature(spec)))
                    style.addLayer(
                        CircleLayer("aircraft-dot", AIRCRAFT_SRC).withProperties(
                            PropertyFactory.circleColor(accentBright),
                            PropertyFactory.circleRadius(7f),
                            PropertyFactory.circleStrokeColor(android.graphics.Color.WHITE),
                            PropertyFactory.circleStrokeWidth(1.6f),
                            PropertyFactory.circleStrokeOpacity(0.85f),
                        ),
                    )
                    // Heading arrow — a rotated glyph, not a bitmap icon: this
                    // card needs no image asset for one marker.
                    style.addLayer(
                        SymbolLayer("aircraft-heading", AIRCRAFT_SRC).withProperties(
                            PropertyFactory.textField("▲"),
                            PropertyFactory.textSize(14f),
                            PropertyFactory.textColor(accentBright),
                            PropertyFactory.textRotate(Expression.get("heading")),
                            PropertyFactory.textRotationAlignment(Property.TEXT_ROTATION_ALIGNMENT_MAP),
                            PropertyFactory.textAllowOverlap(true),
                            PropertyFactory.textIgnorePlacement(true),
                            PropertyFactory.textOffset(arrayOf(0f, -1.6f)),
                        ),
                    )
                    holder.map = map
                    holder.style = style
                    map.moveCamera(CameraUpdateFactory.newLatLngZoom(MlLatLng(spec.lat, spec.lng), 9.0))
                }
            }
            mapView
        },
        modifier = Modifier.fillMaxWidth(),
    )

    // Live position/heading → move the existing source, ease the camera.
    LaunchedEffect(spec.lat, spec.lng, spec.headingDeg) {
        val style = holder.style ?: return@LaunchedEffect
        val src = style.getSourceAs<GeoJsonSource>(AIRCRAFT_SRC) ?: return@LaunchedEffect
        src.setGeoJson(aircraftFeature(spec))
        holder.map?.easeCamera(CameraUpdateFactory.newLatLng(MlLatLng(spec.lat, spec.lng)), 800)
    }
}

private fun aircraftFeature(spec: AircraftSpec) =
    Feature.fromGeometry(Point.fromLngLat(spec.lng, spec.lat)).apply {
        addNumberProperty("heading", spec.headingDeg ?: 0.0)
    }

/* ── Status readout ──────────────────────────────────────────────────────── */

@Composable
private fun StatusReadout(
    spec: AircraftSpec,
    stale: Boolean,
    paused: Boolean,
    autoPaused: Boolean,
    emergency: Boolean,
    palette: HbPalette,
) {
    val t = LocalStrings.current
    val rows = buildList {
        add(t.aircraft.type to (spec.aircraftType ?: "—"))
        add(
            t.aircraft.alt to when {
                spec.onGround -> t.aircraft.ground
                spec.altitudeFt != null -> "${spec.altitudeFt} FT"
                else -> "—"
            },
        )
        if (!spec.onGround) {
            add(t.aircraft.speed to (spec.groundSpeedKt?.let { "${it.toInt()} KT" } ?: "—"))
            add(t.aircraft.heading to (spec.headingDeg?.let { "${it.toInt()}°" } ?: "—"))
        }
        add(t.aircraft.squawk to (spec.squawk ?: "—"))
    }

    Row(
        Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState())
            .padding(horizontal = 12.dp, vertical = 10.dp),
        horizontalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        rows.forEach { (label, value) ->
            Column {
                BasicText(
                    AnnotatedString(label),
                    style = HbType.readout.copy(fontSize = 10.sp, letterSpacing = 0.14.em, color = palette.textFaint),
                )
                BasicText(
                    AnnotatedString(value),
                    style = HbType.readout.copy(fontSize = 12.5.sp, color = palette.text),
                )
            }
        }
    }

    if (emergency) {
        val squawkPart = spec.squawk?.let { " $it" } ?: ""
        val kindPart = spec.emergency?.takeIf { it != "none" }?.let { " — $it" } ?: ""
        Box(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp)
                .padding(bottom = 10.dp)
                .background(EmergencyRed.copy(alpha = 0.12f), RoundedCornerShape(6.dp))
                .border(1.dp, EmergencyRed.copy(alpha = 0.4f), RoundedCornerShape(6.dp))
                .padding(horizontal = 10.dp, vertical = 8.dp),
        ) {
            BasicText(
                AnnotatedString("${t.aircraft.emergencySquawkPrefix}$squawkPart$kindPart"),
                style = HbType.readout.copy(fontSize = 12.sp, color = EmergencyRed),
            )
        }
    } else if (paused || stale) {
        val message = when {
            !paused -> t.aircraft.staleWarning
            spec.onGround -> t.aircraft.landedPausedWarning
            autoPaused -> t.aircraft.signalLostPausedWarning
            else -> t.aircraft.pausedManualWarning
        }
        Box(Modifier.fillMaxWidth().padding(horizontal = 12.dp).padding(bottom = 10.dp)) {
            BasicText(
                AnnotatedString(message),
                style = HbType.readout.copy(fontSize = 12.sp, color = palette.amber),
            )
        }
    }
}
