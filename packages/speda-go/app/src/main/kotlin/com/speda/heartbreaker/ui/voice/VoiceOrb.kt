// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui.voice

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import com.speda.heartbreaker.data.VoiceSpeaker
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/**
 * The orb — voice mode's one moving object.
 *
 * ── What this is, and honestly is not ───────────────────────────────────────
 * The desktop orb is a Three.js scene: an icosahedron core under custom GLSL,
 * wrapped in a particle membrane. This is NOT that, and does not pretend to be.
 * Porting it would mean hand-writing the geometry, camera and particle system
 * that Three.js supplies for free, in OpenGL ES, and the result would still be a
 * second implementation to keep in step with the first.
 *
 * So this is a deliberate 2D reading of the same idea: a lit core, a breathing
 * halo, and a ring that deforms with the voice. It keeps the two behaviours that
 * carry meaning — it REACTS while speaking, and it SHRINKS aside when there is
 * something to present — and gives up the ones that are only spectacle.
 *
 * ── What drives it ──────────────────────────────────────────────────────────
 * [level] is the real output amplitude when the microphone permission has been
 * granted (see data/VoiceLevels), and a flat zero when it has not. Zero is not
 * treated as an error or as silence: the idle breath below runs regardless, so
 * an orb with no meter still looks alive — it just is not lip-syncing.
 */
@Composable
fun VoiceOrb(
    state: VoiceSpeaker.State,
    level: Float,
    modifier: Modifier = Modifier,
) {
    val palette = LocalHbPalette.current

    // The breath. Always running, at a rate that says which of the three states
    // the agent is in without a word of text: slow at rest, quick and shallow
    // while it is generating, and out of the way while it is actually speaking —
    // where the amplitude is the motion and a second rhythm would fight it.
    val breathMs = when (state) {
        VoiceSpeaker.State.IDLE -> 3600
        VoiceSpeaker.State.THINKING -> 1100
        VoiceSpeaker.State.SPEAKING -> 2400
    }
    val transition = rememberInfiniteTransition(label = "orb")
    val breath by transition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(breathMs), RepeatMode.Reverse),
        label = "breath",
    )
    // A slow rotation, so the ring's deformation reads as an object turning
    // rather than as a shape flickering in place.
    val spin by transition.animateFloat(
        initialValue = 0f,
        targetValue = (2 * PI).toFloat(),
        animationSpec = infiniteRepeatable(tween(14_000), RepeatMode.Restart),
        label = "spin",
    )

    // Amplitude is smoothed on its way in. Raw RMS at 30Hz is jittery enough to
    // read as noise rather than as speech, and the ear hears syllables while the
    // eye wants the envelope.
    val amp by animateFloatAsState(
        targetValue = level.coerceIn(0f, 1f),
        animationSpec = tween(90),
        label = "amp",
    )

    val accent = palette.accent
    val bright = palette.accentBright

    Canvas(modifier) {
        val c = Offset(size.width / 2f, size.height / 2f)
        // Everything is expressed against the smaller half-dimension, so the orb
        // is the same object whether it owns the screen or sits in a corner.
        val unit = minOf(size.width, size.height) / 2f
        val coreR = unit * (0.42f + 0.05f * breath + 0.10f * amp)

        // The halo: a soft radial bloom that carries most of the presence. Drawn
        // first and largest so the ring and core sit inside their own glow.
        drawCircle(
            brush = Brush.radialGradient(
                colors = listOf(
                    accent.copy(alpha = 0.30f + 0.30f * amp),
                    accent.copy(alpha = 0.10f),
                    Color.Transparent,
                ),
                center = c,
                radius = unit * (0.95f + 0.05f * amp),
            ),
            radius = unit * (0.95f + 0.05f * amp),
            center = c,
        )

        // The membrane: one closed ring whose radius is modulated by the voice.
        // Three lobes turning slowly, deepened by amplitude — at rest it is very
        // nearly a circle, and speech is what gives it a shape.
        val wobble = 0.03f + 0.14f * amp
        val path = Path()
        val steps = 96
        for (i in 0..steps) {
            val t = i / steps.toFloat() * 2f * PI.toFloat()
            val r = unit * (0.66f + 0.04f * breath) *
                (1f + wobble * sin(3f * t + spin) + wobble * 0.4f * sin(5f * t - spin * 1.7f))
            val p = Offset(c.x + r * cos(t), c.y + r * sin(t))
            if (i == 0) path.moveTo(p.x, p.y) else path.lineTo(p.x, p.y)
        }
        path.close()
        drawPath(
            path = path,
            color = bright.copy(alpha = 0.45f + 0.35f * amp),
            style = Stroke(width = unit * 0.012f),
        )

        // The core: lit from slightly above-left, the way every other glass
        // surface in the app is, so the orb belongs to the same material world.
        drawCircle(
            brush = Brush.radialGradient(
                colors = listOf(
                    Color.White.copy(alpha = 0.85f),
                    bright.copy(alpha = 0.75f),
                    accent.copy(alpha = 0.35f),
                ),
                center = Offset(c.x - coreR * 0.28f, c.y - coreR * 0.32f),
                radius = coreR * 1.7f,
            ),
            radius = coreR,
            center = c,
        )
    }
}

/** The orb sized for a corner rather than a screen — the docked form, used when
 *  the board has taken the surface and the orb has stepped aside for it. */
@Composable
fun orbScale(docked: Boolean): Float {
    val scale by animateFloatAsState(
        targetValue = if (docked) 0.34f else 1f,
        // Long enough to read as the orb MOVING aside rather than cutting to a
        // smaller one. The desktop uses 0.7s for the same reason.
        animationSpec = tween(700),
        label = "orbDock",
    )
    return scale
}
