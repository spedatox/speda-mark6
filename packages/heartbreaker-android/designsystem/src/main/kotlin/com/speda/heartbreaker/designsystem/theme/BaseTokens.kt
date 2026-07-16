package com.speda.heartbreaker.designsystem.theme

/**
 * The structural palette — copied value-for-value from the BASE_HEX / BASE_RGBA
 * tables in profile/theme.ts. These ARE the palette: backgrounds, surfaces,
 * text, lines, glass rims and the dim icon scale, all re-hued to the agent's
 * accent at runtime by [ThemeEngine].
 *
 * Order is preserved from the TS source purely for readability; iteration order
 * does not affect output (buildThemeVars writes a keyed map).
 *
 * Alphas are stored as the exact string the TS emits (JS number → string) so the
 * generated `rgba(r, g, b, a)` values match the fixtures byte-for-byte.
 */
internal object BaseTokens {

    /** `--hb-*` hex tokens, re-hued (hue swapped, S/L preserved). */
    val BASE_HEX: Map<String, String> = linkedMapOf(
        "--hb-void" to "#04080a",
        "--hb-base" to "#060c0f",
        "--hb-petrol" to "#0b1a22",
        "--hb-steel" to "#13303b",
        "--hb-text" to "#cadbe2",
        "--hb-text-dim" to "#7a96a1",
        "--hb-text-faint" to "#46626d",
        "--bg-code" to "#08151b",
        "--bg-code-header" to "#0a1d25",
        "--hb-icon" to "#3a6472",
        "--hb-icon-dim" to "#2e5260",
        "--hb-icon-bright" to "#5d7f8a",
    )

    /** rgba tokens — [base colour for hue/sat/light, alpha-as-emitted-string]. */
    val BASE_RGBA: Map<String, Pair<String, String>> = linkedMapOf(
        "--hb-line" to ("#5fa5bc" to "0.26"),
        "--hb-line-bright" to ("#6ec8e4" to "0.55"),
        "--hb-edge" to ("#96cdf5" to "0.22"),
        "--hb-edge-bright" to ("#aae1ff" to "0.55"),
        "--bg-sidebar" to ("#081217" to "0.72"),
        "--bg-hover" to ("#4696af" to "0.12"),
        "--bg-input" to ("#08141a" to "0.66"),
        "--bg-user-bubble" to ("#183844" to "0.46"),
        "--scrollbar-thumb" to ("#468ca0" to "0.32"),
        "--scrollbar-thumb-hover" to ("#5aafc8" to "0.55"),
        "--glass-tint" to ("#bed7eb" to "0.06"),
        "--glass-tint-hi" to ("#bed7eb" to "0.13"),
        "--glass-fill" to ("#081018" to "0.62"),
        "--glass-menu" to ("#0a141b" to "0.94"),
    )

    /**
     * Semantic colours — meaning-bearing, NEVER re-hued (theme.ts leaves them
     * untouched; they live only in :root of heartbreaker.css). Same on every
     * agent. Kept here so the typed palette carries them.
     */
    const val AMBER = "#d99c44"
    const val AMBER_BRIGHT = "#f2b75c"
    const val AMBER_DIM = "#241a0f" // rgba(217,156,68,0.14) flattened over void; see note in HbPalette
    const val RED = "#c84a3a"
    const val GREEN = "#4fa377"

    /** Body background — the static 160° 4-stop gradient (heartbreaker.css body). */
    val BODY_GRADIENT_STOPS = listOf(
        0.0f to "#03070a",
        0.38f to "#060d14",
        0.62f to "#08131d",
        1.0f to "#040a10",
    )
    const val BODY_GRADIENT_ANGLE_DEG = 160.0f
}
