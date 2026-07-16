package com.speda.heartbreaker.ui.chat

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.Uplink
import com.speda.heartbreaker.designsystem.brand.Brands
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.ui.HbGlassButton
import com.speda.heartbreaker.ui.HbText

/**
 * The chat surface (M1) — the shell body. It carries a temporary control row
 * (agent switch / House Party / reset) and a session strip until the real
 * sidebar + header land in M3.
 */
@Composable
fun ChatScreen(
    graph: AppGraph,
    uplink: Uplink,
    agentId: String,
    partyEngaged: Boolean,
    onAgentChange: (String) -> Unit,
    onPartyToggle: () -> Unit,
    onResetUplink: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val palette = LocalHbPalette.current
    val vm: ChatViewModel = viewModel(
        factory = viewModelFactory { initializer { ChatViewModel(graph.api, graph.messageCache) } },
    )
    val config = remember(uplink, agentId) { AppConfig(uplink.apiBase, uplink.apiKey, agentId) }
    LaunchedEffect(config) { vm.onConfig(config) }

    val state by vm.state.collectAsStateWithLifecycle()
    val listState = rememberLazyListState()
    LaunchedEffect(state.messages.size, state.messages.lastOrNull()?.content?.length) {
        if (state.messages.isNotEmpty()) listState.animateScrollToItem(state.messages.lastIndex)
    }

    Column(modifier.fillMaxSize()) {
        // ── Temporary controls (agent switch / party / reset) — replaced by the
        //    real sidebar + header in M3. ─────────────────────────────────────
        Row(
            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 10.dp, vertical = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            for ((id, brand) in Brands.BRANDS) {
                val selected = id == agentId && !partyEngaged
                HbGlassButton(
                    label = brand.name,
                    onClick = { onAgentChange(id) },
                    state = if (selected) HbGlassState.Active else HbGlassState.Default,
                    shape = HbGlassShape.Pill,
                    contentColor = if (selected) palette.accentBright else palette.iconBright,
                )
            }
            HbGlassButton(
                label = if (partyEngaged) "STAND DOWN" else "HOUSE PARTY",
                onClick = onPartyToggle,
                state = if (partyEngaged) HbGlassState.Amber else HbGlassState.Default,
                shape = HbGlassShape.Pill,
                contentColor = if (partyEngaged) palette.amberBright else palette.iconBright,
            )
            HbGlassButton("RESET", onResetUplink, shape = HbGlassShape.Pill, contentColor = palette.textFaint)
        }

        // ── Session strip ─────────────────────────────────────────────────────
        Row(
            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 10.dp, vertical = 2.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            HbGlassButton("+ NEW", { vm.newChat() }, shape = HbGlassShape.Pill, contentColor = palette.accentBright)
            for (session in state.sessions) {
                val selected = session.id == state.activeSessionId
                HbGlassButton(
                    label = session.title ?: "SESSION ${session.id}",
                    onClick = { vm.selectSession(session.id) },
                    state = if (selected) HbGlassState.Amber else HbGlassState.Default,
                    shape = HbGlassShape.Pill,
                    contentColor = if (selected) palette.amberBright else palette.textDim,
                )
            }
        }

        // ── Messages / empty state ────────────────────────────────────────────
        Box(Modifier.weight(1f).fillMaxWidth()) {
            if (state.messages.isEmpty()) {
                Column(
                    Modifier.fillMaxSize().padding(24.dp),
                    verticalArrangement = Arrangement.Center,
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    HbText(
                        (Brands.BRANDS[agentId]?.name ?: "SPEDA").uppercase(),
                        style = HbType.headerBar,
                        color = palette.accent,
                        caps = true,
                    )
                    HbText("Ask anything.", style = HbType.read, color = palette.textDim)
                }
            } else {
                LazyColumn(
                    state = listState,
                    modifier = Modifier.fillMaxSize().padding(horizontal = 14.dp),
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 12.dp),
                ) {
                    items(state.messages, key = { it.id }) { message ->
                        MessageItem(message)
                    }
                }
            }
        }

        Composer(
            isStreaming = state.isStreaming,
            onSend = { vm.send(it) },
            onStop = { vm.stop() },
        )
    }
}
