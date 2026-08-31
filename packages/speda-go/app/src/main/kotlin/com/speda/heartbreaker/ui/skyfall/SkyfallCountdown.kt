// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui.skyfall

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.data.IgorApi
import com.speda.heartbreaker.data.SkyfallArm
import com.speda.heartbreaker.data.SkyfallResult
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.i18n.LocalStrings
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * SKYFALL — the countdown, on the phone. This screen IS the protocol.
 *
 * Byte-for-byte parity with the desktop's SkyfallCountdown.tsx is not the goal;
 * behavioural parity is, and there are exactly three behaviours that matter:
 *
 * 1. **Both entry routes land here.** Speda arming it over chat raises an SSE
 *    event; the Protocols pane arms from the project list. Neither can skip the
 *    screen, because neither of them can fire — only this composable calls
 *    `fireSkyfall`, and only when its own clock reaches zero.
 *
 * 2. **Aborting is the absence of an action.** It does not cancel a request in
 *    flight; it means this screen never makes one. Nothing can arrive too late,
 *    and every failure — the process dying, the screen being destroyed, the
 *    phone sleeping — falls toward "did not fire". `abortSkyfall` afterwards
 *    only writes it down.
 *
 * 3. **A clock that stopped being shown does not fire.** The loop measures the
 *    WALL CLOCK between ticks rather than counting its own delays. Android
 *    freezes a backgrounded composition, and a naive counter would resume and
 *    fire the instant the owner returned — a request going out with no
 *    countdown and no chance to stop it, which is the worst thing this could
 *    possibly do. A gap past [STALL_MS] means it was not on screen, and the
 *    launch stands down instead. Same rule as the desktop, same reason.
 *
 * Back is not wired to dismiss. The two outcomes are named on two buttons; a
 * launch screen you can leave by accident lies about what leaving did.
 */
private const val STALL_MS = 1500L

private enum class Phase { Armed, Firing, Done, Aborted, Stalled }

@Composable
fun SkyfallCountdown(
    config: AppConfig,
    api: IgorApi,
    arm: SkyfallArm,
    onClose: () -> Unit,
) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current.skyfall
    val scope = rememberCoroutineScope()

    val total = maxOf(1, arm.countdownSeconds)
    var phase by remember { mutableStateOf(Phase.Armed) }
    var remaining by remember { mutableStateOf(total.toDouble()) }
    var result by remember { mutableStateOf<SkyfallResult?>(null) }
    // Guards the one thing that must happen at most once, ever.
    var claimed by remember { mutableStateOf(false) }

    fun fire() {
        if (claimed) return
        claimed = true
        phase = Phase.Firing
        scope.launch {
            result = api.fireSkyfall(config, arm.projectId)
            phase = Phase.Done
        }
    }

    fun abort(left: Double, stalled: Boolean = false) {
        if (claimed) return
        claimed = true
        phase = if (stalled) Phase.Stalled else Phase.Aborted
        scope.launch { api.abortSkyfall(config, arm.projectId, left) }
    }

    LaunchedEffect(arm.projectId) {
        val deadline = System.currentTimeMillis() + total * 1000L
        var lastTick = System.currentTimeMillis()
        while (phase == Phase.Armed) {
            delay(60)
            val now = System.currentTimeMillis()
            if (now - lastTick > STALL_MS) {
                // Backgrounded, or the device slept. Whatever the wall clock
                // says, this countdown was not shown — so it does not fire.
                abort(maxOf(0.0, (deadline - now) / 1000.0), stalled = true)
                return@LaunchedEffect
            }
            lastTick = now
            val left = (deadline - now) / 1000.0
            remaining = if (left > 0) left else 0.0
            if (left <= 0) { fire(); return@LaunchedEffect }
        }
    }

    val urgent = phase == Phase.Armed && remaining <= 5
    val danger = Color(0xFFD8483C)
    val dangerText = Color(0xFFE5897C)

    Box(
        Modifier
            .fillMaxSize()
            .background(
                Brush.radialGradient(
                    colors = listOf(
                        danger.copy(alpha = if (urgent) 0.20f else 0.10f),
                        Color(0xFF060709).copy(alpha = 0.97f),
                        Color(0xFF040506),
                    ),
                ),
            ),
        contentAlignment = Alignment.Center,
    ) {
        // The sweep: time left, legible without reading the number.
        Box(
            Modifier
                .align(Alignment.TopStart)
                .height(2.dp)
                .fillMaxWidth(
                    if (phase == Phase.Armed) (remaining / total).toFloat().coerceIn(0f, 1f) else 0f,
                )
                .background(danger),
        )

        Column(
            Modifier
                .widthIn(max = 520.dp)
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 26.dp, vertical = 32.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            HbText(
                when (phase) {
                    Phase.Armed -> t.armed
                    Phase.Firing -> t.firing
                    Phase.Stalled -> t.stalled
                    Phase.Aborted -> t.aborted
                    Phase.Done -> t.complete
                },
                style = HbType.label.copy(fontSize = 10.sp, letterSpacing = 4.sp),
                color = if (phase == Phase.Aborted || phase == Phase.Stalled) palette.textFaint else dangerText,
                caps = true,
            )

            HbText(
                arm.name,
                style = HbType.headerBar.copy(fontSize = 24.sp, textAlign = TextAlign.Center),
                color = palette.text,
            )
            if (arm.description.isNotBlank()) {
                HbText(
                    arm.description,
                    style = HbType.read.copy(fontSize = 14.sp, textAlign = TextAlign.Center),
                    color = palette.textFaint,
                )
            }
            // What is about to be sent, stated plainly. A launch screen that
            // does not say where it is aiming is theatre with no information.
            HbText(
                "${arm.method} ${arm.url}",
                style = HbType.code.copy(fontSize = 11.sp, textAlign = TextAlign.Center),
                color = palette.textDim,
            )

            when (phase) {
                Phase.Armed -> {
                    Spacer(Modifier.height(4.dp))
                    HbText(
                        kotlin.math.ceil(remaining).toInt().toString(),
                        style = HbType.code.copy(fontSize = if (urgent) 96.sp else 84.sp),
                        color = if (urgent) Color(0xFFFF6A58) else dangerText,
                    )
                    HbText(
                        t.willFire,
                        style = HbType.read.copy(fontSize = 13.sp, textAlign = TextAlign.Center),
                        color = palette.textFaint,
                    )
                }
                Phase.Firing -> HbText(
                    t.sending,
                    style = HbType.code.copy(fontSize = 18.sp, letterSpacing = 3.sp),
                    color = Color(0xFFFF6A58),
                )
                Phase.Aborted, Phase.Stalled -> HbText(
                    if (phase == Phase.Stalled) t.stalledBody else t.abortedBody,
                    style = HbType.read.copy(fontSize = 15.sp, textAlign = TextAlign.Center),
                    color = palette.textDim,
                )
                Phase.Done -> result?.let { Outcome(it) }
            }

            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                when (phase) {
                    Phase.Armed -> {
                        // Abort is the wide one. The screen should be easier to
                        // stop than to hurry along.
                        LaunchButton(t.abort, palette.text, wide = true) { abort(remaining) }
                        LaunchButton(t.fireNow, dangerText) { fire() }
                    }
                    Phase.Firing -> Unit
                    else -> LaunchButton(t.close, palette.text, wide = true, onClick = onClose)
                }
            }
        }
    }
}

@Composable
private fun LaunchButton(
    label: String,
    tint: Color,
    wide: Boolean = false,
    onClick: () -> Unit,
) {
    Box(
        Modifier
            .heightIn(min = 48.dp)
            .then(if (wide) Modifier.widthIn(min = 180.dp) else Modifier)
            .background(tint.copy(alpha = 0.12f), CircleShape)
            .border(1.dp, tint.copy(alpha = 0.34f), CircleShape)
            .clickable(onClick = onClick)
            .padding(horizontal = 26.dp, vertical = 12.dp),
        contentAlignment = Alignment.Center,
    ) {
        HbText(label, style = HbType.read.copy(fontSize = 15.sp), color = tint)
    }
}

/**
 * What came back. `fired` and `ok` are read separately: "it went out and the
 * target said 500" is a different sentence from "it never left", and the case
 * that must never be rendered as either is "we do not know".
 */
@Composable
private fun Outcome(result: SkyfallResult) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current.skyfall
    val good = result.fired && result.ok
    val tint = when {
        !result.fired -> palette.textFaint
        good -> palette.green
        else -> Color(0xFFE5897C)
    }

    Column(
        Modifier
            .fillMaxWidth()
            .background(Color.White.copy(alpha = 0.03f))
            .border(1.dp, tint.copy(alpha = 0.30f))
            .padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        HbText(
            when {
                !result.fired -> t.notSent
                good -> t.delivered(result.status)
                result.status != 0 -> t.rejected(result.status)
                else -> t.failed
            },
            style = HbType.label.copy(fontSize = 10.sp, letterSpacing = 2.sp),
            color = tint,
            caps = true,
        )
        if (result.error.isNotBlank()) {
            HbText(result.error, style = HbType.code.copy(fontSize = 11.sp), color = palette.textDim)
        }
        if (result.body.isNotBlank()) {
            HbText(
                result.body + if (result.truncated) "\n… ${t.truncated}" else "",
                style = HbType.code.copy(fontSize = 11.sp),
                color = palette.textDim,
            )
        }
    }
}
