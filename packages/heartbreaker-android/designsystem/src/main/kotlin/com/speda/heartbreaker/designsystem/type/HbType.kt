package com.speda.heartbreaker.designsystem.type

import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.designsystem.R

/**
 * ════════════════════════════════════════════════════════════════════════════
 *  Typography — mirrors the CSS type ramp in heartbreaker.css. Every size,
 *  weight, tracking and line-height traces back to a CSS rule; `1rem = 16sp`,
 *  the CSS rem values are kept, not re-derived.
 *
 *  FONTS: the real web stack, bundled here from Google Fonts (all OFL). The
 *  renderer's SamsungOne/SamsungSharpSans @font-face rules point at TTFs that do
 *  not exist in the repo, so the web actually renders Rajdhani + Inter — which is
 *  exactly what ships here.
 *
 *  Inter and JetBrains Mono are published only as VARIABLE fonts; Compose drives
 *  their `wght` axis from the requested FontWeight (Font()'s default
 *  variationSettings derive from it), so one file covers every weight.
 * ════════════════════════════════════════════════════════════════════════════
 */
object HbFonts {
    /** Rajdhani — HUD chrome, all-caps letter-spaced labels (`--font-ui`). */
    val Ui: FontFamily = FontFamily(
        Font(R.font.rajdhani_light, FontWeight.Light),        // 300 — .hb-num-thin
        Font(R.font.rajdhani_regular, FontWeight.Normal),     // 400
        Font(R.font.rajdhani_medium, FontWeight.Medium),      // 500 — the greeting
        Font(R.font.rajdhani_semibold, FontWeight.SemiBold),  // 600 — .hb-label
        Font(R.font.rajdhani_bold, FontWeight.Bold),          // 700 — header plates
        // Rajdhani ships no 800; the CSS asks for it and the browser clamps to 700
        // anyway, so the hero maps to Bold rather than synthesising a fake weight.
        Font(R.font.rajdhani_bold, FontWeight.ExtraBold),
    )

    /** Inter — chat reading + "mono" readouts (`--font-read` / `--font-mono`). */
    val Read: FontFamily = FontFamily(
        Font(R.font.inter_variable, FontWeight.Normal),
        Font(R.font.inter_variable, FontWeight.Medium),
        Font(R.font.inter_variable, FontWeight.SemiBold),
        Font(R.font.inter_variable, FontWeight.Bold),
    )

    /** JetBrains Mono — code blocks only (CodeBlock.tsx). */
    val Mono: FontFamily = FontFamily(
        Font(R.font.jetbrains_mono_variable, FontWeight.Normal),
        Font(R.font.jetbrains_mono_variable, FontWeight.Bold),
    )
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
