package com.speda.heartbreaker.designsystem.type

import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp

/**
 * ════════════════════════════════════════════════════════════════════════════
 *  Typography — mirrors the CSS type ramp in heartbreaker.css. Every size,
 *  weight, tracking and line-height traces back to a CSS rule; `1rem = 16sp`,
 *  the CSS rem values are kept, not re-derived.
 *
 *  FONTS: the shipping web stack is Rajdhani (HUD chrome) + Inter (body/readouts)
 *  + JetBrains Mono (code) — all OFL, bundleable in res/font. Those TTF binaries
 *  are not in this repo yet (the web pulls Rajdhani/Inter from the Google CDN;
 *  SamsungOne is separately flagged missing). [HbFonts] is the single swap-point:
 *  drop the TTFs into res/font per its README and point these three families at
 *  the R.font resources. Until then they fall back to the platform families so
 *  the app compiles and runs; the visual-parity pass (§7) swaps in the real
 *  faces. No metric changes when they land — only the FontFamily.
 * ════════════════════════════════════════════════════════════════════════════
 */
object HbFonts {
    /** Rajdhani — HUD chrome, all-caps letter-spaced labels (`--font-ui`). */
    val Ui: FontFamily = FontFamily.SansSerif
    /** Inter — chat reading + "mono" readouts (`--font-read` / `--font-mono`). */
    val Read: FontFamily = FontFamily.SansSerif
    /** JetBrains Mono — code blocks only (CodeBlock.tsx). */
    val Mono: FontFamily = FontFamily.Monospace
}

object HbType {

    /** `.hb-label` — 0.62rem, 600, 0.18em tracking, uppercase (caps at call site). */
    val label = TextStyle(
        fontFamily = HbFonts.Ui,
        fontSize = 10.sp,
        fontWeight = FontWeight.SemiBold,
        letterSpacing = 0.18.em,
    )

    /** `.prose` body copy — Inter 0.95rem, line-height 1.7, no tracking. */
    val read = TextStyle(
        fontFamily = HbFonts.Read,
        fontSize = 15.sp,
        fontWeight = FontWeight.Normal,
        lineHeight = 1.7.em,
        letterSpacing = 0.em,
    )

    /** `.hb-readout` — Inter, 0.04em tracking, accent-bright colour at call site. */
    val readout = TextStyle(
        fontFamily = HbFonts.Read,
        fontSize = 12.sp,
        letterSpacing = 0.04.em,
    )

    /** `.hb-head-light` — Rajdhani 0.72rem, 700, 0.14em tracking, uppercase. */
    val headerBar = TextStyle(
        fontFamily = HbFonts.Ui,
        fontSize = 11.5.sp,
        fontWeight = FontWeight.Bold,
        letterSpacing = 0.14.em,
    )

    /** `.hb-head-cyan` / `.hb-panel-head` — 0.64–0.68rem, 700/600, 0.16em. */
    val headCyan = TextStyle(
        fontFamily = HbFonts.Ui,
        fontSize = 11.sp,
        fontWeight = FontWeight.Bold,
        letterSpacing = 0.16.em,
    )

    /** `.hb-num-thin` — Rajdhani 300, tabular numerals (calendar/countdown). */
    val numThin = TextStyle(
        fontFamily = HbFonts.Ui,
        fontWeight = FontWeight.Light,
        letterSpacing = 0.06.em,
        // tabular-nums + tight line — line-height:1 in CSS.
        lineHeight = 1.0.em,
    )

    /** Code — JetBrains Mono, code blocks (CodeBlock.tsx). */
    val code = TextStyle(
        fontFamily = HbFonts.Mono,
        fontSize = 13.sp,
        lineHeight = 1.5.em,
    )

    /** HUD strip micro-label — the 22px top strip readouts (HudFrame.tsx). */
    val hud = TextStyle(
        fontFamily = HbFonts.Ui,
        fontSize = 9.5.sp,
        fontWeight = FontWeight.SemiBold,
        letterSpacing = 0.12.em,
    )
}
