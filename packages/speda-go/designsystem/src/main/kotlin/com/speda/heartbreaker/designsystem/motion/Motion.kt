package com.speda.heartbreaker.designsystem.motion

import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.Easing

/**
 * Motion constants — durations and easings lifted from theme.ts and
 * heartbreaker.css so the port's timing matches the web frame-for-frame.
 * These are the acceptance values for the timed-capture parity checks (§7).
 */
object Motion {

    // ── Durations (ms) ─────────────────────────────────────────────────────
    /** morphTheme() — full-palette agent swap. */
    const val MORPH_MS = 500
    /** [data-brand-text] crossfade (heartbreaker.css: opacity 0.18s). */
    const val BRAND_TEXT_MS = 180
    /** Brand-text content swap happens at the morph midpoint (App.tsx). */
    const val BRAND_TEXT_SWAP_MS = 200
    /** Sidebar drawer slide (Sidebar.tsx: 0.28s). */
    const val DRAWER_MS = 280

    // ── Party cycle (theme.ts startPartyCycle) ─────────────────────────────
    const val PARTY_LEAD_MS = 700L
    const val PARTY_MS_PER_STOP = 3000L
    /** ~12Hz update throttle (TICK_MS in startPartyCycle). */
    const val PARTY_TICK_MS = 80L

    // ── Easings ────────────────────────────────────────────────────────────
    /**
     * morphTheme's easing — easeInOutQuad, written exactly as the TS expression
     * `t < 0.5 ? 2*t*t : 1 - (-2t+2)^2 / 2`.
     */
    val EaseInOutQuad = Easing { t ->
        if (t < 0.5f) 2f * t * t else 1f - (-2f * t + 2f) * (-2f * t + 2f) / 2f
    }

    /** startPartyCycle's mixing easing — smoothstep `t*t*(3-2t)`. */
    val Smoothstep = Easing { t -> t * t * (3f - 2f * t) }

    /** Sidebar drawer curve — cubic-bezier(0.32, 0.72, 0.33, 1) from Sidebar.tsx. */
    val DrawerEasing: Easing = CubicBezierEasing(0.32f, 0.72f, 0.33f, 1f)
}
