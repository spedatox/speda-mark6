// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.TextUnitType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.data.IgorApi
import com.speda.heartbreaker.data.PendingAsk
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private const val POLL_MS = 3000L

/**
 * Global tray for irreversible operations an external peer (Optimus, Centurion)
 * is waiting on the owner to approve — mobile port of PendingAsksTray.tsx +
 * InteractionPrompt.tsx's PermissionPrompt.
 *
 * Mounted at [ChatScreen]'s root, beside the composer. GET /agents/asks is
 * agent-agnostic — [config]'s agentId does not matter here, only apiBase/apiKey
 * do — so this covers the whole external roster regardless of which agent is
 * on screen, and a switch between agents simply restarts the poll rather than
 * losing any real state.
 *
 * [fastArrival] is the SSE fast path (ChatViewModel.pendingAsk on a
 * `permission_request` frame) — folded into the same list the moment it fires,
 * rather than waiting up to [POLL_MS] for the next poll. It is upserted, not
 * appended: the next poll would return the same ask_id anyway, and treating it
 * as new every recomposition would restart its countdown.
 */
@Composable
fun PendingAsksTray(
    config: AppConfig,
    api: IgorApi,
    fastArrival: PendingAsk?,
    onFastArrivalConsumed: () -> Unit,
) {
    var asks by remember { mutableStateOf<List<PendingAsk>>(emptyList()) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(config.apiBase, config.apiKey) {
        while (true) {
            val fresh = api.fetchPendingAsks(config)
            // Keep any card already resolved locally out of a reappearance if the
            // backend has not caught up yet; otherwise trust the server's list.
            asks = fresh
            delay(POLL_MS)
        }
    }

    LaunchedEffect(fastArrival?.askId) {
        val ask = fastArrival ?: return@LaunchedEffect
        asks = (asks.filterNot { it.askId == ask.askId } + ask)
        onFastArrivalConsumed()
    }

    if (asks.isEmpty()) return

    Column(
        Modifier
            .fillMaxWidth()
            .heightIn(max = 320.dp)
            .padding(horizontal = 14.dp, vertical = 8.dp),
    ) {
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(asks, key = { it.askId }) { ask ->
                PermissionCard(
                    ask = ask,
                    onResolve = { approved, remember ->
                        asks = asks.filterNot { it.askId == ask.askId }
                        // Fire-and-forget: a failed answer means the ask is
                        // already gone server-side (see IgorApi.answerAsk), and
                        // the card is dropped locally either way.
                        scope.launch { api.answerAsk(config, ask.askId, approved, remember) }
                    },
                )
            }
        }
    }
}

/**
 * One irreversible operation a peer's safety gate stopped, waiting on the
 * owner. [ask.actionKey] is shown VERBATIM and never truncated — approving a
 * force-push means knowing which branch.
 *
 * The countdown is a deadline on the PEER's side, not a display flourish: when
 * it runs out the peer denies locally and carries on, so a card the owner never
 * answers is a "no" and has to visibly look like one.
 */
@Composable
fun PermissionCard(ask: PendingAsk, onResolve: (approved: Boolean, remember: Boolean) -> Unit) {
    val palette = LocalHbPalette.current
    var left by remember(ask.askId) { mutableStateOf(kotlin.math.ceil(ask.secondsLeft).toInt()) }
    LaunchedEffect(ask.askId, ask.secondsLeft) {
        left = kotlin.math.ceil(ask.secondsLeft).toInt()
        while (left > 0) { delay(1000); left -= 1 }
    }
    val expired = left <= 0

    Column(
        Modifier
            .fillMaxWidth()
            .hbGlass(shape = HbGlassShape.Card)
            .padding(12.dp),
    ) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            HbText(
                "◈ Permission — ${ask.agentId} wants to run ${ask.tool}",
                style = HbType.readout.copy(fontSize = 10.5.sp, letterSpacing = 0.5.sp),
                color = palette.accentBright,
                maxLines = 1,
                modifier = Modifier.weight(1f),
            )
            Spacer(Modifier.width(8.dp))
            HbText(
                if (expired) "expired — denied" else "${left}s",
                style = HbType.readout.copy(fontSize = 10.5.sp),
                color = if (expired) palette.red else if (left <= 20) palette.amber else palette.textDim,
            )
        }

        Spacer(Modifier.height(8.dp))

        Box(
            Modifier
                .fillMaxWidth()
                .heightIn(max = 140.dp)
                .clip(RoundedCornerShape(4.dp))
                .background(palette.text.copy(alpha = 0.03f))
                .padding(horizontal = 10.dp, vertical = 8.dp),
        ) {
            HbText(
                ask.actionKey,
                style = HbType.code.copy(fontSize = 12.sp),
                color = palette.text,
                modifier = Modifier.verticalScroll(rememberScrollState()),
            )
        }

        if (ask.reason.isNotEmpty()) {
            Spacer(Modifier.height(8.dp))
            HbText(
                ask.reason,
                style = HbType.readout.copy(fontSize = 11.sp, lineHeight = TextUnit(1.4f, TextUnitType.Em)),
                color = palette.textDim,
            )
        }

        Spacer(Modifier.height(10.dp))

        Row(
            Modifier.horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            AskButton("Approve", enabled = !expired, tint = palette.accentBright) { onResolve(true, false) }
            AskButton("Approve — don't ask again", enabled = !expired, tint = palette.textDim) { onResolve(true, true) }
            AskButton("Deny", enabled = !expired, tint = palette.red) { onResolve(false, false) }
        }
    }
}

@Composable
private fun AskButton(label: String, enabled: Boolean, tint: Color, onClick: () -> Unit) {
    val palette = LocalHbPalette.current
    Box(
        Modifier
            .clip(RoundedCornerShape(4.dp))
            .background(if (enabled) tint.copy(alpha = 0.12f) else Color.Transparent)
            .border(1.dp, if (enabled) tint.copy(alpha = 0.4f) else palette.textFaint.copy(alpha = 0.2f), RoundedCornerShape(4.dp))
            .then(if (enabled) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(horizontal = 12.dp, vertical = 7.dp),
    ) {
        HbText(label, style = HbType.readout.copy(fontSize = 10.5.sp), color = if (enabled) tint else palette.textFaint)
    }
}
