package com.speda.heartbreaker.designsystem.background

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.rotate
import com.speda.heartbreaker.designsystem.theme.BaseTokens
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.theme.ThemeEngine
import kotlin.math.max

/**
 * ════════════════════════════════════════════════════════════════════════════
 *  AmbientBackground — living gradient atmosphere behind the glass. Literal port
 *  of NeuralBackground.tsx (the exported AmbientBackground): 3 soft radial blobs
 *  orbiting on independent keyframed paths + 2 slow rotating volumetric sweeps,
 *  all in the live accent so the whole atmosphere re-hues during an agent morph.
 *
 *  Keyframe tables (top%, left%, opacity) and durations are copied verbatim from
 *  the ambOrbit1..3 / ambSweep1..2 CSS keyframes. Per plan §3.2, radial-to-
 *  transparent falloff already reads soft at phone size — heavy Modifier.blur is
 *  omitted until a side-by-side demands it.
 * ════════════════════════════════════════════════════════════════════════════
 */

/** One keyframe stop: [at] in 0..1 of the loop, blob top/left as viewport frac, opacity. */
private data class OrbitKey(val at: Float, val top: Float, val left: Float, val opacity: Float)

private val ORBIT1 = listOf(
    OrbitKey(0f, -0.05f, -0.10f, 0.90f),
    OrbitKey(0.20f, 0.20f, 0.55f, 1.00f),
    OrbitKey(0.40f, 0.55f, 0.60f, 0.75f),
    OrbitKey(0.60f, 0.58f, 0.08f, 0.85f),
    OrbitKey(0.80f, 0.15f, -0.08f, 1.00f),
    OrbitKey(1.00f, -0.05f, -0.10f, 0.90f),
)
private val ORBIT2 = listOf(
    OrbitKey(0f, 0.60f, 0.62f, 0.80f),
    OrbitKey(0.25f, 0.08f, 0.35f, 1.00f),
    OrbitKey(0.50f, -0.08f, -0.05f, 0.70f),
    OrbitKey(0.75f, 0.42f, 0.02f, 0.90f),
    OrbitKey(1.00f, 0.60f, 0.62f, 0.80f),
)
private val ORBIT3 = listOf(
    OrbitKey(0f, 0.30f, 0.70f, 0.70f),
    OrbitKey(0.33f, 0.65f, 0.30f, 1.00f),
    OrbitKey(0.66f, 0.05f, 0.50f, 0.80f),
    OrbitKey(1.00f, 0.30f, 0.70f, 0.70f),
)

private fun sample(frac: Float, keys: List<OrbitKey>): OrbitKey {
    val f = frac.coerceIn(0f, 0.999999f)
    for (i in 0 until keys.size - 1) {
        val a = keys[i]; val b = keys[i + 1]
        if (f >= a.at && f <= b.at) {
            val t = if (b.at == a.at) 0f else (f - a.at) / (b.at - a.at)
            return OrbitKey(
                at = f,
                top = a.top + (b.top - a.top) * t,
                left = a.left + (b.left - a.left) * t,
                opacity = a.opacity + (b.opacity - a.opacity) * t,
            )
        }
    }
    return keys.last()
}

/**
 * @param accentOverride when non-null, forces a fixed accent (previews / gallery
 *   comparison). Normally the ambient reads the live [LocalHbPalette] accent so
 *   it morphs with the agent.
 */
@Composable
fun AmbientBackground(
    modifier: Modifier = Modifier,
    accentOverride: Color? = null,
) {
    val accent = accentOverride ?: LocalHbPalette.current.accent

    // 160° gradient ≈ top-left → bottom-right diagonal; Compose's default
    // start (top-left) is close enough for the near-uniform void wash.
    val bodyStops = BaseTokens.BODY_GRADIENT_STOPS
        .map { (stop, hex) -> stop to ThemeEngine.parseHex(hex) }
        .toTypedArray()
    val bodyBrush = Brush.linearGradient(*bodyStops)

    val transition = rememberInfiniteTransition(label = "ambient")
    val t1 by transition.orbitPhase(22_000, "orbit1")
    val t2 by transition.orbitPhase(16_000, "orbit2")
    val t3 by transition.orbitPhase(12_000, "orbit3")
    val sweep1 by transition.pingPong(28_000, "sweep1")
    val sweep2 by transition.pingPong(20_000, "sweep2")

    Canvas(modifier = modifier.fillMaxSize().background(bodyBrush)) {
        // Blobs — sizes are (vw, vh) fractions like the CSS.
        drawBlob(sample(t1, ORBIT1), 0.55f, 0.55f, accent, baseAlpha = 0.24f, transparentStop = 0.68f)
        drawBlob(sample(t2, ORBIT2), 0.42f, 0.42f, accent, baseAlpha = 0.16f, transparentStop = 0.65f)
        drawBlob(sample(t3, ORBIT3), 0.28f, 0.28f, accent, baseAlpha = 0.12f, transparentStop = 0.60f)

        // Volumetric sweeps — soft accent bands rotating slowly across the scene.
        drawSweep(angleDeg = -15f + 30f * sweep1, heightFrac = 0.35f, peakAlpha = 0.07f, accent = accent)
        drawSweep(angleDeg = 20f - 30f * sweep2, heightFrac = 0.25f, peakAlpha = 0.05f, accent = accent)
    }
}

private fun DrawScope.drawBlob(
    k: OrbitKey,
    wFrac: Float,
    hFrac: Float,
    accent: Color,
    baseAlpha: Float,
    transparentStop: Float,
) {
    val bw = size.width * wFrac
    val bh = size.height * hFrac
    val cx = k.left * size.width + bw / 2f
    val cy = k.top * size.height + bh / 2f
    val radius = max(bw, bh) / 2f
    drawCircle(
        brush = Brush.radialGradient(
            0f to accent.copy(alpha = baseAlpha * k.opacity),
            transparentStop to accent.copy(alpha = baseAlpha * k.opacity),
            1f to Color.Transparent,
            center = Offset(cx, cy),
            radius = radius,
        ),
        radius = radius,
        center = Offset(cx, cy),
    )
}

private fun DrawScope.drawSweep(angleDeg: Float, heightFrac: Float, peakAlpha: Float, accent: Color) {
    val bandH = size.height * heightFrac
    val cy = size.height / 2f
    rotate(degrees = angleDeg, pivot = Offset(size.width / 2f, cy)) {
        drawRect(
            brush = Brush.horizontalGradient(
                0.10f to Color.Transparent,
                0.40f to accent.copy(alpha = peakAlpha * 0.6f),
                0.50f to accent.copy(alpha = peakAlpha),
                0.60f to accent.copy(alpha = peakAlpha * 0.6f),
                0.90f to Color.Transparent,
            ),
            topLeft = Offset(-size.width * 0.1f, cy - bandH / 2f),
            size = Size(size.width * 1.2f, bandH),
        )
    }
}

// A 0→1 phase that loops linearly over [durationMs] (blob orbit progress).
@Composable
private fun androidx.compose.animation.core.InfiniteTransition.orbitPhase(durationMs: Int, label: String) =
    animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(durationMs, easing = LinearEasing), RepeatMode.Restart),
        label = label,
    )

// A 0→1→0 ping-pong for the rotating sweeps (CSS sweeps ease to a mid extreme and back).
@Composable
private fun androidx.compose.animation.core.InfiniteTransition.pingPong(durationMs: Int, label: String) =
    animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(durationMs, easing = LinearEasing), RepeatMode.Reverse),
        label = label,
    )
