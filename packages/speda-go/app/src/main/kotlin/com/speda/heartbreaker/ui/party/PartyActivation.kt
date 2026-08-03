package com.speda.heartbreaker.ui.party

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.blur
import androidx.compose.ui.draw.scale
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.Canvas
import com.speda.heartbreaker.designsystem.brand.Brands
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * ════════════════════════════════════════════════════════════════════════════
 *  HOUSE PARTY PROTOCOL — the activation cinematic.
 *
 *  Port of PartyActivation.tsx. A full-screen frosted-void sequence that plays
 *  WHILE the app transforms underneath it: the directive lands, the title slams
 *  in through blur, the roster boots one agent at a time in its own colour, a
 *  shockwave fires, and the veil dissolves onto an already-transformed console.
 *  Stand down runs the reverse wink-out.
 *
 *  `onIgnite` is the whole point of the timing. It fires mid-sequence, at the
 *  moment the screen is FULLY covered — that is when the profile, the palette
 *  and the session all swap. Do it before and the owner watches the app change
 *  clothes; do it after and the reveal shows the old room for a frame.
 *
 *  Three modes, two timings: `engage` and `standby` are both entrances and share
 *  the sequence exactly — only the copy differs, because opening the war room
 *  and engaging the protocol are the same theatre with different stakes.
 * ════════════════════════════════════════════════════════════════════════════
 */

enum class ActivationMode { Engage, Standby, StandDown }

private const val ENTER_IGNITE_MS = 2_600L
private const val ENTER_DONE_MS = 3_650L
private const val EXIT_IGNITE_MS = 850L
private const val EXIT_DONE_MS = 1_750L

@Composable
fun PartyActivation(
    mode: ActivationMode,
    onIgnite: () -> Unit,
    onDone: () -> Unit,
) {
    val palette = LocalHbPalette.current
    val entering = mode != ActivationMode.StandDown

    // Held in rememberUpdatedState so the sequence below can depend on `mode`
    // ALONE. Keying it on the callbacks would replay the world-swap every time
    // the parent recomposed with a fresh lambda.
    val ignite by rememberUpdatedState(onIgnite)
    val done by rememberUpdatedState(onDone)

    val veil = remember { Animatable(0f) }          // 0 → covered, 1 → dissolved
    val title = remember { Animatable(0f) }         // the slam
    var booted by remember { mutableStateOf(0) }    // how many agents are online
    val wave = remember { Animatable(0f) }          // the shockwave

    LaunchedEffect(mode) {
        if (entering) {
            veil.snapTo(0f)
            launch { title.animateTo(1f, tween(700, easing = LinearOutSlowInEasing)) }
            // Roster boots one at a time — the roll call IS the drama, and all
            // eight arriving together reads as a loading spinner.
            launch {
                delay(650)
                repeat(Brands.ROSTER.size) {
                    booted = it + 1
                    delay(150)
                }
            }
            launch { delay(2_050); wave.animateTo(1f, tween(900, easing = LinearOutSlowInEasing)) }
            delay(ENTER_IGNITE_MS)
            ignite()                                 // the world changes under the veil
            veil.animateTo(1f, tween((ENTER_DONE_MS - ENTER_IGNITE_MS).toInt(), easing = FastOutSlowInEasing))
            done()
        } else {
            booted = Brands.ROSTER.size
            title.snapTo(1f)
            // Reverse roll call, fast — standing down is a wink-out, not a boot.
            launch {
                repeat(Brands.ROSTER.size) {
                    booted = Brands.ROSTER.size - it - 1
                    delay(70)
                }
            }
            delay(EXIT_IGNITE_MS)
            ignite()
            veil.animateTo(1f, tween((EXIT_DONE_MS - EXIT_IGNITE_MS).toInt(), easing = FastOutSlowInEasing))
            done()
        }
    }

    val directive = when (mode) {
        ActivationMode.Engage -> "// DIRECTIVE CONFIRMED — \"TAKE 'EM TO CHURCH\""
        ActivationMode.Standby -> "// WAR ROOM ONLINE — ROSTER ON STATION"
        ActivationMode.StandDown -> "// STAND DOWN — CHANNEL CLOSING"
    }
    val closer = when (mode) {
        ActivationMode.Engage -> "ALL HANDS ONLINE — CHANNEL OPEN"
        ActivationMode.Standby -> "ROSTER ON STATION — STANDBY HELD"
        ActivationMode.StandDown -> "ROSTER RELEASED"
    }

    Box(
        Modifier
            .fillMaxSize()
            // The veil dissolves and pushes away — the console beneath is
            // already transformed by the time any of it is visible.
            .alpha(1f - veil.value)
            .scale(1f + veil.value * 0.02f)
            .background(Color(0xFF04080A).copy(alpha = 0.94f)),
        contentAlignment = Alignment.Center,
    ) {
        // The shockwave — one expanding ring, fired as the roster completes.
        if (wave.value > 0f) {
            Canvas(Modifier.fillMaxSize()) {
                val r = size.minDimension * (0.05f + wave.value * 1.7f)
                drawCircle(
                    color = palette.amberBright.copy(alpha = (1f - wave.value) * 0.55f),
                    radius = r,
                    center = Offset(size.width / 2f, size.height / 2f),
                    style = Stroke(width = (1f - wave.value) * 6f + 1f),
                )
            }
        }

        Column(
            Modifier.padding(28.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            HbText(
                directive,
                style = HbType.readout.copy(fontSize = 11.sp),
                color = palette.amberBright,
                caps = true,
            )

            // The slam: in through blur and scale, settling to rest.
            HbText(
                "HOUSE PARTY",
                style = HbType.headerBar.copy(fontSize = 30.sp),
                color = palette.text,
                caps = true,
                modifier = Modifier
                    .alpha(title.value)
                    .scale(1.25f - title.value * 0.25f)
                    .blur((18f * (1f - title.value)).dp.coerceAtLeast(0.dp)),
            )
            HbText(
                when (mode) {
                    ActivationMode.Engage -> "PROTOCOL"
                    ActivationMode.Standby -> "STANDBY"
                    ActivationMode.StandDown -> "RELEASED"
                },
                style = HbType.label.copy(fontSize = 12.sp),
                color = palette.amber,
                caps = true,
                modifier = Modifier.alpha(title.value),
            )

            // The roll call.
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Brands.ROSTER.forEachIndexed { i, id ->
                    val brand = Brands.BRANDS[id]
                    val online = i < booted
                    Box(
                        Modifier
                            .size(if (online) 11.dp else 7.dp)
                            .alpha(if (online) 1f else 0.22f)
                            .background(
                                if (online && brand != null)
                                    Color(android.graphics.Color.parseColor(brand.accent))
                                else palette.iconDim,
                                CircleShape,
                            ),
                    )
                }
            }

            if (booted >= Brands.ROSTER.size || !entering) {
                HbText(
                    closer,
                    style = HbType.label.copy(fontSize = 10.sp),
                    color = palette.textDim,
                    caps = true,
                )
            }
        }
    }
}
