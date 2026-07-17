package com.speda.heartbreaker.designsystem.glass

import androidx.compose.runtime.Composable
import androidx.compose.runtime.ProvidableCompositionLocal
import androidx.compose.runtime.remember
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.speda.heartbreaker.designsystem.theme.HbPalette
import dev.chrisbanes.haze.HazeState
import dev.chrisbanes.haze.HazeTint
import dev.chrisbanes.haze.hazeEffect
import dev.chrisbanes.haze.hazeSource

/**
 * ════════════════════════════════════════════════════════════════════════════
 *  Real backdrop blur for TOP-LEVEL glass surfaces (`blur(28px) saturate(140%)`).
 *
 *  Deliberately isolated from [hbGlass] so the core material never depends on
 *  Haze — if the Haze API shifts between versions, only this file changes.
 *
 *  THE NESTED-BLUR RULE (mirror of heartbreaker.css): ONLY surfaces sitting
 *  directly over the void get this blur. Anything nested inside another glass
 *  surface uses the occluding fill in [hbGlass] with NO blur — that is both the
 *  fidelity rule and the performance budget (≤3 live blur surfaces per screen).
 *
 *  Usage:
 *    val haze = rememberHbHazeState()
 *    Box(Modifier.hbHazeSource(haze)) { AmbientBackground(...) ; ...content... }
 *    // a top-level glass panel over the ambient void:
 *    Box(Modifier.hbHazeBlur(haze).hbGlass(shape, state)) { ... }
 * ════════════════════════════════════════════════════════════════════════════
 */

internal val BLUR_RADIUS = 28.dp   // --hb-holo-blur: blur(28px)

/**
 * The backdrop every glass surface refracts. Provided once at the shell root over
 * the ambient void; [hbGlass] picks it up automatically, so a surface is real
 * glass without the call site knowing anything about Haze. Null (previews, tests)
 * simply falls back to the occluding-fill material.
 */
val LocalHazeState: ProvidableCompositionLocal<HazeState?> = staticCompositionLocalOf { null }

@Composable
fun rememberHbHazeState(): HazeState = remember { HazeState() }

/** Mark the content that everything blurs over (the ambient void root). */
fun Modifier.hbHazeSource(state: HazeState): Modifier = this.hazeSource(state)

/**
 * Blur the backdrop for a top-level glass surface. Chain BEFORE [hbGlass] so the
 * material's fill/tint/rim draw over the blurred backdrop:
 * `Modifier.hbHazeBlur(state).hbGlass(...)`.
 */
fun Modifier.hbHazeBlur(state: HazeState, palette: HbPalette? = null): Modifier =
    this.hazeEffect(state = state) {
        blurRadius = BLUR_RADIUS
        // saturate(140%) is approximated by a light accent-neutral tint; the
        // material's own milky tint carries most of the frosted body.
        backgroundColor = palette?.void ?: Color(0xFF04080A)
        tints = listOf(HazeTint(Color.White.copy(alpha = 0.02f)))
        noiseFactor = 0f
    }
