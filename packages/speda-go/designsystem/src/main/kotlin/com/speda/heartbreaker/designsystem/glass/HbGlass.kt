// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.designsystem.glass

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.composed
import com.speda.heartbreaker.designsystem.theme.HbPalette
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import dev.chrisbanes.haze.HazeState
import dev.chrisbanes.haze.HazeTint
import dev.chrisbanes.haze.hazeEffect

/**
 * ════════════════════════════════════════════════════════════════════════════
 *  THE ONE GLASS MATERIAL — the single canonical recipe for every glass surface
 *  in the app, ported from the `.glass` block in heartbreaker.css. State is
 *  expressed with thin modifiers ([HbGlassState]), never a per-component recipe
 *  (plan principle 3).
 *
 *  This is the pure-Compose realisation: an occluding dark fill + milky tint,
 *  the cached light stack (top specular, bottom rim, inner glow), and a 1px accent rim. It draws correctly with NO backdrop
 *  blur — which is exactly the CSS-sanctioned nested / sub-blur fallback. Real
 *  backdrop blur for top-level surfaces is layered on via Modifier.hbHazeBlur
 *  (HbHaze.kt), kept separate so the core material never depends on Haze.
 *
 *  CSS reference (`:root` --glass-* tokens):
 *    fill    rgba(7,11,17,0.42)              (palette.glassFill, re-hued)
 *    sheen   linear-gradient(160deg,
 *              --glass-tint, --glass-tint-2) (glassTint → glassTint2, re-hued)
 *    border  1px --hb-edge                   (palette.edge — FIXED neutral
 *                                             white, never re-hued)
 *    shadow  inset 0 1px 0 white .28   (top specular streak)
 *            inset 1px 0 0 white .10   (left light edge)
 *            inset 0 -1px 0 white .06  (bottom rim / slab body)
 *            inset 0 0 2px 1px white .05 (inner glass glow)
 *
 *  The sheen is a GRADIENT, not a flat wash: the slab is lit across its face so
 *  it reads as a pane catching light from above-left, rather than a uniformly
 *  tinted rectangle.
 * ════════════════════════════════════════════════════════════════════════════
 */

/**
 * The radius scale, named by what a surface IS rather than by its number —
 * mirroring `--r-island / --r-card / --r-ctl / --r-tile` in heartbreaker.css.
 *
 * The stops moved with the overhaul (the deck went from a sharp-cornered
 * instrument panel to floating glass islands), and numeric names hid that: a
 * component asking for `R12` was asking for "whatever 12 means today", while one
 * asking for [Ctl] keeps meaning the right thing when the stop moves again.
 *
 *   Island 26  standing panels — sidebar, telemetry column, the settings window
 *   Card   22  cards inside a panel — message bubbles, charts, the composer
 *   Ctl    16  things you press or type into — buttons, fields, header plates
 *   Tile   14  square glyph affordances — list rows, marks, rail tiles
 */
enum class HbGlassShape { Island, Card, Ctl, Tile, Pill, CardTop }

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
    HbGlassShape.Island -> RoundedCornerShape(26.dp)
    HbGlassShape.Card -> RoundedCornerShape(22.dp)
    HbGlassShape.Ctl -> RoundedCornerShape(16.dp)
    HbGlassShape.Tile -> RoundedCornerShape(14.dp)
    HbGlassShape.Pill -> RoundedCornerShape(percent = 50)
    HbGlassShape.CardTop -> RoundedCornerShape(topStart = 22.dp, topEnd = 22.dp)
}

// Fixed white light-stack alphas — meaning-independent, not re-hued.
private object GlassLight {
    const val TOP_SPECULAR = 0.28f
    const val TOP_SPECULAR_ACTIVE = 0.42f
    const val BOTTOM_RIM = 0.06f
    const val BOTTOM_RIM_ACTIVE = 0.08f
    const val INNER_GLOW = 0.05f

}

/**
 * Apply the unified glass material.
 *
 * @param shape corner-radius variant (default [HbGlassShape.Card], `--r-card`).
 * @param state thin modifier layer on the single material.
 */
fun Modifier.hbGlass(
    shape: HbGlassShape = HbGlassShape.Card,
    state: HbGlassState = HbGlassState.Default,
): Modifier = composed {
    val palette = LocalHbPalette.current
    hbGlassInternal(palette, shape, state, LocalHazeState.current)
}

/** Palette-explicit form for previews / static composition (no CompositionLocal). */
fun Modifier.hbGlass(
    palette: HbPalette,
    shape: HbGlassShape = HbGlassShape.Card,
    state: HbGlassState = HbGlassState.Default,
): Modifier = hbGlassInternal(palette, shape, state, null)

private fun Modifier.hbGlassInternal(
    palette: HbPalette,
    shape: HbGlassShape,
    state: HbGlassState,
    haze: HazeState?,
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
    // The resting material's sheen runs tint → tint2 across the face. A state
    // layer (amber, brand tint) is a deliberate flat wash and keeps one colour:
    // it is saying "this surface is in a state", which a gradient would soften
    // into decoration.
    val tint2: Color = if (state is HbGlassState.Default) palette.glassTint2 else tint
    val border: Color = when (state) {
        is HbGlassState.Active -> palette.edgeBright
        is HbGlassState.Amber -> Color(red = 217, green = 156, blue = 68).copy(alpha = 0.45f)
        is HbGlassState.Tint -> state.color.copy(alpha = 0.55f)
        else -> palette.edge
    }
    val topSpecular = if (active) GlassLight.TOP_SPECULAR_ACTIVE else GlassLight.TOP_SPECULAR
    val bottomRim = if (active) GlassLight.BOTTOM_RIM_ACTIVE else GlassLight.BOTTOM_RIM

    return this
        // No blurred mask shadow per control: large tablet panes made this costly.
        .clip(cs)
        // THE REFRACTION. With a backdrop present this is real frosted glass:
        // blur(28px) with the CSS fill + milky tint composited ON TOP of the
        // blurred backdrop, which is what makes it read as glass rather than a
        // flat translucent panel. Without one (previews, nested surfaces) it
        // falls back to the occluding fill — the degradation the CSS itself
        // sanctions, since nested backdrop roots cancel a child's blur anyway.
        .then(
            if (haze != null) {
                Modifier.hazeEffect(state = haze) {
                    blurRadius = BLUR_RADIUS
                    backgroundColor = palette.void
                    // Haze tints are flat colours — it has no gradient stop — so
                    // the lit-across-the-face sheen is only in the fallback path
                    // below. Over a real blurred backdrop the difference is not
                    // visible anyway: the backdrop's own variation dominates.
                    tints = listOf(HazeTint(fill), HazeTint(tint))
                    noiseFactor = 0f
                }
            } else {
                Modifier.drawWithCache {
                    val sheen = Brush.linearGradient(
                        colors = listOf(tint, tint2),
                        start = Offset.Zero,
                        end = Offset(size.width * 0.342f, size.height * 0.940f),
                    )
                    onDrawBehind {
                        drawRect(color = fill)
                        drawRect(brush = sheen)
                    }
                }
            },
        )
        // Rim + light stack drawn over the content, inside the clip.
        .drawWithCache {
            val r = firstCornerRadiusPx(shape)
            val one = 1.dp.toPx()

            val stroke = Stroke(width = one)
            val rimLight = Brush.verticalGradient(
                0f to Color.White.copy(alpha = topSpecular),
                0.5f to Color.White.copy(alpha = topSpecular * 0.18f),
                1f to Color.White.copy(alpha = bottomRim),
            )
            onDrawWithContent {
                drawContent()
                drawRoundRect(color = border, cornerRadius = CornerRadius(r, r), style = stroke)
                drawRoundRect(brush = rimLight, cornerRadius = CornerRadius(r, r), style = stroke)
            // Inner glass glow — a faint inset white stroke (`inset 0 0 2px white .05`).
            drawRoundRect(
                color = Color.White.copy(alpha = GlassLight.INNER_GLOW),
                topLeft = Offset(one, one),
                size = Size(size.width - one * 2, size.height - one * 2),
                cornerRadius = CornerRadius((r - one).coerceAtLeast(0f), (r - one).coerceAtLeast(0f)),
                style = stroke,
            )
            }
        }
}

/** First-corner radius in px for [shape]; Pill resolves to half the min side at draw time. */
private fun androidx.compose.ui.graphics.drawscope.DrawScope.firstCornerRadiusPx(shape: HbGlassShape): Float =
    when (shape) {
        HbGlassShape.Island -> 26.dp.toPx()
        HbGlassShape.Card, HbGlassShape.CardTop -> 22.dp.toPx()
        HbGlassShape.Ctl -> 16.dp.toPx()
        HbGlassShape.Tile -> 14.dp.toPx()
        HbGlassShape.Pill -> minOf(size.width, size.height) / 2f
    }

/** Padding presets matching common CSS glass insets (kept here so call sites stay terse). */
object HbGlassInsets {
    val Chip = PaddingValues(horizontal = 6.dp, vertical = 1.dp)
    val Button = PaddingValues(horizontal = 12.dp, vertical = 6.dp)
    val Panel = PaddingValues(12.dp)
}
