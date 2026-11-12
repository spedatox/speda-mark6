// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui.prose

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.caverock.androidsvg.SVG
import com.caverock.androidsvg.RenderOptions
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.i18n.LocalStrings
import kotlin.math.roundToInt

/**
 * ```svg fences — native vector rendering, the Android counterpart to the web's
 * WidgetFrame(language="svg"). The model emits hand-written SVG for diagrams,
 * flowcharts and timelines (prompts/core/06_visual_output); before this they fell
 * through to the glass code block and showed as raw markup on the phone.
 *
 * AndroidSVG parses the markup into a Picture we scale onto a Compose Canvas —
 * crisp at any density, no WebView, no rasterisation. The SVG contract is
 * transparent-background + viewBox (no width/height), so the panel supplies the
 * dark surface and the viewBox drives the aspect ratio.
 */
@Composable
fun SvgBlock(raw: String, modifier: Modifier = Modifier) {
    val t = LocalStrings.current
    val assets = LocalContext.current.assets
    val themed = "mkvi-diagram" in raw
    val css = remember(assets, themed) {
        if (themed) assets.open("markvi-diagram.css").bufferedReader().use { it.readText() } else null
    }
    val svg = remember(raw, css) {
        val fitted = if (css != null && !looksLikeStreamingSvg(raw)) runCatching {
            fitMarkViLabels(raw, css)
        }.getOrDefault(raw) else raw
        runCatching { SVG.getFromString(fitted) }.getOrNull()
            ?: if (fitted != raw) runCatching { SVG.getFromString(raw) }.getOrNull() else null
    }
    when {
        svg != null -> SvgSurface(svg, modifier, css)
        // A fence still streaming (no closing tag yet) shows the quiet skeleton,
        // not a scary parse error that vanishes a frame later.
        looksLikeStreamingSvg(raw) -> Materializing(t.proseKind.diagram, modifier)
        else -> ParseError(t.proseKind.svg, raw, modifier)
    }
}

@Composable
@OptIn(ExperimentalFoundationApi::class)
private fun SvgSurface(svg: SVG, modifier: Modifier, css: String?) {
    val palette = LocalHbPalette.current
    val labels = LocalStrings.current.proseKind
    val description = remember(svg) {
        listOfNotNull(svg.documentTitle, svg.documentDescription).filter { it.isNotBlank() }.joinToString(". ")
    }
    val diagram = remember(palette, css) {
        css?.let {
            val colors = mapOf(
                "PANEL" to palette.petrol, "TEXT" to palette.text, "MUTED" to palette.textDim,
                "ACCENT" to palette.accentBright, "GOOD" to palette.green, "BAD" to palette.red,
            )
            val resolved = Regex("__([A-Z]+)__").replace(css) { match ->
                "#" + (colors.getValue(match.groupValues[1]).toArgb() and 0xffffff).toString(16).padStart(6, '0')
            }
            RenderOptions.create().css(resolved)
        }
    }
    var zoom by remember(svg) { mutableFloatStateOf(1f) }
    var offset by remember(svg) { mutableStateOf(Offset.Zero) }
    var viewport by remember { mutableStateOf(IntSize.Zero) }
    fun move(nextZoom: Float, nextOffset: Offset) {
        zoom = nextZoom.coerceIn(1f, 8f)
        val x = viewport.width * (zoom - 1f) / 2f
        val y = viewport.height * (zoom - 1f) / 2f
        offset = Offset(nextOffset.x.coerceIn(-x, x), nextOffset.y.coerceIn(-y, y))
    }
    val gestures = rememberTransformableState { scale, pan, _ -> move(zoom * scale, offset + pan) }
    // viewBox ratio drives the panel height; clamp so a degenerate viewBox can't
    // produce a sliver or a wall. Default to 16:10 when the SVG omits a viewBox.
    val ratio = remember(svg) {
        val declared = svg.documentAspectRatio
        val vb = svg.documentViewBox
        val r = when {
            declared > 0f -> declared
            vb != null && vb.height() > 0f -> vb.width() / vb.height()
            else -> 1.6f
        }
        r.coerceIn(0.4f, 3.0f)
    }

    Column(
        modifier
            .fillMaxWidth()
            .padding(vertical = 12.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(palette.base)
            .padding(horizontal = 10.dp, vertical = 4.dp),
    ) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text("◇  " + labels.diagram, Modifier.weight(1f), color = palette.accentBright, fontSize = 11.sp)
            TextButton(onClick = { move(zoom / 1.5f, offset / 1.5f) }, enabled = zoom > 1f,
                modifier = Modifier.semantics { contentDescription = labels.zoomOut }) { Text("−") }
            Text("${(zoom * 100).roundToInt()}%", color = palette.textDim, fontSize = 12.sp)
            TextButton(onClick = { move(zoom * 1.5f, offset * 1.5f) }, enabled = zoom < 8f,
                modifier = Modifier.semantics { contentDescription = labels.zoomIn }) { Text("+") }
            TextButton(onClick = { move(1f, Offset.Zero) }) { Text(labels.fitDiagram) }
        }
        Canvas(
            Modifier
                .fillMaxWidth()
                .heightIn(max = 460.dp)
                .aspectRatio(ratio)
                .clipToBounds()
                .semantics { contentDescription = description.ifBlank { labels.diagram } }
                .onSizeChanged {
                    viewport = it
                    move(zoom, offset)
                }
                .transformable(gestures, canPan = { zoom > 1f }),
        ) {
            val w = size.width.toInt()
            val h = size.height.toInt()
            if (w <= 0 || h <= 0) return@Canvas
            // renderToPicture handles the viewBox → target-size scale for us.
            val picture = runCatching {
                if (diagram == null) svg.renderToPicture(w, h) else svg.renderToPicture(w, h, diagram)
            }.getOrNull() ?: return@Canvas
            drawIntoCanvas {
                val canvas = it.nativeCanvas
                canvas.save()
                canvas.translate(offset.x, offset.y)
                canvas.scale(zoom, zoom, w / 2f, h / 2f)
                canvas.drawPicture(picture)
                canvas.restore()
            }
        }
    }
}

/** A `<svg …>` that hasn't reached its `</svg>` yet — treat as mid-stream, not broken. */
private fun looksLikeStreamingSvg(raw: String): Boolean {
    val t = raw.trimStart()
    return t.startsWith("<svg", ignoreCase = true) && !raw.contains("</svg>", ignoreCase = true)
}
