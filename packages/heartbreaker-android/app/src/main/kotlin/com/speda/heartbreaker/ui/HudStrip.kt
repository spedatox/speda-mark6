package com.speda.heartbreaker.ui

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.data.Health
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.glass.hbSeamBottom
import com.speda.heartbreaker.designsystem.icons.HbGlyphs
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import kotlinx.coroutines.delay
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * The fixed 22dp telemetry strip — the mobile HudFrame variant (which the port
 * IS). Link state, the centred HEARTBREAKER wordmark, the active MODEL, and the
 * DIAG dropdown carrying host / tools / RTT / sess / date / time.
 */
@Composable
fun HudStrip(
    health: Health,
    agentName: String,
    model: String,
    sessionCount: Int,
    apiBase: String,
    modifier: Modifier = Modifier,
) {
    val palette = LocalHbPalette.current
    var diagOpen by remember { mutableStateOf(false) }

    var now by remember { mutableStateOf(LocalDateTime.now()) }
    LaunchedEffect(Unit) { while (true) { delay(1000); now = LocalDateTime.now() } }

    val linkColor = if (health.online) palette.green else palette.red

    Box(modifier.fillMaxWidth()) {
        Row(
            Modifier
                .fillMaxWidth()
                .height(22.dp)
                .background(palette.glassTint)
                .hbSeamBottom()
                .padding(horizontal = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            // Left — the wordmark, with a link jewel (no label: the state reads
            // from the colour, and the strip stays uncluttered on a phone).
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                LinkDot(online = health.online, color = linkColor)
                HbText(
                    "HEARTBREAKER",
                    style = HbType.hud.copy(fontSize = 9.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.2.em),
                    color = palette.accent.copy(alpha = 0.55f),
                    maxLines = 1,
                )
            }

            // Centre — the current agent. Shares space with the clusters and
            // ellipsizes rather than colliding on narrow screens.
            HbText(
                agentName,
                style = HbType.hud.copy(fontSize = 9.5.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.16.em),
                color = palette.accentBright,
                caps = true,
                maxLines = 1,
                modifier = Modifier.weight(1f).padding(horizontal = 8.dp),
            )

            // Right — everything else collapses into DIAG.
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(
                    Modifier
                        .height(16.dp)
                        .hbGlass(shape = HbGlassShape.R9, state = if (diagOpen) HbGlassState.Active else HbGlassState.Default)
                        .clickable { diagOpen = !diagOpen }
                        .padding(horizontal = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(3.dp),
                ) {
                    HbText(
                        "DIAG",
                        style = HbType.hud.copy(fontSize = 9.sp, letterSpacing = 0.1.em),
                        color = if (diagOpen) palette.accentBright else palette.textDim,
                    )
                    HbGlyphs.ChevronDown(if (diagOpen) palette.accentBright else palette.textDim, size = 7.dp)
                }
            }
        }

        if (diagOpen) {
            Column(
                Modifier
                    .align(Alignment.TopEnd)
                    .padding(top = 28.dp, end = 10.dp)
                    .widthIn(min = 180.dp)
                    .hbGlass(shape = HbGlassShape.R12, state = HbGlassState.Menu)
                    .padding(horizontal = 10.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(7.dp),
            ) {
                DiagRow("LINK", if (health.online) "ONLINE" else "OFFLINE", linkColor)
                DiagRow("MODEL", shortModel(model), palette.accentBright)
                DiagRow("HOST", hostOf(apiBase), palette.textDim)
                DiagRow("TOOLS", health.tools?.toString() ?: "--", palette.textDim)
                DiagRow(
                    "RTT",
                    health.latencyMs?.let { "${it}ms" } ?: "--",
                    if ((health.latencyMs ?: Long.MAX_VALUE) < 400) palette.green else palette.amber,
                )
                DiagRow("SESS", sessionCount.toString().padStart(2, '0'), palette.textDim)
                DiagRow("DATE", now.format(DATE_TAG).uppercase(Locale.ENGLISH), palette.amber)
                DiagRow("TIME", now.format(CLOCK), palette.accentBright)
            }
        }
    }
}

@Composable
private fun LinkDot(online: Boolean, color: Color) {
    val transition = rememberInfiniteTransition(label = "link")
    val alpha by transition.animateFloat(
        initialValue = 1f,
        targetValue = if (online) 1f else 0.15f,
        animationSpec = infiniteRepeatable(tween(1000, easing = { if (it < 0.5f) 0f else 1f }), RepeatMode.Reverse),
        label = "linkBlink",
    )
    Box(Modifier.size(6.dp).background(color.copy(alpha = if (online) 1f else alpha)))
}

@Composable
private fun DiagRow(label: String, value: String, valueColor: Color) {
    val palette = LocalHbPalette.current
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        HbText(label, style = HbType.hud.copy(fontSize = 9.5.sp, letterSpacing = 0.12.em), color = palette.textFaint)
        HbText(value, style = HbType.hud.copy(fontSize = 9.5.sp), color = valueColor)
    }
}

/** HudFrame.tsx shortModel(). */
internal fun shortModel(id: String?): String {
    if (id.isNullOrEmpty()) return "—"
    return when (id) {
        "claude-opus-4-7" -> "OPUS 4.7"
        "claude-sonnet-4-6" -> "SONNET 4.6"
        "claude-haiku-4-5-20251001" -> "HAIKU 4.5"
        else -> id.removePrefix("claude-").uppercase(Locale.ENGLISH)
    }
}

/** HudFrame.tsx hostOf(). */
private fun hostOf(apiBase: String?): String {
    if (apiBase.isNullOrEmpty()) return "—"
    return runCatching { java.net.URI(apiBase).host ?: apiBase }.getOrDefault(apiBase)
}

private val CLOCK: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm:ss", Locale.ENGLISH)
private val DATE_TAG: DateTimeFormatter = DateTimeFormatter.ofPattern("EEE'.' dd MM", Locale.ENGLISH)
