package com.speda.heartbreaker.designsystem.color

import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min

/**
 * ════════════════════════════════════════════════════════════════════════════
 *  Literal port of the colour math in
 *  packages/heartbreaker/src/renderer/src/profile/theme.ts
 *
 *  Every operation is kept faithful to the JavaScript original so the Kotlin
 *  theme engine reproduces `buildThemeVars()` byte-for-byte (verified by the
 *  fixture tests in src/test against JSON dumped from theme.ts itself).
 *
 *  PARITY LANDMINE — rounding: JS `Math.round(x)` rounds half toward +Infinity,
 *  i.e. `Math.floor(x + 0.5)`. Kotlin's `kotlin.math.round` rounds half-to-even
 *  (banker's rounding), which disagrees at .5 boundaries. We therefore round
 *  with `floor(x + 0.5)` everywhere, exactly as the spec'd JS does.
 * ════════════════════════════════════════════════════════════════════════════
 */
internal object ColorMath {

    /** Mirrors the TS `Rgb` interface — components are Doubles because hslToRgb
     *  yields fractional values before the final clamp/round. */
    data class Rgb(val r: Double, val g: Double, val b: Double)

    data class Hsl(val h: Double, val s: Double, val l: Double)

    private const val WHITE = "#ffffff"
    private const val VOID = "#04080a"

    fun hexToRgb(hex: String): Rgb {
        val h = hex.removePrefix("#")
        return Rgb(
            r = h.substring(0, 2).toInt(16).toDouble(),
            g = h.substring(2, 4).toInt(16).toDouble(),
            b = h.substring(4, 6).toInt(16).toDouble(),
        )
    }

    /** JS Math.round == floor(x + 0.5), then clamp to a byte. */
    fun clamp(n: Double): Int = max(0.0, min(255.0, floor(n + 0.5))).toInt()

    private fun toHex(n: Double): String = clamp(n).toString(16).padStart(2, '0')

    fun rgbToHex(c: Rgb): String = "#${toHex(c.r)}${toHex(c.g)}${toHex(c.b)}"

    fun rgbToHsl(c: Rgb): Hsl {
        val r = c.r / 255.0
        val g = c.g / 255.0
        val b = c.b / 255.0
        val maxV = max(r, max(g, b))
        val minV = min(r, min(g, b))
        var h = 0.0
        var s = 0.0
        val l = (maxV + minV) / 2.0
        if (maxV != minV) {
            val d = maxV - minV
            s = if (l > 0.5) d / (2.0 - maxV - minV) else d / (maxV + minV)
            h = when (maxV) {
                r -> (g - b) / d + (if (g < b) 6.0 else 0.0)
                g -> (b - r) / d + 2.0
                else -> (r - g) / d + 4.0
            }
            h /= 6.0
        }
        return Hsl(h * 360.0, s, l)
    }

    fun hslToRgb(hDeg: Double, s: Double, l: Double): Rgb {
        val h = hDeg / 360.0
        if (s == 0.0) return Rgb(l * 255.0, l * 255.0, l * 255.0)
        val q = if (l < 0.5) l * (1.0 + s) else l + s - l * s
        val p = 2.0 * l - q
        return Rgb(
            r = hue2rgb(p, q, h + 1.0 / 3.0) * 255.0,
            g = hue2rgb(p, q, h) * 255.0,
            b = hue2rgb(p, q, h - 1.0 / 3.0) * 255.0,
        )
    }

    private fun hue2rgb(p: Double, q: Double, tIn: Double): Double {
        var t = tIn
        if (t < 0) t += 1.0
        if (t > 1) t -= 1.0
        if (t < 1.0 / 6.0) return p + (q - p) * 6.0 * t
        if (t < 1.0 / 2.0) return q
        if (t < 2.0 / 3.0) return p + (q - p) * (2.0 / 3.0 - t) * 6.0
        return p
    }

    /** Linear mix of two hex colours; returns a hex string (rounded like TS). */
    fun mix(hex: String, target: String, t: Double): String {
        val a = hexToRgb(hex)
        val b = hexToRgb(target)
        return rgbToHex(
            Rgb(
                r = a.r + (b.r - a.r) * t,
                g = a.g + (b.g - a.g) * t,
                b = a.b + (b.b - a.b) * t,
            ),
        )
    }

    fun mixWhite(hex: String, t: Double): String = mix(hex, WHITE, t)
    fun mixVoid(hex: String, t: Double): String = mix(hex, VOID, t)

    /** Re-hue a base colour to [hue], preserving its saturation & lightness. */
    fun rehue(baseHex: String, hue: Double): Rgb {
        val hsl = rgbToHsl(hexToRgb(baseHex))
        return hslToRgb(hue, hsl.s, hsl.l)
    }
}
