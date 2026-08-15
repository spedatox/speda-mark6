package com.speda.heartbreaker.ui.party

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
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
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.data.IgorApi
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.launch
import kotlin.math.sin

/**
 * ════════════════════════════════════════════════════════════════════════════
 *  HOUSE PARTY PROTOCOL — the authorization surface.
 *
 *  Port of HousePartyModal.tsx + HousePartyWarning.tsx. The protocol is engaged
 *  by passphrase and the BACKEND is the only thing that judges it — this screen
 *  carries the string and nothing else. It never stores it, never hashes it,
 *  never pre-checks it, and it never lands in the transcript.
 *
 *  Two things can raise it:
 *    - the `house_party_auth` SSE event, which is how the live flow works: the
 *      backend's house_party tool asks and the client opens this; and
 *    - a ```hpp-warning fence, which is SALVAGE only — an old transcript or a
 *      model emitting the fence out of habit. Tapping the banner opens this;
 *      scrolling past it must never pop a window on its own.
 * ════════════════════════════════════════════════════════════════════════════
 */

/** What the owner is being asked to authorize, if the backend said. */
data class HousePartyAsk(val objective: String? = null)

/**
 * Raised from anywhere in the tree — including deep inside rendered prose, where
 * threading a callback down through the markdown renderer would mean every block
 * composable carrying a parameter it has nothing to do with. The web does the
 * same thing with a window CustomEvent.
 */
val LocalHousePartyRequest = compositionLocalOf<(HousePartyAsk) -> Unit> { {} }

@Composable
fun HousePartyModal(
    api: IgorApi,
    config: AppConfig,
    ask: HousePartyAsk,
    onClose: () -> Unit,
    onEngaged: () -> Unit,
) {
    val palette = LocalHbPalette.current
    val scope = rememberCoroutineScope()
    var passphrase by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var refused by remember { mutableStateOf(false) }

    // Refusal shakes the field. Physical feedback for a wrong secret is worth
    // more than a line of red text nobody reads while typing.
    val shake = remember { Animatable(0f) }
    LaunchedEffect(refused) {
        if (!refused) return@LaunchedEffect
        shake.snapTo(0f)
        shake.animateTo(1f, tween(420, easing = LinearEasing))
    }

    val submit = {
        if (!busy && passphrase.isNotBlank()) {
            busy = true
            refused = false
            scope.launch {
                val engaged = api.setHouseParty(config, engaged = true, passphrase = passphrase)
                busy = false
                if (engaged == true) {
                    onEngaged()
                    onClose()
                } else {
                    // Wrong, or the backend refused. Clear it — a rejected
                    // passphrase left in the box invites the same failed retry.
                    passphrase = ""
                    refused = true
                }
            }
        }
    }

    Box(
        Modifier
            .fillMaxSize()
            .background(Color(0xFF04080A).copy(alpha = 0.72f))
            .clickable(enabled = !busy, onClick = onClose),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            Modifier
                .widthIn(max = 420.dp)
                .padding(24.dp)
                .offset { IntOffset((sin(shake.value * 24f) * (1f - shake.value) * 9f).toInt(), 0) }
                .hbGlass(shape = HbGlassShape.Card, state = HbGlassState.Amber)
                // Swallow taps: the scrim behind closes, the window must not.
                .clickable(enabled = false) {}
                .padding(18.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                PulsingDot(palette.amberBright)
                HbText(
                    "HOUSE PARTY PROTOCOL",
                    style = HbType.headerBar.copy(fontSize = 13.sp),
                    color = palette.text,
                    caps = true,
                )
            }

            HbText(
                "Full roster, interactive-grade models, every agent at once. " +
                    "Heavy, expensive, and it stays engaged until you stand it down.",
                style = HbType.read.copy(fontSize = 13.sp),
                color = palette.textDim,
            )

            ask.objective?.takeIf { it.isNotBlank() }?.let {
                Box(
                    Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(9.dp))
                        .background(palette.amber.copy(alpha = 0.08f))
                        .border(1.dp, palette.amber.copy(alpha = 0.30f), RoundedCornerShape(9.dp))
                        .padding(horizontal = 10.dp, vertical = 8.dp),
                ) {
                    HbText(it, style = HbType.readout.copy(fontSize = 11.sp), color = palette.amberBright)
                }
            }

            HbText(
                if (refused) "PASSPHRASE REFUSED" else "PASSPHRASE",
                style = HbType.label.copy(fontSize = 10.sp),
                color = if (refused) palette.red else palette.textFaint,
                caps = true,
            )

            BasicTextField(
                value = passphrase,
                onValueChange = { passphrase = it; refused = false },
                singleLine = true,
                enabled = !busy,
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Password,
                    imeAction = ImeAction.Go,
                ),
                keyboardActions = KeyboardActions(onGo = { submit() }),
                textStyle = HbType.code.copy(fontSize = 15.sp).merge(TextStyle(color = palette.text)),
                cursorBrush = SolidColor(palette.amberBright),
                modifier = Modifier.fillMaxWidth(),
                decorationBox = { inner ->
                    Box(
                        Modifier
                            .fillMaxWidth()
                            .heightIn(min = 44.dp)
                            .hbGlass(
                                shape = HbGlassShape.Tile,
                                state = if (refused) HbGlassState.Tint(palette.red) else HbGlassState.Default,
                            )
                            .padding(horizontal = 12.dp, vertical = 11.dp),
                        contentAlignment = Alignment.CenterStart,
                    ) { inner() }
                },
            )

            // The scanning bar — the window is talking to the server, and a
            // passphrase check is exactly the moment to prove something is
            // happening rather than leaving a dead button.
            if (busy) ScanBar(palette.amberBright)

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                Spacer(Modifier.weight(1f))
                PartyButton("CANCEL", enabled = !busy, tint = null, onClick = onClose)
                PartyButton(
                    if (busy) "AUTHORIZING…" else "ENGAGE",
                    enabled = !busy && passphrase.isNotBlank(),
                    tint = palette.amberBright,
                    onClick = submit,
                )
            }
        }
    }
}

/**
 * The ```hpp-warning salvage banner. Deliberately inert until tapped: history is
 * full of old authorizations and none of them should re-open a window when the
 * owner scrolls past.
 */
@Composable
fun HousePartyBanner(body: String, modifier: Modifier = Modifier) {
    val palette = LocalHbPalette.current
    val raise = LocalHousePartyRequest.current
    val objective = remember(body) {
        Regex("""(?:^|\n)\s*objective\s*:\s*(.+)""", RegexOption.IGNORE_CASE)
            .find(body)?.groupValues?.get(1)?.trim()?.take(180)
    }

    Row(
        modifier
            .fillMaxWidth()
            .padding(vertical = 6.dp)
            .hbGlass(shape = HbGlassShape.Ctl, state = HbGlassState.Amber)
            .clickable { raise(HousePartyAsk(objective)) }
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        PulsingDot(palette.amberBright)
        Column(Modifier.weight(1f)) {
            HbText(
                "HOUSE PARTY PROTOCOL",
                style = HbType.headerBar.copy(fontSize = 12.sp),
                color = palette.text,
                caps = true,
            )
            HbText(
                "Authorization required — tap to open",
                style = HbType.label.copy(fontSize = 9.sp),
                color = palette.amberBright,
                caps = true,
            )
        }
        HbText("ENGAGE", style = HbType.label.copy(fontSize = 11.sp), color = palette.amberBright, caps = true)
    }
}

/* ── Small parts ─────────────────────────────────────────────────────────── */

@Composable
private fun PulsingDot(color: Color) {
    val t = rememberInfiniteTransition(label = "hpp-dot")
    val a by t.animateFloat(
        initialValue = 0.25f, targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(900, easing = LinearEasing), RepeatMode.Reverse),
        label = "hpp-dot-alpha",
    )
    Box(Modifier.size(8.dp).background(color.copy(alpha = a), CircleShape))
}

/** A light bar sweeping the width — the modal's "working" state. */
@Composable
private fun ScanBar(color: Color) {
    val t = rememberInfiniteTransition(label = "hpp-scan")
    val x by t.animateFloat(
        initialValue = 0f, targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(1100, easing = LinearEasing), RepeatMode.Restart),
        label = "hpp-scan-x",
    )
    Box(
        Modifier
            .fillMaxWidth()
            .height(2.dp)
            .background(color.copy(alpha = 0.12f)),
    ) {
        Box(
            Modifier
                .fillMaxWidth(0.28f)
                .height(2.dp)
                .offset { IntOffset(((x * 1.28f - 0.28f) * 380).toInt(), 0) }
                .background(color),
        )
    }
}

@Composable
private fun PartyButton(label: String, enabled: Boolean, tint: Color?, onClick: () -> Unit) {
    val palette = LocalHbPalette.current
    Box(
        Modifier
            .hbGlass(
                shape = HbGlassShape.Pill,
                state = if (tint != null && enabled) HbGlassState.Tint(tint) else HbGlassState.Default,
            )
            .clickable(enabled = enabled, onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 8.dp),
    ) {
        HbText(
            label,
            style = HbType.label.copy(fontSize = 11.sp),
            color = when {
                !enabled -> palette.iconDim
                tint != null -> tint
                else -> palette.textDim
            },
            caps = true,
        )
    }
}
