package com.speda.heartbreaker.designsystem.theme

import androidx.compose.ui.graphics.Color
import com.speda.heartbreaker.designsystem.color.ColorMath

/**
 * ════════════════════════════════════════════════════════════════════════════
 *  THE colour system, ported from profile/theme.ts. One accent in → the ENTIRE
 *  palette out, re-hued. [buildThemeVars] reproduces the TS function's output
 *  map exactly (the fixture-tested parity surface); [buildPalette] turns that
 *  map into a typed [HbPalette] of Compose colours for the UI.
 * ════════════════════════════════════════════════════════════════════════════
 */
object ThemeEngine {

    data class Accents(val accent: String, val bright: String, val dim: String)

    /** Derive the bright (active) and dim shades from a single accent hex. */
    fun deriveAccents(accent: String): Accents = Accents(
        accent = accent,
        bright = ColorMath.mixWhite(accent, 0.28),
        dim = ColorMath.mixVoid(accent, 0.62),
    )

    /**
     * Build every CSS custom property the UI uses, re-hued to [accent].
     * Output keys + string values are identical to theme.ts `buildThemeVars`,
     * which the fixture tests assert against.
     */
    fun buildThemeVars(accent: String): Map<String, String> {
        val h = ColorMath.rgbToHsl(ColorMath.hexToRgb(accent)).h
        val (_, bright, dim) = deriveAccents(accent)
        val a = ColorMath.hexToRgb(accent)
        val br = ColorMath.hexToRgb(bright)
        val dm = ColorMath.hexToRgb(dim)
        val out = LinkedHashMap<String, String>()

        for ((k, hex) in BaseTokens.BASE_HEX) {
            out[k] = ColorMath.rgbToHex(ColorMath.rehue(hex, h))
        }
        for ((k, spec) in BaseTokens.BASE_RGBA) {
            val (hex, alpha) = spec
            val c = ColorMath.rehue(hex, h)
            out[k] = "rgba(${ColorMath.clamp(c.r)}, ${ColorMath.clamp(c.g)}, ${ColorMath.clamp(c.b)}, $alpha)"
        }

        // Accent family — the EXACT brand colour (not re-hued, so it stays true).
        out["--hb-cyan"] = accent
        out["--hb-cyan-bright"] = bright
        out["--hb-cyan-dim"] = dim
        out["--accent"] = accent
        out["--accent-hover"] = bright
        out["--accent-muted"] = "rgba(${a.r.toInt()}, ${a.g.toInt()}, ${a.b.toInt()}, 0.15)"
        out["--bg-active"] = "rgba(${a.r.toInt()}, ${a.g.toInt()}, ${a.b.toInt()}, 0.16)"
        out["--bg-primary"] = out["--hb-base"]!!
        out["--hb-accent-rgb"] = "${a.r.toInt()}, ${a.g.toInt()}, ${a.b.toInt()}"
        out["--hb-cyan-bright-rgb"] = "${br.r.toInt()}, ${br.g.toInt()}, ${br.b.toInt()}"
        out["--hb-cyan-dim-rgb"] = "${dm.r.toInt()}, ${dm.g.toInt()}, ${dm.b.toInt()}"
        out["--hb-bar-cyan"] = "linear-gradient(180deg, $bright 0%, $accent 70%, $dim 100%)"

        return out
    }

    /** Turn the string token map into a typed palette of Compose colours. */
    fun buildPalette(accent: String): HbPalette {
        val v = buildThemeVars(accent)
        fun hex(key: String) = parseHex(v.getValue(key))
        fun rgba(key: String) = parseRgba(v.getValue(key))
        return HbPalette(
            void = hex("--hb-void"),
            base = hex("--hb-base"),
            petrol = hex("--hb-petrol"),
            steel = hex("--hb-steel"),
            text = hex("--hb-text"),
            textDim = hex("--hb-text-dim"),
            textFaint = hex("--hb-text-faint"),
            bgCode = hex("--bg-code"),
            bgCodeHeader = hex("--bg-code-header"),
            icon = hex("--hb-icon"),
            iconDim = hex("--hb-icon-dim"),
            iconBright = hex("--hb-icon-bright"),
            line = rgba("--hb-line"),
            lineBright = rgba("--hb-line-bright"),
            edge = rgba("--hb-edge"),
            edgeBright = rgba("--hb-edge-bright"),
            bgSidebar = rgba("--bg-sidebar"),
            bgHover = rgba("--bg-hover"),
            bgInput = rgba("--bg-input"),
            bgUserBubble = rgba("--bg-user-bubble"),
            scrollbarThumb = rgba("--scrollbar-thumb"),
            scrollbarThumbHover = rgba("--scrollbar-thumb-hover"),
            glassTint = rgba("--glass-tint"),
            glassTintHi = rgba("--glass-tint-hi"),
            glassFill = rgba("--glass-fill"),
            glassMenu = rgba("--glass-menu"),
            accent = hex("--hb-cyan"),
            accentBright = hex("--hb-cyan-bright"),
            accentDim = hex("--hb-cyan-dim"),
            accentMuted = rgba("--accent-muted"),
            bgActive = rgba("--bg-active"),
            // Semantic — fixed on every agent (theme.ts never re-hues these).
            amber = parseHex(BaseTokens.AMBER),
            amberBright = parseHex(BaseTokens.AMBER_BRIGHT),
            amberDim = Color(red = 217, green = 156, blue = 68, alpha = 36), // rgba(217,156,68,0.14) → 0.14*255≈36
            red = parseHex(BaseTokens.RED),
            green = parseHex(BaseTokens.GREEN),
        )
    }

    // ── string → Color parsers (the token map is the single computation) ──────

    /** `#rrggbb` → Color. Public so callers can resolve a raw brand accent. */
    fun parseHex(hex: String): Color {
        val h = hex.removePrefix("#")
        return Color(
            red = h.substring(0, 2).toInt(16),
            green = h.substring(2, 4).toInt(16),
            blue = h.substring(4, 6).toInt(16),
        )
    }

    internal fun parseRgba(s: String): Color {
        val inner = s.substringAfter('(').substringBefore(')')
        val parts = inner.split(',').map { it.trim() }
        return Color(
            red = parts[0].toInt() / 255f,
            green = parts[1].toInt() / 255f,
            blue = parts[2].toInt() / 255f,
            alpha = parts[3].toFloat(),
        )
    }
}
