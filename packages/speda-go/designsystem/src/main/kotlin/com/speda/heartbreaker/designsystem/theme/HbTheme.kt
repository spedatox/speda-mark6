package com.speda.heartbreaker.designsystem.theme

import androidx.compose.animation.core.animate
import androidx.compose.animation.core.tween
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.ProvidableCompositionLocal
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.runtime.withFrameMillis
import com.speda.heartbreaker.designsystem.brand.Brands
import com.speda.heartbreaker.designsystem.color.ColorMath
import com.speda.heartbreaker.designsystem.motion.Motion

/**
 * The current agent palette, provided down the tree. Read it anywhere with
 * `LocalHbPalette.current`. Defaults to SPEDA so previews and tests render
 * without an explicit [HbTheme] wrapper.
 */
val LocalHbPalette: ProvidableCompositionLocal<HbPalette> =
    staticCompositionLocalOf { ThemeEngine.buildPalette(Brands.DEFAULT_AGENT.let { Brands.BRANDS.getValue(it).accent }) }

/** The current accent hex string, for the rare component that needs the raw hue. */
val LocalAccentHex: ProvidableCompositionLocal<String> =
    staticCompositionLocalOf { Brands.BRANDS.getValue(Brands.DEFAULT_AGENT).accent }

/**
 * Root theme host. Owns the live accent and rebuilds the whole palette every
 * frame during a transition — the web's "whole world shifts hue together"
 * (theme.ts morphTheme / startPartyCycle).
 *
 * - [accentHex] is the target brand accent. When it changes, the palette morphs
 *   over [Motion.MORPH_MS] using [Motion.EaseInOutQuad], re-mixing the accent in
 *   sRGB space each frame (identical to morphTheme — NOT Compose's Oklab lerp).
 * - When [partyEngaged] is true, the House Party colour parade owns the accent,
 *   drifting through [Brands.PARTY_COLORS] in ROSTER order (startPartyCycle).
 *   A brand change while engaged must not snap the parade — the party loop wins
 *   until it ends, then the palette morphs to whatever [accentHex] now is.
 */
@Composable
fun HbTheme(
    accentHex: String,
    partyEngaged: Boolean = false,
    content: @Composable () -> Unit,
) {
    var displayedAccent by remember { mutableStateOf(accentHex) }

    // Normal agent morph — skipped while the party parade owns the palette.
    LaunchedEffect(accentHex, partyEngaged) {
        if (partyEngaged) return@LaunchedEffect
        val from = displayedAccent
        if (from == accentHex) return@LaunchedEffect
        animate(
            initialValue = 0f,
            targetValue = 1f,
            animationSpec = tween(durationMillis = Motion.MORPH_MS, easing = Motion.EaseInOutQuad),
        ) { eased, _ ->
            displayedAccent = ColorMath.mix(from, accentHex, eased.toDouble())
        }
        displayedAccent = accentHex
    }

    // House Party parade — literal port of startPartyCycle timing.
    LaunchedEffect(partyEngaged) {
        if (!partyEngaged) return@LaunchedEffect
        val colors = Brands.PARTY_COLORS
        val n = colors.size
        val fromAccent = displayedAccent
        val startMs = withFrameMillis { it }
        var last = 0L
        while (true) {
            val now = withFrameMillis { it }
            if (now - last < Motion.PARTY_TICK_MS) continue
            last = now
            val el = now - startMs
            displayedAccent = if (el < Motion.PARTY_LEAD_MS) {
                // ease out of the current brand into the parade
                ColorMath.mix(fromAccent, colors[0], Motion.Smoothstep.transform(el / Motion.PARTY_LEAD_MS.toFloat()).toDouble())
            } else {
                val t = (el - Motion.PARTY_LEAD_MS).toFloat() / Motion.PARTY_MS_PER_STOP
                val i = (t.toInt()) % n
                val frac = t - t.toInt()
                ColorMath.mix(colors[i], colors[(i + 1) % n], Motion.Smoothstep.transform(frac).toDouble())
            }
        }
    }

    val palette = remember(displayedAccent) { ThemeEngine.buildPalette(displayedAccent) }

    CompositionLocalProvider(
        LocalHbPalette provides palette,
        LocalAccentHex provides displayedAccent,
        content = content,
    )
}

/** Convenience: resolve a brand's palette without composition (previews/tests). */
fun paletteForAgent(agentId: String): HbPalette =
    ThemeEngine.buildPalette(Brands.BRANDS[agentId]?.accent ?: Brands.WARROOM.accent)
