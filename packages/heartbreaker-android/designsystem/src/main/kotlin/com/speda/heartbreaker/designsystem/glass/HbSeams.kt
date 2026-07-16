package com.speda.heartbreaker.designsystem.glass

import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

/**
 * Etched-glass seams — structural boundaries drawn as TWO 1px lines, exactly the
 * `.hb-seam-r/-b/-t` rules in heartbreaker.css. A real glass edge is not a
 * coloured stroke: it is a shadow-casting groove (black 0.4) beside a
 * light-catching rim (white 0.08). Both lines live inside the element and
 * dissolve toward the ends via a gradient (18%/82% vertical, 12%/88% horizontal).
 */

private val GROOVE = Color.Black.copy(alpha = 0.4f)
private val CATCH = Color.White.copy(alpha = 0.08f)

// Horizontal seams fade 12%→88%; the vertical seam fades 18%→82% (CSS stops).
private fun fadedHorizontal(color: Color, width: Float) = Brush.horizontalGradient(
    0f to Color.Transparent,
    0.12f to color,
    0.88f to color,
    1f to Color.Transparent,
    startX = 0f,
    endX = width,
)

private fun fadedVertical(color: Color, height: Float) = Brush.verticalGradient(
    0f to Color.Transparent,
    0.18f to color,
    0.82f to color,
    1f to Color.Transparent,
    startY = 0f,
    endY = height,
)

/** Bottom edge of a plate: groove above, razor light-catch at the very rim. */
fun Modifier.hbSeamBottom(): Modifier = drawWithContent {
    drawContent()
    val one = 1.dp.toPx()
    val w = size.width
    // groove at bottom:1px (row just above the rim), catch at bottom:0.
    drawLine(fadedHorizontal(GROOVE, w), Offset(0f, size.height - 1.5f * one), Offset(w, size.height - 1.5f * one), one)
    drawLine(fadedHorizontal(CATCH, w), Offset(0f, size.height - 0.5f * one), Offset(w, size.height - 0.5f * one), one)
}

/** Top edge of a plate: light-catch on the rim, groove just beneath it. */
fun Modifier.hbSeamTop(): Modifier = drawWithContent {
    drawContent()
    val one = 1.dp.toPx()
    val w = size.width
    drawLine(fadedHorizontal(CATCH, w), Offset(0f, 0.5f * one), Offset(w, 0.5f * one), one)
    drawLine(fadedHorizontal(GROOVE, w), Offset(0f, 1.5f * one), Offset(w, 1.5f * one), one)
}

/** Vertical boundary (sidebar → chat): groove, then light-catch at the edge. */
fun Modifier.hbSeamRight(): Modifier = drawWithContent {
    drawContent()
    val one = 1.dp.toPx()
    val h = size.height
    drawLine(fadedVertical(GROOVE, h), Offset(size.width - 1.5f * one, 0f), Offset(size.width - 1.5f * one, h), one)
    drawLine(fadedVertical(CATCH, h), Offset(size.width - 0.5f * one, 0f), Offset(size.width - 0.5f * one, h), one)
}
