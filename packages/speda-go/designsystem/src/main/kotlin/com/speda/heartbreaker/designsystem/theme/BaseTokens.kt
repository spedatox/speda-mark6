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
        "--hb-void" to "#05070a",
        "--hb-base" to "#080b10",
        "--hb-petrol" to "#0e1319",
        "--hb-steel" to "#161d26",
        "--hb-text" to "#dbe6ec",
        "--hb-text-dim" to "#93a6b1",
        "--hb-text-faint" to "#5d6f7a",
        "--bg-code" to "#0a0f15",
        "--bg-code-header" to "#0e141b",
        "--hb-icon" to "#7c8f9b",
        "--hb-icon-dim" to "#5d6f7a",
        "--hb-icon-bright" to "#93a6b1",
    )

    /**
     * rgba tokens — [base colour for hue/sat/light, alpha-as-emitted-string].
     *
     * `--hb-edge` is deliberately NOT here. The resting panel rim is a fixed
     * neutral white ([EDGE] below), not the brand: a pane of glass does not take
     * the colour of what it frames, and re-hueing the rim was what turned every
     * panel into a tinted box. The brand shows on `--hb-edge-bright`, the
     * focused/active rim.
     */
    val BASE_RGBA: Map<String, Pair<String, String>> = linkedMapOf(
        // Structural hairline stays neutral; the *bright* line is the accent one.
        "--hb-line" to ("#dbe6ec" to "0.08"),
        "--hb-line-bright" to ("#7fa4c4" to "0.3"),
        "--hb-edge-bright" to ("#a9c6dc" to "0.4"),
        "--bg-sidebar" to ("#0a0e14" to "0.55"),
        "--bg-hover" to ("#bed7eb" to "0.06"),
        "--bg-input" to ("#0a0e14" to "0.5"),
        "--bg-user-bubble" to ("#7fa4c4" to "0.16"),
        "--scrollbar-thumb" to ("#7fa4c4" to "0.28"),
        "--scrollbar-thumb-hover" to ("#a9c6dc" to "0.55"),
        // Unified glass material — a near-white frost with only a whisper of
        // brand in it. Two stops so the slab can be lit across its face (160°)
        // rather than washed flat; --glass-tint is also used alone for ghost
        // hovers.
        "--glass-tint" to ("#e6eef6" to "0.07"),
        "--glass-tint-2" to ("#e6eef6" to "0.02"),
        "--glass-tint-hi" to ("#e6eef6" to "0.12"),
        // Dark occluding base. Kept because nested backdrop roots cancel a
        // child's blur, but much lighter than before so the white frost above it
        // is what the eye actually reads.
        "--glass-fill" to ("#070b11" to "0.42"),
        // Floating menus/dropdowns sit inside backdrop roots where their own
        // blur is cancelled, so they need a near-opaque fill to occlude.
        "--glass-menu" to ("#0b1016" to "0.95"),
    )

    /**
     * The resting glass rim — fixed neutral white, never re-hued.
     * `heartbreaker.css`: `--hb-edge: rgba(255, 255, 255, 0.10)`.
     */
    const val EDGE = "rgba(255, 255, 255, 0.1)"

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

    /**
     * Body background.
     *
     * MOBILE DEVIATION (deliberate): the web's static 160° gradient runs
     * #03070a → #060d14 → #08131d → #040a10 — very dark, but every pixel is lit.
     * On the OLED panels this ships to, a true-black pixel is switched OFF, so a
     * flat #000000 base costs no power across the ~90% of the screen that is
     * background. The colour and depth come from the ambient blobs on top, which
     * is where the design's interest lives anyway.
     */
    val BODY_GRADIENT_STOPS = listOf(
        0.0f to "#000000",
        1.0f to "#000000",
    )
    const val BODY_GRADIENT_ANGLE_DEG = 160.0f
}
