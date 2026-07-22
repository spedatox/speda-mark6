package com.speda.heartbreaker.designsystem.brand

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Matrix
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipPath
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/**
 * AgentMark — the agent wordmarks in the fluid-glass material. The Android
 * mirror of the renderer's components/AgentMark.tsx, drawn on Canvas from the
 * shared 100-viewBox geometry in [AgentMarks].
 *
 * Three finishes, same rules as the web:
 *   [Finish.Flat]   solid accent. Dense lists and small chips.
 *   [Finish.Glass]  accent body, sheen down the top-left, specular bloom and a
 *                   lit rim. Use at 28.dp and up — below that the bloom eats
 *                   the geometry.
 *   [Finish.Etched] hairline outline, for already-lit surfaces.
 *
 * Agents with no art (orion, warroom) draw nothing; callers should test
 * [AgentMarks.has] and fall back to a monogram.
 */
enum class Finish { Flat, Glass, Etched }

private const val VIEW_BOX = 100f

/** Parses once per agent and caches — PathParser is not cheap in a list. */
@Composable
private fun rememberMarkPath(agentId: String): Path? {
    val d = AgentMarks.PATHS[agentId] ?: return null
    return remember(agentId) { PathParser().parsePathString(d).toPath() }
}

@Composable
fun AgentMark(
    agentId: String,
    color: Color,
    size: Dp,
    finish: Finish = Finish.Glass,
    modifier: Modifier = Modifier,
) {
    val path = rememberMarkPath(agentId) ?: return
    Box(modifier.size(size)) {
        Canvas(Modifier.size(size)) {
            val k = this.size.minDimension / VIEW_BOX
            val scaled = Path().apply {
                addPath(path)
                transform(Matrix().apply { scale(k, k) })
            }
            when (finish) {
                Finish.Flat -> drawPath(scaled, color)
                Finish.Etched -> drawPath(
                    scaled, color.copy(alpha = 0.85f),
                    style = Stroke(width = 1.4f * k, join = StrokeJoin.Round),
                )
                Finish.Glass -> drawGlass(scaled, color, k)
            }
        }
    }
}

/** Accent body, sheen, specular bloom, lit rim — the .hb-holo recipe. */
private fun DrawScope.drawGlass(path: Path, color: Color, k: Float) {
    val w = size.width
    val h = size.height

    drawPath(path, color.copy(alpha = 0.92f))

    clipPath(path) {
        // Sheen: white falling away from the top-left, same stops as the SVG.
        drawRect(
            Brush.linearGradient(
                0f to Color.White.copy(alpha = 0.62f),
                0.42f to Color.White.copy(alpha = 0.10f),
                1f to Color.Transparent,
                start = Offset(0.08f * w, 0f),
                end = Offset(0.62f * w, h),
            ),
        )
        // Specular bloom off the upper-left shoulder.
        drawRect(
            Brush.radialGradient(
                0f to Color.White.copy(alpha = 0.42f),
                1f to Color.Transparent,
                center = Offset(0.28f * w, 0.20f * h),
                radius = 0.8f * maxOf(w, h),
            ),
        )
    }

    drawPath(
        path, Color.White.copy(alpha = 0.38f),
        style = Stroke(width = 0.9f * k, join = StrokeJoin.Round),
    )
}
