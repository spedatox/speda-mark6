package com.speda.heartbreaker.designsystem.theme

import androidx.compose.runtime.Immutable
import androidx.compose.ui.graphics.Color

/**
 * The fully-resolved token set for ONE agent accent — every `--hb-*` / `--glass-*`
 * / `--bg-*` custom property from theme.ts, as a Compose [Color]. Produced by
 * [ThemeEngine.buildPalette] and handed down the tree via `LocalHbPalette`.
 *
 * Semantic colours (amber / red / green) are meaning-bearing and identical on
 * every agent; they are carried here so components read one palette object.
 */
@Immutable
data class HbPalette(
    // ── Re-hued structural (BASE_HEX) ──────────────────────────────────────
    val void: Color,
    val base: Color,
    val petrol: Color,
    val steel: Color,
    val text: Color,
    val textDim: Color,
    val textFaint: Color,
    val bgCode: Color,
    val bgCodeHeader: Color,
    val icon: Color,
    val iconDim: Color,
    val iconBright: Color,
    // ── Re-hued rgba (BASE_RGBA) ───────────────────────────────────────────
    val line: Color,
    val lineBright: Color,
    val edge: Color,
    val edgeBright: Color,
    val bgSidebar: Color,
    val bgHover: Color,
    val bgInput: Color,
    val bgUserBubble: Color,
    val scrollbarThumb: Color,
    val scrollbarThumbHover: Color,
    val glassTint: Color,
    val glassTintHi: Color,
    val glassFill: Color,
    val glassMenu: Color,
    // ── Accent family (exact brand colour, not re-hued) ────────────────────
    val accent: Color,
    val accentBright: Color,
    val accentDim: Color,
    val accentMuted: Color,
    val bgActive: Color,
    // ── Semantic (fixed across agents) ─────────────────────────────────────
    val amber: Color,
    val amberBright: Color,
    val amberDim: Color,
    val red: Color,
    val green: Color,
) {
    /** Filled header-bar gradient stops: `linear-gradient(180deg, bright, accent 70%, dim)`. */
    val barGradient: Triple<Color, Color, Color> get() = Triple(accentBright, accent, accentDim)
}
