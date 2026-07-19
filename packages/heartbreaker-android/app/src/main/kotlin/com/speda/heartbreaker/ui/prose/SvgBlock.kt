package com.speda.heartbreaker.ui.prose

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.unit.dp
import com.caverock.androidsvg.SVG

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
    val svg = remember(raw) { runCatching { SVG.getFromString(raw) }.getOrNull() }
    when {
        svg != null -> SvgSurface(svg, modifier)
        // A fence still streaming (no closing tag yet) shows the quiet skeleton,
        // not a scary parse error that vanishes a frame later.
        looksLikeStreamingSvg(raw) -> Materializing("DIAGRAM", modifier)
        else -> ParseError("SVG", raw, modifier)
    }
}

@Composable
private fun SvgSurface(svg: SVG, modifier: Modifier) {
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

    Box(
        modifier
            .fillMaxWidth()
            .padding(vertical = 12.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(Color(0xFF060E16).copy(alpha = 0.45f))
            .padding(12.dp),
    ) {
        Canvas(
            Modifier
                .fillMaxWidth()
                .aspectRatio(ratio)
                .heightIn(max = 460.dp),
        ) {
            val w = size.width.toInt()
            val h = size.height.toInt()
            if (w <= 0 || h <= 0) return@Canvas
            // renderToPicture handles the viewBox → target-size scale for us.
            val picture = runCatching { svg.renderToPicture(w, h) }.getOrNull() ?: return@Canvas
            drawIntoCanvas { it.nativeCanvas.drawPicture(picture) }
        }
    }
}

/** A `<svg …>` that hasn't reached its `</svg>` yet — treat as mid-stream, not broken. */
private fun looksLikeStreamingSvg(raw: String): Boolean {
    val t = raw.trimStart()
    return t.startsWith("<svg", ignoreCase = true) && !raw.contains("</svg>", ignoreCase = true)
}
