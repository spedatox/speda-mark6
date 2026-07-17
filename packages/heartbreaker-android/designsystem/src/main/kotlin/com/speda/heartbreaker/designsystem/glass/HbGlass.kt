package com.speda.heartbreaker.designsystem.glass

import android.graphics.BlurMaskFilter
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Paint
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.composed
import com.speda.heartbreaker.designsystem.theme.HbPalette
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette

/**
 * ════════════════════════════════════════════════════════════════════════════
 *  THE ONE GLASS MATERIAL — the single canonical recipe for every glass surface
 *  in the app, ported from the `.glass` block in heartbreaker.css. State is
 *  expressed with thin modifiers ([HbGlassState]), never a per-component recipe
 *  (plan principle 3).
 *
 *  This is the pure-Compose realisation: an occluding dark fill + milky tint,
 *  the 5-part light/shadow stack (top specular, left edge, bottom rim, inner
 *  glow, soft drop), and a 1px accent rim. It draws correctly with NO backdrop
 *  blur — which is exactly the CSS-sanctioned nested / sub-blur fallback. Real
 *  backdrop blur for top-level surfaces is layered on via Modifier.hbHazeBlur
 *  (HbHaze.kt), kept separate so the core material never depends on Haze.
 *
 *  CSS reference (`:root` --glass-* tokens):
 *    fill    rgba(8,16,24,0.62)              (palette.glassFill, re-hued)
 *    tint    rgba(190,215,235,0.06)          (palette.glassTint, re-hued)
 *    border  1px --hb-edge                   (palette.edge, re-hued)
 *    shadow  inset 0 1px 0 white .28   (top specular streak)
 *            inset 1px 0 0 white .10   (left light edge)
 *            inset 0 -1px 0 white .06  (bottom rim / slab body)
 *            inset 0 0 2px 1px white .05 (inner glass glow)
 *            0 8px 32px black .35        (soft lift off the void)
 * ════════════════════════════════════════════════════════════════════════════
 */

enum class HbGlassShape { R14, R12, R9, Pill, TopOnly }

/** Thin state layers on the single material (`.glass-*` modifiers). */
sealed interface HbGlassState {
    data object Default : HbGlassState
    data object Active : HbGlassState
    data object Amber : HbGlassState
    /**
     * Floating menus/dropdowns (`--glass-menu`). They sit inside a backdrop root,
     * where their own blur is cancelled, so the fill must occlude on its own.
     */
    data object Menu : HbGlassState
    /** `.glass-tint` — rim + fill derive from [color] (the CSS `currentColor`). */
    data class Tint(val color: Color) : HbGlassState
    /** `.glass-ghost` — invisible slab; a glass wash appears only on hover. */
    data object Ghost : HbGlassState
}

fun HbGlassShape.toShape(): Shape = when (this) {
    HbGlassShape.R14 -> RoundedCornerShape(14.dp)
    HbGlassShape.R12 -> RoundedCornerShape(12.dp)
    HbGlassShape.R9 -> RoundedCornerShape(9.dp)
    HbGlassShape.Pill -> RoundedCornerShape(percent = 50)
    HbGlassShape.TopOnly -> RoundedCornerShape(topStart = 14.dp, topEnd = 14.dp)
}

// Fixed white light-stack alphas — meaning-independent, not re-hued.
private object GlassLight {
    const val TOP_SPECULAR = 0.28f
    const val TOP_SPECULAR_ACTIVE = 0.42f
    const val BOTTOM_RIM = 0.06f
    const val BOTTOM_RIM_ACTIVE = 0.08f
    const val INNER_GLOW = 0.05f
    const val DROP_ALPHA = 0.35f
    const val DROP_ALPHA_ACTIVE = 0.42f
    val DROP_OFFSET = 8.dp
    val DROP_BLUR = 32.dp
    val DROP_BLUR_ACTIVE = 34.dp
}

/**
 * Apply the unified glass material.
 *
 * @param shape corner-radius variant (default R14, the unified `--glass-r`).
 * @param state thin modifier layer on the single material.
 */
fun Modifier.hbGlass(
    shape: HbGlassShape = HbGlassShape.R14,
    state: HbGlassState = HbGlassState.Default,
): Modifier = composed {
    val palette = LocalHbPalette.current
    hbGlassInternal(palette, shape, state)
}

/** Palette-explicit form for previews / static composition (no CompositionLocal). */
fun Modifier.hbGlass(
    palette: HbPalette,
    shape: HbGlassShape = HbGlassShape.R14,
    state: HbGlassState = HbGlassState.Default,
): Modifier = hbGlassInternal(palette, shape, state)

private fun Modifier.hbGlassInternal(
    palette: HbPalette,
    shape: HbGlassShape,
    state: HbGlassState,
): Modifier {
    if (state is HbGlassState.Ghost) return this // invisible slab; hover wash handled by caller

    val cs = shape.toShape()
    val active = state is HbGlassState.Active

    // Menus occlude on their own (nested backdrop roots cancel their blur).
    val fill: Color = if (state is HbGlassState.Menu) palette.glassMenu else palette.glassFill
    val tint: Color = when (state) {
        is HbGlassState.Amber -> Color(red = 217, green = 156, blue = 68).copy(alpha = 0.20f)
        is HbGlassState.Tint -> state.color.copy(alpha = 0.16f)
        else -> palette.glassTint
    }
    val border: Color = when (state) {
        is HbGlassState.Active -> palette.edgeBright
        is HbGlassState.Amber -> Color(red = 217, green = 156, blue = 68).copy(alpha = 0.45f)
        is HbGlassState.Tint -> state.color.copy(alpha = 0.55f)
        else -> palette.edge
    }
    val topSpecular = if (active) GlassLight.TOP_SPECULAR_ACTIVE else GlassLight.TOP_SPECULAR
    val bottomRim = if (active) GlassLight.BOTTOM_RIM_ACTIVE else GlassLight.BOTTOM_RIM
    val dropAlpha = if (active) GlassLight.DROP_ALPHA_ACTIVE else GlassLight.DROP_ALPHA
    val dropBlur = if (active) GlassLight.DROP_BLUR_ACTIVE else GlassLight.DROP_BLUR

    return this
        // Soft drop that lifts the slab off the void (`0 8px 32px black`).
        .drawBehind { drawDropShadow(cs, dropAlpha, GlassLight.DROP_OFFSET, dropBlur) }
        .clip(cs)
        // Occluding dark fill, then the milky cool-white tint on top.
        .drawBehind {
            drawRect(color = fill)
            drawRect(color = tint)
        }
        // Rim + light stack drawn over the content, inside the clip.
        .drawWithContent {
            drawContent()
            val r = firstCornerRadiusPx(shape)
            val one = 1.dp.toPx()

            // 1px accent rim (border).
            drawRoundRect(
                color = border,
                cornerRadius = CornerRadius(r, r),
                style = Stroke(width = one),
            )
            // Top specular (bright) → bottom rim (faint): a single vertical white
            // gradient stroke captures the slab-thickness light behaviour.
            drawRoundRect(
                brush = Brush.verticalGradient(
                    0f to Color.White.copy(alpha = topSpecular),
                    0.5f to Color.White.copy(alpha = topSpecular * 0.18f),
                    1f to Color.White.copy(alpha = bottomRim),
                ),
                cornerRadius = CornerRadius(r, r),
                style = Stroke(width = one),
            )
            // Inner glass glow — a faint inset white stroke (`inset 0 0 2px white .05`).
            drawRoundRect(
                color = Color.White.copy(alpha = GlassLight.INNER_GLOW),
                topLeft = Offset(one, one),
                size = Size(size.width - one * 2, size.height - one * 2),
                cornerRadius = CornerRadius((r - one).coerceAtLeast(0f), (r - one).coerceAtLeast(0f)),
                style = Stroke(width = one),
            )
        }
}

/** First-corner radius in px for [shape]; Pill resolves to half the min side at draw time. */
private fun androidx.compose.ui.graphics.drawscope.DrawScope.firstCornerRadiusPx(shape: HbGlassShape): Float =
    when (shape) {
        HbGlassShape.R14, HbGlassShape.TopOnly -> 14.dp.toPx()
        HbGlassShape.R12 -> 12.dp.toPx()
        HbGlassShape.R9 -> 9.dp.toPx()
        HbGlassShape.Pill -> minOf(size.width, size.height) / 2f
    }

/** Offset, blurred, rounded black rect — the CSS `0 8px 32px black` drop. */
private fun androidx.compose.ui.graphics.drawscope.DrawScope.drawDropShadow(
    shape: Shape,
    alpha: Float,
    offsetY: Dp,
    blur: Dp,
) {
    val radiusPx = when (shape) {
        is RoundedCornerShape -> 14.dp.toPx() // material shapes are all ≤14dp; exact per-corner not needed for a soft drop
        else -> 14.dp.toPx()
    }
    drawIntoCanvas { canvas ->
        val paint = Paint().apply {
            color = Color.Black.copy(alpha = alpha)
            asFrameworkPaint().maskFilter = BlurMaskFilter(blur.toPx(), BlurMaskFilter.Blur.NORMAL)
        }
        val dy = offsetY.toPx()
        canvas.drawRoundRect(
            left = 0f,
            top = dy,
            right = size.width,
            bottom = size.height + dy,
            radiusX = radiusPx,
            radiusY = radiusPx,
            paint = paint,
        )
    }
}

/** Padding presets matching common CSS glass insets (kept here so call sites stay terse). */
object HbGlassInsets {
    val Chip = PaddingValues(horizontal = 6.dp, vertical = 1.dp)
    val Button = PaddingValues(horizontal = 12.dp, vertical = 6.dp)
    val Panel = PaddingValues(12.dp)
}
